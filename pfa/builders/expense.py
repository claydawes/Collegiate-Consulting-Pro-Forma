"""The expense pro forma: five sheets.

    Summary             the five expense blocks, plus a cash-expenditure
                        block for one-time transition costs
    Scholar             per sport, three columns a year: equivalencies, the
                        budget they cost, and the award they cost it at
    Sports Operations   per sport operating budget
    Admin Operating     institution-level operating lines
    Salaries            two blocks - administration by department, coaching
                        by sport - each with its own salary, benefits and
                        combined totals

**The Scholar page is the one that earns its complexity.** Scholarship cost
is equivalencies times cost of attendance, and a pro forma has to move both:
the institution is awarding more equivalencies *and* the cost of attendance
is rising. Collapsing that into one escalated dollar figure - which is what a
single-column-per-year layout forces - loses the ability to say "we hold
equivalencies flat and absorb a 3% COA rise" versus "we go to the cap". So
each year carries the equivalency, the award, and the product. The client's
own books do exactly this and it is the right call.

**The Salaries page is the one that cannot come out of the report.** FRS
gives one coaching total per sport and one administrative total for the
department; it does not give positions. So this page writes CC's standard
position scaffold with blank base years for the analyst to fill, and the
reported total they are filling up to is handed to them in the app rather
than printed beside the scaffold. What it must not do is distribute the
reported total across invented positions - that would read as reported
detail and be fiction.
"""

from __future__ import annotations

from pathlib import Path

import openpyxl

from pfa.builders import checks
from pfa.export import formats, sheet as sh, style
from pfa.model import schema
from pfa.model.proforma import Scenario, SportPlan

GENDER_HEADINGS = {"male": "Men's Sports", "female": "Women's Sports"}
#: The subtotal label under each gender band. Spelled out rather than
#: sliced off the heading - the old `heading[:-7]` happened to give "Men's"
#: and "Women's" and would silently produce nonsense the moment either
#: heading was reworded.
GENDER_SUBTOTALS = {"male": "Men's Total", "female": "Women's Total"}
#: How a sport is named in a message to the operator.
GENDER_LABEL = {"male": "Men's", "female": "Women's"}

#: The Scholar page's three columns per year. "Per SA" rather than
#: "Average Award" because the column is read against the two beside it -
#: equivalencies and the budget they cost - and in that company the cost per
#: student-athlete is what the reader is actually after.
_SCHOLAR_SUBS = ("Equiv.", "Budget", "Per SA")
_EQUIV, _BUDGET, _AWARD = 0, 1, 2


def build_expense_workbook(scenario: Scenario, output: Path) -> list[str]:
    """Writes the expense pro forma to `output`, returning operator notes."""
    warnings: list[str] = []
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)

    totals: dict[str, sh.TotalRef] = {}

    for key, writer in (
        ("scholarships:*", _write_scholar),
        ("sports_operations:*", _write_sports_operations),
        ("admin_operating:*", _write_admin_operating),
    ):
        totals[key], notes = writer(workbook, scenario)
        warnings.extend(notes)

    admin, coaching, notes = _write_salaries(workbook, scenario)
    totals["salaries:admin"] = admin
    totals["salaries:coaching"] = coaching
    warnings.extend(notes)

    _write_summary(workbook, scenario, totals)
    workbook.move_sheet("Summary", offset=-len(workbook.sheetnames) + 1)

    _set_properties(workbook, scenario)
    workbook.save(output)
    return warnings


# ==========================================================================
# Scholar
# ==========================================================================


