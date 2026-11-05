"""Parses the per-sport "Expenses by Object of Expenditure" tables.

Every operating-expense category in an FRS report (27 Recruiting, 28 Team
Travel, 29 Equipment, and so on) is followed by a small table breaking that
category down by sport, in three columns:

    Expenses by Object of Expenditure | Men's Teams Only | Women's Teams Only
                                      | Not Allocated by Gender
    Baseball                              45,339
    Basketball                            77,670              73,138
    ...
    Others
    Subtotal All Teams                   627,593             185,640        0
    Expenses Not Related to Specific Teams
    Total Expenses                       627,593             185,640        0

This is the source for the per-sport operating budgets. The institution-level
Revenue/Expense Summary (ncaa_frs_summary.py) gives the same categories as
single totals; only these tables say how a category splits across sports.

**Reading the columns.** The figures are right-aligned, so a column's left
edge moves with the width of the number sitting in it while its right edge
stays put - measured across the corpus, the three columns end at x=331.7,
444.9 and 574.1 whether the value is "0" or "2,209,241". Columns are
therefore identified by right edge, and the anchors are taken from the
table's own "Total Expenses" row, which always prints all three values (as
0 where there is nothing). Self-calibrating per table, so a report whose
layout shifts slightly is read correctly rather than approximately.

**Checking the reading.** Each column's per-sport figures must add up to
that column's printed "Subtotal All Teams". That is a genuine check of the
parse rather than a restatement of it - the subtotal is a separate number
printed by the institution - and it is exposed as
`SportExpenseTable.reconciles` so callers can surface a report whose figures
don't tie out instead of silently publishing them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from pfa.extraction.word_rows import Word, read_rows

#: Matched as a prefix, with spaces removed. The heading wraps onto a second
#: line whenever the category's name is long - "Expenses by Object of" /
#: "Expenditure Sports Equipment Uniforms and Supplies ..." - so matching the
#: whole phrase finds only the categories with short names (8 of 18 in the
#: reports checked) and silently loses the rest.
_TABLE_HEADING = "ExpensesbyObject"
_SUBTOTAL_LABEL = "SubtotalAllTeams"
_TOTAL_LABEL = "TotalExpenses"
_NOT_SPECIFIC_LABEL = "ExpensesNot"
_OTHERS_LABEL = "Others"

#: Sport names sit hard against the left margin (x0 = 38.0); the wrapped
#: second line of the column header is indented (x0 = 45.9). That gap is what
#: separates a sport row from the header block above it.
_SPORT_NAME_MARGIN_PT = 42.0

#: A wrapped sport name ("Track and Field, X-" / "Country") sits ~14pt below
#: the line it continues; a new sport row sits ~19pt below. Same measurement,
#: and same threshold, as the scholarships and coaching parsers.
_CONTINUATION_ROW_GAP_THRESHOLD_PT = 16.0

#: How far above the table heading to look for the category number/name line.
#: A category's description can run to a dozen lines plus a Note block before
#: its table starts - categories 36, 41 and 42 sit 13 to 19 rows up - so this
#: has to be generous. Crossing into the category above is prevented by
#: stopping at the previous table instead of by keeping the window tight.
_CATEGORY_SCAN_ROWS = 40

#: A figure is assigned to the column whose right edge it is nearest, but only
#: within this tolerance - anything further away is not in a value column.
_COLUMN_MATCH_TOLERANCE_PT = 12.0

#: Category lines look like "27 Recruiting $813,233" or "35A Athletic
#: Facilities Debt Service...". The id may carry a trailing letter.
_CATEGORY_RE = re.compile(r"^(?P<id>\d{1,2}[A-Z]?)$")

_GENDERS = ("male", "female", "unallocated")


@dataclass(frozen=True)
class SportExpenseTable:
    """One expense category, broken down by sport and gender."""

    category_id: str
    #: {gender: {sport: amount}} - only sports the report printed a figure for.
    by_gender: dict[str, dict[str, float]]
    #: The table's own printed "Subtotal All Teams", per gender; None if absent.
    printed_subtotals: dict[str, float | None]
    #: True when every column's parsed figures sum to its printed subtotal.
    reconciles: bool

    def amount(self, *, gender: str, sport: str) -> float | None:
        return self.by_gender.get(gender, {}).get(sport)


@dataclass
class SportExpenses:
    """Every per-sport expense table in one report, keyed by category id."""

    tables: dict[str, SportExpenseTable] = field(default_factory=dict)
    source_path: Path | None = None

    def amount(self, *, category_id: str, gender: str, sport: str) -> float | None:
        table = self.tables.get(category_id)
        return None if table is None else table.amount(gender=gender, sport=sport)

    def sports(self, *, gender: str) -> list[str]:
        """Every sport this report reports any expense for, on one side."""
        seen: list[str] = []
        for table in self.tables.values():
            for sport in table.by_gender.get(gender, {}):
                if sport not in seen:
                    seen.append(sport)
        return seen

    @property
    def unreconciled_categories(self) -> list[str]:
        return sorted(cid for cid, t in self.tables.items() if not t.reconciles)


def _squash(texts: list[str]) -> str:
    return "".join(texts).replace(" ", "")


def _parse_amount(text: str) -> float | None:
    cleaned = text.replace(",", "").replace("$", "").strip()
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    try:
        return float(cleaned)
    except ValueError:
        return None


def _numeric_words(row: list[Word]) -> list[Word]:
    return [w for w in row if _parse_amount(w["text"]) is not None]


def _find_category_id(rows: list[list[Word]], heading_index: int) -> str | None:
    """Scans up from a table heading for the "27 Recruiting $813,233" line
    that introduces it. The id is the first word on that line.
    """
    for index in range(heading_index - 1, max(heading_index - _CATEGORY_SCAN_ROWS, -1), -1):
        row = rows[index]
        if not row:
            continue
        if _squash([w["text"] for w in row]).startswith(_TABLE_HEADING):
            # The previous category's own table - anything above this belongs
            # to that category, not this one.
            return None
        match = _CATEGORY_RE.match(row[0]["text"])
        if match and row[0]["x0"] < 60:  # the id sits in the far-left margin
            return match.group("id")
    return None


def _column_anchors(rows: list[list[Word]], start: int, stop: int) -> list[float] | None:
    """The three columns' right edges, taken from the table's Total Expenses
    row (falling back to Subtotal All Teams). Both print all three columns.
    """
    for label in (_TOTAL_LABEL, _SUBTOTAL_LABEL):
        for index in range(start, stop):
            row = rows[index]
            if not row or not _squash([w["text"] for w in row]).startswith(label):
                continue
            numbers = _numeric_words(row)
            if len(numbers) == 3:
                return sorted(w["x1"] for w in numbers)
    return None


def _assign_column(word: Word, anchors: list[float]) -> int | None:
    best, best_distance = None, None
    for index, anchor in enumerate(anchors):
        distance = abs(word["x1"] - anchor)
        if best_distance is None or distance < best_distance:
            best, best_distance = index, distance
    if best_distance is not None and best_distance <= _COLUMN_MATCH_TOLERANCE_PT:
        return best
    return None


def _table_end(rows: list[list[Word]], start: int) -> int:
    for index in range(start, min(start + 40, len(rows))):
        squashed = _squash([w["text"] for w in rows[index]])
        if squashed.startswith(_TOTAL_LABEL):
            return index + 1
    return min(start + 40, len(rows))


def _parse_table(rows: list[list[Word]], heading_index: int) -> SportExpenseTable | None:
    category_id = _find_category_id(rows, heading_index)
    if category_id is None:
        return None

    end = _table_end(rows, heading_index + 1)
    anchors = _column_anchors(rows, heading_index + 1, end)
    if anchors is None:
        return None

    by_gender: dict[str, dict[str, float]] = {g: {} for g in _GENDERS}
    printed: dict[str, float | None] = {g: None for g in _GENDERS}

    index = heading_index + 1
    previous_top: float | None = None
    previous_page: int | None = None
    last_sport: str | None = None
    while index < end:
        row = rows[index]
        index += 1
        if not row:
            continue
        texts = [w["text"] for w in row]
        squashed = _squash(texts)

        if squashed.startswith(_SUBTOTAL_LABEL):
            # The sports run from the heading down to this row; everything
            # below it is the table's own totals.
            for word in _numeric_words(row):
                column = _assign_column(word, anchors)
                if column is not None:
                    printed[_GENDERS[column]] = _parse_amount(word["text"])
            break
        if squashed.startswith((_TOTAL_LABEL, _NOT_SPECIFIC_LABEL, "NCAA", "Reporting")):
            continue

        numbers = _numeric_words(row)
        name_words = [w for w in row if w not in numbers and w["x0"] < 230]
        if not name_words:
            continue
        # Anything indented past the margin is the wrapped column-header
        # block, not a sport.
        if name_words[0]["x0"] > _SPORT_NAME_MARGIN_PT:
            continue

        name = " ".join(w["text"] for w in name_words).strip()
        if not name:
            continue

        top, page = row[0]["top"], row[0]["page"]
        gap = (top - previous_top) if previous_top is not None else None
        is_continuation = (
            last_sport is not None
            and not numbers
            and previous_page == page
            and gap is not None
            and 0 < gap < _CONTINUATION_ROW_GAP_THRESHOLD_PT
        )
        previous_top, previous_page = top, page

        if is_continuation:
            # The rest of a wrapped sport name. Rejoined without an inserted
            # space after a trailing hyphen, so "Track and Field, X-" plus
            # "Country" reads as "Track and Field, X-Country" - the spelling
            # the other parsers produce for this same sport.
            joiner = "" if last_sport.endswith("-") else " "
            merged = f"{last_sport}{joiner}{name}".strip()
            for gender in _GENDERS:
                if last_sport in by_gender[gender]:
                    by_gender[gender][merged] = by_gender[gender].pop(last_sport)
            last_sport = merged
            continue

        last_sport = name
        if name == _OTHERS_LABEL:
            # A genuine aggregate row, not a sport - excluded here for the
            # same reason pfa/builders/sports.py excludes it downstream.
            continue

        for word in numbers:
            column = _assign_column(word, anchors)
            if column is None:
                continue
            amount = _parse_amount(word["text"])
            if amount is not None:
                by_gender[_GENDERS[column]][name] = amount

    reconciles = True
    for gender in _GENDERS:
        expected = printed[gender]
        if expected is None:
            continue
        if abs(sum(by_gender[gender].values()) - expected) > 1.0:
            reconciles = False

    return SportExpenseTable(
        category_id=category_id,
        by_gender=by_gender,
        printed_subtotals=printed,
        reconciles=reconciles,
    )


def parse_sport_expenses(
    pdf_path: Path | str, rows: list[list[Word]] | None = None
) -> SportExpenses:
    """`rows` lets a caller that already read this PDF pass the grouped rows
    in rather than have them read again - see pfa/extraction/word_rows.
    """
    pdf_path = Path(pdf_path)
    if rows is None:
        rows = read_rows(pdf_path)

    result = SportExpenses(source_path=pdf_path)
    for index, row in enumerate(rows):
        if not _squash([w["text"] for w in row]).startswith(_TABLE_HEADING):
            continue
        table = _parse_table(rows, index)
        if table is not None and table.category_id not in result.tables:
            result.tables[table.category_id] = table
    return result
