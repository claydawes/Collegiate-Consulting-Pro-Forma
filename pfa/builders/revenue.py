"""The revenue pro forma: six sheets, drawn rather than filled.

    Totals                  one row per detail page, every figure a live
                            reference back to the page it summarises
    Institutional Support   student fees, direct, indirect, state
    Ticket Revenue          one row per sport
    Game Guarantee          one row per sport
    Program Revenue         sponsorship, contributions, in-kind, media,
                            camps, endowment, other
    NCAA                    NCAA and conference distributions

**Drawn, not filled from a template.** The NTR calculator fills a client
template, and that is right there: its sheets are a fixed ten-row block
repeated, so a template is the specification. A pro forma is not that shape.
Its Ticket Revenue page has as many rows as the institution has ticketed
sports, its horizon runs five to eight years wide, and its Salaries page
runs from forty rows to a hundred and seventy. A template for that would
need resizing in both directions on every page, and the resize logic would
end up longer and more fragile than the drawing code it replaced. The house
style lives in pfa/export/style.py instead, which is the same decision the
benchmarking platform made for its colour scheme.

**Every summary figure is a reference, never a copy.** The Totals page reads
`='Ticket Revenue'!J13`, so an analyst who changes a sport's ticket line
sees the summary move. A copied figure would be right the day it was written
and wrong by the first revision - and the revision always happens, because
these documents are negotiated.
"""

from __future__ import annotations

from pathlib import Path

import openpyxl

from pfa.builders import checks
from pfa.export import sheet as sh
from pfa.model import schema
from pfa.model.proforma import Scenario

GENDER_HEADINGS = {"male": "Men's Sports", "female": "Women's Sports"}


def build_revenue_workbook(scenario: Scenario, output: Path) -> list[str]:
    """Writes the revenue pro forma to `output`, returning operator notes."""
    warnings: list[str] = []
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)

    # Detail pages first: the Totals page references their total rows, so it
    # cannot be written until they exist.
    pages: dict[str, sh.TotalRef] = {}
    lines: dict[str, sh.TotalRef] = {}
    for page in schema.REVENUE_PAGES:
        writer = _write_per_sport_page if page.per_sport else _write_line_page
        total, line_refs, notes = writer(workbook, scenario, page)
        pages[page.key] = total
        lines.update(line_refs)
        warnings.extend(notes)

    _write_totals_page(workbook, scenario, pages, lines)
    workbook.move_sheet("Totals", offset=-len(workbook.sheetnames) + 1)

    _set_properties(workbook, scenario, kind="Revenue")
    workbook.save(output)
    return warnings


# --- A fixed-line page -------------------------------------------------


def _write_line_page(workbook, scenario: Scenario, page: schema.Page):
    """One of the pages whose rows are named line items."""
    warnings: list[str] = []
    sheet = workbook.create_sheet(page.sheet_name)
    layout = sh.build_layout(scenario)

    sh.write_widths(sheet, layout, stub_width=40.0)
    sh.write_palette(sheet, scenario, layout)
    sh.write_title(sheet, layout, f"{page.title} — {scenario.institution}")
    sh.write_year_header(sheet, layout, page.stub_header)

    row = layout.first_body_row
    figure_rows: list[int] = []
    line_refs: dict[str, sh.TotalRef] = {}
    for line in page.lines:
        values = scenario.line_figures.get(f"{page.key}:{line.key}", {})
        if not values and not line.starts_at_zero:
            # A line the report said nothing about and the analyst didn't
            # enter. Kept on the page rather than dropped: the taxonomy is
            # the deliverable, and a page that silently loses its Media
            # Rights row at one institution and keeps it at the next is not
            # a standardized output. It writes as blanks and escalates off
            # them to nothing, which is the honest rendering.
            warnings.append(
                f"{page.sheet_name}: no figure for “{line.label}” — the row is on the page, blank."
            )
        sh.write_figure_row(
            sheet, row, layout,
            label=line.label,
            values=values,
            tier=scenario.tier_for(page.key, line.key),
            starts_at_zero=line.starts_at_zero,
            carry_forward=line.recurring,
        )
        figure_rows.append(row)
        line_refs[f"{page.key}:{line.key}"] = sh.total_ref(sheet, layout, row)
        row += 1

    total_row = row
    sh.write_total(sheet, total_row, layout, label=page.total_label, summed_rows=figure_rows)

    sh.finish_page(sheet, layout, total_row)
    return sh.total_ref(sheet, layout, total_row), line_refs, warnings


# --- A per-sport page --------------------------------------------------


