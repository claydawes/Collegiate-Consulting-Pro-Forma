"""Parses the "Revenue Distribution - Pell Grants" page.

One page near the back of every FRS report, in three tables - men's teams,
women's teams and mixed teams - each with the same five columns:

    Sport          2024-25 Pell   Prior Year Pell   Variance   Total Dollar
                       Grants           Grants        Totals   Amount for SAs
                                                               on Pell Grants
    Baseball                6                5             1          35,565
    Basketball              4                3             1          23,378
    Football               48               39             9         255,557
    ...
    Men's Total            71               60            11         378,122

The workbook wants two of those five: the **2024-25 Pell Grants** count per
sport, and the **Total Dollar Amount** behind it. The prior-year column and
the variance are read too, since they cost nothing once the row is parsed and
they are what the variance on the page is computed from - but nothing in the
deliverable uses them today.

**Reading the columns.** The first three numeric columns are *centre*-aligned
and the dollar column is *right*-aligned, which is unusual for these reports
and matters: a centred column's right edge moves with the width of the number
in it (6 ends at x=182.3, 48 at x=185.3, but both are centred on 179.3), so
anchoring the whole table on right edges the way the sport-expense parser does
would mis-assign every two-digit figure. Columns 0-2 are therefore matched on
midpoint and column 3 on right edge.

The anchors come from the table's own total row, which always prints all four
figures. That makes each table self-calibrating, which it has to be: the men's
and women's tables on the same page do not share column positions (men's
column 0 is centred on 179.3, women's on 194.4).

**Checking the reading.** Each table prints its own "Men's Total" /
"Women's Total" row, and the per-sport figures must add up to it in both the
count and the dollar column. That is a real check rather than a restatement -
the totals are separate numbers printed by the institution - and it is exposed
as `PellTable.reconciles` so a report whose figures don't tie out can be
surfaced instead of silently published.

The mixed-team table is parsed for completeness but has no home in the
workbook, which follows the template's men's/women's split. It is exposed so
the builder can say so when a mixed team actually carries Pell money, rather
than quietly dropping it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pfa.core.domain.exceptions import ExtractionError
from pfa.extraction.word_rows import Word, read_rows

_PAGE_HEADING = "RevenueDistribution-PellGrants"

#: Squashed section heading -> gender key.
_SECTIONS = {
    "Men'sTeamSports": "male",
    "Women'sTeamSports": "female",
    "MixedTeamSports": "mixed",
}
#: First word of each table's total row. Matched on that word alone rather
#: than on "Men's Total", because the label wraps when the table is wide -
#: Georgia Southern prints "Men's 83 54 29 507,282" with "Total" alone on the
#: next line, which no test for the full phrase can see. No sport is named
#: "Men's", "Women's" or "Mixed", so the first word is enough.
_TOTAL_FIRST_WORD = {"male": "Men's", "female": "Women's", "mixed": "Mixed"}

#: Page furniture: the running footer and the per-page institution header.
#: Both sit at the left margin, so without skipping them explicitly they read
#: as sport rows on any table that runs across a page break.
_FURNITURE_PREFIXES = ("NCAAMembershipFinancial", "ReportingInstitution")

#: The sub-header under every table's column titles, used for column
#: positions when a table prints no total row to calibrate against.
_SUBHEADER_WORDS = ("Grants", "Grants", "Totals")

#: A data row *starts* hard against the left margin (x0 = 38.0). The repeated
#: column-header block is indented well past it (the word "Sport" starts at
#: 52.6-80.0 depending on the table), which is what separates a data row from
#: the header above it.
_SPORT_NAME_MARGIN_PT = 45.0

#: How far left of the first count column a word has to end to be part of the
#: sport's name. Only a name's *first* word sits at the margin - "Swimming and
#: Diving" runs 38.0 -> 147.0 and "Beach Volleyball" to 119.3 - so a margin
#: test alone reads "Beach Volleyball" as "Beach" and "Water Polo" as "Water".
#: The name is everything left of the first column instead. The widest count
#: in the corpus is three digits, ~18pt, so its left edge sits ~9pt before the
#: column midpoint; 12pt clears it with room to spare and still leaves 20pt
#: between the boundary and the longest sport name.
_NAME_CLEARANCE_PT = 12.0

#: A wrapped sport name ("Swimming and" / "Diving") sits ~14pt below the line
#: it continues; a new sport row sits ~19pt below. Same measurement, and the
#: same threshold, as the scholarships, coaching and sport-expense parsers.
_CONTINUATION_ROW_GAP_THRESHOLD_PT = 16.0

#: How far past a section heading to keep reading before giving up on finding
#: its total row. The largest table in the corpus is 16 sports plus header and
#: total; this leaves room for every one of them to wrap.
_TABLE_SCAN_ROWS = 60

#: A figure belongs to the column whose anchor it is nearest, but only within
#: this tolerance. The columns are 80-200pt apart, so this is generous enough
#: to absorb a wide number and still nowhere near the next column.
_COLUMN_MATCH_TOLERANCE_PT = 14.0

#: Column order across the table.
_PELL_GRANTS, _PRIOR_YEAR, _VARIANCE, _DOLLARS = 0, 1, 2, 3


@dataclass
class PellSport:
    """One sport's row on the page."""

    sport: str
    #: Head count of student-athletes on Pell grants in the reported academic
    #: year - the "2024-25 Pell Grants" column, which is what the workbook shows.
    pell_grants: int | None = None
    prior_year_pell_grants: int | None = None
    variance: int | None = None
    #: "Total Dollar Amount for SAs on Pell Grants" for that sport.
    dollars: float | None = None


