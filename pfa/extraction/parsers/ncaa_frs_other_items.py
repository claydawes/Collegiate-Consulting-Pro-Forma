"""Parses the "Other Reporting Items" page - the institution-level financial
context behind the athletics figures.

One page near the back of every FRS report, in two labelled blocks:

    Other Reporting Items
    AUP Data Categories:
    50 - Excess Transfers to Institution:                        $0
    ...
    52 - Total Athletics Related Debt:                 $132,672,511
    53 - Total Institutional Debt:                     $878,921,000
    54 - Athletics Dedicated Endowments:                $62,601,136
    55 - Institutional Endowments:                     $949,481,686
    ...
    Other Data Categories:
    Institutional Expenses:                          $1,302,416,000
    Athletically-Related Facilities Annual Debt Service: $10,661,900
    Institution's Annual Debt Service:                  $52,410,000
    Average Cost of Full Grant-in-Aid - In-State:           $27,147
    ...

Unlike the sport tables this is not a grid - it is one label and one figure
per line, so there are no columns to anchor and nothing to reconcile against.
What the parse has to get right is *which* line it read, and the risk there is
labels that shadow each other: "Institutional Expenses" against "Institutional
Endowments", "Institution's Annual Debt Service" against "Institution's
Education and General Expenses". Every label is therefore matched whole,
against the text left of the line's colon, rather than by prefix.

Labels are compared with their spaces removed. Some reports extract with the
inter-word spacing collapsed, and squashing both sides reads those the same
way as the rest - the same technique the sport-expense and coaching parsers
use for their headings.

Across the 51 readable reports in the FY2025 corpus all eleven figures this
returns were found in every one, in a single consistent layout. Three cells
were genuinely blank in the report (Oklahoma State's two endowment lines,
Colorado's out-of-state grant-in-aid); those come back as None, which the
workbook shows as a blacked-out cell rather than as a zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pfa.core.domain.exceptions import ExtractionError
from pfa.extraction.word_rows import Word, read_rows

#: The page heading the section is read from. Anchoring on it keeps the labels
#: from matching a line of the same name elsewhere in the document.
_SECTION_HEADING = "OtherReportingItems"

#: How far past the heading to keep reading. The section runs to 20 lines in
#: every report checked and has never spanned a page; this leaves room for a
#: wrapped label or two without reaching the next section.
_SECTION_ROWS = 45

#: Label (squashed) -> attribute on OtherReportingItems. The two blocks share
#: one namespace here because the labels are unique across both.
_FIELDS: dict[str, str] = {
    # AUP Data Categories
    "52-TotalAthleticsRelatedDebt": "athletics_related_debt",
    "53-TotalInstitutionalDebt": "institutional_debt",
    "54-AthleticsDedicatedEndowments": "athletics_dedicated_endowments",
    "55-InstitutionalEndowments": "institutional_endowments",
    # Other Data Categories
    "InstitutionalExpenses": "institutional_expenses",
    "Athletically-RelatedFacilitiesAnnualDebtService": "facilities_annual_debt_service",
    "Institution'sAnnualDebtService": "institution_annual_debt_service",
    "AverageCostofAttendance-In-State": "cost_of_attendance_in_state",
    "AverageCostofAttendance-Out-of-State": "cost_of_attendance_out_of_state",
    "AverageCostofFullGrant-in-Aid-In-State": "grant_in_aid_in_state",
    "AverageCostofFullGrant-in-Aid-Out-of-State": "grant_in_aid_out_of_state",
}


@dataclass
class OtherReportingItems:
    """The eleven figures the Institutional Financial Factors workbook shows.

    Every one is a dollar amount. None means the report left the line blank -
    which is not the same as reporting zero, and is shown differently.
    """

    institutional_expenses: float | None = None
    institutional_debt: float | None = None
    institutional_endowments: float | None = None
    institution_annual_debt_service: float | None = None
    athletics_related_debt: float | None = None
    athletics_dedicated_endowments: float | None = None
    facilities_annual_debt_service: float | None = None
    cost_of_attendance_in_state: float | None = None
    cost_of_attendance_out_of_state: float | None = None
    grant_in_aid_in_state: float | None = None
    grant_in_aid_out_of_state: float | None = None

    #: Labels that were present on the page but carried no figure. Reported so
    #: a blank cell can be explained as "the institution didn't answer" rather
    #: than mistaken for a parsing failure.
    blank_labels: tuple[str, ...] = ()

    def get(self, field: str) -> float | None:
        return getattr(self, field)


def _squash(row: list[Word]) -> str:
    return "".join(word["text"] for word in row).replace(" ", "")


def _amount(text: str) -> float | None:
    """"$1,302,416,000" -> 1302416000.0; a blank or unreadable value -> None.

    Parenthesised figures are read as negative, the accounting convention
    these reports use elsewhere for a credit.
    """
    cleaned = text.strip().replace("$", "").replace(",", "")
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1]
    if not cleaned:
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return -value if negative else value


def parse_other_reporting_items(
    pdf_path: Path, rows: list[list[Word]] | None = None
) -> OtherReportingItems:
    """Reads the Other Reporting Items page out of one report.

    `rows` is the shared document read from pfa.extraction.word_rows; it is
    optional only so the parser can be run against a PDF on its own.
    """
    if rows is None:
        rows = read_rows(pdf_path)

    start = next(
        (index for index, row in enumerate(rows) if _squash(row) == _SECTION_HEADING),
        None,
    )
    if start is None:
        raise ExtractionError(
            f"{Path(pdf_path).name} has no 'Other Reporting Items' page."
        )

    items = OtherReportingItems()
    blank: list[str] = []
    seen: set[str] = set()
    for row in rows[start + 1 : start + 1 + _SECTION_ROWS]:
        label, separator, value = _squash(row).partition(":")
        if not separator:
            continue
        field = _FIELDS.get(label)
        if field is None or field in seen:
            continue
        seen.add(field)
        amount = _amount(value)
        setattr(items, field, amount)
        if amount is None:
            blank.append(label)

    if not seen:
        raise ExtractionError(
            f"{Path(pdf_path).name}: the 'Other Reporting Items' page carried none of "
            "the expected AUP / Other Data Category lines."
        )
    items.blank_labels = tuple(blank)
    return items
