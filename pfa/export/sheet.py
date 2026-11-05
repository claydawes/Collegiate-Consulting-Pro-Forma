"""The pro forma table writer - one place that knows how a CC pro forma
page is laid out, so eleven sheets across two workbooks cannot drift apart.

**The grid.** Every page in the client's books uses the same one, and this
reproduces it:

    column A        a narrow gutter, so the table's left border isn't
                    jammed against the window edge
    column B        the stub - line items, sports, positions
    columns C...    one per fiscal year
    one gap column  left empty
    final column    "$ Change": the last projected year less the base year

    row 1           the escalation palette, in unlabelled input cells
    row 2           the black title band
    row 3           the gold header band carrying the year labels
    row 4 onward    the body: group bands, figure rows, total bands

**Where the formulas come from.** A year that is reported or entered gets a
literal. Every other year gets `=ROUND(prior*(1+$H$1),0)` against the
palette cell for that line's tier. Two consequences are the point of the
whole design: the workbook recalculates when the client changes an
assumption, and the assumption is visible on the page rather than buried in
a thousand hand-typed `*1.03`s - which is what the hand-built books are, and
why a rate change in one of them takes an afternoon and misses a row.

The ROUND is not cosmetic. An eight-year chain of `*1.035` accumulates
fractions of a cent that the accounting format hides, and the client's own
files show the symptom: a Direct Institutional Support figure stored as
12,075,012.305916252 under a column printed to the dollar. Rounding each
step keeps the stored value and the printed one the same number, so a
column adds up to its own total.
"""

from __future__ import annotations

from dataclasses import dataclass

from openpyxl.utils import get_column_letter

from pfa.export import formats, style
from pfa.model.proforma import Horizon, RateTier, Scenario

#: The gutter, the stub, and where the years start.
GUTTER_COL = 1
STUB_COL = 2
FIRST_YEAR_COL = 3

PALETTE_ROW = 1
TITLE_ROW = 2
HEADER_ROW = 3
FIRST_BODY_ROW = 4


@dataclass(frozen=True)
class TotalRef:
    """Where a page's total sits, as another page can address it.

    A summary page references detail pages whose geometry is not its own -
    the Scholar page's total lands in the second of three sub-columns per
    year, everything else's in the only one. Carrying the resolved column
    letters here rather than recomputing the source page's layout at the
    reference site is what keeps that arithmetic in one place: the page that
    knows its own shape says where its total is, and the summary just points
    at it.
    """

    sheet_name: str
    row: int
    #: One column letter per year, in horizon order.
    columns: tuple[str, ...]

    def reference(self, year_index: int) -> str:
        quoted = "'" + self.sheet_name.replace("'", "''") + "'"
        return f"={quoted}!{self.columns[year_index]}{self.row}"