def _write_scholar(workbook, scenario: Scenario):
    page = schema.SCHOLARSHIPS
    warnings: list[str] = []
    sheet = workbook.create_sheet(page.sheet_name)
    layout = sh.build_layout(
        scenario,
        columns_per_year=3,
        reference_headers=("DI NCAA Max", "New DI Max"),
        header_depth=2,
    )

    sh.write_widths(
        sheet, layout, stub_width=26.0, year_widths=(9.0, 14.0, 12.5), reference_width=12.0
    )
    sh.write_palette(sheet, scenario, layout)
    sh.write_title(sheet, layout, f"{page.title} — {scenario.institution}")
    sh.write_year_header(sheet, layout, page.stub_header, sub_labels=_SCHOLAR_SUBS)

    row = layout.first_body_row
    subtotal_rows: list[int] = []
    added: list[str] = []

    for gender, heading in GENDER_HEADINGS.items():
        plans = scenario.sports_for(gender)
        if not plans:
            continue
        sh.write_group(sheet, row, layout, heading)
        row += 1
        sport_rows: list[int] = []
        for plan in plans:
            _write_scholar_row(sheet, row, layout, scenario, plan)
            sport_rows.append(row)
            if plan.is_addition:
                added.append(plan.sport)
            row += 1
        subtotal_rows.append(row)
        _write_scholar_total(
            sheet, row, layout, label=GENDER_SUBTOTALS[gender], summed_rows=sport_rows
        )
        row += 1

    # Aid the filing reports but attributes to no team - the form's "Expenses
    # Not Related to Specific Teams" row, which in practice is aid to injured
    # athletes and to those with exhausted eligibility. It is real money in
    # category 20 and belongs to no sport, so without a row of its own the
    # page cannot tie to the filing. The client's own books carry it under
    # this name.
    other_aid = scenario.line_figures.get("scholarships:other_aid", {})
    other_aid_row: int | None = None
    if other_aid:
        sh.write_figure_row(
            sheet, row, layout,
            label="Other Scholarships (injured, exhausted eligibility)",
            values=other_aid,
            tier=scenario.tier_for("scholarships", "other_aid"),
            column_offset=_BUDGET,
        )
        # The longest label on the page, over the narrowest stub. Merged
        # across the two NCAA-limit columns, which are blank on this row by
        # construction - the cap on non-team-specific aid is not a thing
        # that exists - so the label has somewhere to go rather than being
        # clipped at "Other Scholarships (injured, exhaust".
        if layout.reference_cols:
            sheet.merge_cells(
                start_row=row, start_column=sh.STUB_COL,
                end_row=row, end_column=layout.reference_cols[-1],
            )
        other_aid_row = row
        row += 1

    total_row = row
    if subtotal_rows:
        _write_scholar_total(
            sheet, total_row, layout, label=page.total_label, summed_rows=subtotal_rows
        )
    else:
        warnings.append(
            "Scholar: no sports in the scenario — the page is a header and a zero total."
        )
        _write_scholar_total(sheet, total_row, layout, label=page.total_label, summed_rows=[])

    # Two grand totals, not one. "Grand Total" is the sports: the figure a
    # reader compares against a roster plan or another institution's squad
    # list, and the only one the per-sport rows above it actually add to.
    # Non-team-specific aid - injured athletes, exhausted eligibility - is
    # real money in category 20 that belongs to no sport, so a single total
    # carrying both answers neither question. The second row adds it back,
    # and that is the row that ties to the filing and the row the Summary
    # points at.
    reference_row = total_row
    if other_aid_row is not None:
        reference_row = total_row + 1
        _write_scholar_total(
            sheet, reference_row, layout,
            label="Grand Total + Other Scholarship",
            summed_rows=[total_row, other_aid_row],
        )

    if added:
        warnings.append(
            "Scholar: not sponsored in the reported years — "
            f"{', '.join(sorted(set(added)))}. Those rows start at zero by "
            "construction, not by omission."
        )
    warnings.extend(checks.tie_out(
        scenario, "scholarships",
        page="Scholar",
        source="FRS category 20",
        computed=_scholar_computed(scenario),
    ))

    sh.finish_page(sheet, layout, reference_row)
    return sh.total_ref(sheet, layout, reference_row, column_offset=_BUDGET), warnings


def _scholar_computed(scenario: Scenario) -> dict[str, float]:
    """What the Scholar page's bottom row shows in each reported year.

    Worked out the way the sheet works it out - equivalencies times the
    implied award, rounded per sport, plus non-team-specific aid - rather
    than by re-adding the filing's own aid figures. Re-adding the source
    would compare the filing against itself and always agree, which is the
    one answer this check must not be able to give.
    """
    totals: dict[str, float] = {}
    for year in scenario.horizon.years:
        if not year.reported:
            continue
        total = 0.0
        carried = False
        for plan in scenario.sports:
            equivalencies = plan.equivalencies.get(year.label)
            award = _implied_award(plan, year.label)
            if equivalencies is None or award is None:
                continue
            total += round(equivalencies * award, 0)
            carried = True
        other = scenario.line_figures.get("scholarships:other_aid", {}).get(year.label)
        if other is not None:
            total += other
            carried = True
        if carried:
            totals[year.label] = total
    return totals


