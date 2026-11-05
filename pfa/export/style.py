"""The Collegiate Consulting pro forma house style, as a set of row kinds.

Read out of the client's own pro formas (the FY26 ASUN pair and the Lenoir-
Rhyne SoCon expense book) rather than designed here. Every pro forma CC has
produced uses the same five-band scheme, and it is the same scheme the FRS
benchmarking workbooks use - black for a title, gold for a header or a
total, nothing for the figures in between:

    TITLE    black fill, white bold, centred, merged across the table
             ("Sports Operations", "Pro Forma Summary - SoCon Expenses")
    HEADER   gold FFC000 fill, black bold, centred, medium box
             (the row carrying the year labels)
    GROUP    black fill, white bold - a section break inside the table
             ("Men's Sports", "Athletic Training", "Baseball")
    DATA     no fill, thin box, accounting format, right-aligned figures
    TOTAL    gold FFC000 fill, black bold, medium box
             ("Total Amount", "Grand Total", "Total Salary + Benefits")

Labels are title case throughout. The hand-built books shout some of their
total rows in capitals and not others; capitals on a gold band are shouting
twice, and the band is already doing the work.

**Why FFC000 and not the website's yellow.** The workbooks' gold came from
the client's own Excel reference, not from collegiateconsulting.com, whose
headings are #FFE739/#FFD00F. The two are set independently and deliberately
left that way: a reader comparing a pro forma against a benchmarking
workbook on the same desk should see one colour, and FFC000 is the one
already in every workbook CC has sent.

**Borders.** Horizontally: medium under a band, thin between figure rows.
Vertically: medium on the table's outer edges and on every block boundary -
a block being the stub, the Scholar page's pair of NCAA-limit columns, and
each fiscal year's sub-columns - thin only between the columns inside a
block.

The vertical rule is what makes a wide sheet legible. A Scholar year is
three columns and a Salaries sheet is 140 rows; with one weight of rule
throughout, the first reads as eighteen unrelated columns and the second as
a drift of rows. Drawing the block boundaries heavier groups the columns
into years without the reader going back to the header band. The hand-built
pro formas do this inconsistently - some sheets lose the right edge partway
down - and here it comes from one place, so it cannot.
"""

from __future__ import annotations

from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from pfa.export import formats

# --- The palette -------------------------------------------------------

#: The gold on every header and total row. Excel's standard accent orange.
GOLD = "FFFFC000"
BLACK = "FF000000"
WHITE = "FFFFFFFF"
#: A soft grey for the assumption cells above a table - an input the reader
#: may change, marked as such without competing with the gold bands.
INPUT_GREY = "FFF2F2F2"

TITLE_FILL = PatternFill(fill_type="solid", start_color=BLACK, end_color=BLACK)
HEADER_FILL = PatternFill(fill_type="solid", start_color=GOLD, end_color=GOLD)
GROUP_FILL = TITLE_FILL
TOTAL_FILL = HEADER_FILL
INPUT_FILL = PatternFill(fill_type="solid", start_color=INPUT_GREY, end_color=INPUT_GREY)

_THIN = Side(style="thin")
_MEDIUM = Side(style="medium")

#: Calibri 11 is what every one of the client's pro formas is set in. The
#: website's Montserrat is a web face and deliberately not used here - the
#: workbooks are opened in Excel on machines that do not have it, where it
#: silently falls back and the column widths stop fitting their contents.
FONT_NAME = "Calibri"
FONT_SIZE = 11


def _font(*, bold: bool = False, color: str = BLACK, italic: bool = False) -> Font:
    return Font(name=FONT_NAME, size=FONT_SIZE, bold=bold, italic=italic, color=color)


TITLE_FONT = _font(bold=True, color=WHITE)
HEADER_FONT = _font(bold=True)
GROUP_FONT = _font(bold=True, color=WHITE)
DATA_FONT = _font()
TOTAL_FONT = _font(bold=True)


