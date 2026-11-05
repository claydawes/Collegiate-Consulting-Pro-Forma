"""Number formats for the pro forma workbooks.

Every format here was read out of the client's own pro formas rather than
chosen - the Accounting string in particular is copied character for
character from what Excel wrote into "LRU - Pro-forma - Expense - SoCon.xlsx",
including the `??` in its zero section. That detail matters: `"-"??` pads a
zero to line its dash up with the cents column of the figures above it, and
`"-"_` (which the FRS benchmarking workbooks use) does not. Two workbooks
going to the same client with their dashes in different places is the kind
of thing a reader notices without being able to say why.

Equivalencies are 0.00 throughout - the Scholar sheet's EQUIV columns. An
equivalency is fractional by nature (7.58 scholarships), and a sheet that
rounds it to 8 no longer reconciles against the FRS report it came from.

Escalation rates get a percent format at the precision they were entered,
so an assumption of 3.5% doesn't display as 4% while the sheet goes on
computing 3.5% - the same gap between shown and stored that the ROUND()
wrappers elsewhere exist to close.
"""

from __future__ import annotations

from openpyxl.styles import Alignment

#: Excel Accounting, whole dollars. Taken verbatim from the client's pro
#: formas - see the module docstring on the `??` padding.
ACCOUNTING = '_("$"* #,##0_);_("$"* \\(#,##0\\);_("$"* "-"??_);_(@_)'

#: Accounting to the cent, for the average-award column of the Scholar
#: sheet where the per-athlete figure is a division and genuinely has cents.
ACCOUNTING_CENTS = '_("$"* #,##0.00_);_("$"* \\(#,##0.00\\);_("$"* "-"??_);_(@_)'

#: Scholarship equivalencies, e.g. 7.58.
EQUIVALENCY = "0.00"

#: Whole counts - headcounts, position counts.
COUNT = "0"

#: Plain text, for label cells.
TEXT = "General"

CENTER = Alignment(horizontal="center", vertical="center")
RIGHT = Alignment(horizontal="right", vertical="center")
LEFT = Alignment(horizontal="left", vertical="center")

#: Decimal places that go with each format, used to build ROUND() calls so
#: the stored value matches the displayed one.
DECIMALS: dict[str, int] = {
    ACCOUNTING: 0,
    ACCOUNTING_CENTS: 2,
    EQUIVALENCY: 2,
    COUNT: 0,
}


def percent_format(rate: float) -> str:
    """A percent format showing `rate` to the precision it was entered at.

    3% -> "0%", 3.5% -> "0.0%", 3.25% -> "0.00%". Excel will happily display
    3.5% as "4%" under a "0%" format while still computing on 3.5, which
    makes an assumptions cell lie about the model it drives.
    """
    percent = rate * 100
    if abs(percent - round(percent)) < 1e-9:
        return "0%"
    if abs(percent * 10 - round(percent * 10)) < 1e-9:
        return "0.0%"
    return "0.00%"


def rounded(formula_body: str, number_format: str = ACCOUNTING) -> str:
    """Wraps a formula body in ROUND() at the format's precision.

    `formula_body` carries no leading '=' - "SUM(C5:C24)" becomes
    "=ROUND(SUM(C5:C24),0)".

    Applied to the totals and to every escalated figure. Without it a column
    of whole-dollar figures can visibly fail to add up to its own whole-dollar
    total, because the format rounded only what was shown: the client's own
    pro formas have exactly this - an escalation chain carrying six decimal
    places of accumulated drift under figures printed to the dollar.
    """
    return f"=ROUND({formula_body},{DECIMALS.get(number_format, 0)})"


#: Centred and wrapped, for a header that carries two lines - the Difference
#: column's "Change / <base> → <last>". openpyxl's Alignment is immutable and
#: has no copy(), so the variant is defined here rather than derived at the
#: call site.
CENTER_WRAP = Alignment(horizontal="center", vertical="center", wrap_text=True)