def _write_per_sport_page(workbook, scenario: Scenario, page: schema.Page):
    """Ticket Revenue and Game Guarantee: a row per sport, under Men's and
    Women's bands.

    Only sports the scenario has a figure for appear. That is the one place
    these pages depart from the fixed taxonomy, and deliberately: a Ticket
    Revenue page listing nineteen sports to carry three non-zero rows buries
    the three. A sport with no ticket revenue has nothing to project.
    """
    warnings: list[str] = []
    sheet = workbook.create_sheet(page.sheet_name)
    layout = sh.build_layout(scenario)
    attribute = "ticket" if page.key == "ticket_revenue" else "guarantee"

    sh.write_widths(sheet, layout, stub_width=28.0)
    sh.write_palette(sheet, scenario, layout)
    sh.write_title(sheet, layout, f"{page.title} — {scenario.institution}")
    sh.write_year_header(sheet, layout, page.stub_header)

    row = layout.first_body_row
    figure_rows: list[int] = []
    added: list[str] = []
    for gender, heading in GENDER_HEADINGS.items():
        plans = [
            plan for plan in scenario.sports_for(gender)
            if getattr(plan, attribute)
        ]
        if not plans:
            continue
        sh.write_group(sheet, row, layout, heading)
        row += 1
        for plan in plans:
            sh.write_figure_row(
                sheet, row, layout,
                label=plan.sport,
                values=getattr(plan, attribute),
                tier=scenario.tier_for(page.key, plan.sport),
                starts_at_zero=plan.is_addition,
            )
            figure_rows.append(row)
            if plan.is_addition:
                added.append(plan.sport)
            row += 1

    if not figure_rows:
        warnings.append(
            f"{page.sheet_name}: no sport carries a figure — the page has a header and a "
            "zero total. Enter the sports that generate this revenue, or expect a reader "
            "to ask why the page is there."
        )

    total_row = row
    sh.write_total(sheet, total_row, layout, label=page.total_label, summed_rows=figure_rows)

    if added:
        warnings.append(
            f"{page.sheet_name}: not sponsored in the reported years — "
            f"{', '.join(sorted(set(added)))}. Those rows start at zero by "
            "construction, not by omission."
        )
    # The split across sports is the analyst's: FRS reports this revenue at
    # institution level only. So the check here is not of the mapping but of
    # the allocation - whether the rows they typed still add up to what the
    # institution filed.
    warnings.extend(checks.tie_out(
        scenario, page.key,
        page=page.sheet_name,
        source=f"FRS category {'1' if page.key == 'ticket_revenue' else '7'}",
        computed=_per_sport_computed(scenario, attribute),
    ))

    sh.finish_page(sheet, layout, total_row)
    return sh.total_ref(sheet, layout, total_row), {}, warnings


def _per_sport_computed(scenario: Scenario, attribute: str) -> dict[str, float]:
    """What a per-sport revenue page totals in each reported year."""
    totals: dict[str, float] = {}
    for year in scenario.horizon.years:
        if not year.reported:
            continue
        figures = [
            getattr(plan, attribute)[year.label]
            for plan in scenario.sports
            if year.label in getattr(plan, attribute)
        ]
        if figures:
            totals[year.label] = sum(figures)
    return totals


# --- The Totals page ---------------------------------------------------


def _write_totals_page(
    workbook,
    scenario: Scenario,
    pages: dict[str, sh.TotalRef],
    lines: dict[str, sh.TotalRef],
) -> None:
    """The summary: one row per schema summary entry, every cell a reference.

    A `page:*` entry points at that page's total row. A `page:line` entry
    points at the line's own row, which is how institutional support arrives
    broken into its four components while the other pages roll up whole.
    """
    sheet = workbook.create_sheet("Totals")
    layout = sh.build_layout(scenario)

    sh.write_widths(sheet, layout, stub_width=36.0)
    sh.write_title(sheet, layout, f"Pro Forma Summary — {scenario.institution} — Revenue")
    sh.write_year_header(sheet, layout, f"Revenue — {scenario.scenario_name}")

    row = layout.first_body_row
    figure_rows: list[int] = []
    for target, label in schema.REVENUE_SUMMARY_ROWS:
        page_key, line_key = target.split(":", 1)
        source = pages[page_key] if line_key == "*" else lines[target]
        sh.write_reference_row(sheet, row, layout, label=label, source=source)
        figure_rows.append(row)
        row += 1

    total_row = row
    sh.write_total(sheet, total_row, layout, label="Total Revenue", summed_rows=figure_rows)

    sh.finish_page(sheet, layout, total_row)


def _quote(sheet_name: str) -> str:
    return "'" + sheet_name.replace("'", "''") + "'"


def _set_properties(workbook, scenario: Scenario, *, kind: str) -> None:
    properties = workbook.properties
    properties.creator = "Collegiate Consulting"
    properties.title = f"{scenario.institution} — Pro Forma — {kind} — {scenario.scenario_name}"
    properties.description = (
        f"{kind} pro forma, {scenario.horizon.labels[0]} through "
        f"{scenario.horizon.labels[-1]}. Reported years from the institution's NCAA "
        "Financial Reporting System filing; projected years escalated against the "
        "assumptions stated on each sheet."
    )
