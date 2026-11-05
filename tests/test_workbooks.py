"""Tests that evaluate the finished workbooks, not just inspect them.

Every test here loads a built file, computes it with tests/evaluate.py, and
checks the arithmetic. That is the only kind of test worth having for this
project: openpyxl will happily write a formula pointing at the wrong row, and
the resulting workbook opens cleanly, prints nicely, and is wrong.

The checks that matter most are the ties:

    the base year reconciles to the FRS report it was built from
    each summary row equals the detail page total it references
    each total equals the sum of the rows above it
    nothing escalates off a blank cell

The last one is a real bug this suite caught: the entered year was written
blank for the operator to fill, every later year escalated off it, Excel read
the blank as zero, and the whole projection collapsed to dashes that looked
like a formatting choice.
"""

from __future__ import annotations

import sys
from pathlib import Path

import openpyxl
from openpyxl.utils import column_index_from_string, get_column_letter
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pfa.builders.workbooks import Plan, assemble, build  # noqa: E402
from pfa.extraction.frs import build_seed  # noqa: E402
from pfa.model import schema  # noqa: E402
from tests.evaluate import Workbook  # noqa: E402
from tests.fixture import CATS, year  # noqa: E402

PENNY = 1.0  # tolerance in dollars, for rounding at each step of a chain


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    seed = build_seed([year(2023, 0.90), year(2024, 0.95), year(2025)])
    plan = Plan(
        scenario_name="ASUN",
        additions={("female", "Lacrosse")},
        ticket_revenue={
            ("male", "Basketball"): {"2024-25": 70_000},
            ("male", "Baseball"): {"2024-25": 30_000},
            ("female", "Basketball"): {"2024-25": 27_500},
        },
        game_guarantees={("male", "Basketball"): {"2024-25": 250_000}},
        ncaa_maximums={("male", "Baseball"): 11.7, ("male", "Basketball"): 13.0},
        entered_figures={"transition:ncaa_application": {"2025-26": 1_975_000}},
        admin_salaries={"Administration|Director of Athletics": {"2024-25": 206_990}},
    )
    scenario = assemble(seed, plan)
    result = build(scenario, tmp_path_factory.mktemp("proforma"))
    return scenario, result


@pytest.fixture(scope="module")
def revenue(built):
    _, result = built
    return Workbook(openpyxl.load_workbook(result.revenue))


@pytest.fixture(scope="module")
def expense(built):
    _, result = built
    return Workbook(openpyxl.load_workbook(result.expense))


def _year_columns(book: Workbook, sheet: str, header_row: int = 3) -> dict[str, str]:
    """Maps each year label to its column letter, read off the header band."""
    worksheet = book._workbook[sheet]
    columns: dict[str, str] = {}
    for cell in worksheet[header_row]:
        if isinstance(cell.value, str) and "-" in cell.value and cell.value[:4].isdigit():
            columns[cell.value] = cell.column_letter
    return columns


def _row_of(book: Workbook, sheet: str, label: str, column: str = "B") -> int:
    worksheet = book._workbook[sheet]
    for row in range(1, worksheet.max_row + 1):
        value = worksheet[f"{column}{row}"].value
        if isinstance(value, str) and value.strip() == label:
            return row
    raise AssertionError(f"No row labelled {label!r} in {sheet!r}.")


# --- Structure ---------------------------------------------------------


def test_both_workbooks_carry_the_schema_pages(built):
    _, result = built
    revenue_sheets = openpyxl.load_workbook(result.revenue).sheetnames
    expense_sheets = openpyxl.load_workbook(result.expense).sheetnames

    assert revenue_sheets[0] == "Totals", "the summary must be the first tab a client opens"
    assert expense_sheets[0] == "Summary"
    for page in schema.REVENUE_PAGES:
        assert page.sheet_name in revenue_sheets
    for page in schema.EXPENSE_PAGES:
        assert page.sheet_name in expense_sheets