@dataclass
class PellTable:
    gender: str
    sports: list[PellSport] = field(default_factory=list)
    printed_grants: int | None = None
    printed_dollars: float | None = None

    @property
    def reconciles(self) -> bool:
        """True when the per-sport figures add up to the table's printed total
        in both the count and the dollar column.
        """
        for parsed, printed in (
            (sum(s.pell_grants or 0 for s in self.sports), self.printed_grants),
            (sum(s.dollars or 0 for s in self.sports), self.printed_dollars),
        ):
            if printed is not None and parsed != printed:
                return False
        return True


@dataclass
class PellGrants:
    #: "2024-25", straight off the page - the year the counts describe.
    academic_year: str | None = None
    tables: dict[str, PellTable] = field(default_factory=dict)

    def team_specific(self, *, gender: str) -> list[PellSport]:
        table = self.tables.get(gender)
        return list(table.sports) if table else []

    def printed_total(self, *, gender: str) -> tuple[int | None, float | None]:
        table = self.tables.get(gender)
        return (table.printed_grants, table.printed_dollars) if table else (None, None)

    @property
    def reconciles(self) -> bool:
        return all(t.reconciles for t in self.tables.values())


def _squash(row: list[Word]) -> str:
    return "".join(word["text"] for word in row).replace(" ", "")


def _numeric_words(row: list[Word]) -> list[Word]:
    return [w for w in row if _as_number(w["text"]) is not None]


def _as_number(text: str) -> float | None:
    cleaned = text.replace(",", "").replace("$", "")
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1]
    if cleaned.startswith("-"):
        negative, cleaned = True, cleaned[1:]
    if not cleaned or not cleaned.isdigit():
        return None
    return -float(cleaned) if negative else float(cleaned)


def _is_total_row(row: list[Word], gender: str) -> bool:
    return (
        bool(row)
        and row[0]["x0"] < _SPORT_NAME_MARGIN_PT
        and row[0]["text"] == _TOTAL_FIRST_WORD[gender]
    )


def _is_furniture(row: list[Word]) -> bool:
    return _squash(row).startswith(_FURNITURE_PREFIXES)


def _anchors(row: list[Word]) -> tuple[list[float], float] | None:
    """The table's column positions, taken from its own total row.

    Returns the midpoints of the three count columns and the right edge of the
    dollar column - the alignment each of them actually uses.
    """
    numbers = _numeric_words(row)
    if len(numbers) != 4:
        return None
    ordered = sorted(numbers, key=lambda w: w["x0"])
    centres = [(w["x0"] + w["x1"]) / 2 for w in ordered[:3]]
    return centres, ordered[3]["x1"]


def _column_of(word: Word, centres: list[float], dollar_edge: float) -> int | None:
    centre = (word["x0"] + word["x1"]) / 2
    best, distance = None, None
    for index, anchor in enumerate(centres):
        gap = abs(centre - anchor)
        if distance is None or gap < distance:
            best, distance = index, gap
    edge_gap = abs(word["x1"] - dollar_edge)
    if distance is None or edge_gap < distance:
        best, distance = _DOLLARS, edge_gap
    return best if distance <= _COLUMN_MATCH_TOLERANCE_PT else None


def parse_pell_grants(pdf_path: Path, rows: list[list[Word]] | None = None) -> PellGrants:
    """Reads the Pell Grants page out of one report.

    `rows` is the shared document read from pfa.extraction.word_rows; it is
    optional only so the parser can be run against a PDF on its own.
    """
    if rows is None:
        rows = read_rows(pdf_path)

    start = next(
        (index for index, row in enumerate(rows) if _squash(row) == _PAGE_HEADING), None
    )
    if start is None:
        raise ExtractionError(
            f"{Path(pdf_path).name} has no 'Revenue Distribution - Pell Grants' page."
        )

    result = PellGrants()
    for row in rows[start + 1 : start + 6]:
        squashed = _squash(row)
        if squashed.startswith("AcademicYearofPellGrantInformation:"):
            result.academic_year = squashed.split(":", 1)[1] or None
            break

    for index in range(start + 1, min(start + _TABLE_SCAN_ROWS * 3, len(rows))):
        gender = _SECTIONS.get(_squash(rows[index]))
        if gender is None or gender in result.tables:
            continue
        table = _read_table(rows, index, gender)
        if table is not None:
            result.tables[gender] = table

    if not any(g in result.tables for g in ("male", "female")):
        raise ExtractionError(
            f"{Path(pdf_path).name}: the Pell Grants page carried neither a men's nor a "
            "women's team table in the expected layout."
        )
    return result