def _border(*, top: Side | None, bottom: Side | None, left: Side, right: Side) -> Border:
    return Border(top=top, bottom=bottom, left=left, right=right)


# --- Applying a band ---------------------------------------------------


def _edges(
    column: int,
    first_col: int,
    last_col: int,
    heavy_cols: frozenset[int] = frozenset(),
) -> tuple[Side, Side]:
    """The left and right side for one cell of a banded row.

    Medium on the table's outer edges and on every *block* boundary, thin
    between the columns inside a block. A block is a group of columns that
    belong together: the stub, the Scholar page's two NCAA-limit columns,
    and each fiscal year's sub-columns. `heavy_cols` names the column that
    opens each one.

    This is what makes a wide sheet readable. On the Scholar page a year is
    three columns - equivalencies, budget, award - and with one weight of
    rule throughout, eighteen columns of figures read as eighteen unrelated
    columns. With the year boundaries drawn heavier, the eye groups them
    into six years without having to read the header band again. On a page
    with one column per year every column is its own block, so every rule
    is medium, which is the same statement.
    """
    left = _MEDIUM if column == first_col or column in heavy_cols else _THIN
    right = _MEDIUM if column == last_col or column + 1 in heavy_cols else _THIN
    return left, right


def band(
    sheet,
    row: int,
    *,
    first_col: int,
    last_col: int,
    fill: PatternFill,
    font: Font,
    align: Alignment = formats.CENTER,
    heavy_top: bool = True,
    heavy_bottom: bool = True,
    heavy_cols: frozenset[int] = frozenset(),
) -> None:
    """Paints one row as a band: fill, font and box, every cell across the
    table whether or not it holds a value.

    Painting the empty cells too is the point - a gold total row that stops
    at the last column holding a figure leaves a white notch where a sport
    reported nothing, which reads as a hole in the table rather than as a
    figure the report didn't carry.
    """
    for column in range(first_col, last_col + 1):
        cell = sheet.cell(row=row, column=column)
        left, right = _edges(column, first_col, last_col, heavy_cols)
        cell.fill = fill
        cell.font = font
        cell.alignment = align
        cell.border = _border(
            top=_MEDIUM if heavy_top else _THIN,
            bottom=_MEDIUM if heavy_bottom else _THIN,
            left=left,
            right=right,
        )


def title_row(
    sheet, row: int, text: str, *, first_col: int, last_col: int,
    heavy_cols: frozenset[int] = frozenset(),
) -> None:
    """The black title band across the top of a table, merged."""
    sheet.cell(row=row, column=first_col).value = text
    band(
        sheet, row, first_col=first_col, last_col=last_col,
        fill=TITLE_FILL, font=TITLE_FONT, heavy_cols=heavy_cols,
    )
    if last_col > first_col:
        sheet.merge_cells(
            start_row=row, start_column=first_col, end_row=row, end_column=last_col
        )


def header_row(
    sheet, row: int, labels: dict[int, str], *, first_col: int, last_col: int,
    heavy_cols: frozenset[int] = frozenset(),
) -> None:
    """The gold header band, `labels` keyed by column index."""
    band(
        sheet, row, first_col=first_col, last_col=last_col,
        fill=HEADER_FILL, font=HEADER_FONT, heavy_cols=heavy_cols,
    )
    for column, label in labels.items():
        sheet.cell(row=row, column=column).value = label


def group_row(
    sheet, row: int, text: str, *, first_col: int, last_col: int,
    heavy_cols: frozenset[int] = frozenset(),
) -> None:
    """A black section break inside a table - "Men's Sports", a department,
    a sport heading above its coaching positions.

    Left-aligned rather than centred: these are labels for the rows beneath
    them, and a centred label floating over the middle of an eight-column
    table does not read as belonging to the column it labels. The client's
    own files centre some and left-align others; this picks one.
    """
    band(
        sheet, row, first_col=first_col, last_col=last_col,
        fill=GROUP_FILL, font=GROUP_FONT, align=formats.LEFT,
        heavy_top=False, heavy_bottom=False, heavy_cols=heavy_cols,
    )
    sheet.cell(row=row, column=first_col).value = text


