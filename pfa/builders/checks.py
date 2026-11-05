"""Does the base year tie to the filing it was built from?

This check used to live on the page, as two rows under every table: the
figure the FRS report stated, and a red "Variance vs. report" line holding
the difference. It has moved here because the workbook is a client
deliverable and a board packet should not argue with itself in the margin -
but the check itself is the single most valuable thing in this codebase and
is not negotiable, so it runs on every build and the result goes to the
analyst instead.

**Why it matters more than it looks.** Every mapping fault found while
building this tool produced a workbook that opened cleanly, summed
correctly, and was wrong by millions: nine FRS categories claimed both per
sport and at institution level ($4,047,392 counted twice at one FY25
filing), a category claimed by a page that could never display it
($358,380 absent), a residual with no row ($1,016,778 at Clemson), a
reported credit clamped away by a `max(x, 0)` ($62,439 at Lamar). No
formula check catches any of those. Comparing the page's own base-year
total against the filing's own stated figure catches all four.

A variance is not automatically a defect in this code. Some filings
disagree with themselves - Penn State's FY25 category 29 reports $961,004
of equipment against $1,799,918 across its own per-sport tables - and a pro
forma that ties to a figure the institution never filed would be worse than
one that does not tie. So the message states both numbers and the
difference and leaves the judgement to the person who can make it.
"""

from __future__ import annotations

from pfa.model.proforma import Scenario


class TieOut(str):
    """A warning that a page's base year does not reconcile to the filing.

    A string subclass rather than a flag on a message, so every existing
    caller that treats a warning as text keeps working while the app can
    still tell this class of problem apart from the rest and show it as an
    error rather than as another item on a to-do list.

    The distinction matters now that the variance row has come off the page.
    A scaffold note says "there is work left"; this says "what is on the
    page does not add up to what the institution filed", and those two must
    not arrive looking the same.
    """


#: Differences at or under this are rounding in the filing's own arithmetic
#: rather than a mapping fault, and saying so on every build would train the
#: reader to skip the warnings that matter.
TOLERANCE = 1.0


def tie_out(
    scenario: Scenario,
    key: str,
    *,
    page: str,
    source: str,
    computed: dict[str, float],
) -> list[TieOut]:
    """Compare a page's reported-year totals against the filing's own.

    `computed` is what the page itself shows, by year label - worked out
    from the same figures the sheet was written from, so the comparison is
    against the workbook rather than against a parallel calculation that
    could be right while the sheet is wrong.

    Only reported years are checked. There is nothing to reconcile a
    projection against.
    """
    reported = scenario.reconciliation.get(key)
    if not reported:
        return []

    messages: list[TieOut] = []
    for year in scenario.horizon.years:
        if not year.reported:
            continue
        stated = reported.get(year.label)
        ours = computed.get(year.label)
        if stated is None or ours is None:
            continue
        gap = ours - stated
        if abs(gap) <= TOLERANCE:
            continue
        direction = "above" if gap > 0 else "below"
        messages.append(TieOut(
            f"{page} does not tie for {year.label}: the page totals "
            f"${ours:,.0f} against {source}'s ${stated:,.0f} — "
            f"${abs(gap):,.0f} {direction}. Either the mapping has missed "
            "something or the filing disagrees with itself; check before "
            "this leaves the building."
        ))
    return messages


def scaffold_note(
    scenario: Scenario,
    key: str,
    *,
    block: str,
    source: str,
    blank: int,
) -> str:
    """The message for a salary block whose positions are not filled in yet.

    Not a tie-out. FRS reports one coaching total per sport and one
    support-staff total for the department; it does not report positions, so
    the scaffold starting empty is the normal state of a fresh build rather
    than a fault. What the analyst needs is the figure they are filling the
    scaffold up to, which is what the deleted reconciliation row carried and
    what this carries instead.
    """
    message = (
        f"{block}: {blank} scaffold position(s) have no figures — "
        "fill in what the institution carries and delete the rest."
    )
    reported = scenario.reconciliation.get(key) or {}
    base = scenario.horizon.base_year.label
    stated = reported.get(base)
    if stated is None:
        return message
    return (
        f"{message} The filing reports ${stated:,.0f} of {source} for "
        f"{base} to distribute across them."
    )