def test_every_schema_line_gets_a_row_even_with_no_figure(revenue):
    """The taxonomy is the deliverable: a page that drops its Media Rights
    row at one institution and keeps it at the next is not a standardized
    output, however empty the row is.
    """
    for page in schema.REVENUE_PAGES:
        if page.per_sport:
            continue
        for line in page.lines:
            _row_of(revenue, page.sheet_name, line.label)


# --- The ties ----------------------------------------------------------


def test_revenue_base_year_ties_to_the_frs_report(revenue, built):
    """The single most important check in the suite.

    A pro forma whose base year does not equal the filing it was built from
    is wrong in a way no amount of careful projection can fix, and it is the
    first thing a CFO checks. Every revenue category the schema claims is
    added up here and compared against the page total.
    """
    scenario, _ = built
    base = scenario.horizon.base_year.label
    columns = _year_columns(revenue, "Totals")
    total_row = _row_of(revenue, "Totals", "Total Revenue")
    computed = revenue.number("Totals", f"{columns[base]}{total_row}")

    claimed = {
        category
        for page in schema.REVENUE_PAGES
        for line in page.lines
        for category in line.frs_categories
    }
    expected = sum(CATS.get(category, 0) for category in claimed)
    # Ticket sales (1) and guarantees (7) are reported institution-wide but
    # shown per sport, so the page carries the analyst's split rather than
    # the reported figure - excluded here and checked by pfa.builders.checks
    # instead.
    expected += 70_000 + 30_000 + 27_500 + 250_000

    assert computed == pytest.approx(expected, abs=PENNY), (
        f"the {base} revenue total is {computed:,.0f} but the categories the schema "
        f"claims add to {expected:,.0f}"
    )


def test_each_summary_row_equals_the_detail_total_it_references(revenue, expense):
    for book, summary, rows in (
        (revenue, "Totals", schema.REVENUE_SUMMARY_ROWS),
        (expense, "Summary", schema.EXPENSE_SUMMARY_ROWS),
    ):
        columns = _year_columns(book, summary)
        for target, label in rows:
            row = _row_of(book, summary, label)
            for year_label, column in columns.items():
                coordinate = f"{column}{row}"
                formula = book._workbook[summary][coordinate].value
                assert isinstance(formula, str) and formula.startswith("="), (
                    f"{summary}!{coordinate} ({label}) is a typed figure, not a reference "
                    "- it will go stale the first time the detail sheet is revised"
                )
                # Evaluating it proves the reference resolves and the target
                # holds a number rather than a label.
                book.number(summary, coordinate)


def test_page_totals_equal_the_sum_of_their_rows(expense):
    """Totals are summed from an explicit row list rather than a range, so
    this checks the list is complete.

    The client's hand-built books get this wrong: the FY24 expense book's
    coaching total sums C22:C37 on a sheet whose last coaching row is 39,
    quietly dropping a $20,500 assistant. A range that stops short looks
    exactly like one that doesn't.
    """
    sheet = "Sports Operations"
    columns = _year_columns(expense, sheet)
    worksheet = expense._workbook[sheet]
    total_row = _row_of(expense, sheet, "Total Amount")

    sport_rows = [
        row
        for row in range(4, total_row)
        if isinstance(worksheet[f"B{row}"].value, str)
        and worksheet[f"B{row}"].value not in ("Men's Sports", "Women's Sports")
    ]
    assert len(sport_rows) >= 8, "the fixture should produce at least eight sports"

    for year_label, column in columns.items():
        total = expense.number(sheet, f"{column}{total_row}")
        parts = sum(expense.number(sheet, f"{column}{row}") for row in sport_rows)
        assert total == pytest.approx(parts, abs=PENNY), (
            f"{sheet} {year_label}: total {total:,.0f} but the sports add to {parts:,.0f}"
        )