def _write_scholar_row(
    sheet, row: int, layout: sh.Layout, scenario: Scenario, plan: SportPlan
) -> None:
    """One sport across the horizon: equivalency, budget, award per year."""
    style.data_row(
        sheet, row, first_col=sh.STUB_COL, last_col=layout.table_last_col,
        label_col=sh.STUB_COL, heavy_cols=layout.block_columns,
    )
    sheet.cell(row=row, column=sh.STUB_COL).value = plan.sport

    for column, value in zip(layout.reference_cols, (plan.ncaa_maximum, plan.new_maximum)):
        cell = sheet.cell(row=row, column=column)
        cell.value = value
        cell.number_format = formats.EQUIVALENCY
        cell.alignment = formats.CENTER

    tier = scenario.tier_for("scholarships", plan.sport)
    for index, year in enumerate(layout.horizon.years):
        label = year.label
        equiv_letter = layout.year_letter(index, _EQUIV)
        award_letter = layout.year_letter(index, _AWARD)

        equiv = sheet.cell(row=row, column=layout.year_col(index, _EQUIV))
        equiv.number_format = formats.EQUIVALENCY
        equiv.alignment = formats.CENTER
        entered = plan.equivalencies.get(label)
        if entered is not None:
            equiv.value = entered
        elif index > 0:
            # An equivalency count carries forward rather than escalating -
            # see the page note. Written as a reference so changing the year
            # it was last set in moves every year after it.
            equiv.value = f"={layout.year_letter(index - 1, _EQUIV)}{row}"
        else:
            equiv.value = 0 if plan.is_addition else None

        award = sheet.cell(row=row, column=layout.year_col(index, _AWARD))
        award.number_format = formats.ACCOUNTING
        award.alignment = formats.RIGHT
        # A reported year's award is what that year's filing implies - its aid
        # over its equivalencies - not the year before escalated. Escalating
        # through a reported year would overwrite a figure the institution
        # actually filed with a guess at it, and the two reported years are
        # precisely what a reader checks the slope against.
        implied = _implied_award(plan, label)
        if implied is not None:
            award.value = implied
        elif index > 0:
            prior = layout.year_letter(index - 1, _AWARD)
            award.value = formats.rounded(
                f"{prior}{row}*(1+{layout.palette_cell(tier.key)})", formats.ACCOUNTING
            )
        else:
            award.value = None

        budget = sheet.cell(row=row, column=layout.year_col(index, _BUDGET))
        budget.number_format = formats.ACCOUNTING
        budget.alignment = formats.RIGHT
        budget.value = formats.rounded(f"{equiv_letter}{row}*{award_letter}{row}")

    sh.write_difference(sheet, row, layout, column_offset=_BUDGET)


def _implied_award(plan: SportPlan, year: str) -> float | None:
    """The base year's average award: what the report implies, where it gave
    both halves.

    FRS reports a sport's total athletic aid and its equivalency count, so
    the per-equivalency cost is the quotient. Derived here rather than left
    as a live division because a sport with zero equivalencies would divide
    by zero on the page - and a sport with no awards has no average award,
    which is a blank, not an error.
    """
    if plan.average_award.get(year) is not None:
        return plan.average_award[year]
    dollars = plan.aid_dollars.get(year)
    equivalencies = plan.equivalencies.get(year)
    if dollars is None or not equivalencies:
        return None
    return round(dollars / equivalencies, 2)