@dataclass(frozen=True)
class Layout:
    """Where everything sits on one finished page."""

    horizon: Horizon
    #: How many columns each year occupies. One, except on the Scholar page,
    #: where a year is EQUIV / BUDGET / AVG AWARD.
    columns_per_year: int = 1
    #: Column index of each tier's palette cell, by tier key.
    palette_columns: dict[str, int] = None  # type: ignore[assignment]
    #: Fixed reference columns between the stub and the first year - the
    #: Scholar page's two NCAA-limit columns, which belong to no year.
    reference_headers: tuple[str, ...] = ()
    #: How many rows the header band occupies. Two on a page whose years are
    #: split into sub-columns, where the year label spans its sub-columns on
    #: the first row and they are named on the second.
    header_depth: int = 1

    @property
    def year_count(self) -> int:
        return len(self.horizon.years)

    @property
    def first_year_col(self) -> int:
        return FIRST_YEAR_COL + len(self.reference_headers)

    @property
    def reference_cols(self) -> tuple[int, ...]:
        return tuple(
            FIRST_YEAR_COL + offset for offset in range(len(self.reference_headers))
        )

    @property
    def first_body_row(self) -> int:
        return HEADER_ROW + self.header_depth

    def year_col(self, index: int, offset: int = 0) -> int:
        """The column index for year `index`, sub-column `offset`."""
        return self.first_year_col + index * self.columns_per_year + offset

    def year_letter(self, index: int, offset: int = 0) -> str:
        return get_column_letter(self.year_col(index, offset))

    @property
    def last_year_col(self) -> int:
        return self.year_col(self.year_count - 1, self.columns_per_year - 1)

    @property
    def gap_col(self) -> int:
        return self.last_year_col + 1

    @property
    def block_columns(self) -> frozenset[int]:
        """The column that opens each block of the table.

        A block is a set of columns that belong together and should be
        fenced off from its neighbours by a heavier rule: the stub, the
        reference columns as a pair, and each fiscal year with whatever
        sub-columns it has. On a page with one column per year every column
        opens its own block, so every rule is medium - the same statement
        made of a simpler table.
        """
        columns = {STUB_COL}
        if self.reference_headers:
            columns.add(self.reference_cols[0])
        columns.update(self.year_col(index) for index in range(self.year_count))
        return frozenset(columns)

    @property
    def diff_col(self) -> int:
        return self.last_year_col + 2

    @property
    def table_last_col(self) -> int:
        """The right edge of the banded table - the years, not the Difference
        column, which sits outside the box with a gap before it so it reads
        as a commentary on the table rather than another year of it.
        """
        return self.last_year_col

    def palette_cell(self, tier_key: str) -> str:
        """The absolute reference an escalation formula points at."""
        column = self.palette_columns[tier_key]
        return f"${get_column_letter(column)}${PALETTE_ROW}"


def build_layout(
    scenario: Scenario,
    *,
    columns_per_year: int = 1,
    reference_headers: tuple[str, ...] = (),
    header_depth: int = 1,
) -> Layout:
    """Places the escalation palette and returns the page's layout.

    The palette is laid out left to right from the first year column, one
    cell per tier, which puts it directly above the years it drives.
    """
    first = FIRST_YEAR_COL + len(reference_headers)
    columns = {
        tier.key: first + index for index, tier in enumerate(scenario.tiers)
    }
    return Layout(
        horizon=scenario.horizon,
        columns_per_year=columns_per_year,
        palette_columns=columns,
        reference_headers=reference_headers,
        header_depth=header_depth,
    )


# --- Page furniture ----------------------------------------------------


def write_palette(sheet, scenario: Scenario, layout: Layout) -> None:
    """Row 1: the escalation assumptions, marked as inputs.

    Six grey cells above the table, one per tier, descending from the most
    aggressive rate to flat. Each carries a comment naming its tier, so
    hovering over one says which lines it drives.

    Deliberately unlabelled. A stub reading "Escalation assumptions →" was
    the obvious thing and it is wrong here: it is the only sentence of
    instruction on an otherwise finished page, and it puts the loudest text
    on the sheet above the title band rather than in it. Six descending
    percentages in input-grey, sitting directly over the years they move,
    say the same thing without saying it.
    """
    for tier in scenario.tiers:
        column = layout.palette_columns[tier.key]
        coordinate = f"{get_column_letter(column)}{PALETTE_ROW}"
        style.assumption_cell(sheet, coordinate, tier.rate)
        sheet[coordinate].comment = _tier_comment(tier)


def _tier_comment(tier: RateTier):
    from openpyxl.comments import Comment

    return Comment(
        f"{tier.label}\n\nEvery line on this tier escalates at this rate. "
        f"Change it here and the whole projection follows.",
        "Collegiate Consulting",
        width=260,
        height=90,
    )


def write_title(sheet, layout: Layout, text: str) -> None:
    style.title_row(
        sheet, TITLE_ROW, text, first_col=STUB_COL, last_col=layout.table_last_col,
        heavy_cols=layout.block_columns,
    )