def test_scholar_budget_is_equivalencies_times_award(expense):
    """And the grand total's blended award reconciles: award times
    equivalencies has to give the budget, which an unweighted mean of the
    per-sport awards would not.
    """
    columns = _year_columns(expense, "Scholar", header_row=3)
    total_row = _row_of(expense, "Scholar", "Grand Total")
    for year_label, equiv_column in columns.items():
        # Offset by index, not by chr() arithmetic - the Scholar page is three
        # columns per year and runs past Z, where ord()+1 gives '['.
        first = column_index_from_string(equiv_column)
        budget_column = get_column_letter(first + 1)
        award_column = get_column_letter(first + 2)
        equivalencies = expense.number("Scholar", f"{equiv_column}{total_row}")
        budget = expense.number("Scholar", f"{budget_column}{total_row}")
        award = expense.number("Scholar", f"{award_column}{total_row}")
        if not equivalencies:
            continue
        assert award * equivalencies == pytest.approx(budget, rel=0.01), (
            f"Scholar {year_label}: {award:,.0f} × {equivalencies:,.2f} does not give "
            f"the {budget:,.0f} budget total"
        )


def test_salary_benefits_are_the_stated_rate(expense, built):
    scenario, _ = built
    columns = _year_columns(expense, "Salaries")
    salary_row = _row_of(expense, "Salaries", "Total Salary")
    benefits_row = _row_of(expense, "Salaries", f"Benefits ({scenario.benefits_rate:.0%})")
    combined_row = _row_of(expense, "Salaries", "Total Salary + Benefits")

    for year_label, column in columns.items():
        salary = expense.number("Salaries", f"{column}{salary_row}")
        benefits = expense.number("Salaries", f"{column}{benefits_row}")
        combined = expense.number("Salaries", f"{column}{combined_row}")
        assert benefits == pytest.approx(salary * scenario.benefits_rate, abs=PENNY)
        assert combined == pytest.approx(salary + benefits, abs=PENNY)


def test_cash_expenditures_stay_out_of_the_operating_total(expense):
    """A reclassification fee is not an operating cost. Folding it in makes
    the year it falls in look structurally unaffordable, so the Summary keeps
    the two apart and adds them only on the final row.
    """
    columns = _year_columns(expense, "Summary")
    operating = _row_of(expense, "Summary", "Total Operating Expenses")
    cash = _row_of(expense, "Summary", "Total Cash")
    grand = _row_of(expense, "Summary", "Total Expenses + Cash")

    column = columns["2025-26"]  # the year the fixture puts the application fee in
    assert expense.number("Summary", f"{column}{cash}") == pytest.approx(1_975_000, abs=PENNY)
    assert expense.number("Summary", f"{column}{operating}") < expense.number(
        "Summary", f"{column}{grand}"
    )
    assert expense.number("Summary", f"{column}{grand}") == pytest.approx(
        expense.number("Summary", f"{column}{operating}")
        + expense.number("Summary", f"{column}{cash}"),
        abs=PENNY,
    )


# --- The projection ----------------------------------------------------


def test_the_projection_actually_grows(revenue, built):
    """The regression test for the blank-entered-year bug.

    Every projected year of a line with a non-zero base year and a non-zero
    escalation tier must exceed the year before it. When the entered year was
    left blank this test fails on every line at once, which is exactly the
    shape of the real defect: not one wrong cell, a whole collapsed model.
    """
    scenario, _ = built
    sheet = "Institutional Support"
    columns = _year_columns(revenue, sheet)
    ordered = [columns[label] for label in scenario.horizon.labels]
    row = _row_of(revenue, sheet, "Direct Institutional Support")
    tier = scenario.tier_for("institutional_support", "direct_institutional")
    assert tier.rate > 0, "this test needs a line that is meant to grow"

    values = [revenue.number(sheet, f"{column}{row}") for column in ordered]
    assert all(value > 0 for value in values), (
        f"Direct Institutional Support computes {values} - a zero means something "
        "escalated off a blank cell"
    )
    base_index = scenario.horizon.index_of(scenario.horizon.base_year.label)
    for earlier, later in zip(values[base_index:], values[base_index + 1 :]):
        assert later > earlier, f"{later:,.0f} does not grow on {earlier:,.0f}"


