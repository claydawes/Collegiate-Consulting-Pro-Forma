"""Turning one or more NCAA FRS reports into a pro forma scenario.

**What the report can and cannot supply.** An FRS filing is a complete
statement of one year. A pro forma needs a base year and a plan, so the
report supplies the former entirely and the latter not at all:

    supplied in full     institution-level revenue and expense by category,
                         per-sport operating expense, per-sport athletic aid
                         and equivalencies, per-sport coaching totals,
                         rosters, cost of attendance
    supplied partly      ticket revenue and guarantees, which the form
                         reports institution-wide only - the per-sport split
                         is the analyst's
    not supplied at all  individual positions and salaries, conference fee
                         schedules, the growth the institution intends

So this module fills the reported years and leaves the rest to the app. It
never invents the missing half: a position with no figure stays blank and
the sheet says so, and the reconciliation rows let a reader confirm that
what *is* filled ties to the filing.

**Several reports, one scenario.** Hand `load_scenario` the institution's
last three filings and it builds three reported years. That is what makes the
projection arguable - a board can see the slope behind the forecast. Reports
are sorted by fiscal year and the latest becomes the base year.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pfa.extraction.parsers.ncaa_frs_coaching_expenses import parse_coaching_expenses
from pfa.extraction.parsers.ncaa_frs_other_items import parse_other_reporting_items
from pfa.extraction.parsers.ncaa_frs_participation import parse_participation_table
from pfa.extraction.parsers.ncaa_frs_scholarships import parse_scholarship_equivalencies
from pfa.extraction.parsers.ncaa_frs_sport_expenses import parse_sport_expenses
from pfa.extraction.parsers.ncaa_frs_summary import parse_frs_summary
from pfa.extraction.word_rows import read_rows, require_text_layer
from pfa.model import schema
from pfa.sports import display_name, form_order_key
from pfa.model.proforma import Horizon, SportPlan, Year

GENDERS = ("male", "female")


def fiscal_label(fiscal_year: int) -> str:
    """FRS's reporting year as CC's fiscal-year spelling.

    FRS reports "Reporting Year (FY): 2025", which is the year the fiscal
    year *ends*, and every pro forma CC produces labels that same year
    "2024-25". Getting this backwards shifts the whole horizon by one and the
    error is invisible until someone compares the pro forma against the
    filing.
    """
    return f"{fiscal_year - 1}-{str(fiscal_year)[-2:]}"


@dataclass
class ReportedYear:
    """One FRS filing, reduced to what a pro forma year needs."""

    path: Path
    institution: str
    fiscal_year: int
    #: Institution-level category ID -> amount.
    categories: dict[str, int] = field(default_factory=dict)
    #: (gender, sport) -> figure, for each per-sport table.
    operating: dict[tuple[str, str], float] = field(default_factory=dict)
    #: Per-sport expense category ID -> the total attributed to sports in
    #: that category. The institution-level figure for the same category is
    #: larger, because it also carries what the form calls "Expenses Not
    #: Related to Specific Teams" - so this is what has to be taken off the
    #: institution-level figure to get the departmental remainder. See
    #: _admin_remainder.
    operating_by_category: dict[str, float] = field(default_factory=dict)
    aid_dollars: dict[tuple[str, str], float] = field(default_factory=dict)
    equivalencies: dict[tuple[str, str], float] = field(default_factory=dict)
    coaching: dict[tuple[str, str], float] = field(default_factory=dict)
    roster: dict[tuple[str, str], int] = field(default_factory=dict)
    cost_of_attendance_in_state: float | None = None
    cost_of_attendance_out_of_state: float | None = None
    total_operating_revenues: int | None = None
    total_operating_expenses: int | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return fiscal_label(self.fiscal_year)

    def category_sum(self, categories: tuple[str, ...]) -> float | None:
        """The categories added, or None if the report carried none of them.

        None rather than 0 throughout. A category absent from the form and a
        category reported as zero are different claims, and the pro forma
        renders them differently - blank versus a dash - so they must not be
        conflated here.
        """
        present = [
            self.categories[category]
            for category in categories
            if category in self.categories
        ]
        return float(sum(present)) if present else None

    @property
    def sports(self) -> list[tuple[str, str]]:
        """Every (gender, sport) the filing says anything financial about.

        Built from the financial tables rather than from participation,
        because the two disagree in one direction: FRS reports aid, coaching
        pay and operating expense for track and cross country as a single
        combined row while participation splits them into two or three.
        Taking the axis from participation would produce rows with a
        headcount and no money.
        """
        keys = {
            *self.operating, *self.aid_dollars, *self.equivalencies, *self.coaching,
        }
        return sorted(keys, key=lambda pair: (pair[0] != "male", form_order_key(pair[1])))


def load_report(path: Path) -> ReportedYear:
    """Parses one FRS PDF into a ReportedYear.

    The header is required - without it the file is not an FRS report and
    there is nothing to build from. Every table below it is optional: a
    filing with one unreadable section still produces a year, with those
    figures missing and a warning naming what was missed. Refusing the whole
    report over one bad table would be worse, because the operator can fill
    a gap by hand and cannot fill a refusal.
    """
    require_text_layer(path)
    summary = parse_frs_summary(path)
    year = ReportedYear(
        path=path,
        institution=summary.institution_name,
        fiscal_year=summary.fiscal_year,
        categories=dict(summary.amounts_by_category_id),
        total_operating_revenues=summary.total_operating_revenues,
        total_operating_expenses=summary.total_operating_expenses,
    )

    # One read of the document, shared by every table parser - each walks the
    # whole 80-95 page file, and reading it once rather than six times is most
    # of this step's runtime.
    rows = read_rows(path)

    def attempt(label: str, parse):
        try:
            return parse(path, rows)
        except Exception as exc:  # noqa: BLE001 - degraded, not fatal
            year.warnings.append(
                f"{path.name}: could not read the {label} section ({exc})."
            )
            return None

    scholarships = attempt("Athlete Scholarships", parse_scholarship_equivalencies)
    coaching = attempt("Coaching Expenses", parse_coaching_expenses)
    expenses = attempt("per-sport Expenses by Object of Expenditure", parse_sport_expenses)
    participation = attempt("Athletics Participation", parse_participation_table)
    other = attempt("Other Reporting Items", parse_other_reporting_items)

    if other is not None:
        year.cost_of_attendance_in_state = other.cost_of_attendance_in_state
        year.cost_of_attendance_out_of_state = other.cost_of_attendance_out_of_state

    for gender in GENDERS:
        if scholarships is not None:
            for row in scholarships.team_specific(gender=gender):
                sport = display_name(row.sport)
                if sport is None:
                    continue
                if row.total_dollar_amount is not None:
                    year.aid_dollars[(gender, sport)] = row.total_dollar_amount
                if row.athletic_aid_equivalency is not None:
                    year.equivalencies[(gender, sport)] = row.athletic_aid_equivalency

        if coaching is not None:
            for row in coaching.team_specific(gender=gender):
                sport = display_name(row.sport)
                if sport is None:
                    continue
                paid = [
                    value
                    for value in (
                        row.head_coach_university_paid,
                        row.assistant_coach_university_paid,
                    )
                    if value is not None
                ]
                if paid:
                    year.coaching[(gender, sport)] = float(sum(paid))

        if expenses is not None:
            for sport in expenses.sports(gender=gender):
                canonical = display_name(sport)
                if canonical is None:
                    continue
                budget = _operating_budget(expenses, gender=gender, sport=canonical)
                if budget is not None:
                    year.operating[(gender, canonical)] = budget
            for category in schema.SPORT_OPERATING_CATEGORIES:
                table = expenses.tables.get(category)
                if table is None:
                    continue
                attributed = sum(
                    amount
                    for reported, amount in table.by_gender.get(gender, {}).items()
                    if amount is not None and display_name(reported) is not None
                )
                if attributed:
                    year.operating_by_category[category] = (
                        year.operating_by_category.get(category, 0.0) + attributed
                    )

        if participation is not None:
            counts = (
                participation.mens_roster() if gender == "male"
                else participation.womens_roster()
            )
            for sport, count in counts.items():
                canonical = display_name(sport)
                if canonical is not None and count is not None:
                    key = (gender, canonical)
                    # Combined rows (track/XC) take the sum of the
                    # participation rows they cover.
                    year.roster[key] = int(year.roster.get(key, 0) + count)

    return year


def _operating_budget(expenses, *, gender: str, sport: str) -> float | None:
    """A sport's operating budget: the schema's per-sport categories summed."""
    running: float | None = None
    for category in schema.SPORT_OPERATING_CATEGORIES:
        table = expenses.tables.get(category)
        if table is None:
            continue
        for reported, amount in table.by_gender.get(gender, {}).items():
            if display_name(reported) == sport and amount is not None:
                running = amount if running is None else running + amount
    return running


# --- Assembling a scenario --------------------------------------------


@dataclass
class ScenarioSeed:
    """Everything the reported years contribute to a scenario.

    Kept separate from the Scenario itself so the app can show the operator
    what was read, let them add the entered year and the plan, and only then
    assemble the immutable scenario the builders consume.
    """

    institution: str
    years: list[ReportedYear]
    line_figures: dict[str, dict[str, float]]
    sports: list[SportPlan]
    reconciliation: dict[str, dict[str, float]]
    warnings: list[str]

    @property
    def base(self) -> ReportedYear:
        return self.years[-1]

    @property
    def reported_labels(self) -> list[str]:
        return [year.label for year in self.years]

    def horizon(self, *, entered: int = 1, projected: int = 4) -> Horizon:
        """A horizon starting at the earliest reported year."""
        years = [Year(year.label, reported=True) for year in self.years]
        next_start = self.base.fiscal_year
        for offset in range(entered + projected):
            fiscal = next_start + 1 + offset
            years.append(
                Year(fiscal_label(fiscal), entered=offset < entered)
            )
        return Horizon(tuple(years))


def build_seed(reports: list[ReportedYear]) -> ScenarioSeed:
    """Folds a set of filings into the reported half of a scenario.

    Filings are sorted by fiscal year, so the operator can upload them in any
    order, and the latest becomes the base year.
    """
    if not reports:
        raise ValueError("No FRS reports were supplied, so there is no base year.")

    years = sorted(reports, key=lambda report: report.fiscal_year)
    warnings = [warning for year in years for warning in year.warnings]

    names = {year.institution for year in years}
    if len(names) > 1:
        warnings.append(
            "The uploaded reports name more than one institution "
            f"({', '.join(sorted(names))}) — check they belong to the same client. "
            f"Using “{years[-1].institution}”, from the most recent filing."
        )

    duplicates = _duplicate_years(years)
    if duplicates:
        warnings.append(
            f"More than one report for fiscal year(s) {', '.join(duplicates)}. Each year "
            "gets one column, so the later-sorted file silently wins — remove the duplicate."
        )

    line_figures = _line_figures(years, warnings)
    sports = _sport_plans(years)
    reconciliation = _reconciliation(years)

    return ScenarioSeed(
        institution=years[-1].institution,
        years=years,
        line_figures=line_figures,
        sports=sports,
        reconciliation=reconciliation,
        warnings=warnings,
    )


def _duplicate_years(years: list[ReportedYear]) -> list[str]:
    seen: set[int] = set()
    duplicates: set[int] = set()
    for year in years:
        if year.fiscal_year in seen:
            duplicates.add(year.fiscal_year)
        seen.add(year.fiscal_year)
    return [str(fiscal) for fiscal in sorted(duplicates)]


def _line_figures(
    years: list[ReportedYear], warnings: list[str]
) -> dict[str, dict[str, float]]:
    """Every fixed line item's reported years, keyed "page:line"."""
    figures: dict[str, dict[str, float]] = {}
    for page in (*schema.REVENUE_PAGES, *schema.EXPENSE_PAGES):
        for line in page.lines:
            if not line.frs_categories:
                continue
            key = f"{page.key}:{line.key}"
            departmental = page.key == "admin_operating"
            for year in years:
                value = (
                    _admin_remainder(year, line.frs_categories)
                    if departmental
                    else year.category_sum(line.frs_categories)
                )
                if value is not None:
                    figures.setdefault(key, {})[year.label] = value

    # Athletic aid the filing reports under category 20 but attributes to no
    # team. Without a line of its own the Scholar page cannot tie to the
    # filing: at Coastal Carolina FY25 it is $147,540 and at Clemson
    # $1,676,654, which is not a rounding difference.
    for year in years:
        reported = year.category_sum(("20",))
        if reported is None:
            continue
        attributed = sum(year.aid_dollars.values())
        remainder = reported - attributed
        if round(remainder, 2) > 0:
            figures.setdefault("scholarships:other_aid", {})[year.label] = remainder
        elif round(remainder, 2) < 0:
            # The per-sport scholarship table adds to more than category 20 -
            # the filing contradicting itself, exactly as it sometimes does
            # on the expense side. The Scholar page shows the per-sport rows,
            # so it runs above the reported total and there is no row that
            # could hold a negative remainder without reading as a refund.
            # Reported rather than reconciled: three institutions in the
            # FY2025 corpus do this, by $11, $32,898 and $229,358.
            warnings.append(
                f"FY{year.fiscal_year}: the per-sport scholarship table adds to "
                f"${attributed:,.0f} but category 20 reports ${reported:,.0f}. The "
                f"Scholar page shows the per-sport figures, so it runs "
                f"${-remainder:,.0f} above the reported total — the filing disagrees "
                "with itself and the institution should confirm which is right."
            )

    # The per-sport categories' own residual - see
    # schema.sport_categories_without_admin_home for why it needs a row.
    residual_categories = schema.sport_categories_without_admin_home()
    for year in years:
        residual = _admin_remainder(year, residual_categories)
        if residual is not None and round(residual, 2) > 0:
            figures.setdefault("sports_operations:unattributed", {})[year.label] = residual

    _warn_unclaimed(years[-1], warnings)
    _warn_double_counting(warnings)
    for year in years:
        _warn_internal_inconsistency(year, warnings)
    return figures


