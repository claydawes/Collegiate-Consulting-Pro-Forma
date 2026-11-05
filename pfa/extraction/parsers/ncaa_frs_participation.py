"""Parses the "Athletics Participation" table (NCAA Table 481/Table 1) that
appears in the NCAA FRS report, listing roster headcounts per sport, split
into Coed / Men's Teams / Women's Teams columns (each of those repeated
twice more for "participating on a second team" / "third team" - this
parser reads only the first, primary count for each sport/gender, not the
second/third-team duplicate columns).

Verified against Oregon State's FY2025 report (page 76): summing this
parser's per-sport Men's Teams values reproduces the table's own printed
"Total Participants" Men's figure (276) exactly, and likewise for Women's
(205) - a genuine internal-consistency check, not just spot-checked
numbers.

One structural note worth knowing before trusting this data at face value:
a sport that's contiguous across seasons for the same roster (Cross
Country, Indoor Track, Outdoor Track) is sometimes reported under only one
or two of those three rows, with the others left blank - the athletes
aren't double-counted across all three. Confirmed for Oregon State: Cross
Country (29) and Track, Indoor (30) each have a figure, but Track, Outdoor
is blank, and the three don't sum to any printed total - they're reported
as distinct headcounts per the school's own choice of which row(s) to use,
not required to all be populated. This is intentional in the source
document, not a parsing gap; a blank SportParticipation.participants for a
sponsored sport should be treated as "not separately reported here", not
as an extraction failure.
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

_HEADER_ROW_REQUIRED_TOKENS = ("Sport", "Coed", "Men's", "Women's")
_STOP_LABEL = "Total"
_COLUMN_GUTTER_PT = 8.0
_ROW_MERGE_TOLERANCE_PT = 2.0
_SPORT_NAME_RIGHT_BOUND_SLACK_PT = 15.0


@dataclass(frozen=True)
class SportParticipation:
    sport: str
    mens_participants: int | None
    womens_participants: int | None
    coed_participants: int | None


@dataclass(frozen=True)
class ParticipationTable:
    sports: tuple[SportParticipation, ...]
    source_path: Path
    printed_mens_total: int | None = None
    printed_womens_total: int | None = None

    def mens_roster(self) -> dict[str, int]:
        return {s.sport: s.mens_participants for s in self.sports if s.mens_participants is not None}

    def womens_roster(self) -> dict[str, int]:
        return {s.sport: s.womens_participants for s in self.sports if s.womens_participants is not None}


def _find_header_row_index(rows: list[list[Word]]) -> int | None:
    for index, row in enumerate(rows):
        texts = [w["text"] for w in row]
        if texts[:1] == ["Sport"] and all(token in texts for token in _HEADER_ROW_REQUIRED_TOKENS):
            return index
    return None


def _locate_column_bounds(header_row: list[Word]) -> tuple[float, float, float, float]:
    """Returns (coed_x0, mens1_x0, womens1_x0, mens2_x0) - the fourth value
    is only used as the right-hand boundary for the (first) women's column.
    """
    matches = [w["x0"] for w in header_row if w["text"] in ("Coed", "Men's", "Women's")]
    matches.sort()
    if len(matches) < 4:
        raise ValueError(
            "Expected at least 4 Coed/Men's/Women's column markers in the Athletics Participation "
            f"header row, found {len(matches)} - table layout does not match what this parser expects."
        )
    return matches[0], matches[1], matches[2], matches[3]


def _parse_int(text: str) -> int | None:
    stripped = text.replace(",", "").strip()
    if not stripped.isdigit():
        return None
    return int(stripped)


def parse_participation_table(
    pdf_path: Path | str, rows: list[list[Word]] | None = None
) -> ParticipationTable:
    """`rows` lets a caller that already read this PDF pass the grouped
    rows in rather than have them read again - see pfa/extraction/word_rows.
    Omit it and the document is read here, as before.
    """
    pdf_path = Path(pdf_path)
    if rows is None:
        rows = read_rows(pdf_path)

    header_index = _find_header_row_index(rows)
    if header_index is None:
        raise ValueError(f"Could not find the Athletics Participation table header row in {pdf_path.name}")

    coed_x0, mens1_x0, womens1_x0, mens2_x0 = _locate_column_bounds(rows[header_index])
    coed_left = coed_x0 - _COLUMN_GUTTER_PT
    mens1_left = mens1_x0 - _COLUMN_GUTTER_PT
    womens1_left = womens1_x0 - _COLUMN_GUTTER_PT
    mens2_left = mens2_x0 - _COLUMN_GUTTER_PT
    name_right_bound = coed_left - _SPORT_NAME_RIGHT_BOUND_SLACK_PT

    results: list[SportParticipation] = []
    printed_mens_total: int | None = None
    printed_womens_total: int | None = None

    # Distinguishing a true wrapped-name continuation line (e.g. a second
    # sub-line squeezed under a long sport name) from a legitimate new sport
    # row that simply has no participant figures at all (observed for real:
    # Oregon State's "Track, Outdoor" row is a genuine, fully-sponsored
    # sport with every value column blank - not a wrap of "Track, Indoor")
    # can't be done from "does this row have a value" alone. What's
    # reliable is vertical spacing: normal rows in this table sit a full
    # row-height apart (~19pt, confirmed from Baseball -> Basketball ->
    # Football...); a wrapped sub-line sits much closer beneath the row it
    # continues (~14pt, confirmed from "Track and Field," -> "X-Country" in
    # the sibling scholarships table). A gap below this threshold with no
    # value on the row is treated as a continuation; a full-height gap is
    # treated as a new (possibly blank) sport row.
    _CONTINUATION_ROW_GAP_THRESHOLD_PT = 16.0

    index = header_index + 1
    previous_row_top: float | None = None
    while index < len(rows):
        row = rows[index]
        texts = [w["text"] for w in row]
        if not texts:
            index += 1
            continue
        if texts[0].startswith("Reporting") or texts[0] == "NCAA":
            index += 1
            continue
        if texts[0] == _STOP_LABEL:
            numbers = [w["text"] for w in row if w["x0"] >= coed_left]
            values = [_parse_int(t) for t in numbers if _parse_int(t) is not None]
            if len(values) >= 2:
                printed_mens_total, printed_womens_total = values[0], values[1]
            break
        if texts[0] in ("Participant", "Unduplicated"):
            index += 1
            continue

        row_top = row[0]["top"]
        name_words = [w for w in row if w["x0"] < name_right_bound]
        coed_words = [w for w in row if coed_left <= w["x0"] < mens1_left]
        mens_words = [w for w in row if mens1_left <= w["x0"] < womens1_left]
        womens_words = [w for w in row if womens1_left <= w["x0"] < mens2_left]
        name_text = " ".join(w["text"] for w in name_words).strip()

        has_any_value = bool(coed_words or mens_words or womens_words)
        row_gap = (row_top - previous_row_top) if previous_row_top is not None else None
        is_continuation = (
            not has_any_value
            and results
            and row_gap is not None
            and row_gap < _CONTINUATION_ROW_GAP_THRESHOLD_PT
        )

        if is_continuation:
            previous = results[-1]
            results[-1] = SportParticipation(
                sport=f"{previous.sport} {name_text}".strip(),
                mens_participants=previous.mens_participants,
                womens_participants=previous.womens_participants,
                coed_participants=previous.coed_participants,
            )
            # See ncaa_frs_coaching_expenses.py's identical fix: a wrapped
            # name can span more than two lines, so the gap for the *next*
            # line must be measured from this continuation line, not from
            # the original row that started the record.
            previous_row_top = row_top
        elif name_text:
            results.append(
                SportParticipation(
                    sport=name_text,
                    mens_participants=_parse_int(mens_words[0]["text"]) if mens_words else None,
                    womens_participants=_parse_int(womens_words[0]["text"]) if womens_words else None,
                    coed_participants=_parse_int(coed_words[0]["text"]) if coed_words else None,
                )
            )
            previous_row_top = row_top
        index += 1

    return ParticipationTable(
        sports=tuple(results),
        source_path=pdf_path,
        printed_mens_total=printed_mens_total,
        printed_womens_total=printed_womens_total,
    )
