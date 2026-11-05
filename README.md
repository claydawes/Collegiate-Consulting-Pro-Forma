# Pro Forma Builder

Collegiate Consulting internal tool. Turns an institution's NCAA Financial
Reporting System filings into the two pro forma workbooks — revenue and
expense — with the reported years filled in from the filings and the
projection running off escalation assumptions that live as editable cells on
every page.

Third in the set, built to the same shape as the other two so an analyst who
can drive one can drive all three:

| | Net Tuition Revenue Calculator | FRS Workbook Builder | **Pro Forma Builder** |
|---|---|---|---|
| input | one FRS report | a conference of FRS reports | one institution's FRS reports, several years |
| output | one workbook, a sheet per sport | nine benchmarking workbooks | two workbooks, eleven sheets |
| style | fills a client template | fills client templates | draws from `pfa/export/style.py` |

```bash
streamlit run app.py
```

---

## What it produces

**Revenue** — `<Institution> - Pro Forma - Revenue - <Scenario>.xlsx`

| sheet | rows |
|---|---|
| Totals | one line per detail page, every figure a live reference |
| Institutional Support | student fees, direct, indirect, state, transfers |
| Ticket Revenue | one row per sport |
| Game Guarantee | one row per sport |
| Program Revenue | sponsorship, contributions, in-kind, media, camps, endowment, other |
| NCAA | NCAA and conference distributions, post-season |

**Expense** — `<Institution> - Pro Forma - Expense - <Scenario>.xlsx`

| sheet | rows |
|---|---|
| Summary | the five expense blocks, plus a separate cash-expenditure block |
| Scholar | per sport, three columns a year: equivalencies, budget, cost per student-athlete; two grand totals, sports alone and with non-team-specific aid |
| Sports Operations | per sport operating budget, plus the unattributed residual |
| Admin Operating | departmental operating lines |
| Salaries | administration by department and coaching by sport, each with salary, benefits and combined totals |

Both carry a `$ Change` column measuring the last projected year against the
base year.

Nothing else. No footnotes, no reconciliation rows, no frozen panes — these
go to a board, and a deliverable that annotates itself in the margin reads
as a working paper. Provenance lives beside each line in
`pfa/model/schema.py`, and the reconciliation lives in the app (below).

---

## How the projection works

Each page has an **escalation palette** in row 1 — six editable cells
(Aggressive 30% … Flat 0%), descending, directly above the years they move
— and every line is assigned one. They carry comments naming their tier and
no printed label: six descending percentages in input-grey say what they are
without putting a line of instruction above the title band. A
projected year is `=ROUND(prior*(1+$H$1),0)` against its tier's cell.

This is the reason the deliverable is a workbook rather than a PDF: changing
3% to 4% in one cell moves every line on that tier, across every year. The
hand-built pro formas this replaces have the rate typed into each of several
hundred cells, which is why a rate change in one of them takes an afternoon
and misses a row.

A palette rather than one rate per page because the client's own books work
that way, and the reason holds: corporate sponsorship grows aggressively
while the institution is buying attention and steadily afterwards, while an
endowment draw grows at 5% throughout. One page rate cannot say that.

Years come in three kinds:

- **reported** — from an FRS filing, written as literals
- **entered** — yours to set, usually the year being budgeted now; seeded
  with the escalated figure so the chain stays whole
- **projected** — escalated

---

## What comes from the filing and what does not

| | source |
|---|---|
| institution-level revenue and expense by category | the filing, in full |
| per-sport operating expense, athletic aid, equivalencies | the filing, in full |
| ticket revenue, game guarantees | institution-wide in the filing; **the per-sport split is yours** |
| individual positions and salaries | **not in the filing at all** |
| conference fee schedules, intended growth | **not in the filing at all** |

The Salaries page writes CC's standard Division I position scaffold with blank
base years, and puts the filing's reported coaching and support-staff totals
beside them as reconciliation rows. It does **not** distribute the reported
total across invented positions — that would read as reported detail and be
fiction.

A blank cell means the filing carried no figure. It is deliberately not
zeroed: a real $0 and "not reported" are different claims, and the workbook
renders them differently.

---

## The base year ties to the filing

This is the property the whole design serves, and the one a CFO checks first.
It is checked on every build by `pfa/builders/checks.py`, which compares each
page's own base-year total against the figure the filing stated for the same
thing. A page that does not tie is reported in the app, by name and amount,
under its own heading rather than among the notes — because a workbook that
does not reconcile is wrong, not unfinished, and the two must not arrive
looking the same.

The check used to be two rows under every table, the second of them red.
Moving it off the page cost nothing: a variance row is only useful to the
person who can act on it, and that person is looking at the app.
Across the 2025 corpus — 223 readable filings — **every revenue total ties to
the dollar**, and so does every expense total except where the filing
disagrees with itself, in which case the app says so with the amount.

Getting there turned up four faults that no formula check would have caught,
because each produced a workbook that opened cleanly and summed correctly:

