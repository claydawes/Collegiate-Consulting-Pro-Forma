"""A small Excel evaluator, so the tests can check what the workbooks
actually compute rather than only what formulas they contain.

openpyxl writes formulas; it does not evaluate them. Without something that
does, a test can assert that the Totals page says `=ROUND(SUM(C4:C12),0)`
and still miss that C4 points at the wrong row, that a chain escalates off a
blank cell, or that the base year does not tie to the FRS report it came
from. Those are exactly the failures that produce a workbook which opens
cleanly and is wrong.

**Scope, deliberately narrow.** This understands only what the builders
emit: cell and range references with optional sheet names and `$`, the five
functions used (ROUND, SUM, AVERAGEIF, IFERROR, COUNT), and `+ - * / ( )`.
Anything else raises rather than guessing, so the evaluator cannot quietly
diverge from the workbooks as they grow - a new function in a builder fails
the tests until it is taught here.

Blank cells evaluate to 0, as Excel does in arithmetic. That is the point of
several of the tests: a blank is not a safe default, it is a hole that
silently zeroes everything downstream, and the evaluator has to reproduce
Excel's behaviour for the tests to catch it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from openpyxl.utils import column_index_from_string, get_column_letter

#: 'Sheet Name'!$C$4  |  Sheet!C4  |  $C$4  |  C4
_REFERENCE = re.compile(
    r"(?:(?P<sheet>'[^']+'|[A-Za-z_][A-Za-z0-9_ ]*)!)?"
    r"\$?(?P<col>[A-Z]{1,3})\$?(?P<row>\d+)"
    r"(?::\$?(?P<col2>[A-Z]{1,3})\$?(?P<row2>\d+))?"
)


class UnsupportedFormula(RuntimeError):
    """A formula using something this evaluator was never taught."""


@dataclass
class Cell:
    sheet: str
    column: int
    row: int

    @property
    def key(self) -> tuple[str, int, int]:
        return (self.sheet, self.column, self.row)


class Workbook:
    """A loaded workbook that can be asked for a cell's computed value."""

    def __init__(self, workbook) -> None:
        self._workbook = workbook
        self._cache: dict[tuple[str, int, int], float | str | None] = {}
        self._in_progress: set[tuple[str, int, int]] = set()

    # --- public ---------------------------------------------------------

    def value(self, sheet: str, coordinate: str) -> float | str | None:
        match = _REFERENCE.fullmatch(coordinate)
        if match is None:
            raise UnsupportedFormula(f"{coordinate!r} is not a cell reference.")
        return self._cell(
            Cell(sheet, column_index_from_string(match["col"]), int(match["row"]))
        )

    def number(self, sheet: str, coordinate: str) -> float:
        value = self.value(sheet, coordinate)
        return 0.0 if value is None or isinstance(value, str) else float(value)

    # --- evaluation -----------------------------------------------------

    def _cell(self, cell: Cell) -> float | str | None:
        if cell.key in self._cache:
            return self._cache[cell.key]
        if cell.key in self._in_progress:
            # A circular reference. Excel refuses the workbook outright, so a
            # test that reaches this has found a real defect; raising names
            # the cell rather than hanging.
            raise UnsupportedFormula(
                f"Circular reference at {cell.sheet}!"
                f"{get_column_letter(cell.column)}{cell.row}"
            )

        raw = self._workbook[cell.sheet].cell(row=cell.row, column=cell.column).value
        if not isinstance(raw, str) or not raw.startswith("="):
            result = raw
        else:
            self._in_progress.add(cell.key)
            try:
                result = self._evaluate(raw[1:], cell.sheet)
            finally:
                self._in_progress.discard(cell.key)

        self._cache[cell.key] = result
        return result

    def _evaluate(self, expression: str, sheet: str) -> float:
        python = _lazy_iferror(self._translate(expression, sheet))
        try:
            return eval(python, {"__builtins__": {}}, _FUNCTIONS)  # noqa: S307
        except UnsupportedFormula:
            raise
        except Exception as exc:  # noqa: BLE001
            raise UnsupportedFormula(
                f"Could not evaluate {expression!r} on {sheet!r} (as {python!r}): {exc}"
            ) from exc

    def _translate(self, expression: str, sheet: str) -> str:
        """Rewrites one Excel formula as a Python expression.

        References become resolved literals, so a range inside SUM arrives as
        a Python list and a lone cell as a number. Functions keep their names
        and are supplied by _FUNCTIONS.
        """
        known = set(_FUNCTIONS) | {"IFERROR"}
        for name in re.findall(r"\b([A-Z][A-Z0-9]*)\s*\(", expression):
            if name not in known:
                raise UnsupportedFormula(
                    f"{name}() is used in a workbook formula but the test evaluator "
                    "does not implement it. Teach it, or stop emitting it."
                )

        def replace(match: re.Match) -> str:
            # Not a reference if it is immediately followed by '(' - that is a
            # function name that happens to match the column/row shape.
            if match.group(0).isalpha():
                return match.group(0)
            source = match["sheet"] or sheet
            source = source.strip("'")
            first = Cell(source, column_index_from_string(match["col"]), int(match["row"]))
            if match["col2"] is None:
                return repr(self._numeric(self._cell(first)))
            last = Cell(source, column_index_from_string(match["col2"]), int(match["row2"]))
            return repr(self._range(first, last))

        # Quoted sheet names can contain characters the reference pattern
        # would otherwise split on, so they are substituted first.
        return _REFERENCE.sub(replace, expression)

    def _range(self, first: Cell, last: Cell) -> list[float]:
        values: list[float] = []
        for row in range(min(first.row, last.row), max(first.row, last.row) + 1):
            for column in range(
                min(first.column, last.column), max(first.column, last.column) + 1
            ):
                values.append(
                    self._numeric(self._cell(Cell(first.sheet, column, row)))
                )
        return values

    @staticmethod
    def _numeric(value) -> float:
        """Excel's own coercion in an arithmetic context: a blank and a text
        cell are both zero.

        Reproduced rather than treated as an error, because the tests need to
        see what the client would see. A chain escalating off a blank really
        does compute zero in Excel, silently, and a test that raised instead
        would never catch it.
        """
        if value is None or isinstance(value, str) or isinstance(value, bool):
            return 0.0
        return float(value)