def write_year_header(
    sheet, layout: Layout, stub_header: str, *, sub_labels: tuple[str, ...] = ()
) -> None:
    """The gold header band: the stub header, the reference columns, and the
    years.

    On a page whose years are split into sub-columns the band is two rows
    deep - the year label merged across its sub-columns above, and
    `sub_labels` naming them below. The stub and reference headers merge
    vertically through both, so the band reads as one box.
    """
    labels = {STUB_COL: stub_header}
    for column, header in zip(layout.reference_cols, layout.reference_headers):
        labels[column] = header
    for index, year in enumerate(layout.horizon.years):
        labels[layout.year_col(index)] = year.label

    style.header_row(
        sheet, HEADER_ROW, labels, first_col=STUB_COL, last_col=layout.table_last_col,
        heavy_cols=layout.block_columns,
    )
    if layout.header_depth > 1:
        second = HEADER_ROW + 1
        style.header_row(
            sheet, second, {}, first_col=STUB_COL, last_col=layout.table_last_col,
            heavy_cols=layout.block_columns,
        )
        for index in range(layout.year_count):
            for offset, text in enumerate(sub_labels):
                sheet.cell(row=second, column=layout.year_col(index, offset)).value = text
            if layout.columns_per_year > 1:
                sheet.merge_cells(
                    start_row=HEADER_ROW,
                    start_column=layout.year_col(index),
                    end_row=HEADER_ROW,
                    end_column=layout.year_col(index, layout.columns_per_year - 1),
                )
        for column in (STUB_COL, *layout.reference_cols):
            sheet.merge_cells(
                start_row=HEADER_ROW, start_column=column, end_row=second, end_column=column
            )
    write_difference_header(sheet, layout)


def write_difference_header(sheet, layout: Layout, row: int = HEADER_ROW) -> None:
    """The Difference column's own header, outside the table's box.

    `row` so a sheet carrying two tables - the Salaries page - labels each
    one's Difference column over that table's own header band. Defaulting it
    to the page header meant the second table's column ran unlabelled while
    its figures were populated, which is the sort of thing a reader reads as
    a stray column of numbers.

    Named for what it is rather than "Difference": a reader three pages in
    has lost track of which two years are being subtracted, and the client's
    own books never say.
    """
    base_index, last_index = layout.horizon.comparison_pair
    base = layout.horizon.years[base_index].label
    last = layout.horizon.years[last_index].label
    cell = sheet.cell(row=row, column=layout.diff_col)
    cell.value = f"$ Change\n{base} to {last}"
    cell.fill = style.HEADER_FILL
    cell.font = style.HEADER_FONT
    cell.alignment = formats.CENTER_WRAP
    sheet.row_dimensions[row].height = 30


def write_widths(
    sheet,
    layout: Layout,
    *,
    stub_width: float = 34.0,
    year_widths: tuple[float, ...] = (14.5,),
    reference_width: float = 11.5,
) -> None:
    """Column widths. `year_widths` gives one width per sub-column, so the
    Scholar page's narrow EQUIV column and wide BUDGET column each get their
    own rather than sharing an average that fits neither.
    """
    widths = {GUTTER_COL: 2.6, STUB_COL: stub_width}
    for column in layout.reference_cols:
        widths[column] = reference_width
    for index in range(layout.year_count):
        for offset in range(layout.columns_per_year):
            widths[layout.year_col(index, offset)] = year_widths[
                min(offset, len(year_widths) - 1)
            ]
    widths[layout.gap_col] = 2.6
    widths[layout.diff_col] = 20.0
    style.set_widths(sheet, widths)
    sheet.row_dimensions[HEADER_ROW].height = 30


# --- Body rows ---------------------------------------------------------


def write_group(sheet, row: int, layout: Layout, text: str) -> None:
    style.group_row(
        sheet, row, text, first_col=STUB_COL, last_col=layout.table_last_col,
        heavy_cols=layout.block_columns,
    )


