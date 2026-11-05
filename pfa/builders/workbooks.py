"""Building both pro formas, and assembling the scenario they come from.

Two entry points:

    assemble()   reported years + the operator's plan -> a Scenario
    build()      a Scenario -> the two .xlsx files

Kept apart because the app needs the first on its own: the operator picks
sports, sets the horizon and enters the plan against what the reports
actually carried, and that needs a scenario built, inspected and rebuilt
several times before anything is written to disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pfa.builders.expense import build_expense_workbook
from pfa.builders.revenue import build_revenue_workbook
from pfa.extraction.frs import ScenarioSeed
from pfa.model.proforma import (
    DEFAULT_PAGE_TIERS,
    DEFAULT_TIERS,
    RateTier,
    Scenario,
    SportPlan,
)
from pfa.sports import form_order_key


@dataclass(frozen=True)
class Plan:
    """What the operator supplies on top of the reports.

    Everything here is a judgement the filings cannot make. The defaults are
    CC's usual starting point rather than a guess at this institution: a
    six-year horizon with one entered year, the default escalation palette,
    and a 30% benefits load.
    """

    scenario_name: str
    #: Years of projection after the entered year.
    projected_years: int = 4
    #: Years the operator enters by hand between the last filing and the
    #: projection. Usually one - the year the institution is budgeting now.
    entered_years: int = 1
    benefits_rate: float = 0.30
    tiers: tuple[RateTier, ...] = DEFAULT_TIERS
    line_tiers: dict[str, str] | None = None
    #: (gender, sport) pairs to include. None means every sport the reports
    #: carried.
    sports: set[tuple[str, str]] | None = None
    #: Sports to add that the institution does not currently sponsor, as
    #: (gender, sport). They get zeroed reported years and a note saying so.
    additions: set[tuple[str, str]] = frozenset()
    #: NCAA equivalency limits by (gender, sport), for the Scholar page's
    #: reference columns.
    ncaa_maximums: dict[tuple[str, str], float] | None = None
    new_maximums: dict[tuple[str, str], float] | None = None
    #: Figures the operator entered, keyed exactly as Scenario.line_figures.
    entered_figures: dict[str, dict[str, float]] | None = None
    admin_salaries: dict[str, dict[str, float]] | None = None
    #: Per-sport coaching salaries: (gender, sport) -> position -> year -> $.
    coaching_salaries: dict[tuple[str, str], dict[str, dict[str, float]]] | None = None
    #: Per-sport ticket and guarantee revenue: (gender, sport) -> year -> $.
    ticket_revenue: dict[tuple[str, str], dict[str, float]] | None = None
    game_guarantees: dict[tuple[str, str], dict[str, float]] | None = None


def assemble(seed: ScenarioSeed, plan: Plan) -> Scenario:
    """Folds the reports and the plan into one immutable Scenario."""
    horizon = seed.horizon(entered=plan.entered_years, projected=plan.projected_years)

    chosen = plan.sports
    sports: list[SportPlan] = []
    for base in seed.sports:
        key = (base.gender, base.sport)
        if chosen is not None and key not in chosen:
            continue
        sports.append(_with_plan(base, key, plan, is_addition=False))

    carried = {(base.gender, base.sport) for base in seed.sports}
    blended = _blended_award(seed, horizon.base_year.label)
    for key in sorted(plan.additions, key=lambda pair: (pair[0] != "male", form_order_key(pair[1]))):
        if key in carried:
            continue
        gender, sport = key
        # An added sport needs a per-equivalency award in the base year or
        # its whole scholarship chain computes to zero: the award column
        # escalates off the year to its left, and escalating nothing gives
        # nothing, so the sport's budget reads as free. Seeded with what an
        # equivalency actually costs at this institution - total aid over
        # total equivalencies across the sports it does run - which is both
        # the best available estimate and a figure a reader can check against
        # the Grand Total row two inches below.
        sports.append(
            _with_plan(
                SportPlan(
                    sport=sport,
                    gender=gender,
                    average_award=(
                        {horizon.base_year.label: blended} if blended else {}
                    ),
                ),
                key, plan, is_addition=True,
            )
        )

    sports.sort(key=lambda item: (item.gender != "male", form_order_key(item.sport)))

    figures = {key: dict(values) for key, values in seed.line_figures.items()}
    for key, values in (plan.entered_figures or {}).items():
        # The operator's entries win over the report's, line by line and year
        # by year. That ordering matters: an analyst correcting a figure the
        # parser misread must not have it overwritten on the next rebuild.
        figures.setdefault(key, {}).update(values)

    return Scenario(
        institution=seed.institution,
        scenario_name=plan.scenario_name,
        horizon=horizon,
        tiers=plan.tiers,
        line_tiers=dict(plan.line_tiers if plan.line_tiers is not None else DEFAULT_PAGE_TIERS),
        default_tier="inflation",
        benefits_rate=plan.benefits_rate,
        line_figures=figures,
        admin_salaries=dict(plan.admin_salaries or {}),
        sports=tuple(sports),
        reconciliation=dict(seed.reconciliation),
        source_fiscal_year=seed.base.fiscal_year,
        source_note=_source_note(seed),
    )


def _blended_award(seed: ScenarioSeed, year: str) -> float | None:
    """What one equivalency costs at this institution in `year`: total
    athletic aid over total equivalencies across the sports it runs.

    Weighted rather than a mean of the per-sport awards, for the same reason
    the Scholar page's total rows are - an unweighted mean counts a
    four-equivalency golf programme equally with a thirteen-equivalency
    basketball one. None when the reports give no equivalencies to divide by.
    """
    dollars = sum(
        plan.aid_dollars[year] for plan in seed.sports if year in plan.aid_dollars
    )
    equivalencies = sum(
        plan.equivalencies[year] for plan in seed.sports if year in plan.equivalencies
    )
    if not equivalencies or not dollars:
        return None
    return round(dollars / equivalencies, 2)


def _with_plan(
    base: SportPlan, key: tuple[str, str], plan: Plan, *, is_addition: bool
) -> SportPlan:
    """One sport, with the operator's own figures layered over the report's."""
    return SportPlan(
        sport=base.sport,
        gender=base.gender,
        ncaa_maximum=(plan.ncaa_maximums or {}).get(key, base.ncaa_maximum),
        new_maximum=(plan.new_maximums or {}).get(key, base.new_maximum),
        equivalencies=base.equivalencies,
        aid_dollars=base.aid_dollars,
        average_award=base.average_award,
        operating=base.operating,
        ticket=(plan.ticket_revenue or {}).get(key, base.ticket),
        guarantee=(plan.game_guarantees or {}).get(key, base.guarantee),
        coaching=(plan.coaching_salaries or {}).get(key, base.coaching),
        is_addition=is_addition,
    )


def _source_note(seed: ScenarioSeed) -> str:
    years = ", ".join(seed.reported_labels)
    files = ", ".join(sorted(year.path.name for year in seed.years))
    return (
        f"Reported years ({years}) are as filed with the NCAA Financial Reporting "
        f"System: {files}. Every later year is a projection."
    )


@dataclass(frozen=True)
class Built:
    """What a build produced."""

    revenue: Path
    expense: Path
    warnings: list[str]


def build(scenario: Scenario, directory: Path, *, stem: str | None = None) -> Built:
    """Writes both workbooks into `directory`.

    Named the way CC names them - "<institution> - Pro Forma - Revenue -
    <scenario>.xlsx" - so a folder of them sorts by institution and the pair
    for one engagement sits together.
    """
    directory.mkdir(parents=True, exist_ok=True)
    prefix = stem or f"{scenario.institution} - Pro Forma"
    revenue = directory / f"{_safe(prefix)} - Revenue - {_safe(scenario.scenario_name)}.xlsx"
    expense = directory / f"{_safe(prefix)} - Expense - {_safe(scenario.scenario_name)}.xlsx"

    warnings = build_revenue_workbook(scenario, revenue)
    warnings += build_expense_workbook(scenario, expense)
    warnings += _cross_check(scenario)
    return Built(revenue=revenue, expense=expense, warnings=warnings)


def _cross_check(scenario: Scenario) -> list[str]:
    """Checks the scenario makes sense before the files go out.

    These are the mistakes that produce a workbook which opens cleanly and
    is wrong - the kind nobody catches until a client does.
    """
    warnings: list[str] = []

    if not scenario.sports:
        warnings.append(
            "No sports are in the scenario, so the Scholar, Sports Operations and "
            "coaching pages are empty. Pick the sports before sending this."
        )

    unfunded = [
        plan.sport for plan in scenario.sports
        if plan.equivalencies and not any(
            plan.average_award.get(year) or plan.aid_dollars.get(year)
            for year in scenario.horizon.labels
        )
    ]
    if unfunded:
        warnings.append(
            "Equivalencies but no aid dollars for "
            f"{', '.join(sorted(set(unfunded)))} — the Scholar page cannot derive an "
            "average award, so those budgets compute as blank. Enter the award."
        )

    # No base-year completeness check here: every page already names each of
    # its own blank lines as it writes them, and repeating that per line at
    # the end turns a short, actionable list into a wall nobody reads.

    if scenario.benefits_rate <= 0 or scenario.benefits_rate > 0.6:
        warnings.append(
            f"The benefits load is set to {scenario.benefits_rate:.0%}, which is outside "
            "the 20-40% range these pro formas normally use. Confirm it before sending."
        )

    return warnings


def _safe(text: str) -> str:
    """A filename component with the characters a file system objects to
    removed, and runs of whitespace collapsed.
    """
    cleaned = "".join(" " if character in '/\\:*?"<>|' else character for character in text)
    return " ".join(cleaned.split()) or "Pro Forma"