def _warn_internal_inconsistency(year: ReportedYear, warnings: list[str]) -> None:
    """Flags a filing that disagrees with itself.

    Two kinds show up in the FY2025 corpus, and both are the institution's
    filing rather than anything this code does - but both make the pro forma
    fail to tie, so the operator has to be told rather than left to discover
    it in front of a client.

    **Per-sport exceeding institution-level.** _admin_remainder clamps the
    remainder at zero, because a negative expense on the page would read as
    income. That clamp is right and it also hides the problem: Penn State's
    FY25 category 29 reports $961,004 of equipment at institution level
    against $1,799,918 across its own per-sport tables, so the Sports
    Operations page shows $839k more than the category it came from. The page
    is reporting what the per-sport tables say, which is the defensible
    choice, but the discrepancy is the institution's to explain.

    **A stated total that is not the sum of its own parts.** The US Naval
    Academy's FY25 filing states Total Operating Expenses of $71,564,811
    while its categories 20-44 add to $35,768,641. Nothing can be
    reconciled against a total like that, and a reader comparing the pro
    forma to the filing's own summary line will find a 2x gap that has no
    cause on our side.
    """
    for category, attributed in year.operating_by_category.items():
        reported = year.categories.get(category)
        if reported is None or attributed <= reported + 1:
            continue
        warnings.append(
            f"FY{year.fiscal_year} category {category}: the per-sport tables add to "
            f"${attributed:,.0f} but the institution-level figure is ${reported:,.0f}. "
            f"Sports Operations shows the per-sport total, so the expense pro forma runs "
            f"${attributed - reported:,.0f} above that category — the filing disagrees "
            "with itself and the institution should confirm which figure is right."
        )

    for label, total, first, last in (
        ("revenues", year.total_operating_revenues, 1, 19),
        ("expenses", year.total_operating_expenses, 20, 44),
    ):
        if total is None:
            # The filing's own roll-up line didn't parse, so there is nothing
            # to check the page against. Said out loud rather than skipped:
            # the reconciliation rows are the only guard against a mapping
            # error, and an operator should know when one of them is blank
            # for a reason other than the figures agreeing. One FY2025 filing
            # (Vermont) is like this.
            warnings.append(
                f"FY{year.fiscal_year}: could not read the filing's stated total "
                f"operating {label}, so the pro forma's {label} cannot be reconciled "
                "against it. The line items themselves parsed fine — check the total "
                "by hand before sending."
            )
            continue
        parts = sum(
            amount
            for category, amount in year.categories.items()
            if first <= _category_number(category) <= last
            and category not in _ROLLUP_CATEGORIES
        )
        if abs(total - parts) > 1:
            warnings.append(
                f"FY{year.fiscal_year}: the filing states total operating {label} of "
                f"${total:,.0f}, but its own categories {first}-{last} add to "
                f"${parts:,.0f} — a ${abs(total - parts):,.0f} discrepancy in the source "
                "document. The pro forma follows the categories, since those are what the "
                "line items are built from."
            )


