"""Parser for the NCAA Financial Reporting System (FRS) "Revenue/Expense
Summary" table - the numbered ID/Item/Amount/Definition table on the first
several pages of every FRS PDF (before the much longer, per-sport
"Revenue/Expense Details" section).

Verified against real FY2024 reports (Appalachian State, Boise State,
Coastal Carolina) and a real FY2025 report (Oregon State): the category ID
system is stable across years, but the NCAA added five new categories
starting FY2025 - 35A, 42, 42A, 43, 44 (and also split category 12 into
12A/12B/12C, and added 41B and 42B) - reflecting the House settlement /
NIL revenue-sharing changes. Confirmed these are genuinely absent (not
mis-parsed) from all three FY2024 samples.

This parser is deliberately presence-based, not year-hardcoded: it
extracts whatever category IDs actually appear in a given document and
simply omits IDs that aren't present, rather than assuming a fixed
category list keyed off fiscal year. That's more robust than an
"if fiscal_year < 2025: skip category X" branch - it keeps working
unchanged if the NCAA revises the form again, and callers only need one
rule ("missing from the parsed result" = "not applicable to this
document"), not a maintained table of which categories appeared in which
year.

KNOWN_FY2025_ONLY_CATEGORY_IDS below is diagnostic metadata only, for
callers that want to distinguish "expected absence" (pre-2025 report) from
"unexpected absence" (2025+ report missing a category it should have) -
the parser itself never consults it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

from pfa.core.domain.exceptions import ExtractionError

_INSTITUTION_YEAR_RE = re.compile(
    r"Reporting Institution:\s*(?P<institution>.+?)\s+Reporting Year \(FY\):\s*(?P<year>\d{4})"
)
_ID_AMOUNT_RE = re.compile(r"^(?P<id>\d{1,2}[A-Z]?)\s+.+?(?P<amount>-?\$[\d,]+)\s", re.MULTILINE)
_DETAILS_SECTION_MARKER = "Revenue/Expense Details"

# The Revenue/Expense Summary section ends with two roll-up lines, e.g.:
#   "Total Operating $104,767,707 Total of Categories 1-19."      (revenues)
#   "Total Operating $100,306,455 Total of Categories 20-44."     (expenses)
# The word immediately after ("Revenues" or "Expenses") is not used to tell
# these apart - pdfplumber's line-wrapping of that trailing word is
# inconsistent across reports (confirmed: on one page it wraps as part of
# the total line, on another it's actually the *next* section's heading and
# only coincidentally sits on the following line). The starting category
# number is unambiguous and used instead: category 1 always starts the
# revenue categories, category 20 always starts the expense categories.
_TOTAL_OPERATING_RE = re.compile(
    r"Total Operating\s+(?P<sign>-?)\$(?P<amount>[\d,]+)\s+Total of Categories\s+"
    r"(?P<start>\d+)-(?P<end>\d+[A-Z]?)\."
)

#: Diagnostic-only - see module docstring. Not used by the parser itself.
KNOWN_FY2025_ONLY_CATEGORY_IDS = frozenset(
    {"12A", "12B", "12C", "35A", "41B", "42", "42A", "42B", "43", "44"}
)


@dataclass(frozen=True)
class FRSSummary:
    """Every ID -> Amount pair found in one report's Revenue/Expense Summary,
    plus the institution/year identified from the report header.
    """

    institution_name: str
    fiscal_year: int
    amounts_by_category_id: dict[str, int]
    source_path: Path
    total_operating_revenues: int | None = None
    total_operating_expenses: int | None = None

    def get(self, category_id: str) -> int | None:
        """Returns the amount for a category, or None if that category
        simply doesn't appear in this document - the correct behavior for a
        pre-FY2025 report and categories 35A/42/42A/43/44 (or any other
        category the NCAA adds or removes in a future form revision).
        None is deliberately distinct from 0: a category can genuinely be
        reported as $0 (present, just no activity) - that must not be
        confused with "this category doesn't exist in this year's form."
        """
        return self.amounts_by_category_id.get(category_id)

    def is_fy2025_category_unexpectedly_missing(self, category_id: str) -> bool:
        """Diagnostic check: True if this is a FY2025+ report and a category
        known to exist starting FY2025 is nonetheless missing - worth
        flagging for review (possible extraction miss or a genuinely unusual
        filing), as opposed to a pre-2025 report where absence is expected.
        """
        return (
            self.fiscal_year >= 2025
            and category_id in KNOWN_FY2025_ONLY_CATEGORY_IDS
            and self.get(category_id) is None
        )


def _parse_amount(raw: str) -> int:
    """'$12,500,000' -> 12500000, '-$43,135' -> -43135.

    NCAA FRS amounts are always whole dollars (no cents observed across any
    sampled report) - returned as int rather than Decimal for this reason;
    revisit if a future report is found reporting cents.
    """
    negative = raw.startswith("-")
    digits = raw.lstrip("-").lstrip("$").replace(",", "")
    value = int(digits)
    return -value if negative else value


#: The page header is a two-column band: "Reporting Institution: <name>" on
#: the left and "Reporting Year (FY): <year>" on the right, both at top=37.4,
#: set in Times-Roman 12. The name starts just after its label (x0 ~ 151.7).
#: The form gives it a box about 195pt wide - the two longest names in the
#: FY2025 corpus, "Texas A&M University, College Station" and "California
#: State University, Bakersfield", both stop at x1 = 341-343 - and a longer
#: name wraps onto a second line. Reading only the first line truncates it:
#: "The University of North Carolina at Greensboro" comes back as
#: "The University of North Carolina at".
#:
#: The year block at x0 = 439.8 is the hard right bound used below, since it
#: is the one position that can be measured on the page itself rather than
#: inferred from how wide the longest name happened to be.
#:
#: A wrapped line sits one 14.4pt line under the one it continues. The page's
#: own content starts 36pt below the header - but only 21.7pt below a
#: continuation line, which is close enough that a gap test alone swallowed
#: the "School Info" heading. Hence the left-edge rule below as well.
_HEADER_CONTINUATION_GAP_PT = 18.0

#: What actually separates a wrapped name from the next thing on the page: a
#: continuation resumes at the name's own left edge (x0 ~ 151.7), while the
#: content below is a centred heading or starts back at the page margin. The
#: tolerance covers the sub-point jitter between one line's x0 and the next's.
_HEADER_COLUMN_TOLERANCE_PT = 3.0

#: How far down the page to read when looking for the header and anything
#: continuing it. The header sits at top=37.4 and the page's own content
#: starts at 73.5, so this is generous without reaching into the tables.
_HEADER_BAND_PT = 140.0


def _header_rows(page) -> list[list[dict]]:
    """The page's top band as visual rows, left to right.

    Grouped by pfa.extraction.word_rows, so a line whose words differ by a
    fraction of a point still reads as one row - the same tolerance every
    other parser in this package uses.
    """
    from pfa.extraction.word_rows import group_into_rows

    stream = [
        {"page": 0, "top": w["top"], "x0": w["x0"], "x1": w["x1"], "text": w["text"]}
        for w in page.extract_words()
        if w["top"] <= _HEADER_BAND_PT
    ]
    return group_into_rows(stream)


def _read_header(page) -> tuple[str, int] | None:
    """The institution name and fiscal year, read from word positions.

    Taken from positions rather than from the extracted text because the text
    gives no way to tell a wrapped name from the next line of the page - both
    are just the line after the header. The name is every word between the end
    of its label and the start of the year block, continued onto the following
    line while that line sits in the same column and close enough underneath.
    """
    rows = _header_rows(page)
    for index, row in enumerate(rows):
        texts = [w["text"] for w in row]
        if texts[:2] != ["Reporting", "Institution:"]:
            continue

        label_end = row[1]["x1"]
        rest = [w for w in row[2:] if w["x0"] > label_end]
        year_block = [
            position for position, word in enumerate(rest) if word["text"] == "Reporting"
        ]
        if not year_block:
            return None
        split = year_block[0]
        name_words, year_words = rest[:split], rest[split:]
        if not name_words:
            return None

        year = next(
            (w["text"] for w in year_words if w["text"].isdigit() and len(w["text"]) == 4),
            None,
        )
        if year is None:
            return None

        name_left = min(w["x0"] for w in name_words)
        year_left = rest[split]["x0"]
        parts = [" ".join(w["text"] for w in name_words)]

        previous_top = row[0]["top"]
        for following in rows[index + 1:]:
            gap = following[0]["top"] - previous_top
            if not 0 < gap < _HEADER_CONTINUATION_GAP_PT:
                break
            starts_in_column = (
                abs(following[0]["x0"] - name_left) <= _HEADER_COLUMN_TOLERANCE_PT
            )
            stays_in_column = all(w["x1"] <= year_left for w in following)
            if not (starts_in_column and stays_in_column):
                break
            parts.append(" ".join(w["text"] for w in following))
            previous_top = following[0]["top"]

        return " ".join(parts).strip(), int(year)
    return None


def parse_frs_summary(pdf_path: Path | str) -> FRSSummary:
    """Parses the Revenue/Expense Summary section of an NCAA FRS PDF.

    Reads only as many pages as needed to reach the "Revenue/Expense
    Details" section boundary - typically ~14-16 pages out of an 80-93 page
    document - rather than the whole file, since only the summary table is
    needed for institution-level category totals.
    """
    pdf_path = Path(pdf_path)
    institution: str | None = None
    fiscal_year: int | None = None
    summary_text_parts: list[str] = []

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            header_match = (
                _INSTITUTION_YEAR_RE.search(text) if institution is None else None
            )
            if header_match:
                # Positions first, so a name that wraps onto a second line
                # comes back whole; the text match is the fallback for a
                # layout this doesn't recognise, and is what every report in
                # the corpus resolved to before. Read before the flush below,
                # while the page's words are still parsed.
                positioned = _read_header(page)
                if positioned is not None:
                    institution, fiscal_year = positioned
                else:
                    institution = header_match.group("institution").strip()
                    fiscal_year = int(header_match.group("year"))
            # pdfplumber keeps every page's parsed objects on the page once
            # touched, so this is released as we go to keep memory flat
            # regardless of how long the document is.
            page.flush_cache()
            if _DETAILS_SECTION_MARKER in text:
                break
            summary_text_parts.append(text)

    if institution is None or fiscal_year is None:
        raise ExtractionError(
            f"could not find 'Reporting Institution' / 'Reporting Year (FY)' header in {pdf_path} - "
            "is this an NCAA FRS report?"
        )

    full_text = "\n".join(summary_text_parts)
    amounts: dict[str, int] = {}
    for match in _ID_AMOUNT_RE.finditer(full_text):
        category_id = match.group("id")
        # First match wins per category id - the summary table lists each
        # category exactly once, so a repeat match this early in the
        # document would indicate a parsing anomaly, not new information.
        amounts.setdefault(category_id, _parse_amount(match.group("amount")))

    if not amounts:
        raise ExtractionError(
            f"found the report header in {pdf_path} but no revenue/expense category amounts - "
            "the document structure may have changed or this may be a scanned/non-digital PDF"
        )

    total_operating_revenues: int | None = None
    total_operating_expenses: int | None = None
    for match in _TOTAL_OPERATING_RE.finditer(full_text):
        amount = _parse_amount(match.group("sign") + "$" + match.group("amount"))
        start_category = int(match.group("start"))
        if start_category == 1:
            total_operating_revenues = amount
        elif start_category == 20:
            total_operating_expenses = amount

    return FRSSummary(
        institution_name=institution,
        fiscal_year=fiscal_year,
        amounts_by_category_id=amounts,
        source_path=pdf_path,
        total_operating_revenues=total_operating_revenues,
        total_operating_expenses=total_operating_expenses,
    )
