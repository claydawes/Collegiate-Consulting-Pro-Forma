"""Parses the "Sports Sponsorship" table that appears on page 1-2 of every
NCAA FRS report (a fixed institution-level checklist: Sport x Men's-Teams-
Only x Women's-Teams-Only x Mixed-Teams, marked with "x").

Two uses so far:

1. A secondary/cross-check data source for the scholarships workbook, not
   its primary source of per-sport values (see ncaa_frs_scholarships.py's
   module docstring for why): a sport can be sponsored here with zero
   athletic aid awarded in a given year, and conversely the Athletic
   Student Aid detail tables sometimes report closely-related sports (e.g.
   Cross Country, Indoor Track, Outdoor Track) under one combined "Track
   and Field, X-Country" row. Comparing this module's sponsored-sport
   counts against the scholarship table's row counts is used as a
   plausibility check, surfaced as a warning rather than silently trusted
   as the row-3 header source.
2. The primary source for the "# of Sports" figure in the Total Athletic
   Budget workbook (SponsorshipTable.total_sponsored_team_count) - there,
   cross country/indoor track/outdoor track being reported as three
   separate rows here (rather than combined, as in the scholarships
   detail) is exactly the behavior wanted, per the client's own template
   notes ("cross country, indoor track and field and outdoor track and
   field all count as individual sports").
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

_SECTION_HEADING = "Sports Sponsorship:"
_STOP_LABEL = "Totals"
_COLUMN_HEADER_WORDS = ("Men's", "Women's", "Mixed")
_COLUMN_GUTTER_PT = 6.0
_ROW_MERGE_TOLERANCE_PT = 2.0
_SPORT_NAME_RIGHT_BOUND_PT = 210.0  # NCAA FRS's fixed page layout: sport-name column ends well before the "Men's" header


@dataclass(frozen=True)
class SportSponsorship:
    sport: str
    mens_team: bool
    womens_team: bool
    mixed_team: bool

    @property
    def sponsored(self) -> bool:
        return self.mens_team or self.womens_team or self.mixed_team


@dataclass(frozen=True)
class SponsorshipTable:
    sports: tuple[SportSponsorship, ...]
    source_path: Path
    #: (men's-only count, women's-only count, mixed count) as printed on the
    #: table's own "Totals" row - None if that row couldn't be parsed. Used
    #: only as a self-check against this parser's own counts (see
    #: total_sponsored_team_count).
    printed_totals: tuple[int, int, int] | None = None

    def sponsored_mens_sports(self) -> list[str]:
        return [s.sport for s in self.sports if s.mens_team or s.mixed_team]

    def sponsored_womens_sports(self) -> list[str]:
        return [s.sport for s in self.sports if s.womens_team or s.mixed_team]

    @property
    def total_sponsored_team_count(self) -> int:
        """The "# of Sports" figure used in athletic-budget benchmarking:
        one count per sponsored *team*, not per distinct sport name - a
        sport sponsored separately for men and women (e.g. Basketball)
        counts twice, a coed/mixed-team sport counts once. This matches how
        NCAA's own "Totals" row at the bottom of the Sports Sponsorship
        table is computed (men's-only + women's-only + mixed), and how the
        Total Athletic Budget template's own notes describe counting (e.g.
        cross country, indoor track, and outdoor track each count
        individually, since each is its own row/team here).
        """
        return sum((1 if s.mens_team else 0) + (1 if s.womens_team else 0) + (1 if s.mixed_team else 0) for s in self.sports)


def _find_heading_row_index(rows: list[list[Word]], heading_text: str) -> int | None:
    for index, row in enumerate(rows):
        if heading_text in " ".join(w["text"] for w in row):
            return index
    return None


def _locate_column_bounds(rows: list[list[Word]], start_index: int) -> tuple[float, float, int]:
    """Returns (womens_column_left_x0, mixed_column_left_x0, data_start_index)."""
    mens_x0 = womens_x0 = mixed_x0 = None
    index = start_index
    scanned = 0
    while index < len(rows) and scanned < 4:
        for word in rows[index]:
            if word["text"] == "Men's":
                mens_x0 = word["x0"]
            elif word["text"] == "Women's":
                womens_x0 = word["x0"]
            elif word["text"] == "Mixed":
                mixed_x0 = word["x0"]
        index += 1
        scanned += 1
        if mens_x0 is not None and womens_x0 is not None and mixed_x0 is not None:
            return womens_x0, mixed_x0, index

    raise ValueError("Could not locate the Men's/Women's/Mixed column headers in the Sports Sponsorship table.")


def parse_sponsorship_table(
    pdf_path: Path | str, rows: list[list[Word]] | None = None
) -> SponsorshipTable:
    """`rows` lets a caller that already read this PDF pass the grouped
    rows in rather than have them read again - see pfa/extraction/word_rows.
    Omit it and the document is read here, as before.
    """
    pdf_path = Path(pdf_path)
    if rows is None:
        rows = read_rows(pdf_path)

    heading_index = _find_heading_row_index(rows, _SECTION_HEADING)
    if heading_index is None:
        raise ValueError(f"Could not find '{_SECTION_HEADING}' heading in {pdf_path.name}")

    womens_left_x0, mixed_left_x0, data_start_index = _locate_column_bounds(rows, heading_index + 1)
    womens_bound = womens_left_x0 - _COLUMN_GUTTER_PT
    mixed_bound = mixed_left_x0 - _COLUMN_GUTTER_PT

    results: list[SportSponsorship] = []
    printed_totals: tuple[int, int, int] | None = None
    index = data_start_index
    while index < len(rows):
        row = rows[index]
        texts = [w["text"] for w in row]
        if not texts:
            index += 1
            continue
        if texts[0] == _STOP_LABEL:
            numbers = [w["text"] for w in row if w["x0"] >= _SPORT_NAME_RIGHT_BOUND_PT and w["text"].isdigit()]
            if len(numbers) >= 3:
                printed_totals = (int(numbers[0]), int(numbers[1]), int(numbers[2]))
            break
        joined = " ".join(texts)
        if joined.startswith("Reporting Institution:") or joined.startswith("Sport Men's"):
            index += 1
            continue

        name_words = [w for w in row if w["x0"] < _SPORT_NAME_RIGHT_BOUND_PT]
        mens_mark = any(w["x0"] < womens_bound for w in row if w["text"] == "x" and w["x0"] >= _SPORT_NAME_RIGHT_BOUND_PT)
        womens_mark = any(womens_bound <= w["x0"] < mixed_bound for w in row if w["text"] == "x")
        mixed_mark = any(w["x0"] >= mixed_bound for w in row if w["text"] == "x")
        name_text = " ".join(w["text"] for w in name_words).strip()

        if name_text:
            results.append(
                SportSponsorship(sport=name_text, mens_team=mens_mark, womens_team=womens_mark, mixed_team=mixed_mark)
            )
        index += 1

    return SponsorshipTable(sports=tuple(results), source_path=pdf_path, printed_totals=printed_totals)