def _write_scholar_total(
    sheet, row: int, layout: sh.Layout, *, label: str, summed_rows: list[int]
) -> None:
    """A Scholar total band: equivalencies and budget sum, and the award
    column reports the blended cost per equivalency.

    The award column is not a sum - the sum of eighteen sports' per-athlete
    awards is a meaningless number that looks like a cost. Nor is it a mean
    of those awards: that weights a four-equivalency golf programme the same
    as a thirteen-equivalency basketball one.

    It is total budget over total equivalencies, which is the blended cost of
    an equivalency across the group - the figure that actually answers "what
    does a scholarship cost here". It also has the property that it
    reconciles: multiply it by the equivalency total and you get the budget
    total, which no average of the per-sport awards does.

    IFERROR because the denominator is zero for a group awarding nothing, and
    a page of #DIV/0! in front of a client is unforgivable for a sheet whose
    correct answer is a dash.
    """
    style.total_row(
        sheet, row, label, first_col=sh.STUB_COL, last_col=layout.table_last_col,
        heavy_cols=layout.block_columns,
    )
    for column in layout.reference_cols:
        cell = sheet.cell(row=row, column=column)
        letter = cell.column_letter
        cell.value = (
            formats.rounded(sh.sum_expression(letter, summed_rows), formats.EQUIVALENCY)
            if summed_rows else None
        )
        cell.number_format = formats.EQUIVALENCY
        cell.alignment = formats.CENTER

    for index in range(layout.year_count):
        for offset, number_format in (
            (_EQUIV, formats.EQUIVALENCY),
            (_BUDGET, formats.ACCOUNTING),
            (_AWARD, formats.ACCOUNTING),
        ):
            letter = layout.year_letter(index, offset)
            cell = sheet.cell(row=row, column=layout.year_col(index, offset))
            cell.number_format = number_format
            cell.alignment = formats.CENTER if offset == _EQUIV else formats.RIGHT
            if not summed_rows:
                continue
            if offset == _AWARD:
                equiv = layout.year_letter(index, _EQUIV)
                budget = layout.year_letter(index, _BUDGET)
                cell.value = formats.rounded(
                    f'IFERROR({budget}{row}/{equiv}{row},0)', number_format
                )
            else:
                cell.value = formats.rounded(
                    sh.sum_expression(letter, summed_rows), number_format
                )
    sh.write_difference(sheet, row, layout, column_offset=_BUDGET)
    sheet.cell(row=row, column=layout.diff_col).font = style.TOTAL_FONT


# ==========================================================================
# Sports Operations
# ==========================================================================


def _write_sports_operations(workbook, scenario: Scenario):
    page = schema.SPORTS_OPERATIONS
    warnings: list[str] = []
    sheet = workbook.create_sheet(page.sheet_name)
    layout = sh.build_layout(scenario)

    sh.write_widths(sheet, layout, stub_width=28.0)
    sh.write_palette(sheet, scenario, layout)
    sh.write_title(sheet, layout, f"{page.title} — {scenario.institution}")
    sh.write_year_header(sheet, layout, page.stub_header)

    row = layout.first_body_row
    figure_rows: list[int] = []
    missing: list[str] = []
    for gender, heading in GENDER_HEADINGS.items():
        plans = scenario.sports_for(gender)
        if not plans:
            continue
        sh.write_group(sheet, row, layout, heading)
        row += 1
        for plan in plans:
            sh.write_figure_row(
                sheet, row, layout,
                label=plan.sport,
                values=plan.operating,
                tier=scenario.tier_for("sports_operations", plan.sport),
                starts_at_zero=plan.is_addition,
            )
            figure_rows.append(row)
            if not plan.operating and not plan.is_addition:
                missing.append(f"{GENDER_LABEL[gender]} {plan.sport}")
            row += 1

    # The residual the filing reports in these categories but charges to no
    # sport - central game operations, equipment bought departmentally, and
    # the post-season categories. Named as the FRS form names it.
    unattributed = scenario.line_figures.get("sports_operations:unattributed", {})
    if unattributed:
        sh.write_group(sheet, row, layout, "Not Related to Specific Teams")
        row += 1
        sh.write_figure_row(
            sheet, row, layout,
            label="Central / unattributed operating expense",
            values=unattributed,
            tier=scenario.tier_for("sports_operations", "unattributed"),
        )
        figure_rows.append(row)
        row += 1

    total_row = row
    sh.write_total(sheet, total_row, layout, label=page.total_label, summed_rows=figure_rows)

    if missing:
        warnings.append(
            "Sports Operations: no operating budget for "
            f"{', '.join(missing)} — those rows are blank."
        )

    warnings.extend(checks.tie_out(
        scenario, "sports_operations",
        page="Sports Operations",
        source="the filing's per-sport tables",
        computed=_operations_computed(scenario),
    ))

    sh.finish_page(sheet, layout, total_row)
    return sh.total_ref(sheet, layout, total_row), warnings