def _category_number(category: str) -> int:
    digits = "".join(character for character in category if character.isdigit())
    return int(digits) if digits else -1


#: Categories that are the sum of their own lettered parts, so counting them
#: alongside those parts double-counts. Only one in the FY2025 form: 12
#: ("Total NCAA Distributions") = 12A + 12B + 12C exactly. See
#: _warn_unclaimed for why 41 and 42 are not roll-ups despite looking like
#: they should be.
_ROLLUP_CATEGORIES = frozenset({"12"})


def _admin_remainder(year: ReportedYear, categories: tuple[str, ...]) -> float | None:
    """An Admin Operating line's figure: the institution-level total less
    what the per-sport pages already claim.

    **This is the one piece of arithmetic in the whole project that is not
    just a lookup, and getting it wrong inflates the expense pro forma by
    millions.** Nine FRS categories are reported twice over - once per sport
    in the Expenses by Object of Expenditure tables, and once at institution
    level in the Revenue/Expense Summary, where the figure additionally
    carries what the form calls "Expenses Not Related to Specific Teams".
    Team travel, recruiting, medical, memberships, game expenses and the rest
    all work this way.

    Claiming both put $4,047,392 of Coastal Carolina's FY25 expense on the
    page twice: once inside Sports Operations and again inside Administrative
    Operations. The workbook opened cleanly, every total summed correctly,
    and the pro forma overstated the department's cost base by 8%.

    So a departmental line takes the remainder. That is also the right number
    conceptually: a reader looking at Administrative Operations wants the
    cost of running the department, not the cost of running the department
    plus every sport's travel budget restated.

    **The floor is the reported figure, not zero.** An earlier version
    clamped at zero, on the reasoning that a negative expense reads as
    income. That was wrong, and quietly: FRS amounts are genuinely signed,
    and several institutions report a negative category 26 where a severance
    accrual was reversed. Clamping those to zero threw the credit away and
    ran the expense page high - $3,494 at Bowling Green, $62,439 at Lamar.

    What actually needs guarding is the *subtraction* going negative, which
    means the per-sport tables add to more than the institution-level figure
    and is a contradiction in the filing. So the remainder is floored at the
    reported total rather than at zero: a reported credit passes through
    intact, while a subtraction that overshoots is stopped at the point where
    it would start inventing one.
    """
    total = year.category_sum(categories)
    if total is None:
        return None
    attributed = sum(
        year.operating_by_category.get(category, 0.0) for category in categories
    )
    return max(total - attributed, min(total, 0.0))