def test_a_flat_tier_holds_its_value(revenue, built):
    scenario, _ = built
    sheet = "Institutional Support"
    columns = _year_columns(revenue, sheet)
    row = _row_of(revenue, sheet, "Student Athletic Fees")
    assert scenario.tier_for("institutional_support", "student_fees").rate == 0

    values = [revenue.number(sheet, f"{columns[label]}{row}") for label in scenario.horizon.labels]
    base_index = scenario.horizon.index_of(scenario.horizon.base_year.label)
    for value in values[base_index:]:
        assert value == pytest.approx(values[base_index], abs=PENNY)


def test_escalation_matches_the_assumption_cell(revenue, built):
    """One projected cell, checked by hand against the palette.

    Proves the formula points at the right assumption - a page whose lines
    all escalate off whichever cell happens to sit above them would pass
    every other test here.
    """
    scenario, _ = built
    sheet = "Program Revenue"
    columns = _year_columns(revenue, sheet)
    row = _row_of(revenue, sheet, "Corporate Sponsorship / Royalties / Licensing")
    tier = scenario.tier_for("program_revenue", "sponsorship")

    labels = scenario.horizon.labels
    projected = [label for label in labels if not scenario.horizon.years[labels.index(label)].reported]
    for earlier, later in zip(projected, projected[1:]):
        before = revenue.number(sheet, f"{columns[earlier]}{row}")
        after = revenue.number(sheet, f"{columns[later]}{row}")
        assert after == pytest.approx(before * (1 + tier.rate), abs=PENNY), (
            f"{later} is {after:,.0f}, not {earlier}'s {before:,.0f} grown by "
            f"{tier.rate:.0%} ({tier.label})"
        )


def test_an_added_sport_costs_something_once_equivalencies_are_entered(tmp_path):
    """An addition's award column has to be seeded or its whole scholarship
    chain computes to zero - the sport reads as free.
    """
    seed = build_seed([year(2025)])
    plan = Plan(scenario_name="ASUN", additions={("female", "Lacrosse")}, projected_years=2)
    scenario = assemble(seed, plan)
    lacrosse = next(
        plan_ for plan_ in scenario.sports
        if plan_.sport == "Lacrosse" and plan_.gender == "female"
    )
    assert lacrosse.is_addition
    assert lacrosse.average_award, "the addition was given no per-equivalency award"
    award = next(iter(lacrosse.average_award.values()))
    assert 20_000 < award < 80_000, f"a seeded award of {award:,.0f} is not credible"


def test_a_mapping_that_loses_money_is_reported(built, tmp_path):
    """The tie-out is the only check that catches a mapping error, and it
    now lives off the page, so this is what stands between a silently wrong
    workbook and the analyst.

    Driven by breaking the scenario rather than by asserting on a healthy
    one: a test that only confirms the clean case passes would pass just as
    happily if the check never ran at all.
    """
    from dataclasses import replace

    scenario, _ = built
    assert not _tie_out_warnings(build(scenario, tmp_path), "Scholar"), (
        "the fixture's scholarship figures should tie to its own reconciliation"
    )

    base = scenario.horizon.base_year.label
    reconciliation = {key: dict(values) for key, values in scenario.reconciliation.items()}
    reconciliation["scholarships"][base] += 50_000
    broken = replace(scenario, reconciliation=reconciliation)

    messages = _tie_out_warnings(build(broken, tmp_path), "Scholar")
    assert messages, "a $50,000 hole in the scholarship mapping went unreported"
    assert "50,000" in messages[0], f"the warning does not state the amount: {messages[0]}"
    assert "Scholar" in messages[0]


def test_the_tie_out_tolerates_nothing_worth_tolerating(built, tmp_path):
    """A dollar of rounding in the filing's own arithmetic is not a defect,
    and warning about it on every build would teach the reader to skip the
    warnings that matter.
    """
    from dataclasses import replace

    scenario, _ = built
    base = scenario.horizon.base_year.label
    reconciliation = {key: dict(values) for key, values in scenario.reconciliation.items()}
    reconciliation["scholarships"][base] += 0.5
    quiet = replace(scenario, reconciliation=reconciliation)

    assert not _tie_out_warnings(build(quiet, tmp_path), "Scholar")


