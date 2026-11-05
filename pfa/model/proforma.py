"""The scenario a pro forma projects: its horizon, its growth assumptions,
and the figures its base years are anchored to.

**The horizon.** A pro forma is a run of fiscal years, some reported and some
projected. The reported ones are hard figures off an FRS report; the
projected ones are formulas against the year to their left. The client's own
books run between five and eight years and start with two or three reported
years, which is deliberate - a board looking at a six-year projection wants
to see the two years behind it to judge whether the slope is credible.

**Growth, as a palette rather than a rate.** The obvious design is one
escalation rate per page. The client's own pro formas don't do that, and the
reason is worth keeping: the FY26 ASUN revenue book puts five rates in a row
above the Program Revenue table - 30%, 20%, 15%, 10%, 5% - and each line
points at whichever one its analyst judged. Corporate sponsorship grows
aggressively in the years the institution is buying attention and steadily
after; an endowment draw grows at 5% throughout. A single page rate cannot
say that, and forcing one makes the model less honest rather than simpler.

So a scenario carries a named palette, and each line is assigned a tier. The
finished sheet puts the palette in labelled input cells at the top and every
projected cell is `=ROUND(prior*(1+$H$2),0)` against one of them. Changing a
tier moves every line on that tier, across every year - which is the thing
an analyst sitting with a CFO actually needs to do.

**What is not here.** No line items (pfa/model/schema.py) and no cell
addresses (the builders). A scenario is the judgement; the schema is the
shape; the builders put them on a page.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Year:
    """One column of the pro forma.

    `label` is the fiscal-year spelling CC uses throughout - "2025-26", not
    "FY26" and not 2026. It is what prints in the header.

    `reported` marks a year whose figures come out of an FRS report, so the
    builders write them as literals and the projection starts after the last
    of them. A year can also be `entered`: not reported, but given by the
    analyst rather than escalated - the first projected year of a
    reclassification is usually one of these, because that is the year the
    institution is making decisions about and a 3% bump off last year
    describes nothing.
    """

    label: str
    reported: bool = False
    entered: bool = False

    @property
    def is_literal(self) -> bool:
        """Whether this year's figures are written as values rather than as
        an escalation off the year before it.
        """
        return self.reported or self.entered


@dataclass(frozen=True)
class RateTier:
    """One escalation assumption in a scenario's palette."""

    key: str
    label: str
    rate: float


@dataclass(frozen=True)
class Horizon:
    """The run of years a pro forma covers."""

    years: tuple[Year, ...]

    def __post_init__(self) -> None:
        if len(self.years) < 2:
            raise ValueError(
                "A pro forma needs at least two years - one to anchor on and one to project."
            )
        if not self.years[0].is_literal:
            raise ValueError(
                f"The first year ({self.years[0].label}) has nothing to its left to escalate "
                "from, so it has to be a reported or entered year."
            )

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(year.label for year in self.years)

    @property
    def reported_years(self) -> tuple[Year, ...]:
        return tuple(year for year in self.years if year.reported)

    @property
    def base_year(self) -> Year:
        """The last reported year - what the pro forma is anchored to, and
        the year the reconciliation rows check against.
        """
        reported = self.reported_years
        return reported[-1] if reported else self.years[0]

    def index_of(self, label: str) -> int:
        return self.labels.index(label)

    @property
    def comparison_pair(self) -> tuple[int, int]:
        """The two years the Difference column subtracts: the base year and
        the last projected year.

        The client's books compare the final year against the *first* year of
        the projection rather than against the earliest reported year, and
        that is the right pair - the question a pro forma answers is what
        changes from where the institution is now, not from two years ago.
        """
        return (self.index_of(self.base_year.label), len(self.years) - 1)