def total_row(
    sheet, row: int, label: str, *, first_col: int, last_col: int,
    label_align: Alignment | None = None,
    heavy_cols: frozenset[int] = frozenset(),
) -> None:
    """A gold total band. The label is right-aligned into its own column,
    pointing at the figures it totals.
    """
    band(
        sheet, row, first_col=first_col, last_col=last_col,
        fill=TOTAL_FILL, font=TOTAL_FONT, heavy_cols=heavy_cols,
    )
    cell = sheet.cell(row=row, column=first_col)
    cell.value = label
    cell.alignment = label_align or formats.RIGHT


def data_row(
    sheet,
    row: int,
    *,
    first_col: int,
    last_col: int,
    label_col: int | None = None,
    number_format: str = formats.ACCOUNTING,
    heavy_cols: frozenset[int] = frozenset(),
) -> None:
    """A plain figure row: thin box, accounting format, figures right.

    The label column keeps the General format and left alignment; everything
    to its right is a figure.
    """
    label_col = first_col if label_col is None else label_col
    for column in range(first_col, last_col + 1):
        cell = sheet.cell(row=row, column=column)
        left, right = _edges(column, first_col, last_col, heavy_cols)
        cell.font = DATA_FONT
        cell.border = _border(top=_THIN, bottom=_THIN, left=left, right=right)
        if column <= label_col:
            cell.alignment = formats.LEFT
        else:
            cell.number_format = number_format
            cell.alignment = formats.RIGHT


def assumption_cell(sheet, coordinate: str, value: float, *, rate: bool = True) -> None:
    """One escalation assumption, above the table that uses it.

    Marked with the input fill and a thin box so a reader can tell at a
    glance which cells are theirs to change. Every projected figure in these
    workbooks is a formula against one of these, which is the whole reason
    the deliverable is a workbook and not a PDF: changing 3% to 4% here moves
    every year of every line that uses it.
    """
    cell = sheet[coordinate]
    cell.value = value
    cell.fill = INPUT_FILL
    cell.font = _font(bold=True)
    cell.alignment = formats.CENTER
    cell.border = Border(top=_THIN, bottom=_THIN, left=_THIN, right=_THIN)
    cell.number_format = formats.percent_format(value) if rate else formats.ACCOUNTING


# There is deliberately no footnote kind and no variance kind here any
# more. Both used to be written under the tables - grey italic provenance
# notes, and a red "Variance vs. report" row carrying the base year's
# difference from the filing.
#
# They are gone because the workbook is a client deliverable rather than a
# working paper, and a board packet that argues with itself in red in the
# margin reads as unfinished. The check itself is not gone: it runs in
# pfa/builders/checks.py on every build and surfaces in the app, where the
# analyst who can act on it is the only person who sees it.


# --- Sheet furniture ---------------------------------------------------


def set_widths(sheet, widths: dict[int, float]) -> None:
    """Column widths by index."""
    for index, width in widths.items():
        sheet.column_dimensions[get_column_letter(index)].width = width


def finish(sheet, *, freeze: str | None = None, last_row: int = 1, last_col: int = 1) -> None:
    """The last pass over a finished sheet.

    Freezes the header so the year columns stay visible on a long Salaries
    sheet, turns the gridlines off so the table's own borders are what the
    reader sees, and sets a landscape fit-to-width print area - these get
    printed and dropped into board packets, and a pro forma that spills its
    last two years onto a second sheet of paper is the version the client
    remembers.
    """
    if freeze:
        sheet.freeze_panes = freeze
    sheet.sheet_view.showGridLines = False
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.print_area = (
        f"A1:{get_column_letter(max(last_col, 1))}{max(last_row, 1)}"
    )
