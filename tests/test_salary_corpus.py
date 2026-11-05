"""Position titles taken out of CC's own hand-built pro formas.

Three books, three institutions, three different people's habits of
abbreviation — and the scaffold in `pfa/model/schema.py` was written from
the same books, so these are exactly the titles the matcher has to survive.
Read out of the Salaries sheets of:

    2025-26 Pro Forma - Expense - ASUN.xlsx
    LRU - Pro-forma - Expense - SoCon.xlsx
    Pro Forma - Expenses.xlsx

Each title is paired with the scaffold row it has to reach, or with `None`
where it has nowhere to go — because that institution has two marketing
people and the scaffold has one marketing row, or because nobody has an
"Intern" row. A `None` means **must not be confidently matched**: left for
the analyst, with candidates offered. It does not mean the matcher may do
as it likes.

That second half is the point. A matcher that reaches 100% by guessing is
worse than one that reaches 80% and says so, because the 20% it invents
looks exactly like the 80% it knows — and a $190,000 Director of Athletics
on the Nutritionist row still adds up.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pfa.extraction import salaries  # noqa: E402

#: title -> the scaffold key it must reach, or None for "must stay open".
Book = list[tuple[str, str | None]]

ASUN: Book = [
    ("Director of Athletics", "Administration|Director of Athletics"),
    ("Associate AD - Administration", None),
    ("Admin Assistant", "Administration|Administrative Assistant"),
    ("Business Coordinator", "Business Office|Business Manager"),
    ("Assistant AD - Facilities & Operations",
     "Internal Operations|Director of Facilities & Operations"),
    ("Facilities/Operations Manager",
     "Internal Operations|Facilities / Operations Coordinator"),
    ("Associate AD - External Operations ",
     "External Operations|Associate AD - External Operations"),
    ("Director of Development", "External Operations|Director of Development"),
    ("Manager - Mkt & Promotions", "External Operations|Director of Marketing & Promotions"),
    ("Coordinator - Revenue Generation",
     "External Operations|Coordinator - Revenue Generation"),
    ("Coordinator - Special Events", None),
    ("Manager - Athletics Communication",
     "Communications|Director of Athletics Communications"),
    ("Coordinator - Athletics Communications",
     "Communications|Assistant Director of Athletics Communications"),
    ("Coordinator - Digital Media/Graphic Design",
     "Communications|Coordinator - Digital Media / Graphic Design"),
    ("Coordinator - GA", "Communications|Communications - GA"),
    ("Associate AD - Compliance", "Compliance / Academics|Associate AD - Compliance"),
    ("Ass't Director - Compliance", "Compliance / Academics|Assistant Director - Compliance"),
    ("Assoc AD - Academic Servics", "Compliance / Academics|Associate AD - Academic Services"),
    ("Academic Svcs. Manager", "Compliance / Academics|Academic Services Coordinator"),
    ("Compliance/Academic Coordinator", None),
    ("Compliance/Academic - GA", "Compliance / Academics|Compliance / Academics - GA"),
    ("Head Trainer", "Sports Medicine|Head Athletic Trainer"),
    ("Assistant Trainer 1", "Sports Medicine|Assistant Athletic Trainer #1"),
    ("Assistant Trainer 2", "Sports Medicine|Assistant Athletic Trainer #2"),
    ("Assistant Trainer 3", "Sports Medicine|Assistant Athletic Trainer #3"),
    ("Assistant Trainer 4", "Sports Medicine|Assistant Athletic Trainer #4"),
    ("Nutritionist", "Student-Athlete Welfare|Nutritionist"),
    ("Sport Psychologist", None),
    ("Director - Sports Performance", "Strength & Conditioning|Director - Sports Performance"),
    ("Ass't Director - Sports Performance",
     "Strength & Conditioning|Assistant Director - Sports Performance"),
    ("Strength and Conditioning - GA", "Strength & Conditioning|Strength & Conditioning - GA"),
]

LENOIR_RHYNE: Book = [
    ("Director of Athletics", "Administration|Director of Athletics"),
    ("Assistant AD -Revenue Generation", "External Operations|Coordinator - Revenue Generation"),
    ("Associate AD - Internal / SWA", None),
    ("Facility Director", None),
    ("Facilities GA", "Internal Operations|Facilities - GA"),
    ("Deputy AD - Development", "External Operations|Director of Development"),
    ("Senior AD Marketing & Branding", None),
    ("Assistant Director,  Marketing / Development", None),
    ("Director of Ticketing", "External Operations|Director of Ticketing"),
    ("Assistant Director, Athletic Communication",
     "Communications|Director of Athletics Communications"),
    ("Ass't SID", "Communications|Assistant Director of Athletics Communications"),
    ("Sports Information Intern", None),
    ("Sports Information GA", "Communications|Communications - GA"),
    ("Associate AD - Compliance", "Compliance / Academics|Associate AD - Compliance"),
    ("Assistant AD - Academics", None),
    ("Compliance GA", "Compliance / Academics|Compliance / Academics - GA"),
    ("Assistant Director of Development #1",
     "External Operations|Assistant Director of Development"),
    ("Assistant Director of Development #2", None),
    ("Athletic Trainer #1", "Sports Medicine|Assistant Athletic Trainer #1"),
    ("Athletic Trainer #2", "Sports Medicine|Assistant Athletic Trainer #2"),
    ("Athletic Trainer #3", "Sports Medicine|Assistant Athletic Trainer #3"),
    ("Assistant AD, Sports Medicine", "Sports Medicine|Assistant AD - Sports Medicine"),
    ("Director S&C", "Strength & Conditioning|Director - Sports Performance"),
    ("S&C Coach", "Strength & Conditioning|Assistant Director - Sports Performance"),
    ("S&C GA #1", "Strength & Conditioning|Strength & Conditioning - GA"),
    ("S&C GA #2", None),
]

THIRD_BOOK: Book = [
    ("Compliance Coordinator", "Compliance / Academics|Assistant Director - Compliance"),
    ("Academic Coordinator", "Compliance / Academics|Academic Services Coordinator"),
    ("Compliance - GA", "Compliance / Academics|Compliance / Academics - GA"),
    ("Ass't AD - Marketing/Sales", None),
    ("Assistant SID", "Communications|Assistant Director of Athletics Communications"),
    ("Ass't Director - Facilities", None),
    ("Assistant Athletic Trainer", "Sports Medicine|Assistant Athletic Trainer #1"),
    ("Assistant Athletic Trainer", "Sports Medicine|Assistant Athletic Trainer #2"),
    ("Assistant Athletic Trainer", "Sports Medicine|Assistant Athletic Trainer #3"),
    ("Assistant Strength & Conditioning ",
     "Strength & Conditioning|Assistant Director - Sports Performance"),
    ("Assistant Strength & Conditioning - GA",
     "Strength & Conditioning|Strength & Conditioning - GA"),
]

BOOKS: dict[str, Book] = {
    "ASUN": ASUN,
    "Lenoir-Rhyne": LENOIR_RHYNE,
    "third book": THIRD_BOOK,
}


def run(book: Book) -> list[salaries.Assignment]:
    slots = salaries.admin_slots()
    rows = [
        salaries.SourceRow(index=index, title=title, salary=50_000 + index)
        for index, (title, _) in enumerate(book)
    ]
    return salaries.match(rows, slots)


@pytest.mark.parametrize("name", list(BOOKS))
def test_a_real_book_lands_where_it_should(name):
    """Every title with a home reaches it; every title without one stays
    open for the analyst.
    """
    book = BOOKS[name]
    wrong: list[str] = []
    for (title, expected), assignment in zip(book, run(book)):
        if expected is not None:
            if assignment.slot_key != expected:
                wrong.append(
                    f"{title!r} -> {assignment.slot_key!r} ({assignment.score:.2f}), "
                    f"wanted {expected!r}"
                )
        elif assignment.confidence == "strong":
            wrong.append(
                f"{title!r} has no home but was confidently sent to "
                f"{assignment.slot_key!r} ({assignment.score:.2f})"
            )
    assert not wrong, f"{name}:\n  " + "\n  ".join(wrong)


def test_most_of_a_real_book_needs_no_attention():
    """The safety rules above are only worth having if the matcher still
    does most of the work. If this falls a long way, the rules have been
    tuned into uselessness and the analyst may as well type it in.
    """
    total = clear = 0
    for book in BOOKS.values():
        for assignment in run(book):
            total += 1
            clear += assignment.confidence == "strong"
    assert clear / total >= 0.75, f"only {clear} of {total} titles matched cleanly"


def test_nothing_in_a_real_book_is_confidently_wrong():
    """The property that matters most, stated on its own so a failure says
    so plainly: no title is sent anywhere with confidence unless this file
    says that is where it goes.
    """
    for name, book in BOOKS.items():
        # Positionally, not by title. The third book lists "Assistant
        # Athletic Trainer" three times for three different people, and
        # keying the expectations by title would quietly compare all three
        # against the last one.
        for (title, wanted), assignment in zip(book, run(book)):
            if assignment.confidence != "strong":
                continue
            assert wanted == assignment.slot_key, (
                f"{name}: {title!r} confidently matched to "
                f"{assignment.slot_key!r}, which is not where it belongs"
            )


def test_the_dropdown_offers_only_plausible_rows():
    """A candidate list is only useful if it is short.

    "Ass't Director - Facilities" should offer the facilities rows and
    nothing else. Every "Assistant Director of ..." in the scaffold shares
    two of its three words, and offering all of them turns the one control
    the analyst has into a wall of plausible wrong answers.
    """
    slots = salaries.admin_slots()
    by_key = {slot.key: slot for slot in slots}
    row = salaries.SourceRow(index=0, title="Ass't Director - Facilities", salary=60_000)
    assignment = salaries.match([row], slots)[0]

    offered = [by_key[key].group for key in assignment.candidates]
    assert offered, "nothing was offered at all"
    assert set(offered) == {"Internal Operations"}, (
        f"the dropdown strayed outside facilities: {sorted(set(offered))}"
    )