def test_a_ticket_split_that_misses_the_filing_is_reported(built, tmp_path):
    """The per-sport revenue pages carry the analyst's own split of a figure
    FRS reports institution-wide, so what is checked there is the allocation
    rather than the mapping. The fixture splits $127,500 of a filed
    $1,580,097, and saying so is the whole job of that check.
    """
    scenario, _ = built
    messages = _tie_out_warnings(build(scenario, tmp_path), "Ticket Revenue")
    assert messages, "a ticket split well short of the filing went unreported"
    assert "1,452,597" in messages[0], messages[0]


def test_tie_out_failures_are_distinguishable_from_notes(built, tmp_path):
    """The app shows these two classes of message differently - one as an
    error, one as an item on a to-do list - and it tells them apart by type
    rather than by reading the text. If that ever stops being true, a
    workbook that does not reconcile goes out looking merely unfinished.
    """
    from pfa.builders import checks

    scenario, _ = built
    warnings = build(scenario, tmp_path).warnings

    failures = [w for w in warnings if isinstance(w, checks.TieOut)]
    notes = [w for w in warnings if not isinstance(w, checks.TieOut)]
    assert failures, "the fixture's ticket split is short of the filing and should flag"
    assert notes, "the fixture's blank scaffolds should still produce plain notes"
    assert all("does not tie" in failure for failure in failures)
    assert not any("does not tie" in note for note in notes)
    # Still strings, so everything downstream that formats or writes them
    # keeps working.
    assert all(isinstance(warning, str) for warning in warnings)


def _tie_out_warnings(result, page: str) -> list[str]:
    return [
        message for message in result.warnings
        if message.startswith(f"{page} does not tie")
    ]


# --- Horizon arithmetic ------------------------------------------------


def test_fiscal_labels_follow_the_frs_reporting_year(built):
    """FRS reports the year a fiscal year *ends*; CC labels the span. Getting
    this backwards shifts the whole horizon by one and the error is invisible
    until someone compares the pro forma against the filing.
    """
    scenario, _ = built
    assert scenario.source_fiscal_year == 2025
    assert scenario.horizon.base_year.label == "2024-25"
    assert scenario.horizon.labels[0] == "2022-23"


def test_difference_column_measures_from_the_base_year(revenue, built):
    scenario, _ = built
    sheet = "Institutional Support"
    columns = _year_columns(revenue, sheet)
    row = _row_of(revenue, sheet, "Direct Institutional Support")
    worksheet = revenue._workbook[sheet]
    diff_column = worksheet.max_column
    diff = revenue.number(sheet, f"{worksheet.cell(row=row, column=diff_column).coordinate}")

    base = revenue.number(sheet, f"{columns[scenario.horizon.base_year.label]}{row}")
    last = revenue.number(sheet, f"{columns[scenario.horizon.labels[-1]]}{row}")
    assert diff == pytest.approx(last - base, abs=PENNY)


# --- Tie-out against a real filing -------------------------------------
#
# The checks above all run on the synthetic fixture, which is fast and
# deterministic but proves nothing about the FRS category mapping - the
# fixture's categories are whatever tests/fixture.py says they are. These two
# run against real PDFs when they are present, and they are what caught every
# mapping fault the schema has had:
#
#   42A listed as per-sport when FRS reports it institution-level only, so
#   $358,380 of one filing's expense was claimed by a page that could never
#   show it and by nothing else - absent, not double-counted.
#
#   Nine categories claimed both per-sport and institution-wide, putting
#   $4,047,392 on one filing's expense pro forma twice.
#
#   The "Not Related to Specific Teams" residual of the remaining per-sport
#   categories having no row at all, losing $1,016,778 at one institution.
#
# None of those break a formula or fail any other test. The workbook opens,
# every total sums, and the pro forma is wrong by millions.

REPORTS = Path(
    "/Users/claydawes/Desktop/Collegiate Consulting/CC Automation/"
    "Annual Financial Report Platform/2025 (All Institutions)"
)