def _operations_computed(scenario: Scenario) -> dict[str, float]:
    """What the Sports Operations total shows in each reported year: every
    sport's operating budget plus the residual the filing charges to no
    team.
    """
    totals: dict[str, float] = {}
    unattributed = scenario.line_figures.get("sports_operations:unattributed", {})
    for year in scenario.horizon.years:
        if not year.reported:
            continue
        figures = [
            plan.operating[year.label]
            for plan in scenario.sports
            if year.label in plan.operating
        ]
        residual = unattributed.get(year.label)
        if not figures and residual is None:
            continue
        totals[year.label] = sum(figures) + (residual or 0.0)
    return totals


# ==========================================================================
# Admin Operating
# ==========================================================================


def _write_admin_operating(workbook, scenario: Scenario):
    page = schema.ADMIN_OPERATING
    warnings: list[str] = []
    sheet = workbook.create_sheet(page.sheet_name)
    layout = sh.build_layout(scenario)

    sh.write_widths(sheet, layout, stub_width=44.0)
    sh.write_palette(sheet, scenario, layout)
    sh.write_title(sheet, layout, f"{page.title} — {scenario.institution}")
    sh.write_year_header(sheet, layout, page.stub_header)

    row = layout.first_body_row
    figure_rows: list[int] = []
    for line in page.lines:
        values = scenario.line_figures.get(f"{page.key}:{line.key}", {})
        sh.write_figure_row(
            sheet, row, layout,
            label=line.label,
            values=values,
            tier=scenario.tier_for(page.key, line.key),
            starts_at_zero=line.starts_at_zero,
            carry_forward=line.recurring,
        )
        figure_rows.append(row)
        if not values and not line.starts_at_zero:
            warnings.append(
                f"Admin Operating: no figure for “{line.label}” — the row is blank."
            )
        row += 1

    total_row = row
    sh.write_total(sheet, total_row, layout, label=page.total_label, summed_rows=figure_rows)

    warnings.extend(checks.tie_out(
        scenario, "admin_operating",
        page="Admin Operating",
        source="the filing's departmental remainder",
        computed=_admin_computed(scenario, page),
    ))

    sh.finish_page(sheet, layout, total_row)
    return sh.total_ref(sheet, layout, total_row), warnings


def _admin_computed(scenario: Scenario, page: schema.Page) -> dict[str, float]:
    """What the Admin Operating total shows in each reported year.

    Checked against the filing's *remainder* - its institution-level figure
    net of whatever it attributes to individual sports - and not against the
    gross. Nine FRS categories are reported both ways, so checking this page
    against the gross would report a several-million variance on a page that
    is right, which is the fastest way to teach a reader to ignore a
    warning.
    """
    totals: dict[str, float] = {}
    for year in scenario.horizon.years:
        if not year.reported:
            continue
        figures = [
            value
            for line in page.lines
            if (value := scenario.line_figures.get(
                f"{page.key}:{line.key}", {}
            ).get(year.label)) is not None
        ]
        if figures:
            totals[year.label] = sum(figures)
    return totals


# ==========================================================================
# Salaries
# ==========================================================================


