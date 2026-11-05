"""Reading a salary file and landing each line on the right scaffold row.

The failure that matters here is silent and expensive: a title that matches
the wrong position produces a workbook that opens cleanly, adds up, and puts
a $190,000 Director of Athletics on the Nutritionist row. So these tests are
mostly a corpus of titles as athletics departments actually write them, each
with the row it has to reach.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pfa.extraction import salaries  # noqa: E402

SPORTS = [
    ("male", "Baseball"),
    ("male", "Basketball"),
    ("male", "Golf"),
    ("female", "Basketball"),
    ("female", "Soccer"),
    ("female", "Softball"),
    ("female", "Volleyball"),
]


@pytest.fixture(scope="module")
def slots() -> list[salaries.Slot]:
    return salaries.all_slots(SPORTS)


@pytest.fixture(scope="module")
def by_key(slots) -> dict[str, salaries.Slot]:
    return {slot.key: slot for slot in slots}


def assign(slots, cases: list[tuple[str, str | None]]) -> list[salaries.Assignment]:
    rows = [
        salaries.SourceRow(index=index, title=title, salary=50_000 + index, sport=sport)
        for index, (title, sport) in enumerate(cases)
    ]
    return salaries.match(rows, slots)


# --- The titles themselves ---------------------------------------------

#: (title, sport column, expected slot key). Written the way the files
#: actually arrive - abbreviated, punctuated, inconsistently cased.
TITLES: list[tuple[str, str | None, str]] = [
    # Administration, abbreviated every way a payroll export does it
    ("Director of Athletics", None, "Administration|Director of Athletics"),
    ("Athletic Director", None, "Administration|Director of Athletics"),
    ("AD", None, "Administration|Director of Athletics"),
    ("Deputy AD", None, "Administration|Deputy / Executive Associate AD"),
    ("SWA", None, "Administration|Senior Woman Administrator"),
    ("Senior Woman Administrator", None, "Administration|Senior Woman Administrator"),
    ("Admin. Assistant", None, "Administration|Administrative Assistant"),
    # Business
    ("Business Manager", None, "Business Office|Business Manager"),
    ("Assoc AD - Business/Finance", None, "Business Office|Associate AD - Business / Finance"),
    # Internal / external
    ("Equipment Mgr", None, "Internal Operations|Equipment Manager"),
    ("Dir. of Facilities & Operations", None, "Internal Operations|Director of Facilities & Operations"),
    ("Assoc. Athletic Director, External Ops", None, "External Operations|Associate AD - External Operations"),
    ("Director of Marketing and Promotions", None, "External Operations|Director of Marketing & Promotions"),
    ("Dir of Ticketing", None, "External Operations|Director of Ticketing"),
    ("Director of Development", None, "External Operations|Director of Development"),
    # Communications
    ("SID", None, "Communications|Director of Athletics Communications"),
    ("Sports Information Director", None, "Communications|Director of Athletics Communications"),
    # Compliance / academics
    ("Assoc AD - Compliance", None, "Compliance / Academics|Associate AD - Compliance"),
    ("Academic Services Coordinator", None, "Compliance / Academics|Academic Services Coordinator"),
    # Sports medicine, where the numbering has to survive
    ("Head Athletic Trainer", None, "Sports Medicine|Head Athletic Trainer"),
    ("Asst AT #2", None, "Sports Medicine|Assistant Athletic Trainer #2"),
    ("Assistant Athletic Trainer #4", None, "Sports Medicine|Assistant Athletic Trainer #4"),
    # Strength, which is written four ways and never the scaffold's way
    ("S&C GA", None, "Strength & Conditioning|Strength & Conditioning - GA"),
    ("Director of Strength and Conditioning", None, "Strength & Conditioning|Director - Sports Performance"),
    # Welfare
    ("Mental Health Counselor", None, "Student-Athlete Welfare|Mental Health / Sport Psychologist"),
    ("Dietitian", None, "Student-Athlete Welfare|Nutritionist"),
    # Coaching, named in the title
    ("Head Men's Basketball Coach", None, "male|Basketball|Head Coach"),
    ("Womens Basketball Assistant Coach #3", None, "female|Basketball|Assistant Coach #3"),
    ("Dir. of Basketball Ops (M)", None, "male|Basketball|Director of Basketball Operations"),
    ("Head Softball Coach", None, "female|Softball|Head Coach"),
    ("Head Volleyball Coach", None, "female|Volleyball|Head Coach"),
    # Coaching, named by a Sport column instead
    ("Head Coach", "Baseball", "male|Baseball|Head Coach"),
    ("Associate Head Coach", "Baseball", "male|Baseball|Associate Head Coach"),
    ("Graduate Assistant", "Softball", "female|Softball|Assistant Coach - GA"),
    ("Head Coach", "Women's Soccer", "female|Soccer|Head Coach"),
]


@pytest.mark.parametrize("title,sport,expected", TITLES, ids=[t for t, _, _ in TITLES])
def test_a_title_reaches_its_scaffold_row(slots, title, sport, expected):
    assignment = assign(slots, [(title, sport)])[0]
    assert assignment.slot_key == expected, (
        f"{title!r} went to {assignment.slot_key!r} at {assignment.score:.2f}"
    )


def test_the_whole_corpus_lands_at_once(slots):
    """One at a time is the easy case. Together they compete for the same
    slots, and the one-to-one assignment has to hold.

    One title per position here. The corpus above deliberately carries
    several ways of writing the same job - "Director of Athletics", "AD" and
    "Athletic Director" - and feeding all three into one file would be three
    people competing for one row, which the matcher is right to refuse.
    """
    once: list[tuple[str, str | None, str]] = []
    claimed: set[str] = set()
    for title, sport, key in TITLES:
        if key in claimed:
            continue
        claimed.add(key)
        once.append((title, sport, key))

    assignments = assign(slots, [(title, sport) for title, sport, _ in once])
    wrong = [
        (title, want, assignment.slot_key)
        for (title, _, want), assignment in zip(once, assignments)
        if want != assignment.slot_key
    ]
    assert not wrong, "\n".join(f"{t!r}: wanted {w}, got {m}" for t, w, m in wrong)


# --- The properties that keep it honest --------------------------------


def test_identical_titles_fill_separate_numbered_slots(slots):
    """Two "Assistant Coach" rows under one sport are two people.

    Without one-to-one assignment they score identically against the same
    slot, one of them loses, and a coach vanishes from a page that still
    totals correctly.
    """
    assignments = assign(slots, [("Assistant Coach", "Women's Soccer")] * 2)
    keys = [assignment.slot_key for assignment in assignments]
    assert sorted(keys) == [
        "female|Soccer|Assistant Coach #1",
        "female|Soccer|Assistant Coach #2",
    ], keys


def test_a_third_assistant_is_offered_the_ga_row_rather_than_given_it(slots):
    """Soccer's scaffold has two numbered assistants and a GA. A third plain
    "Assistant Coach" has nowhere obvious to go.

    It is left unassigned, with the GA row offered in its dropdown. A
    graduate assistant is not an assistant - the labels differ by one word
    and by about $45,000 - and placing one on the other is a judgement, not
    a match. The row is listed as needing attention, so the money is in
    front of the analyst rather than lost. The first two, which are
    unambiguous, stay clear.
    """
    assignments = assign(slots, [("Assistant Coach", "Women's Soccer")] * 3)
    assert [a.confidence for a in assignments[:2]] == ["strong", "strong"]

    last = assignments[-1]
    assert last.slot_key is None
    assert "female|Soccer|Assistant Coach - GA" in last.candidates


def test_a_sport_the_scenario_does_not_run_is_left_alone(slots):
    """Football is in the file; the institution does not play it here.

    The row must come back unassigned rather than being pushed onto the
    nearest available coaching slot, which would be a different sport's.
    """
    assignment = assign(slots, [("Head Football Coach", None)])[0]
    assert assignment.slot_key is None
    assert assignment.candidates == []


def test_a_coaching_title_with_no_sport_is_not_guessed(slots):
    """"Head Coach" with nothing naming a sport could be any of seven. The
    matcher must decline rather than pick the first.
    """
    assignment = assign(slots, [("Head Coach", None)])[0]
    assert assignment.slot_key is None


def test_rank_disagreement_outweighs_shared_words(slots):
    """"Head Coach" and "Assistant Coach" share every word but one and are
    never the same person.
    """
    assert salaries.similarity("Head Coach", "Head Coach") == salaries.EXACT
    assert salaries.similarity("Head Coach", "Assistant Coach #2") < 0.5


def test_an_exact_label_wins_every_tie(slots):
    """A title identical to a scaffold label must take that label, even
    when a longer label containing it scores just as well.

    This is a real regression. "Director - Sports Performance" and
    "Assistant Director - Sports Performance" both saturated at 1.0 against
    a file row reading "Director - Sports Performance", and the tie broke
    alphabetically onto the assistant - putting the director's salary on
    the assistant's row, and the assistant's somewhere else again.
    """
    assert salaries.similarity(
        "Director - Sports Performance", "Director - Sports Performance"
    ) > salaries.similarity(
        "Director - Sports Performance", "Assistant Director - Sports Performance"
    )

    # And through the assignment, with the other row competing for it.
    assignments = assign(slots, [
        ("Director - Sports Performance", None),
        ("Director S&C", None),
    ])
    assert assignments[0].slot_key == "Strength & Conditioning|Director - Sports Performance"


def test_a_weak_match_is_left_for_the_analyst(slots):
    """Below the likely threshold nothing is assigned - but the candidates
    are still offered, so the dropdown is useful rather than empty.
    """
    assignment = assign(slots, [("Pep Band Liaison", None)])[0]
    assert assignment.slot_key is None
    assert assignment.confidence == "unmatched"


def test_every_assignment_is_reviewable(slots):
    """Nothing is applied that the analyst cannot see and re-point: each
    assignment names its score and carries candidates.
    """
    for assignment in assign(slots, [(title, sport) for title, sport, _ in TITLES]):
        assert assignment.confidence in ("strong", "likely", "weak", "unmatched")
        if assignment.slot_key:
            assert assignment.slot_key in assignment.candidates


# --- Reading the file --------------------------------------------------


def workbook_bytes(rows: list[list]) -> bytes:
    book = openpyxl.Workbook()
    sheet = book.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def test_a_header_below_a_title_block_is_found():
    """Payroll exports lead with a title and a blank line. Reading row 1 as
    the header finds one column called "FY2025 Salary Detail".
    """
    data = workbook_bytes([
        ["Lakeview State University"],
        ["FY2025 Athletics Salary Detail"],
        [],
        ["Position", "Sport", "Base Salary"],
        ["Head Coach", "Baseball", 92_000],
        ["Director of Athletics", None, 210_000],
    ])
    rows = salaries.read_salary_file("payroll.xlsx", data)
    assert [row.title for row in rows] == ["Head Coach", "Director of Athletics"]
    assert rows[0].sport == "Baseball"
    assert rows[1].salary == 210_000


def test_money_formatting_and_total_rows_are_handled():
    data = workbook_bytes([
        ["Position", "Salary"],
        ["Director of Athletics", "$210,000.00"],
        ["Business Manager", "68,500"],
        ["Severance adjustment", "(4,000)"],
        ["Total", "274,500"],
        ["Vacant", ""],
    ])
    rows = salaries.read_salary_file("list.xlsx", data)
    titles = [row.title for row in rows]
    assert "Total" not in titles, "a subtotal row was read as a position"
    assert "Vacant" not in titles, "a row with no figure was read as a position"
    assert rows[0].salary == 210_000
    assert rows[2].salary == -4_000


def test_a_csv_with_no_header_still_reads():
    data = b"Director of Athletics,210000\nBusiness Manager,68500\nEquipment Manager,41000\n"
    rows = salaries.read_salary_file("paste.csv", data)
    assert len(rows) == 3
    assert rows[0].title == "Director of Athletics"
    assert rows[0].salary == 210_000


def test_a_file_that_cannot_be_read_says_why():
    """Refusing clearly beats importing half of it. A salary file that
    silently contributes three rows out of ninety is worse than one that
    does not import at all, because the workbook looks filled in.
    """
    with pytest.raises(salaries.SalaryFileError) as wrong_type:
        salaries.read_salary_file("payroll.pdf", b"%PDF-1.4")
    assert "xlsx" in str(wrong_type.value)

    with pytest.raises(salaries.SalaryFileError) as no_figures:
        salaries.read_salary_file("empty.csv", b"Position,Notes\nHead Coach,vacant\n")
    assert "title" in str(no_figures.value)


# --- Handing the result to the Plan ------------------------------------


def test_figures_land_in_the_shape_the_plan_wants(slots, by_key):
    assignments = assign(slots, [
        ("Director of Athletics", None),
        ("Head Coach", "Baseball"),
    ])
    admin, coaching = salaries.to_plan_figures(assignments, slots, "2024-25")
    assert admin["Administration|Director of Athletics"] == {"2024-25": 50_000}
    assert coaching[("male", "Baseball")]["Head Coach"] == {"2024-25": 50_001}


def test_two_rows_on_one_position_are_added_not_overwritten(slots):
    """A file listing base pay and a supplement separately is one person on
    one row of the page. Silently keeping the last one read would drop the
    other, and the figure would look plausible.
    """
    rows = [
        salaries.SourceRow(index=0, title="Head Coach", salary=200_000, sport="Baseball"),
        salaries.SourceRow(index=1, title="Head Coach", salary=25_000, sport="Baseball"),
    ]
    assignments = [
        salaries.Assignment(row=rows[0], slot_key="male|Baseball|Head Coach", score=1.0),
        salaries.Assignment(row=rows[1], slot_key="male|Baseball|Head Coach", score=1.0),
    ]
    _, coaching = salaries.to_plan_figures(assignments, slots, "2024-25")
    assert coaching[("male", "Baseball")]["Head Coach"] == {"2024-25": 225_000}


def test_a_skipped_row_contributes_nothing(slots):
    rows = [salaries.SourceRow(index=0, title="Pep Band", salary=9_000)]
    assignments = [salaries.Assignment(row=rows[0], slot_key=None, score=0.0)]
    admin, coaching = salaries.to_plan_figures(assignments, slots, "2024-25")
    assert admin == {} and coaching == {}


# --- All the way to the sheet ------------------------------------------


def test_an_uploaded_file_reaches_the_workbook(tmp_path):
    """File in, workbook out, with every assigned dollar landing on a row.

    The unit tests above check the matching. This checks the plumbing that
    carries the result through the Plan and onto the page, which is where a
    shape mismatch would quietly produce a scaffold that is still blank.
    """
    import openpyxl
    from pfa.builders.workbooks import Plan, assemble, build
    from tests.evaluate import Workbook
    from tests.fixture import year

    from pfa.extraction.frs import build_seed

    seed = build_seed([year(2024, 0.95), year(2025)])
    base = seed.base.label
    chosen = sorted({(plan.gender, plan.sport) for plan in seed.sports})
    slots = salaries.all_slots(chosen)

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["Position", "Sport", "Salary"])
    sheet.append(["Director of Athletics", None, 245_000])
    sheet.append(["Equipment Mgr", None, 47_000])
    sheet.append(["SWA", None, 118_500])
    payload = io.BytesIO()
    book.save(payload)

    rows = salaries.read_salary_file("payroll.xlsx", payload.getvalue())
    assignments = salaries.match(rows, slots)
    admin, coaching = salaries.to_plan_figures(assignments, slots, base)
    assert admin, "nothing was assigned, so this would pass for the wrong reason"

    result = build(
        assemble(seed, Plan(
            scenario_name="Salaries",
            admin_salaries=admin,
            coaching_salaries=coaching,
        )),
        tmp_path,
    )

    workbook = openpyxl.load_workbook(result.expense)
    engine = Workbook(workbook)
    worksheet = workbook["Salaries"]

    def row_of(label: str) -> int:
        for number in range(1, worksheet.max_row + 1):
            if str(worksheet[f"B{number}"].value).strip() == label:
                return number
        raise AssertionError(f"no row labelled {label!r}")

    # Not column C. With two filings the leftmost column is the *earlier*
    # reported year, which no salary detail was supplied for; the figures
    # belong in the base year, which is the one the page is anchored to and
    # the one the Change column measures from.
    column = next(
        cell.column_letter
        for cell in worksheet[3]
        if cell.value == base
    )
    assert column != "C", "the fixture has two filings, so this would pass trivially"

    for label, expected in (
        ("Director of Athletics", 245_000),
        ("Equipment Manager", 47_000),
        ("Senior Woman Administrator", 118_500),
    ):
        got = engine.number("Salaries", f"{column}{row_of(label)}")
        assert got == expected, f"{label} shows {got:,.0f} in {base}"

    total = engine.number("Salaries", f"{column}{row_of('Total Salary')}")
    assert total == pytest.approx(245_000 + 47_000 + 118_500), (
        f"the admin block totals {total:,.0f}, so something did not land"
    )


# --- The conference survey form ----------------------------------------
#
# The other shape these arrive in, and the one that breaks naive readers.
# Modelled on a real SoCon athletic directors' survey: a sheet per member
# institution, a page of instructions above the data, a numbering column
# that is all small integers, a title column with no header at all, and the
# sport carried as a section heading rather than a column.
#
# Synthetic rather than the real thing - the real file is eleven
# institutions' salary detail and does not belong in a repository.


def survey_bytes(sheets: dict[str, list[list]]) -> bytes:
    import openpyxl

    book = openpyxl.Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def survey_sheet(institution: str) -> list[list]:
    return [
        [None, "2026-2027 SOUTHERN CONFERENCE ATHLETIC DIRECTORS SURVEY"],
        ["INSTITUTION:", institution],
        ["Name of Person Completling this Survey:", "Brennan Hand - Associate AD/Finance"],
        [],
        [" Department Personnel, Salaries"],
        ["Note: Please use current 2026-27 budgeted information when completing this section."],
        ["Please list the title of ALL your athletic administrative staff. The list should "
         "include, but not be limited to, general administration, compliance, development."],
        [],
        ["Athletic Administration"],
        ["Full Time with Benefits", None, "Base Salary"],
        [1, "Director of Athletics", 275_706],
        [2, "Asst AD for Compliance", 63_873],
        ["Baseball"],
        [3, "Head Baseball Coach", 118_000],
        [4, "Asst. Baseball Coach", 52_000],
        ["Men's Basketball"],
        [5, "Head Men's Basketball Coach", 285_000],
        ["Men's & Women's Golf"],
        [6, "Head Women's Golf Coach", 61_000],
    ]


def test_a_survey_form_reads_without_a_title_header():
    """Three traps in one sheet: a page of instructions above the data, a
    numbering column of small integers, and a title column the header row
    never names.
    """
    data = survey_bytes({"Citadel": survey_sheet("The Citadel")})
    rows = salaries.read_salary_file("survey.xlsx", data, sheet="Citadel")

    assert [row.title for row in rows] == [
        "Director of Athletics", "Asst AD for Compliance", "Head Baseball Coach",
        "Asst. Baseball Coach", "Head Men's Basketball Coach", "Head Women's Golf Coach",
    ]
    assert rows[0].salary == 275_706, (
        "the numbering column was read as the salary — every figure would be a row number"
    )


def test_a_section_heading_supplies_the_sport():
    """The survey form has no Sport column. The sport is a heading above the
    rows it covers, and without it four identical "Asst. Coach" rows have
    nothing to tell them apart.
    """
    data = survey_bytes({"Citadel": survey_sheet("The Citadel")})
    rows = {
        row.title: row
        for row in salaries.read_salary_file("survey.xlsx", data, sheet="Citadel")
    }
    assert rows["Head Baseball Coach"].sport == "Baseball"
    assert rows["Asst. Baseball Coach"].sport == "Baseball"
    assert rows["Director of Athletics"].sport is None
    assert rows["Director of Athletics"].department == "Athletic Administration"
    # And the instructions above the data are not mistaken for headings.
    assert not any(
        row.department and "Please list" in row.department for row in rows.values()
    )


def test_a_combined_section_does_not_decide_the_gender():
    """"Men's & Women's Golf" names both, so it names neither — and the
    title says which. Letting the heading answer for it put every women's
    golf coach on the men's rows.
    """
    data = survey_bytes({"Citadel": survey_sheet("The Citadel")})
    rows = salaries.read_salary_file("survey.xlsx", data, sheet="Citadel")
    slots = salaries.all_slots([("male", "Golf"), ("female", "Golf")])
    by_title = {
        assignment.row.title: assignment for assignment in salaries.match(rows, slots)
    }
    assert by_title["Head Women's Golf Coach"].slot_key == "female|Golf|Head Coach"


def test_the_sheets_are_listed_so_the_right_school_is_built():
    """A conference survey is one workbook with a sheet per institution.
    Reading "the first sheet with anything in it" silently builds the wrong
    school's pro forma.
    """
    data = survey_bytes({
        "Citadel": survey_sheet("The Citadel"),
        "Wofford": survey_sheet("Wofford College"),
    })
    assert salaries.salary_sheets("survey.xlsx", data) == ["Citadel", "Wofford"]

    with pytest.raises(salaries.SalaryFileError) as wrong:
        salaries.read_salary_file("survey.xlsx", data, sheet="Furman")
    assert "Citadel" in str(wrong.value), "the message should name the sheets there are"


def test_a_combined_track_section_reaches_the_combined_row():
    """The form files cross-country and track separately; the pro forma
    carries one "Track and Field, X-Country" row, because that is how FRS
    files it. Matched name-for-name, every one of those coaches lands
    nowhere.
    """
    rows = [
        salaries.SourceRow(
            index=1, title="Asst. M & W Cross Country/T&F Coach",
            salary=44_000, sport="Men's & Women's Cross Country",
        ),
    ]
    slots = salaries.all_slots([
        ("male", "Track and Field, X-Country"),
        ("female", "Track and Field, X-Country"),
    ])
    assignment = salaries.match(rows, slots)[0]
    assert assignment.slot_key is not None, "the track coach reached no row at all"
    assert "Track and Field, X-Country" in assignment.slot_key


def test_the_athletics_director_is_found_whatever_they_are_called():
    """At a private institution the AD is often a vice president, and the
    title says so. "VP of Intercollegiate Athletics" shares no word at all
    with "Director of Athletics" once the stopwords are gone, so the
    highest-paid person in the file was landing nowhere.
    """
    slots = salaries.admin_slots()
    for title in (
        "VP of Intercollegiate Athletics",
        "Vice President of Intercollegiate Athletics",
        "Director of Athletics",
        "Athletic Director",
        "AD",
    ):
        rows = [salaries.SourceRow(index=0, title=title, salary=275_000)]
        assignment = salaries.match(rows, slots)[0]
        assert assignment.slot_key == "Administration|Director of Athletics", (
            f"{title!r} went to {assignment.slot_key!r}"
        )