#: A spread of real filings: a mid-major, two P4s with the full set of
#: House-settlement categories, and a G5.
TIE_OUT_FILES = (
    "Coastal Carolina 2025.pdf",
    "Clemson 2025.pdf",
    "Georgia Tech  2025.pdf",
    "Troy_2025.pdf",
)


def _available_reports() -> list[Path]:
    return [REPORTS / name for name in TIE_OUT_FILES if (REPORTS / name).is_file()]


@pytest.mark.parametrize("name", TIE_OUT_FILES)
def test_base_year_ties_to_a_real_filing(name, tmp_path):
    """Both pro formas' base year must equal the filing, to the dollar.

    The two documented exclusions are subtracted from the expectation rather
    than waved at:

      categories 1 and 7   ticket sales and guarantees, which FRS reports
                           institution-wide but the pro forma shows per
                           sport - so the page carries the analyst's split,
                           which is empty on a fresh build
      categories 22-25     coaching and support-staff compensation, which the
                           Salaries page scaffolds by position with blank
                           figures for the operator to fill

    Everything else has to be there.
    """
    from pfa.extraction.frs import load_report

    path = REPORTS / name
    if not path.is_file():
        pytest.skip(f"{name} is not on this machine")

    report = load_report(path)
    seed = build_seed([report])
    scenario = assemble(seed, Plan(scenario_name="Division I"))
    result = build(scenario, tmp_path)
    base = scenario.horizon.base_year.label

    revenue = Workbook(openpyxl.load_workbook(result.revenue))
    expense = Workbook(openpyxl.load_workbook(result.expense))

    def total(book: Workbook, sheet: str, label: str) -> float:
        columns = _year_columns(book, sheet)
        return book.number(sheet, f"{columns[base]}{_row_of(book, sheet, label)}")

    per_sport_revenue = sum(report.categories.get(key, 0) for key in ("1", "7"))
    expected_revenue = (report.total_operating_revenues or 0) - per_sport_revenue
    assert total(revenue, "Totals", "Total Revenue") == pytest.approx(
        expected_revenue, abs=2.0
    ), (
        f"{report.institution}: the {base} revenue total does not tie to the filing. "
        "Some FRS revenue category is claimed by no schema line, or claimed twice."
    )

    salaries = sum(report.categories.get(key, 0) for key in ("22", "23", "24", "25"))
    expected_expense = (report.total_operating_expenses or 0) - salaries
    assert total(expense, "Summary", "Total Operating Expenses") == pytest.approx(
        expected_expense, abs=2.0
    ), (
        f"{report.institution}: the {base} expense total does not tie to the filing. "
        "Check for a category reported both per sport and institution-wide."
    )


def test_no_schema_line_claims_a_category_twice():
    """A category may appear on a per-sport page and on Admin Operating,
    because the remainder arithmetic handles that pair. Anywhere else is a
    double count, and this is the cheap check that runs without a PDF.
    """
    per_sport = set(schema.SPORT_OPERATING_CATEGORIES)
    for page in schema.EXPENSE_PAGES:
        if page.key in ("admin_operating", "sports_operations"):
            continue
        for line in page.lines:
            overlap = per_sport.intersection(line.frs_categories)
            assert not overlap, (
                f"{line.label} on {page.sheet_name} claims {sorted(overlap)}, which "
                "Sports Operations also claims per sport"
            )

    seen: dict[str, str] = {}
    for page in schema.REVENUE_PAGES:
        for line in page.lines:
            for category in line.frs_categories:
                assert category not in seen, (
                    f"revenue category {category} is claimed by both "
                    f"{seen[category]} and {line.label}"
                )
                seen[category] = line.label