def _write_salaries(workbook, scenario: Scenario):
    """Two tables on one sheet, as every one of the client's books has it.

    One sheet rather than two because the two blocks share the Summary's
    attention and an analyst works them together - a position moved out of
    coaching and into administration has to land somewhere, and a reader
    checking that it did should not be switching tabs.
    """
    warnings: list[str] = []
    sheet = workbook.create_sheet(schema.SALARIES.sheet_name)
    layout = sh.build_layout(scenario)

    sh.write_widths(sheet, layout, stub_width=42.0)
    sh.write_palette(sheet, scenario, layout)

    admin_total, row, admin_notes = _write_salary_block(
        sheet, layout, scenario,
        start_row=sh.TITLE_ROW,
        title=f"Administrative Salaries — {scenario.institution}",
        block_name="Administrative Salaries",
        groups=_admin_groups(scenario),
        reconciliation_key="admin_salaries",
        reconciliation_source="support staff compensation (FRS 24 + 25)",
    )
    warnings.extend(admin_notes)

    # Two blank rows between the blocks, the same gap the Summary leaves
    # above its cash block. One is too few to read as a break between two
    # tables on one sheet; three starts to read as a missing table.
    row += 3
    coaching_total, row, coaching_notes = _write_salary_block(
        sheet, layout, scenario,
        start_row=row,
        title=f"Coaches' Salaries — {scenario.institution}",
        block_name="Coaches' Salaries",
        groups=_coaching_groups(scenario),
        reconciliation_key="coaching_salaries",
        reconciliation_source="coaching compensation (FRS 22 + 23)",
    )
    warnings.extend(coaching_notes)

    sh.finish_page(sheet, layout, row)
    return (
        sh.total_ref(sheet, layout, admin_total),
        sh.total_ref(sheet, layout, coaching_total),
        warnings,
    )


def _admin_groups(scenario: Scenario) -> list[tuple[str, list[tuple[str, dict]]]]:
    """The administrative scaffold, with whatever figures the scenario has."""
    groups = []
    for department, positions in schema.ADMIN_DEPARTMENTS:
        rows = [
            (position, scenario.admin_salaries.get(f"{department}|{position}", {}))
            for position in positions
        ]
        groups.append((department, rows))
    return groups


def _coaching_groups(scenario: Scenario) -> list[tuple[str, list[tuple[str, dict]]]]:
    """The coaching scaffold: a band per gender, then a band per sport with
    its positions under it.

    A sport sponsored on both sides gets a row under each gender band rather
    than a combined one - the pay is different and the pro forma is costing
    staff, not counting sports.
    """
    groups: list[tuple[str, list[tuple[str, dict]]]] = []
    for gender, heading in GENDER_HEADINGS.items():
        plans = scenario.sports_for(gender)
        if not plans:
            continue
        groups.append((heading, []))
        for plan in plans:
            rows = [
                (position, plan.coaching.get(position, {}))
                for position in schema.coaching_staff(plan.sport)
            ]
            groups.append((f"    {plan.sport}", rows))
    return groups


def _write_salary_block(
    sheet,
    layout: sh.Layout,
    scenario: Scenario,
    *,
    start_row: int,
    title: str,
    block_name: str,
    groups: list[tuple[str, list[tuple[str, dict]]]],
    reconciliation_key: str,
    reconciliation_source: str,
):
    """One salary table: title, header, grouped positions, then the three
    total rows - salary, benefits, and the two combined.

    Returns the row holding salary-plus-benefits, which is what the Summary
    page points at.
    """
    warnings: list[str] = []
    style.title_row(
        sheet, start_row, title, first_col=sh.STUB_COL, last_col=layout.table_last_col,
        heavy_cols=layout.block_columns,
    )
    header = start_row + 1
    labels = {sh.STUB_COL: "Position"}
    for index, year in enumerate(layout.horizon.years):
        labels[layout.year_col(index)] = year.label
    style.header_row(
        sheet, header, labels, first_col=sh.STUB_COL, last_col=layout.table_last_col,
        heavy_cols=layout.block_columns,
    )
    sh.write_difference_header(sheet, layout, header)

    row = header + 1
    position_rows: list[int] = []
    blank = 0
    for group_label, positions in groups:
        sh.write_group(sheet, row, layout, group_label)
        row += 1
        for position, values in positions:
            sh.write_figure_row(
                sheet, row, layout,
                label=position,
                values=values,
                tier=scenario.tier_for("salaries", position),
            )
            position_rows.append(row)
            if not values:
                blank += 1
            row += 1

    salary_row = row
    sh.write_total(sheet, salary_row, layout, label="Total Salary", summed_rows=position_rows)

    benefits_row = salary_row + 1
    style.total_row(
        sheet, benefits_row, f"Benefits ({scenario.benefits_rate:.0%})",
        first_col=sh.STUB_COL, last_col=layout.table_last_col,
        heavy_cols=layout.block_columns,
    )
    for index in range(layout.year_count):
        column = layout.year_letter(index)
        cell = sheet.cell(row=benefits_row, column=layout.year_col(index))
        cell.value = formats.rounded(f"{column}{salary_row}*{scenario.benefits_rate}")
        cell.number_format = formats.ACCOUNTING
        cell.alignment = formats.RIGHT
    sh.write_difference(sheet, benefits_row, layout)
    sheet.cell(row=benefits_row, column=layout.diff_col).font = style.TOTAL_FONT

    combined_row = benefits_row + 1
    sh.write_total(
        sheet, combined_row, layout,
        label="Total Salary + Benefits",
        summed_rows=[salary_row, benefits_row],
    )

    if blank:
        warnings.append(checks.scaffold_note(
            scenario, reconciliation_key,
            block=block_name,
            source=reconciliation_source,
            blank=blank,
        ))
    return combined_row, combined_row, warnings


