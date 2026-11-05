"""Parses the per-sport Athletic Student Aid equivalency tables ("Male
Athlete Scholarships" / "Female Athlete Scholarships") that appear in the
NCAA FRS "Revenue/Expense Details" section, under Category 20 (Athletic
Student Aid). Location within the document varies by institution (it
depends on total page count / how much detail precedes it) - this module
searches for the section by its heading text rather than assuming a page
number.

Layout, per sub-table:

    Sport | Athletic Aid Equivalency (A) | Exhausted Eligibility or Medical
    Equivalency (B) | Total Equivalencies Awarded (A+B) | Number of Students
    Receiving Athletic Aid | Total Dollar Amount

This is parsed from word *positions* (pdfplumber's extract_words), not
extract_text() line-splitting, for two reasons confirmed against real
reports:

1. Long sport names wrap onto a second line (e.g. "Track and Field," /
   "X-Country") while that row's numeric columns stay on the first line -
   a naive whitespace-split parse would either drop the wrapped word or
   attach it to the wrong row.
2. A genuinely-zero "(B)" value is sometimes rendered as a blank cell
   rather than the text "0", which shifts every later column left in a
   plain text parse. Reading each number by its x-position (which column
   it visually sits in) rather than its ordinal position on the line
   avoids that entirely.

The non-sport "Expenses Not Related to Specific Teams" row (aid not
attributable to any specific team) is parsed but flagged via
`SportEquivalency.is_team_specific = False` rather than silently dropped -
callers decide whether to exclude it (the scholarships-by-sport workbook
does; a total-aid reconciliation would not want to).

**Total Dollar Amount.** This calculator needs the aid *in dollars* per
sport - the "SA Financial Aid" column of the net tuition revenue model -
which the benchmarking app never read, since it benchmarks equivalencies.
There is no per-sport "Expenses by Object of Expenditure" table for
category 20 to take it from (checked against the FY2025 corpus: the
category-20 tables are these ones), so it is read from this table's own
last column.

Those two right-hand columns are read differently from column (A): they are
right-aligned, so a column's left edge moves with the width of the figure
in it while its right edge stays put. They are matched on right edge
against anchors taken from the sub-table's own "Totals" row, which always
prints all five figures - the same self-calibrating technique the per-sport
expense and coaching parsers use, and for the same reason. Reading them by
a fixed left bound instead puts "963,794" and "10,416,469" in different
columns.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pfa.extraction.word_rows import Word, extract_word_stream, group_into_rows, read_rows

# Kept as module-private aliases: these were this parser's own copies until
# they were found to be identical across all four detail parsers.
_extract_word_stream = extract_word_stream
_group_into_rows = group_into_rows

_MALE_HEADING = "Male Athlete Scholarships"
_FEMALE_HEADING = "Female Athlete Scholarships"
_STOP_LABEL = "Totals"
#: How many rows past the heading to look for the "(A)"/"(B)" column markers.
#: The header block itself is four lines and sits three lines below the
#: heading, so six was exactly enough for a clean report and nothing more.
#: Reports filed through a document system carry an extra stamp line between
#: the two (University of Louisiana Monroe's "20260115_Goodrich_000083"),
#: which pushed "(B)" one row past the window and failed the whole section.
#: Ten leaves room for a couple of those without reaching the first sport.
_HEADER_SCAN_ROW_LIMIT = 10
_ROW_MERGE_TOLERANCE_PT = 2.0  # word 'top' values within this many points are treated as the same visual row
_COLUMN_GUTTER_PT = 15.0  # margin subtracted from a column's marker x0 to build an inclusive left boundary

#: A wrapped second line of a sport name sits ~14.4pt below the line it
#: continues; a new table row sits ~18.9pt below the previous one. Measured
#: across the report corpus, and the gap is the only reliable way to tell a
#: wrapped name from a sport that simply has no equivalency to report - both
#: look like "text in the name column, nothing in column (A)". Getting this
#: wrong glues two sports together ("Basketball Beach Volleyball").
_CONTINUATION_ROW_GAP_THRESHOLD_PT = 16.0

#: First word of the name column on the rows of the repeated column-header
#: block ("Sport | Athletic Aid Equivalency (A) | ..." wrapped over four
#: lines). These reappear mid-table whenever a sub-table crosses a page.
_HEADER_BLOCK_FIRST_WORDS = frozenset({"Sport", "Equivalency", "Athletic"})

#: Page furniture. Compared against the row's text with spaces removed, since
#: some reports' PDFs render these with no spaces between words at all
#: ("NCAAMembershipFinancial", "ReportingInstitution:").
_BOILERPLATE_PREFIXES = ("NCAA", "Reporting", "MaleAthlete", "FemaleAthlete")


def _squashed(texts: list[str]) -> str:
    return "".join(texts).replace(" ", "")


#: How far a figure may sit from a column's right-edge anchor and still be
#: read as belonging to that column. Matches the per-sport expense parser's
#: tolerance; the gap between these two columns is ~30pt, so there is no
#: overlap.
_RIGHT_EDGE_TOLERANCE_PT = 12.0


@dataclass(frozen=True)
class SportEquivalency:
    """One row of a Male/Female Athlete Scholarships table."""

    sport: str
    athletic_aid_equivalency: float | None  # column (A); None if the PDF rendered no value for this row
    is_team_specific: bool
    #: "Number of Students Receiving Athletic Aid", and "Total Dollar Amount"
    #: - the last two columns. The dollar figure is what the net tuition
    #: revenue model calls SA Financial Aid. None where the report printed
    #: nothing, which is not the same as a reported zero.
    students_receiving_aid: int | None = None
    total_dollar_amount: float | None = None  # False only for the "Expenses Not Related to Specific Teams" row


@dataclass(frozen=True)
class ScholarshipEquivalencyTables:
    male: tuple[SportEquivalency, ...]
    female: tuple[SportEquivalency, ...]
    source_path: Path

    def team_specific(self, *, gender: str) -> list[SportEquivalency]:
        """gender: 'male' or 'female'. Excludes the non-team-specific aid row."""
        rows = self.male if gender == "male" else self.female
        return [row for row in rows if row.is_team_specific]


def _find_heading_row_index(rows: list[list[Word]], heading_text: str) -> int | None:
    for index, row in enumerate(rows):
        if heading_text in " ".join(w["text"] for w in row):
            return index
    return None


def _locate_column_markers(rows: list[list[Word]], start_index: int) -> tuple[float, float, int]:
    """Scans forward from a sub-table heading for the "(A)" / "(B)" column
    header markers, returning their x0 positions plus the row index where
    data rows begin. Raises ValueError if the expected header isn't found -
    that means the document's table structure differs from what this parser
    was built against, and should surface as a hard error, not silently
    produce wrong column alignment.
    """
    col_a_x0: float | None = None
    col_b_x0: float | None = None
    index = start_index
    rows_scanned = 0
    while index < len(rows) and rows_scanned < _HEADER_SCAN_ROW_LIMIT:
        for word in rows[index]:
            if word["text"] == "(A)":
                col_a_x0 = word["x0"]
            elif word["text"] == "(B)":
                col_b_x0 = word["x0"]
        index += 1
        rows_scanned += 1
        if col_a_x0 is not None and col_b_x0 is not None:
            return col_a_x0, col_b_x0, index

    raise ValueError(
        "Could not locate the '(A)'/'(B)' equivalency column headers within "
        f"{_HEADER_SCAN_ROW_LIMIT} rows of the scholarships table heading - "
        "the table layout does not match what this parser expects."
    )


def _number(text: str) -> float | None:
    cleaned = text.replace(",", "").replace("$", "").strip()
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    try:
        return float(cleaned)
    except ValueError:
        return None


def _right_edge_anchors(rows: list[list[Word]], start_index: int) -> tuple[float, float] | None:
    """The right edges of the "Number of Students" and "Total Dollar Amount"
    columns, taken from the sub-table's own "Totals" row.

    Those are the two right-most figures on that row whatever else it prints:
    a total headcount and a total dollar amount are never left blank, while
    columns (A)/(B)/(A+B) can be. Taking the last two rather than requiring
    a five-figure row is what keeps a report that omits one of the left
    columns readable.
    """
    for index in range(start_index, len(rows)):
        squashed = _squashed([w["text"] for w in rows[index]])
        if not squashed.startswith(_STOP_LABEL):
            continue
        edges = sorted(w["x1"] for w in rows[index] if _number(w["text"]) is not None)
        return (edges[-2], edges[-1]) if len(edges) >= 2 else None
    return None


def _at_right_edge(row: list[Word], anchor: float | None) -> float | None:
    if anchor is None:
        return None
    for word in row:
        if abs(word["x1"] - anchor) <= _RIGHT_EDGE_TOLERANCE_PT:
            value = _number(word["text"])
            if value is not None:
                return value
    return None


def _parse_subtable(rows: list[list[Word]], heading_row_index: int) -> tuple[SportEquivalency, ...]:
    col_a_x0, col_b_x0, data_start_index = _locate_column_markers(rows, heading_row_index + 1)
    col_a_left_bound = col_a_x0 - _COLUMN_GUTTER_PT
    col_b_left_bound = col_b_x0 - _COLUMN_GUTTER_PT
    anchors = _right_edge_anchors(rows, data_start_index)
    students_anchor = anchors[0] if anchors else None
    dollars_anchor = anchors[1] if anchors else None

    results: list[SportEquivalency] = []
    previous_row_top: float | None = None
    previous_row_page: int | None = None
    boilerplate_top: float | None = None
    boilerplate_page: int | None = None
    index = data_start_index
    while index < len(rows):
        row = rows[index]
        texts = [w["text"] for w in row]
        if not texts:
            index += 1
            continue

        squashed = _squashed(texts)
        if squashed.startswith(_STOP_LABEL):
            break
        if squashed.startswith(_BOILERPLATE_PREFIXES):
            # Page header/footer that repeats mid-table when a sub-table
            # spans a page break.
            boilerplate_top, boilerplate_page = row[0]["top"], row[0]["page"]
            index += 1
            continue

        name_words = [w for w in row if w["x0"] < col_a_left_bound]
        col_a_words = [w for w in row if col_a_left_bound <= w["x0"] < col_b_left_bound]
        name_text = " ".join(w["text"] for w in name_words).strip()
        row_top, row_page = row[0]["top"], row[0]["page"]

        if (
            not col_a_words
            and boilerplate_page == row_page
            and boilerplate_top is not None
            and 0 < row_top - boilerplate_top < _CONTINUATION_ROW_GAP_THRESHOLD_PT
        ):
            # The wrapped second line of a page-header line - a long
            # institution name spilling over ("...Virginia Polytechnic
            # Institute and State" / "University"). Skipped rather than read
            # as a sport, and rather than glued onto whichever sport the
            # previous page ended on.
            boilerplate_top = row_top
            index += 1
            continue

        if not name_text:
            # Nothing in the name column. Either a stray fragment or one of
            # the lower lines of the repeated column-header block, whose
            # words all sit to the right of the sport column - the rows that
            # used to arrive as a sport named "".
            index += 1
            continue
        if name_words[0]["text"] in _HEADER_BLOCK_FIRST_WORDS:
            # The repeated "Sport | Athletic Aid Equivalency ..." header,
            # which otherwise reads as sports called "Sport Athletic" and
            # "Equivalency".
            index += 1
            continue

        row_gap = (row_top - previous_row_top) if previous_row_top is not None else None
        is_continuation = (
            not col_a_words
            and bool(results)
            and not name_text.startswith("Expenses")
            # Same page, and genuinely *below* the line it continues. Without
            # the page check the gap is computed across a page break, comes
            # out large and negative, and passes the "< threshold" test - so
            # the first row on a new page was being glued onto the last sport
            # of the previous one ("Basketball Fencing").
            and previous_row_page == row_page
            and row_gap is not None
            and 0 < row_gap < _CONTINUATION_ROW_GAP_THRESHOLD_PT
        )

        if is_continuation:
            # A wrapped second line of the previous row's sport name (e.g.
            # "X-Country" following "Track and Field,"). Distinguished from a
            # sport that reports no equivalency purely by how close it sits
            # to the line above - see _CONTINUATION_ROW_GAP_THRESHOLD_PT.
            previous = results[-1]
            results[-1] = SportEquivalency(
                sport=f"{previous.sport} {name_text}".strip(),
                athletic_aid_equivalency=previous.athletic_aid_equivalency,
                is_team_specific=previous.is_team_specific,
                # Carried through explicitly: a wrapped name rebuilds the
                # whole record, and anything not restated here reverts to
                # None - which would silently blank the aid dollars of every
                # sport whose name wraps ("Track and Field," / "X-Country",
                # "Swimming and" / "Diving").
                students_receiving_aid=previous.students_receiving_aid,
                total_dollar_amount=previous.total_dollar_amount,
            )
        else:
            value: float | None = None
            if col_a_words:
                try:
                    value = float(col_a_words[0]["text"].replace(",", ""))
                except ValueError:
                    value = None
            students = _at_right_edge(row, students_anchor)
            results.append(
                SportEquivalency(
                    sport=name_text,
                    athletic_aid_equivalency=value,
                    # Checked by prefix, not full-string equality: this row's
                    # name_text may still be a partial wrapped fragment (e.g.
                    # "Expenses Not") at creation time - the rest arrives on
                    # continuation rows and gets appended above.
                    is_team_specific=not name_text.startswith("Expenses"),
                    students_receiving_aid=None if students is None else int(students),
                    total_dollar_amount=_at_right_edge(row, dollars_anchor),
                )
            )
        # Tracked from continuation rows too: a name can wrap over more than
        # two lines, and each wrapped line sits close to the line before it
        # rather than to the row that started the record.
        previous_row_top, previous_row_page = row_top, row_page
        index += 1

    return tuple(results)


def parse_scholarship_equivalencies(
    pdf_path: Path | str, rows: list[list[Word]] | None = None
) -> ScholarshipEquivalencyTables:
    """`rows` lets a caller that already read this PDF pass the grouped
    rows in rather than have them read again - see ntr/extraction/word_rows.
    Omit it and the document is read here, as before.
    """
    pdf_path = Path(pdf_path)
    if rows is None:
        rows = read_rows(pdf_path)

    male_heading_index = _find_heading_row_index(rows, _MALE_HEADING)
    female_heading_index = _find_heading_row_index(rows, _FEMALE_HEADING)
    if male_heading_index is None or female_heading_index is None:
        missing = []
        if male_heading_index is None:
            missing.append(_MALE_HEADING)
        if female_heading_index is None:
            missing.append(_FEMALE_HEADING)
        raise ValueError(f"Could not find heading(s) {missing!r} in {pdf_path.name}")

    male = _parse_subtable(rows, male_heading_index)
    female = _parse_subtable(rows, female_heading_index)
    return ScholarshipEquivalencyTables(male=male, female=female, source_path=pdf_path)