@dataclass(frozen=True)
class SportPlan:
    """One sport's row on the per-sport pages, across the horizon.

    Figures are by year label. A missing year is escalated off the one
    before it; a present one is written as a literal. That is how a sport
    being added mid-horizon is expressed - zero in the reported years, an
    entered figure in the year it starts, escalation after.

    `equivalencies` and `aid_dollars` feed the Scholar page, `operating`
    the Sports Operations page, `ticket` and `guarantee` the two per-sport
    revenue pages. All optional: a sport the institution sponsors but which
    sells no tickets simply has none.
    """

    sport: str
    gender: str
    #: NCAA equivalency limit for this sport under the division being
    #: projected into, printed as a reference column on the Scholar page.
    ncaa_maximum: float | None = None
    #: The post-settlement limit, where it differs - the client's FY26 books
    #: carry both ("DI NCAA Max" and "NEW DI Max") because the gap between
    #: them is the scholarship cost the pro forma is sizing.
    new_maximum: float | None = None
    equivalencies: dict[str, float] = field(default_factory=dict)
    aid_dollars: dict[str, float] = field(default_factory=dict)
    #: Per-athlete award, where the analyst sets it directly rather than
    #: letting the sheet derive it from aid / equivalencies.
    average_award: dict[str, float] = field(default_factory=dict)
    operating: dict[str, float] = field(default_factory=dict)
    ticket: dict[str, float] = field(default_factory=dict)
    guarantee: dict[str, float] = field(default_factory=dict)
    #: Per-position salary figures by year, keyed by position label. Feeds
    #: the coaching block of the Salaries page.
    coaching: dict[str, dict[str, float]] = field(default_factory=dict)
    #: True when the institution does not sponsor this sport in the reported
    #: years - it gets a row, zeroed, and the note says so.
    is_addition: bool = False


@dataclass(frozen=True)
class Scenario:
    """Everything that makes one institution's pro forma this pro forma."""

    institution: str
    #: What the projection is towards, as it appears in every sheet title -
    #: "ASUN", "SoCon", "Division I". Not a conference lookup: it is a label.
    scenario_name: str
    horizon: Horizon
    tiers: tuple[RateTier, ...]
    #: "page:line" or "page:*" -> tier key. A page-level entry sets the
    #: default for every line on it.
    line_tiers: dict[str, str] = field(default_factory=dict)
    default_tier: str = "steady"
    #: Employer benefits load, as a share of salary. The client's books use
    #: 30-32%; it is stated on the page because it is one of the two or three
    #: assumptions a CFO always questions.
    benefits_rate: float = 0.30
    #: Fixed line-item figures by "page:line" then year label. Whatever the
    #: FRS report supplies lands here for the reported years; the analyst's
    #: own entries land here for the entered ones.
    line_figures: dict[str, dict[str, float]] = field(default_factory=dict)
    #: Administrative positions by year, keyed "Department|Position".
    admin_salaries: dict[str, dict[str, float]] = field(default_factory=dict)
    sports: tuple[SportPlan, ...] = ()
    #: The reported totals the base-year columns are checked against, keyed
    #: by the reconciliation row's own key.
    reconciliation: dict[str, float] = field(default_factory=dict)
    #: FY of the report the base year came from, for the source notes.
    source_fiscal_year: int | None = None
    source_note: str | None = None

    def __post_init__(self) -> None:
        keys = {tier.key for tier in self.tiers}
        if not self.tiers:
            raise ValueError("A scenario needs at least one escalation tier.")
        if self.default_tier not in keys:
            raise ValueError(
                f"The default tier {self.default_tier!r} is not in the palette "
                f"({', '.join(sorted(keys))})."
            )
        unknown = {value for value in self.line_tiers.values() if value not in keys}
        if unknown:
            raise ValueError(
                f"Lines are assigned tiers that are not in the palette: {', '.join(sorted(unknown))}."
            )

    # --- Tier resolution -----------------------------------------------

    def tier_for(self, page_key: str, line_key: str) -> RateTier:
        """The tier one line escalates on: its own assignment, else its
        page's, else the scenario default.
        """
        for candidate in (f"{page_key}:{line_key}", f"{page_key}:*"):
            if candidate in self.line_tiers:
                return self.tier(self.line_tiers[candidate])
        return self.tier(self.default_tier)

    def tier(self, key: str) -> RateTier:
        for tier in self.tiers:
            if tier.key == key:
                return tier
        raise KeyError(f"No escalation tier {key!r} in this scenario.")

    # --- Figure lookup -------------------------------------------------

    def figure(self, page_key: str, line_key: str, year: str) -> float | None:
        return self.line_figures.get(f"{page_key}:{line_key}", {}).get(year)

    def sports_for(self, gender: str) -> tuple[SportPlan, ...]:
        return tuple(plan for plan in self.sports if plan.gender == gender)