# ==========================================================================
# Summary
# ==========================================================================


def _write_summary(workbook, scenario: Scenario, totals: dict[str, sh.TotalRef]) -> None:
    sheet = workbook.create_sheet("Summary")
    layout = sh.build_layout(scenario)

    sh.write_widths(sheet, layout, stub_width=36.0)
    # The operating block above is nothing but references, so for a long
    # time this page carried no palette - and the cash block below escalates
    # its recurring dues line against the Flat tier's cell, which on a page
    # without a palette is empty. Excel reads an empty cell as zero, flat is
    # zero, and the line therefore came out right by arithmetic accident.
    # It would have stopped being right the moment anyone moved a cash line
    # onto a tier that is not flat, and the failure would have been silent.
    sh.write_palette(sheet, scenario, layout)
    sh.write_title(
        sheet, layout, f"Pro Forma Summary — {scenario.institution} — Expenses"
    )
    sh.write_year_header(sheet, layout, f"Expenses — {scenario.scenario_name}")

    row = layout.first_body_row
    figure_rows: list[int] = []
    for target, label in schema.EXPENSE_SUMMARY_ROWS:
        sh.write_reference_row(sheet, row, layout, label=label, source=totals[target])
        figure_rows.append(row)
        row += 1

    operating_total = row
    sh.write_total(
        sheet, operating_total, layout,
        label="Total Operating Expenses", summed_rows=figure_rows,
    )

    # --- the cash block -------------------------------------------------
    row = operating_total + 3
    style.title_row(
        sheet, row, "Cash Expenditures — One-time and Transition Costs",
        first_col=sh.STUB_COL, last_col=layout.table_last_col,
        heavy_cols=layout.block_columns,
    )
    row += 1
    labels = {sh.STUB_COL: "Cash Expenditures"}
    for index, year in enumerate(layout.horizon.years):
        labels[layout.year_col(index)] = year.label
    style.header_row(
        sheet, row, labels, first_col=sh.STUB_COL, last_col=layout.table_last_col,
        heavy_cols=layout.block_columns,
    )
    row += 1

    cash_rows: list[int] = []
    for line in schema.TRANSITION_COST_LINES:
        sh.write_figure_row(
            sheet, row, layout,
            label=line.label,
            values=scenario.line_figures.get(f"transition:{line.key}", {}),
            tier=scenario.tier("flat"),
            starts_at_zero=True,
            carry_forward=line.recurring,
        )
        cash_rows.append(row)
        row += 1
    cash_total = row
    sh.write_total(sheet, cash_total, layout, label="Total Cash", summed_rows=cash_rows)

    row = cash_total + 1
    sh.write_total(
        sheet, row, layout,
        label="Total Expenses + Cash",
        summed_rows=[operating_total, cash_total],
    )
    sh.finish_page(sheet, layout, row)


def _set_properties(workbook, scenario: Scenario) -> None:
    properties = workbook.properties
    properties.creator = "Collegiate Consulting"
    properties.title = (
        f"{scenario.institution} — Pro Forma — Expense — {scenario.scenario_name}"
    )
    properties.description = (
        f"Expense pro forma, {scenario.horizon.labels[0]} through "
        f"{scenario.horizon.labels[-1]}. Reported years from the institution's NCAA "
        "Financial Reporting System filing; projected years escalated against the "
        "assumptions stated on each sheet."
    )