def write_figure_row(
    sheet,
    row: int,
    layout: Layout,
    *,
    label: str,
    values: dict[str, float],
    tier: RateTier,
    number_format: str = formats.ACCOUNTING,
    starts_at_zero: bool = False,
    carry_forward: bool = True,
    column_offset: int = 0,
) -> None:
    """One line of the table across the whole horizon.

    A year present in `values` is written as a literal. A year that isn't is
    escalated off the column to its left against this line's tier cell.

    `starts_at_zero` writes a hard 0 into every reported year instead of
    leaving them blank - for a cost that by construction did not exist yet,
    like a conference entry fee. The distinction is deliberate: a blank cell
    says the report didn't carry this figure, a 0 says the model asserts
    there was none, and a reader has to be able to tell which claim is being
    made.

    `carry_forward=False` writes 0 into every year not explicitly given,
    instead of escalating from the left - for a cost paid once. Without it a
    one-time fee entered in a single year propagates through the whole
    projection, and the pro forma shows a $1,975,000 reclassification fee
    being paid six times over.
    """
    style.data_row(
        sheet, row, first_col=STUB_COL, last_col=layout.table_last_col, label_col=STUB_COL,
        number_format=number_format, heavy_cols=layout.block_columns,
    )
    sheet.cell(row=row, column=STUB_COL).value = label

    for index, year in enumerate(layout.horizon.years):
        cell = sheet.cell(row=row, column=layout.year_col(index, column_offset))
        value = values.get(year.label)
        if value is not None:
            cell.value = value
        elif not carry_forward:
            # A one-time cost in a year it isn't paid. A hard 0 rather than a
            # blank: the model is asserting there is no payment here, which
            # is a different claim from having no figure.
            cell.value = 0
        elif year.reported or index == 0:
            # A reported year the filing had no figure for, or the leftmost
            # column, which has nothing to its left to escalate from. Zero
            # only where the line says the zero is meant; otherwise blank, so
            # the sheet says "not reported" rather than "none".
            cell.value = 0 if starts_at_zero else None
        else:
            # Everything else escalates off the column to its left - the
            # projected years, and an entered year the operator has not filled
            # in yet.
            #
            # That last case is the one worth stating. An entered year is the
            # operator's to set, so the obvious thing is to leave it blank for
            # them. Doing that breaks the model: the year after it escalates
            # off an empty cell, Excel reads the blank as zero, and the whole
            # projection collapses to nothing - silently, because a column of
            # accounting dashes looks like a formatting choice rather than a
            # broken chain. Seeding it with the escalated figure keeps the
            # chain intact and still shows through as a placeholder, since
            # an entered year that nobody has touched reads as "last year plus
            # inflation", which is exactly what it is.
            prior = layout.year_letter(index - 1, column_offset)
            cell.value = formats.rounded(
                f"{prior}{row}*(1+{layout.palette_cell(tier.key)})", number_format
            )

    write_difference(sheet, row, layout, number_format=number_format)


def write_difference(
    sheet, row: int, layout: Layout, *, number_format: str = formats.ACCOUNTING,
    column_offset: int = 0,
) -> None:
    """The Difference cell: last projected year less the base year."""
    base_index, last_index = layout.horizon.comparison_pair
    base = layout.year_letter(base_index, column_offset)
    last = layout.year_letter(last_index, column_offset)
    cell = sheet.cell(row=row, column=layout.diff_col)
    cell.value = f"={last}{row}-{base}{row}"
    cell.number_format = number_format
    cell.alignment = formats.RIGHT
    cell.font = style.DATA_FONT


def write_total(
    sheet,
    row: int,
    layout: Layout,
    *,
    label: str,
    summed_rows: list[int],
    number_format: str = formats.ACCOUNTING,
    column_offset: int = 0,
) -> None:
    """A gold total band summing the given body rows.

    Summed by an explicit list of rows rather than by a range, because the
    body is not contiguous - group bands sit between the figure rows, and a
    SUM over the range would add the group bands' empty cells today and
    whatever someone types into one tomorrow.

    The client's own books use ranges, and one of them is wrong because of
    it: the FY24 expense book's coaching total sums C22:C37 on a sheet whose
    last coaching row is 39, quietly dropping a $20,500 assistant.
    """
    style.total_row(
        sheet, row, label, first_col=STUB_COL, last_col=layout.table_last_col,
        heavy_cols=layout.block_columns,
    )
    for index in range(layout.year_count):
        column = layout.year_letter(index, column_offset)
        cell = sheet.cell(row=row, column=layout.year_col(index, column_offset))
        cell.value = formats.rounded(sum_expression(column, summed_rows), number_format)
        cell.number_format = number_format
        cell.alignment = formats.RIGHT
    write_difference(sheet, row, layout, number_format=number_format, column_offset=column_offset)
    sheet.cell(row=row, column=layout.diff_col).font = style.TOTAL_FONT


