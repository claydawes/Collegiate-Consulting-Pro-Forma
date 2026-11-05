"""Parses the "Men's Teams Coaching Expenses" / "Women's Teams Coaching
Expenses" tables in the NCAA FRS "Revenue/Expense Details" section (part of
Category 22/23, Coaching Salaries) - typically around page 46-47, though
(as with the scholarships/participation tables) this module locates the
section by heading text rather than assuming a page number.

Each sub-table has two 4-column groups per sport - Head Coaches and
Assistant Coaches - each group being (Number of Positions, FTE, Coaching
Salaries/Benefits/Bonuses paid by the University and Related Entities,
Coaching Salaries/Benefits/Bonuses paid by a Third Party). Only the
"paid by the University and Related Entities" figure from each group is
parsed here (that's the value the client asked for); Number of Positions,
FTE, and the third-party-paid figure are read past but not kept.

As with the scholarships table, the non-sport "Expenses Not Related to
Specific Teams" row and the sub-table's own "Subtotal"/"Total" rows are
recognized as section boundaries, not sports.

Verified against Oregon State's FY2025 report: summing this parser's
per-sport Head/Assistant "paid by University" values reproduces the
table's own printed Subtotal All Teams figures exactly, for both the
Men's (Head $6,823,229 / Assistant $7,264,539) and Women's (Head
$3,077,363 / Assistant $2,580,395) tables.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from pfa.extraction.word_rows import Word, extract_word_stream, group_into_rows, read_rows

# Kept as module-private aliases: these were this parser's own copies until
# they were found to be identical across all four detail parsers.
_extract_word_stream = extract_word_stream
_group_into_rows = group_into_rows

_MENS_HEADING = "Men's Teams Coaching Expenses"
_WOMENS_HEADING = "Women's Teams Coaching Expenses"
_COLUMN_MARKER_TEXT = "Coaching"  # appears 4x in the header row: head-univ, head-third, asst-univ, asst-third
_ROW_LABEL_MARKER_TEXT = "NumbeFTE"  # pdfplumber's kerning-collapsed rendering of "Number" + "FTE"; used only to bound the sport-name column
_STOP_LABEL = "Subtotal"
#: Page furniture and repeated column headers, matched against the row's text
#: with spaces removed (see _parse_subtable).
_BOILERPLATE_PREFIXES = ("NCAA", "Reporting", "Sport", "Men'sTeams", "Women'sTeams")
_COLUMN_GUTTER_PT = 15.0
_SPORT_NAME_GUTTER_PT = 15.0
_ROW_MERGE_TOLERANCE_PT = 2.0
_CONTINUATION_ROW_GAP_THRESHOLD_PT = 16.0

#: The eight figures on a row, left to right:
#:   head: positions, FTE, university-paid, third-party-paid
#:   assistant: the same four
#: The salary figures are located by left-hand column bounds (see
#: _locate_column_bounds); the position counts sit close together and are
#: instead matched on right edge, anchored to the "Subtotal All Teams" row,
#: which always prints all eight. Same approach as the per-sport expense and
#: coaching-assignment parsers, and for the same reason - the figures are
#: right-aligned, so only the right edge is stable.
_HEAD_POSITIONS_COLUMN, _ASSISTANT_POSITIONS_COLUMN = 0, 4
_POSITION_MATCH_TOLERANCE_PT = 8.0


@dataclass(frozen=True)
class SportCoachingExpense:
    sport: str
    head_coach_university_paid: float | None
    assistant_coach_university_paid: float | None
    is_team_specific: bool
    #: "Number of Positions" for each group - the coach head count, and the
    #: source for the Coaching Staff workbook. Read from the same rows as the
    #: salaries above them so the two workbooks can never disagree about how
    #: many coaches a sport has.
    head_coach_positions: float | None = None
    assistant_coach_positions: float | None = None


@dataclass(frozen=True)
class CoachingExpenseTables:
    mens: tuple[SportCoachingExpense, ...]
    womens: tuple[SportCoachingExpense, ...]
    source_path: Path

    def team_specific(self, *, gender: str) -> list[SportCoachingExpense]:
        rows = self.mens if gender == "male" else self.womens
        return [row for row in rows if row.is_team_specific]


def _find_heading_row_index(rows: list[list[Word]], heading_text: str, after: int = 0) -> int | None:
    for index in range(after, len(rows)):
        if heading_text in " ".join(w["text"] for w in rows[index]):
            return index
    return None


def _locate_column_bounds(
    rows: list[list[Word]], start_index: int
) -> tuple[float, float, float, float, float, int]:
    """Returns (name_right_bound, head_univ_left, head_third_left,
    asst_univ_left, asst_third_left, data_start_index).
    """
    index = start_index
    scanned = 0
    while index < len(rows) and scanned < 8:
        coaching_x0s = sorted(w["x0"] for w in rows[index] if w["text"] == _COLUMN_MARKER_TEXT)
        numfte_x0s = sorted(w["x0"] for w in rows[index] if w["text"] == _ROW_LABEL_MARKER_TEXT)
        if len(coaching_x0s) >= 4 and numfte_x0s:
            return (
                numfte_x0s[0] - _SPORT_NAME_GUTTER_PT,
                coaching_x0s[0] - _COLUMN_GUTTER_PT,
                coaching_x0s[1] - _COLUMN_GUTTER_PT,
                coaching_x0s[2] - _COLUMN_GUTTER_PT,
                coaching_x0s[3] - _COLUMN_GUTTER_PT,
                index + 1,
            )
        index += 1
        scanned += 1

    raise ValueError(
        "Could not locate the 'NumbeFTE'/'Coaching Salaries...' column headers within the expected "
        "distance of the Coaching Expenses table heading - table layout does not match what this "
        "parser expects."
    )


def _parse_float(text: str) -> float | None:
    stripped = text.replace(",", "").replace("$", "").strip()
    try:
        return float(stripped)
    except ValueError:
        return None


def _numeric_words(row: list[Word]) -> list[Word]:
    return [w for w in row if _parse_float(w["text"]) is not None]


def _position_anchors(rows: list[list[Word]], start: int) -> list[float] | None:
    """The eight columns' right edges, from the table's Subtotal row."""
    for index in range(start, min(start + 40, len(rows))):
        squashed = "".join(w["text"] for w in rows[index]).replace(" ", "")
        if squashed.startswith(_STOP_LABEL):
            numbers = _numeric_words(rows[index])
            if len(numbers) == 8:
                return sorted(w["x1"] for w in numbers)
            return None
    return None


def _column_value(row: list[Word], anchors: list[float] | None, column: int) -> float | None:
    if anchors is None:
        return None
    target = anchors[column]
    for word in row:
        if abs(word["x1"] - target) <= _POSITION_MATCH_TOLERANCE_PT:
            return _parse_float(word["text"])
    return None


def _parse_subtable(rows: list[list[Word]], heading_index: int) -> tuple[SportCoachingExpense, ...]:
    (
        name_right_bound,
        head_univ_left,
        head_third_left,
        asst_univ_left,
        asst_third_left,
        data_start_index,
    ) = _locate_column_bounds(rows, heading_index + 1)
    position_anchors = _position_anchors(rows, data_start_index)

    results: list[SportCoachingExpense] = []
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
        # Compared with spaces removed: some reports' PDFs render these runs
        # with no spaces between the words at all ("SubtotalAllTeams",
        # "NCAAMembershipFinancial", "Men'sTeams"). Matching on the spaced
        # forms meant the Subtotal row never registered as the end of the
        # table, so the parser ran on through the rest of the document and
        # turned page furniture into dozens of sports.
        squashed = "".join(texts).replace(" ", "")
        if squashed.startswith(_STOP_LABEL):
            break
        if squashed.startswith(_BOILERPLATE_PREFIXES):
            # Page-break boilerplate: repeated report header/footer, or the
            # column-header block repeating after a page break.
            boilerplate_top, boilerplate_page = row[0]["top"], row[0]["page"]
            index += 1
            continue

        row_top, row_page = row[0]["top"], row[0]["page"]
        name_words = [w for w in row if w["x0"] < name_right_bound]
        head_univ_words = [w for w in row if head_univ_left <= w["x0"] < head_third_left]
        asst_univ_words = [w for w in row if asst_univ_left <= w["x0"] < asst_third_left]
        name_text = " ".join(w["text"] for w in name_words).strip()

        has_any_value = bool(head_univ_words or asst_univ_words)

        if (
            not has_any_value
            and boilerplate_page == row_page
            and boilerplate_top is not None
            and 0 < row_top - boilerplate_top < _CONTINUATION_ROW_GAP_THRESHOLD_PT
        ):
            # Wrapped second line of a page-header line, e.g. an institution
            # name spilling over ("...Institute and State" / "University").
            # Read as a sport it becomes "Basketball University".
            boilerplate_top = row_top
            index += 1
            continue

        row_gap = (row_top - previous_row_top) if previous_row_top is not None else None
        is_continuation = (
            not has_any_value
            and results
            # Same page, and genuinely below the line it continues - across a
            # page break the gap comes out large and negative, which passed
            # the "< threshold" test and glued the first sport on a new page
            # onto the last sport of the previous one.
            and previous_row_page == row_page
            and row_gap is not None
            and 0 < row_gap < _CONTINUATION_ROW_GAP_THRESHOLD_PT
        )

        if is_continuation:
            previous = results[-1]
            # No inserted space after a trailing hyphen (a wrapped word like
            # "X-" / "Country" should rejoin as "X-Country", matching the
            # naming already used by the scholarships/participation
            # parsers for this same sport).
            joiner = "" if previous.sport.endswith("-") else " "
            results[-1] = SportCoachingExpense(
                sport=f"{previous.sport}{joiner}{name_text}".strip(),
                head_coach_university_paid=previous.head_coach_university_paid,
                assistant_coach_university_paid=previous.assistant_coach_university_paid,
                is_team_specific=previous.is_team_specific,
                # Carried through: a wrapped name rebuilds the record, and
                # anything not restated here silently reverts to None - which
                # is how "Swimming and Diving" lost its position counts.
                head_coach_positions=previous.head_coach_positions,
                assistant_coach_positions=previous.assistant_coach_positions,
            )
            # Gap tracked from this continuation row too, not just the row
            # that started the record - a name can wrap across more than
            # two lines (e.g. "Track and" / "Field, X-" / "Country"), and
            # each successive wrapped line sits close to the *previous*
            # line, not close to the original row start.
            previous_row_top, previous_row_page = row_top, row_page
        elif name_text:
            # Prefix, not exact match on the first word: the whitespace-
            # collapsed reports render this row as one run
            # ("ExpensesNotRelatedtoSpecificTeams"), which an equality check
            # reads as a team-specific sport.
            is_team_specific = not name_text.startswith("Expenses")
            results.append(
                SportCoachingExpense(
                    sport=name_text,
                    head_coach_university_paid=_parse_float(head_univ_words[0]["text"]) if head_univ_words else None,
                    assistant_coach_university_paid=(
                        _parse_float(asst_univ_words[0]["text"]) if asst_univ_words else None
                    ),
                    is_team_specific=is_team_specific,
                    head_coach_positions=_column_value(row, position_anchors, _HEAD_POSITIONS_COLUMN),
                    assistant_coach_positions=_column_value(
                        row, position_anchors, _ASSISTANT_POSITIONS_COLUMN
                    ),
                )
            )
            previous_row_top, previous_row_page = row_top, row_page
        index += 1

    return tuple(results)


def parse_coaching_expenses(
    pdf_path: Path | str, rows: list[list[Word]] | None = None
) -> CoachingExpenseTables:
    """`rows` lets a caller that already read this PDF pass the grouped
    rows in rather than have them read again - see pfa/extraction/word_rows.
    Omit it and the document is read here, as before.
    """
    pdf_path = Path(pdf_path)
    if rows is None:
        rows = read_rows(pdf_path)

    mens_heading_index = _find_heading_row_index(rows, _MENS_HEADING)
    if mens_heading_index is None:
        raise ValueError(f"Could not find '{_MENS_HEADING}' heading in {pdf_path.name}")
    womens_heading_index = _find_heading_row_index(rows, _WOMENS_HEADING, after=mens_heading_index + 1)
    if womens_heading_index is None:
        raise ValueError(f"Could not find '{_WOMENS_HEADING}' heading in {pdf_path.name}")

    mens = _parse_subtable(rows, mens_heading_index)
    womens = _parse_subtable(rows, womens_heading_index)
    return CoachingExpenseTables(mens=mens, womens=womens, source_path=pdf_path)