def _excel_round(value: float, digits: float = 0) -> float:
    """Excel's ROUND: half away from zero, not Python's banker's rounding.

    Python's round(2.5) is 2 and Excel's is 3. Using the wrong one here would
    make the tests disagree with the workbook by a dollar in a way that looks
    like a real defect.
    """
    from decimal import Decimal, ROUND_HALF_UP

    quantum = Decimal(1).scaleb(-int(digits))
    return float(Decimal(repr(float(value))).quantize(quantum, rounding=ROUND_HALF_UP))


def _excel_sum(*args) -> float:
    total = 0.0
    for argument in args:
        total += sum(argument) if isinstance(argument, list) else float(argument)
    return total


def _lazy_iferror(python: str) -> str:
    """Rewrites IFERROR(a, b) so `a` is only evaluated inside a try.

    Needed because Python evaluates arguments before the call: the workbooks
    emit `IFERROR(L18/K18,0)` precisely for the case where K18 is zero, and
    left as a plain call the division raises before IFERROR can catch it -
    so the test would fail on the exact input the formula exists to handle.

    Done by balancing parentheses rather than with a regex, since the first
    argument contains its own.
    """
    while True:
        start = python.find("IFERROR(")
        if start == -1:
            return python
        open_at = start + len("IFERROR")
        depth = 0
        split_at = None
        for index in range(open_at, len(python)):
            character = python[index]
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    close_at = index
                    break
            elif character == "," and depth == 1 and split_at is None:
                split_at = index
        else:
            raise UnsupportedFormula(f"Unbalanced IFERROR in {python!r}.")
        if split_at is None:
            raise UnsupportedFormula(f"IFERROR needs two arguments in {python!r}.")
        attempt = python[open_at + 1 : split_at]
        fallback = python[split_at + 1 : close_at]
        python = (
            python[:start]
            + f"IFERROR_LAZY(lambda: ({attempt}), lambda: ({fallback}))"
            + python[close_at + 1 :]
        )


def _excel_iferror_lazy(attempt, fallback):
    try:
        value = attempt()
    except Exception:  # noqa: BLE001 - any error is what IFERROR catches
        return fallback()
    return fallback() if value is None else value


def _excel_averageif(values, criterion: str) -> float:
    """Only the ">0" form, which is the only one the builders emit."""
    if criterion != ">0":
        raise UnsupportedFormula(f"AVERAGEIF criterion {criterion!r} is not implemented.")
    matching = [value for value in values if value > 0]
    if not matching:
        raise ZeroDivisionError("AVERAGEIF matched nothing, which is Excel's #DIV/0!")
    return sum(matching) / len(matching)


def _excel_count(*args) -> int:
    total = 0
    for argument in args:
        total += len(argument) if isinstance(argument, list) else 1
    return total


#: Everything the evaluator knows. A formula using anything else raises.
_FUNCTIONS = {
    "ROUND": _excel_round,
    "SUM": _excel_sum,
    "IFERROR_LAZY": _excel_iferror_lazy,
    "AVERAGEIF": _excel_averageif,
    "COUNT": _excel_count,
}