def _warn_double_counting(warnings: list[str]) -> None:
    """Guards the schema against the mistake _admin_remainder exists to fix.

    If a future edit adds a category to an Admin Operating line that is not
    also a per-sport category, the remainder arithmetic is a no-op for it and
    all is well. The danger is the reverse: a category on a *revenue* page or
    in the Scholar/Salaries mapping that also appears in the per-sport list,
    where nothing subtracts anything. Checked here so it surfaces as a note
    on the next build rather than as an inflated total nobody questions.
    """
    per_sport = set(schema.SPORT_OPERATING_CATEGORIES)
    for page in schema.EXPENSE_PAGES:
        if page.key in ("admin_operating", "sports_operations"):
            continue
        for line in page.lines:
            overlap = per_sport.intersection(line.frs_categories)
            if overlap:
                warnings.append(
                    f"Schema problem: “{line.label}” on {page.sheet_name} claims NCAA "
                    f"categor{'y' if len(overlap) == 1 else 'ies'} {', '.join(sorted(overlap))}, "
                    "which Sports Operations also claims per sport. That money is on the "
                    "expense pro forma twice."
                )


def _warn_unclaimed(base: ReportedYear, warnings: list[str]) -> None:
    """Flags money in the filing that no schema line claims.

    This is the check that catches a form revision. When the NCAA adds a
    category - as it did with the House settlement items in FY2025 - a pro
    forma built on the old taxonomy silently drops it, and the base year
    stops tying to the filing with nothing to say why. Here it says why.

    Roll-up categories are excluded, because claiming one as well as its
    parts would double-count. 12 ("Total NCAA Distributions") is the only
    one: at Coastal Carolina FY25, 12A + 12B + 12C = 1,394,305 + 4,600 +
    164,975 = 1,563,880, which is exactly category 12.

    41 and 42 look like roll-ups of 41A/41B and 42A/42B and are not - the
    lettered forms are separate line items, which the figures settle: at
    Clemson FY25, 41 is 1,727,539 against 41A's 2,036,547, and 42 is 991,291
    against 42A's 328,000 plus 42B's 251,273. A parent is never smaller than
    its parts, so these are siblings and each has to be claimed on its own.
    Treating them as roll-ups here is what hid 41B and 42B from the schema
    until this check was pointed at a real filing.
    """
    rollups = set(_ROLLUP_CATEGORIES)
    claimed = schema.revenue_frs_categories() | schema.expense_frs_categories() | rollups
    unclaimed = sorted(
        category
        for category, amount in base.categories.items()
        if category not in claimed and amount
    )
    if unclaimed:
        warnings.append(
            f"FY{base.fiscal_year} reports figures in NCAA categories "
            f"{', '.join(unclaimed)}, which no pro forma line claims — that money is not "
            "on either workbook. Check whether the NCAA form has changed."
        )