def write_reference_total(
    sheet,
    row: int,
    layout: Layout,
    *,
    label: str,
    references: list[str],
    number_format: str = formats.ACCOUNTING,
) -> None:
    """A gold total band that adds up other cells of the same column - used
    where a page's total is the sum of its own subtotals (Men's Total plus
    Women's Total) rather than of its figure rows.
    """
    style.total_row(
        sheet, row, label, first_col=STUB_COL, last_col=layout.table_last_col,
        heavy_cols=layout.block_columns,
    )
    for index in range(layout.year_count):
        column = layout.year_letter(index)
        body = "+".join(f"{column}{reference}" for reference in references)
        cell = sheet.cell(row=row, column=layout.year_col(index))
        cell.value = formats.rounded(body, number_format)
        cell.number_format = number_format
        cell.alignment = formats.RIGHT
    write_difference(sheet, row, layout, number_format=number_format)
    sheet.cell(row=row, column=layout.diff_col).font = style.TOTAL_FONT


def sum_expression(column: str, rows: list[int]) -> str:
    """SUM over an explicit row list, collapsed into ranges where the rows
    happen to be adjacent - so a 40-row Salaries total reads as
    SUM(C5:C44) rather than forty terms, while a body broken up by group
    bands still only sums its figure rows.
    """
    if not rows:
        return "0"
    ordered = sorted(rows)
    spans: list[tuple[int, int]] = []
    start = previous = ordered[0]
    for row in ordered[1:]:
        if row == previous + 1:
            previous = row
            continue
        spans.append((start, previous))
        start = previous = row
    spans.append((start, previous))
    terms = [
        f"{column}{first}" if first == last else f"{column}{first}:{column}{last}"
        for first, last in spans
    ]
    return f"SUM({','.join(terms)})"


def total_ref(sheet, layout: Layout, row: int, *, column_offset: int = 0) -> TotalRef:
    """A TotalRef naming this page's total row and the columns it sits in."""
    return TotalRef(
        sheet_name=sheet.title,
        row=row,
        columns=tuple(
            layout.year_letter(index, column_offset) for index in range(layout.year_count)
        ),
    )


def write_reference_row(
    sheet, row: int, layout: Layout, *, label: str, source: TotalRef,
    number_format: str = formats.ACCOUNTING,
) -> None:
    """One summary row: the label, then a live reference per year.

    Referenced rather than copied so an analyst who revises a detail sheet
    sees the summary move. A copied figure is right the day it is written and
    wrong by the first revision - and with these documents the revision
    always happens, because they get negotiated.
    """
    style.data_row(
        sheet, row, first_col=STUB_COL, last_col=layout.table_last_col, label_col=STUB_COL,
        number_format=number_format, heavy_cols=layout.block_columns,
    )
    sheet.cell(row=row, column=STUB_COL).value = label
    for index in range(layout.year_count):
        cell = sheet.cell(row=row, column=layout.year_col(index))
        cell.value = source.reference(index)
        cell.number_format = number_format
        cell.alignment = formats.RIGHT
    write_difference(sheet, row, layout, number_format=number_format)


def finish_page(sheet, layout: Layout, last_row: int) -> None:
    """The last pass over a finished page: gridlines off, print setup, and
    the print area out to the Change column.

    No freeze panes. The obvious thing is to freeze the header band and the
    stub so a 140-row Salaries page keeps its labels, and these sheets
    deliberately do not: the workbook is read on paper and on a screen
    share as often as it is scrolled, and a frozen pane splits the printed
    preview and the shared window in a way that reads as a broken sheet to
    everyone who did not set it. The table is short enough per block that
    the header stays in view where it matters.
    """
    style.finish(
        sheet,
        freeze=None,
        last_row=last_row,
        last_col=layout.diff_col,
    )