# --- The default palette ----------------------------------------------

#: The tiers a scenario starts with, and the labels they print under.
#:
#: Five of them, at the rates the client's own books use. "Flat" is 0% rather
#: than absent because a line that genuinely does not grow - a fixed
#: conference due, a GA stipend - needs to say so in the same language as
#: everything else, and a 0% tier keeps its projected cells as formulas that
#: an analyst can retier later rather than as hard-coded repeats.
DEFAULT_TIERS: tuple[RateTier, ...] = (
    RateTier("aggressive", "Aggressive", 0.30),
    RateTier("growth", "Growth", 0.20),
    RateTier("moderate", "Moderate", 0.10),
    RateTier("steady", "Steady", 0.05),
    RateTier("inflation", "Inflation", 0.03),
    RateTier("flat", "Flat", 0.00),
)

#: Which tier each page defaults to, where the schema's own reading of the
#: client's books is clear. Revenue lines an institution has to go and earn
#: are given the growth tiers; costs that simply inflate take Inflation;
#: anything institutionally fixed is Flat and the analyst moves it.
DEFAULT_PAGE_TIERS: dict[str, str] = {
    "institutional_support:*": "inflation",
    "institutional_support:student_fees": "flat",
    "ticket_revenue:*": "moderate",
    "game_guarantee:*": "steady",
    "program_revenue:*": "steady",
    "program_revenue:sponsorship": "growth",
    "program_revenue:contributions": "moderate",
    "program_revenue:endowment": "inflation",
    "ncaa_conference:*": "inflation",
    "scholarships:*": "inflation",
    "sports_operations:*": "inflation",
    "admin_operating:*": "inflation",
    "admin_operating:new_conference_dues": "flat",
    "admin_operating:new_conference_initiation": "flat",
    "admin_operating:current_conference_dues": "flat",
    "salaries:*": "inflation",
}


def default_scenario(
    institution: str,
    scenario_name: str,
    horizon: Horizon,
    **overrides,
) -> Scenario:
    """A scenario on the default palette and page tiers, for callers that
    only want to supply the figures.
    """
    settings: dict = {
        "tiers": DEFAULT_TIERS,
        "line_tiers": dict(DEFAULT_PAGE_TIERS),
        "default_tier": "inflation",
    }
    settings.update(overrides)
    return Scenario(
        institution=institution,
        scenario_name=scenario_name,
        horizon=horizon,
        **settings,
    )


def horizon_from(
    first_label: str,
    *,
    reported: int,
    entered: int = 1,
    projected: int,
) -> Horizon:
    """A horizon built by counting years forward from a label.

    `first_label` is a "2024-25"-style spelling. `reported` years come first
    (FRS figures), then `entered` (the analyst's own, usually the year the
    institution is budgeting now), then `projected` (escalated).
    """
    start = _start_year(first_label)
    years: list[Year] = []
    for offset in range(reported + entered + projected):
        label = _label(start + offset)
        if offset < reported:
            years.append(Year(label, reported=True))
        elif offset < reported + entered:
            years.append(Year(label, entered=True))
        else:
            years.append(Year(label))
    return Horizon(tuple(years))


def _start_year(label: str) -> int:
    """2024 out of "2024-25"."""
    try:
        return int(label.split("-")[0])
    except (ValueError, IndexError) as exc:
        raise ValueError(
            f"{label!r} is not a fiscal-year label - expected a spelling like '2024-25'."
        ) from exc


def _label(start: int) -> str:
    return f"{start}-{str(start + 1)[-2:]}"