def _table_end(rows: list[list[Word]], heading: int, gender: str) -> int:
    """Where the table stops: its own total row, wherever that falls.

    The scan deliberately runs past the page footer. A table wide enough to
    spill - UC Davis's women's table ends on the following page - keeps its
    total row on the later page, and stopping at the footer would cut the
    table off mid-list and lose both the last sports and the checksum.

    When no total row turns up at all, the table ended on its own page and the
    footer is the boundary; carrying on past it would start reading the next
    section of the report as sports.
    """
    stop = min(heading + _TABLE_SCAN_ROWS, len(rows))
    footer = None
    for index in range(heading + 1, stop):
        row = rows[index]
        if _squash(row) in _SECTIONS:
            break
        if _is_furniture(row):
            if footer is None:
                footer = index
            continue
        if _is_total_row(row, gender):
            return index
    return footer if footer is not None else stop


def _subheader_anchors(
    rows: list[list[Word]], heading: int, end: int
) -> list[float] | None:
    """Column midpoints taken from the "Grants Grants Totals" sub-header.

    The fallback for a table that prints no total row. Measured against the
    corpus these agree exactly with the totals-row midpoints, so it is the
    same calibration from a different line rather than an approximation.
    """
    for index in range(heading + 1, end):
        row = rows[index]
        if tuple(w["text"] for w in row[:3]) == _SUBHEADER_WORDS:
            return [(w["x0"] + w["x1"]) / 2 for w in row[:3]]
    return None


def _read_table(rows: list[list[Word]], heading: int, gender: str) -> PellTable | None:
    end = _table_end(rows, heading, gender)
    total_row = rows[end] if end < len(rows) and _is_total_row(rows[end], gender) else None

    anchored = _anchors(total_row) if total_row is not None else None
    if anchored is None:
        centres = _subheader_anchors(rows, heading, end)
        if centres is None:
            return PellTable(gender=gender)
        # Every dollar figure on the page is right-aligned to the same edge,
        # so the widest right edge in the table body is that column's anchor.
        edges = [
            w["x1"]
            for index in range(heading + 1, end)
            for w in _numeric_words(rows[index])
        ]
        if not edges:
            return PellTable(gender=gender)
        dollar_edge = max(edges)
    else:
        centres, dollar_edge = anchored

    table = PellTable(gender=gender)
    if total_row is not None:
        by_column = {}
        for word in _numeric_words(total_row):
            column = _column_of(word, centres, dollar_edge)
            if column is not None:
                by_column[column] = _as_number(word["text"])
        grants, dollars = by_column.get(_PELL_GRANTS), by_column.get(_DOLLARS)
        table.printed_grants = None if grants is None else int(grants)
        table.printed_dollars = dollars

    previous_top = previous_page = None
    name_boundary = centres[0] - _NAME_CLEARANCE_PT
    for index in range(heading + 1, end):
        row = rows[index]
        if not row or _is_furniture(row):
            continue
        if row[0]["x0"] >= _SPORT_NAME_MARGIN_PT:
            # The repeated column-header block, indented past the margin.
            continue
        name = " ".join(
            w["text"]
            for w in row
            if w["x1"] < name_boundary and _as_number(w["text"]) is None
        ).strip()
        if not name:
            continue
        numbers = _numeric_words(row)
        top, page = row[0]["top"], row[0]["page"]
        gap = (top - previous_top) if previous_top is not None else None
        continues = (
            bool(table.sports)
            and not numbers
            and previous_page == page
            and gap is not None
            and 0 < gap < _CONTINUATION_ROW_GAP_THRESHOLD_PT
        )
        previous_top, previous_page = top, page

        if continues:
            # No inserted space after a trailing hyphen, so "Track and Field,
            # X-" + "Country" rejoins as "X-Country" - the spelling the other
            # parsers already produce for this sport.
            last = table.sports[-1]
            joiner = "" if last.sport.endswith("-") else " "
            last.sport = f"{last.sport}{joiner}{name}".strip()
            continue

        entry = PellSport(sport=name)
        for word in numbers:
            column = _column_of(word, centres, dollar_edge)
            value = _as_number(word["text"])
            if column == _PELL_GRANTS:
                entry.pell_grants = int(value)
            elif column == _PRIOR_YEAR:
                entry.prior_year_pell_grants = int(value)
            elif column == _VARIANCE:
                entry.variance = int(value)
            elif column == _DOLLARS:
                entry.dollars = value
        table.sports.append(entry)

    return table