1. **Nine categories claimed twice.** Team travel, recruiting, medical,
   memberships and others are reported *both* per sport and at institution
   level, where the figure additionally carries "Expenses Not Related to
   Specific Teams". Claiming both put **$4,047,392** of one FY25 filing on
   the expense pro forma twice — 8% of that department's cost base. Admin
   Operating now takes the *remainder*, which is also the right number
   conceptually.

2. **A category claimed by a page that could never show it.** `42A` is
   institution-level only, with no per-sport table, but was listed as
   per-sport — so **$358,380** was claimed by Sports Operations, which could
   never display it, and by nothing else. Absent, not double-counted.

3. **The residual with no row.** The per-sport categories without an Admin
   Operating line had their "Not Related to Specific Teams" portion dropped
   entirely: **$1,016,778** at Clemson, **$1,197,363** at Georgia Tech. Sports
   Operations now carries it as one row, named as the form names it.

4. **A reported credit clamped away.** FRS amounts are signed, and several
   filings report a *negative* category 26 where a severance accrual was
   reversed. The remainder arithmetic floored at zero — on the reasoning
   that a negative expense reads as income — and threw those credits out:
   **$3,494** at Bowling Green, **$62,439** at Lamar. The floor is now the
   reported figure, so a credit passes through while a subtraction that
   overshoots still stops before inventing one.

Some filings disagree with *themselves*. The app reports each precisely
rather than reconciling it silently, because a pro forma that ties to a
figure the institution never filed is worse than one that doesn't tie:

- Penn State FY25 category 29 reports $961,004 of equipment at institution
  level against $1,799,918 across its own per-sport tables.
- Three filings' per-sport scholarship tables exceed their own category 20 —
  by $11, $32,898 and $229,358.
- The US Naval Academy FY25 filing states total operating expenses of
  $71,564,811 while its own categories 20–44 add to $35,768,641.
- One filing's stated total doesn't parse at all, so there is nothing to
  reconcile against; the app says so rather than leaving a blank
  reconciliation row to be read as agreement.

---

## Tests

```bash
python -m pytest tests/ -q
```

Twenty-seven tests. The point of them is that they **evaluate** the finished
workbooks rather than inspecting them — `tests/evaluate.py` is a small Excel
engine (references, ranges, `ROUND`, `SUM`, lazy `IFERROR`) written because
openpyxl writes formulas and does not compute them. Without it a test can
confirm the Totals page says `=ROUND(SUM(C4:C12),0)` and still miss that `C4`
points at the wrong row.

They check the ties, that every total equals the rows above it, that benefits
are the stated rate, that the projection actually grows, and — against four
real filings on disk — that the base year equals the filing. The tie-out
check is tested by *breaking* a scenario: punch a $50,000 hole in the
scholarship mapping and the build has to name it and state the amount. A
test that only confirmed the healthy case would pass just as happily if the
check never ran. Blank cells
evaluate to 0 exactly as Excel does, which is what lets the suite catch a
chain escalating off an empty cell.

That last one is a real regression test. The entered year was once written
blank for the operator to fill; every later year escalated off it, Excel read
the blank as zero, and the whole projection collapsed into accounting dashes
that looked like a formatting choice. Reintroduce it and two tests fail.

---

## Layout

```
app.py                      Streamlit, four steps behind a shared password
pfa/
  model/
    schema.py               THE TAXONOMY — line items, FRS category mapping,
                            position scaffolds. The standardized output.
    proforma.py             scenario, horizon, escalation palette
  export/
    style.py                the house style as five row kinds
    formats.py              number formats, taken from the client's own files
    sheet.py                the table writer — one place that knows the grid
  builders/
    revenue.py              six sheets
    expense.py              five sheets
    checks.py               does the base year tie to the filing?
    workbooks.py            assemble a scenario, build both files
  extraction/
    frs.py                  FRS filings -> a scenario seed
    parsers/                shared with the other two projects
  sports.py                 the NCAA master list and name matching
  web/                      branding and the password gate
tests/
  evaluate.py               the Excel engine
  test_workbooks.py         twenty-four tests
  fixture.py                synthetic filings, so most tests need no PDF
```

`pfa/model/schema.py` is the file to read first and the one to edit when the
NCAA revises the form. Every line is tied to the categories it is built from,
beside the line, so the tie-out is checkable rather than a matter of trust.

---

## Deploying

Streamlit Community Cloud, same as the other two. Set the password in
**Settings → Secrets**:

```toml
[auth]
password = "the-shared-password"
```

It fails closed — with no password configured nobody gets in, and the message
is aimed at whoever administers the app rather than at the person being turned
away. `.streamlit/secrets.toml` is gitignored; `.streamlit/secrets.toml.example`
documents the shape.

A scanned or photographed filing has no text layer and is rejected with a
message saying so, rather than being read for two minutes and failing anyway.
Five of the 2025 corpus are like this.