def _sport_plans(years: list[ReportedYear]) -> list[SportPlan]:
    """One SportPlan per sport the filings carry, across every reported year.

    Average award is left empty: the Scholar page derives the base year's
    from aid over equivalencies, which is the figure the filing implies, and
    deriving it there keeps the arithmetic visible on the page rather than
    baked into a literal here.
    """
    keys: list[tuple[str, str]] = []
    for year in years:
        for key in year.sports:
            if key not in keys:
                keys.append(key)

    plans: list[SportPlan] = []
    for gender, sport in keys:
        plans.append(
            SportPlan(
                sport=sport,
                gender=gender,
                equivalencies={
                    year.label: year.equivalencies[(gender, sport)]
                    for year in years
                    if (gender, sport) in year.equivalencies
                },
                aid_dollars={
                    year.label: year.aid_dollars[(gender, sport)]
                    for year in years
                    if (gender, sport) in year.aid_dollars
                },
                operating={
                    year.label: year.operating[(gender, sport)]
                    for year in years
                    if (gender, sport) in year.operating
                },
                coaching={},
            )
        )
    return plans


def _reconciliation(years: list[ReportedYear]) -> dict[str, dict[str, float]]:
    """The reported totals each page checks itself against.

    Keyed to match pfa/model/schema page keys where there is one, plus the two
    salary blocks. A page with no entry here simply gets no reconciliation
    row, which is correct for the per-sport revenue pages at an institution
    whose filing reported nothing in those categories.
    """
    sources: dict[str, tuple[str, ...]] = {
        "ticket_revenue": ("1",),
        "game_guarantee": ("7",),
        "scholarships": ("20",),
        "admin_salaries": ("24", "25"),
        "coaching_salaries": ("22", "23"),
    }
    result: dict[str, dict[str, float]] = {}
    for key, categories in sources.items():
        for year in years:
            value = year.category_sum(categories)
            if value is not None:
                result.setdefault(key, {})[year.label] = value

    # Sports Operations and Admin Operating reconcile against the sum of what
    # each actually draws on, not against a single reported category - there
    # isn't one. Per-sport operating expense is reported per sport and summed
    # here; the institution-level lines are summed from their own categories.
    for year in years:
        # Sports Operations reconciles against the per-sport figures plus the
        # residual the page also carries, so the check matches what the page
        # actually shows rather than only its sport rows.
        attributed = sum(year.operating.values())
        residual = _admin_remainder(
            year, schema.sport_categories_without_admin_home()
        ) or 0.0
        if attributed or residual:
            result.setdefault("sports_operations", {})[year.label] = float(
                attributed + residual
            )

        # Admin Operating reconciles against the remainder, not against the
        # raw institution-level sum - the page carries the remainder, so
        # checking it against the gross figure would report a variance of
        # several million on a page that is right.
        categories = tuple(
            category
            for line in schema.ADMIN_OPERATING.lines
            for category in line.frs_categories
        )
        remainder = _admin_remainder(year, categories)
        if remainder is not None:
            result.setdefault("admin_operating", {})[year.label] = remainder

    return result