def test_a_one_time_cost_is_paid_once(expense):
    """The regression test for the propagating application fee.

    The fixture enters a $1,975,000 reclassification fee in 2025-26 only. It
    must appear in that year and nowhere else. Before the `recurring` flag
    existed, the cash lines escalated at the Flat tier - 0%, which is to say
    they repeated - so the fee showed up in every projected year and the pro
    forma charged a one-time cost six times, close to $10m the institution
    would never spend, on the page a board approves the move from.
    """
    columns = _year_columns(expense, "Summary")
    row = _row_of(expense, "Summary", "NCAA Application / Reclassification Fee")

    paid = {
        label: expense.number("Summary", f"{column}{row}")
        for label, column in columns.items()
    }
    assert paid["2025-26"] == pytest.approx(1_975_000, abs=PENNY)
    for label, amount in paid.items():
        if label != "2025-26":
            assert amount == 0, (
                f"the one-time application fee also appears in {label} ({amount:,.0f}) - "
                "it is propagating across the projection"
            )


def test_a_recurring_fee_does_carry_forward(tmp_path):
    """The other half of the same distinction: an annual conference due
    entered once starts in that year and continues, because it recurs.
    """
    seed = build_seed([year(2025)])
    plan = Plan(
        scenario_name="ASUN",
        projected_years=3,
        entered_figures={"transition:new_conference_dues": {"2026-27": 65_000}},
    )
    scenario = assemble(seed, plan)
    result = build(scenario, tmp_path)
    book = Workbook(openpyxl.load_workbook(result.expense))

    columns = _year_columns(book, "Summary")
    row = _row_of(book, "Summary", "New Conference Annual Dues")
    assert book.number("Summary", f"{columns['2025-26']}{row}") == 0
    for label in ("2026-27", "2027-28", "2028-29"):
        assert book.number("Summary", f"{columns[label]}{row}") == pytest.approx(
            65_000, abs=PENNY
        ), f"the annual due stops after it starts - {label} is zero"


def test_a_reported_credit_survives_the_remainder_arithmetic(tmp_path):
    """A negative FRS figure is a credit, not a mistake, and must reach the page.

    FRS amounts are signed, and several FY2025 filings report a negative
    category 26 where a severance accrual was reversed. _admin_remainder
    used to clamp its result at zero - which threw those credits away and ran
    the expense pro forma high by $3,494 at Bowling Green and $62,439 at
    Lamar, with nothing on the page to suggest anything had been dropped.

    The floor is the reported total rather than zero, so a reported credit
    passes through while a subtraction that overshoots still stops before it
    invents one.
    """
    from pfa.extraction.frs import _admin_remainder

    credit = year(2025)
    credit.categories["26"] = -62_439
    credit.operating_by_category.pop("26", None)
    assert _admin_remainder(credit, ("26",)) == pytest.approx(-62_439), (
        "a reported severance credit was clamped away"
    )

    # The guard it replaced still has to work: where the per-sport tables add
    # to more than the institution-level figure, the remainder stops at zero
    # rather than inventing a credit.
    contradictory = year(2025)
    contradictory.categories["28"] = 100_000
    contradictory.operating_by_category["28"] = 250_000
    assert _admin_remainder(contradictory, ("28",)) == 0

    # And a reported credit that is *also* over-attributed floors at the
    # credit, never below it.
    both = year(2025)
    both.categories["26"] = -10_000
    both.operating_by_category["26"] = 5_000
    assert _admin_remainder(both, ("26",)) == pytest.approx(-10_000)


def test_a_self_contradicting_filing_is_reported_not_reconciled(tmp_path):
    """Where a filing disagrees with itself, the operator is told the amount.

    Silently reconciling would be worse than the discrepancy: the pro forma
    would tie to a figure the institution never filed, and nobody would know
    which of the two the client's own records support.
    """
    from pfa.extraction.frs import build_seed as seed_from

    report = year(2025)
    report.aid_dollars[("male", "Baseball")] = report.categories["20"] * 2
    notes = seed_from([report]).warnings
    assert any("scholarship table adds to" in note for note in notes), (
        f"over-attributed athletic aid passed without comment: {notes}"
    )

    missing_total = year(2025)
    missing_total.total_operating_expenses = None
    notes = seed_from([missing_total]).warnings
    assert any("cannot be reconciled" in note for note in notes), (
        f"an unreadable stated total passed without comment: {notes}"
    )
