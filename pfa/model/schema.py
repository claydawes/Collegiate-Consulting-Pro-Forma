"""The standardized pro forma line-item taxonomy.

This module is the deliverable the rest of the project exists to produce
consistently. Every pro forma Collegiate Consulting has built carries the
same six revenue pages and five expense pages, but the line items inside
them have drifted - the FY24 pair calls a row "Direct/Indirect
Institutional Support" and totals two FRS categories into it, while the FY26
ASUN pair splits the same money across "Direct Institutional Support" and
"Indirect Institutional Support"; one book's Program Revenue page has four
rows, another's has five with different names. The structure below is one
reading of those files, reconciled, with each line tied to the NCAA FRS
categories it is actually built from.

**Why the FRS category is part of the schema and not a lookup elsewhere.**
A pro forma's base year is the institution's last reported year, and it has
to tie to the FRS report in front of the client. Writing the mapping beside
the line it feeds is what makes that checkable: the Totals page of the
revenue book can state, and the builders can assert, that the base-year
column sums to the report's own Total Operating Revenues. When the mapping
lives in a separate table, the two drift and nobody finds out until a board
meeting.

**On splitting what FRS combines.** Several schema lines are finer than the
FRS form:

* Ticket Revenue and Game Guarantees are reported institution-wide
  (categories 1 and 7) but shown per sport, because the whole point of a
  reclassification pro forma is which sports generate what. Per-sport
  figures come from the report's Revenue Details section where it carries
  them, and otherwise from the analyst.
* The Salaries page's individual positions are finer than anything FRS
  reports - the form gives one coaching total per sport (category 22, and
  the per-sport Coaching Expenses table) and one administrative total
  (category 24). The position scaffolds below are CC's standard build, and
  the sheet carries the reported total beside them as a reconciliation row
  rather than pretending the detail came out of the report.

Nothing here is an escalation assumption. Growth rates belong to a scenario,
not to the taxonomy - see pfa/model/proforma.py.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Line:
    """One row of a pro forma page.

    `frs_categories` are the NCAA FRS Revenue/Expense Summary category IDs
    that make up this line's base-year figure, added together. Empty means
    the line has no institution-level FRS source - either because it is
    per-sport (and comes from a detail table) or because it is a transition
    cost that by definition did not exist in the reported year, like a
    conference entry fee.
    """

    key: str
    label: str
    frs_categories: tuple[str, ...] = ()
    #: A note printed under the page, for a line whose derivation a reader
    #: would otherwise have to guess at.
    note: str | None = None
    #: True for a line that is zero in the base year by construction - a
    #: new-division cost, a fee that starts on reclassification. Its base
    #: year is written as a hard 0 rather than left blank, because here the
    #: zero is a claim the model makes rather than a figure nobody reported.
    starts_at_zero: bool = False
    #: False for a cost paid once rather than every year. An unentered year
    #: of a recurring line carries the previous year forward (escalated);
    #: an unentered year of a one-time line is zero.
    #:
    #: This distinction is not cosmetic. A reclassification application fee
    #: entered in one year and left to carry forward appears in every
    #: subsequent year of the projection - the pro forma then shows a
    #: $1,975,000 fee being paid six times, nearly $10m of cost the
    #: institution will never incur, on the page a board approves the move
    #: from. An annual conference due entered once *should* carry forward,
    #: which is why this is per line rather than per block.
    recurring: bool = True


@dataclass(frozen=True)
class Page:
    """One worksheet of a pro forma workbook."""

    key: str
    sheet_name: str
    title: str
    #: The header label over the leftmost column.
    stub_header: str
    lines: tuple[Line, ...] = ()
    total_label: str = "Total"
    #: Set for a page whose rows are sports rather than fixed line items.
    per_sport: bool = False


# ==========================================================================
# REVENUE
# ==========================================================================

INSTITUTIONAL_SUPPORT = Page(
    key="institutional_support",
    sheet_name="Institutional Support",
    title="Institutional Support",
    stub_header="Institutional Support",
    lines=(
        Line("student_fees", "Student Athletic Fees", ("3",)),
        Line("direct_institutional", "Direct Institutional Support", ("4",)),
        Line("indirect_institutional", "Indirect Institutional Support", ("6", "6A")),
        Line(
            "government",
            "Direct State or Other Government Support",
            ("2",),
            note="FRS category 2. Zero at most private institutions and at many publics, "
                 "where state appropriation reaches athletics through category 4 instead.",
        ),
        Line(
            "transfers_out",
            "Less - Transfers to Institution",
            ("5",),
            note="FRS category 5, reported as a negative. Left as reported rather than "
                 "flipped, so the page's total ties to the report's own revenue total.",
        ),
    ),
)

TICKET_REVENUE = Page(
    key="ticket_revenue",
    sheet_name="Ticket Revenue",
    title="Ticket Revenue",
    stub_header="Ticket Revenue",
    per_sport=True,
)

GAME_GUARANTEE = Page(
    key="game_guarantee",
    sheet_name="Game Guarantee",
    title="Game Guarantee",
    stub_header="Game Guarantee",
    per_sport=True,
    total_label="Total Revenue",
)

PROGRAM_REVENUE = Page(
    key="program_revenue",
    sheet_name="Program Revenue",
    title="Program Revenue",
    stub_header="Program Revenue",
    lines=(
        Line("sponsorship", "Corporate Sponsorship / Royalties / Licensing", ("15",)),
        Line("contributions", "Contributions / Advancement", ("8",)),
        Line("in_kind", "In-Kind", ("9",)),
        Line("concessions", "Program, Novelty, Parking & Concessions", ("14",)),
        Line("media_rights", "Media Rights", ("11",)),
        Line("sports_camps", "Sports Camps", ("16",)),
        Line("endowment", "Athletics Restricted Endowment & Investment Income", ("17",)),
        Line("third_party_comp", "Third-Party Compensation & Benefits", ("10",)),
        Line("other", "Other Operating Revenue", ("18",)),
    ),
)

NCAA_CONFERENCE = Page(
    key="ncaa_conference",
    sheet_name="NCAA",
    title="NCAA / Conference Revenue",
    stub_header="NCAA / Conference",
    lines=(
        Line(
            "ncaa_distributions",
            "NCAA Distributions",
            ("12A",),
            note="FRS 12A. Category 12 is the roll-up of 12A/12B/12C and is deliberately "
                 "not used here, so the three are not counted twice.",
        ),
        Line("ncaa_host", "NCAA Host Revenue", ("12B",)),
        Line("ncaa_postseason", "Post-Season Non-Football Revenue", ("12C",)),
        Line("conference_distributions", "Conference Distributions", ("13", "13A")),
        Line("postseason_football", "Post-Season Football Revenue", ("19",)),
        Line(
            "sports_sponsorship",
            "NCAA - Sports Sponsorship Fund",
            (),
            note="A component of the NCAA distribution a reclassifying institution does "
                 "not receive until full membership. Entered, not read from the report.",
            starts_at_zero=True,
        ),
        Line(
            "equivalencies_fund",
            "NCAA - Grants-in-Aid / Equivalencies Fund",
            (),
            note="As above - earned on awarded equivalencies once eligible.",
            starts_at_zero=True,
        ),
    ),
)

REVENUE_PAGES: tuple[Page, ...] = (
    INSTITUTIONAL_SUPPORT,
    TICKET_REVENUE,
    GAME_GUARANTEE,
    PROGRAM_REVENUE,
    NCAA_CONFERENCE,
)

#: What the revenue Totals page shows, one row per detail page. Ticket and
#: guarantee money rolls up as a single line each; institutional support is
#: broken out, because the share of the budget that is a subsidy is the
#: number a president reads this document for.
REVENUE_SUMMARY_ROWS: tuple[tuple[str, str], ...] = (
    ("institutional_support:student_fees", "Student Athletic Fees"),
    ("institutional_support:direct_institutional", "Direct Institutional Support"),
    ("institutional_support:indirect_institutional", "Indirect Institutional Support"),
    ("institutional_support:government", "State / Government Support"),
    ("institutional_support:transfers_out", "Less - Transfers to Institution"),
    ("ticket_revenue:*", "Ticket Revenue"),
    ("game_guarantee:*", "Game Guarantee"),
    ("program_revenue:*", "Program Revenue"),
    ("ncaa_conference:*", "NCAA / Conference"),
)


# ==========================================================================
# EXPENSE
# ==========================================================================

#: The per-sport operating-expense categories that make up a sport's Sports
#: Operations line. Copied unchanged from the NTR calculator, which copied it
#: from the benchmarking platform's Sport Operating Budget workbook: the
#: figure on this page has to be the same number the other two deliverables
#: report for the same sport, or CC contradicts itself in front of a client.
#:
#: Coaching pay (22/23) and administrative pay (24/25) are deliberately absent
#: - they are the Salaries page. Athletic student aid (20) is the Scholar
#: page. Everything institution-level is Admin Operating.
SPORT_OPERATING_CATEGORIES: tuple[str, ...] = (
    "21", "27", "28", "29", "30", "31", "35", "35A", "36",
    "37", "38", "39", "40", "41", "41B", "42", "42B", "43", "44",
)

#: Which of the House-settlement categories belong in that list took being
#: checked against real filings rather than reasoned about, and both
#: directions were wrong at first:
#:
#: * 41B and 42B are reported per sport and were missing. That dropped real
#:   money off the page - at Coastal Carolina FY25, 42B carries $189,346 of
#:   baseball expense; at Clemson, $189,798 of baseball plus $4,364 of men's
#:   soccer.
#: * 42A and 41A are institution-level only, with no per-sport table in any
#:   sampled filing, and 42A was wrongly listed here. Listing a category FRS
#:   does not report per sport is not harmless: the parser finds no table, so
#:   the category is "claimed" by a page that can never show it, and nothing
#:   else claims it either. That hid $358,380 of Coastal's FY25 expense
#:   completely - not double-counted, just absent. Both now sit on the Admin
#:   Operating page, which is where an institution-level figure belongs.
#:
#: Both faults were found by auditing every expense category of a real filing
#: against what the schema claims, which is what tests/test_workbooks.py's
#: tie-out tests now do on every run.

SCHOLARSHIPS = Page(
    key="scholarships",
    sheet_name="Scholar",
    title="Scholarships",
    stub_header="Scholarships",
    per_sport=True,
    total_label="Grand Total",
)

SPORTS_OPERATIONS = Page(
    key="sports_operations",
    sheet_name="Sports Operations",
    title="Sports Operations",
    stub_header="Sports Operations",
    per_sport=True,
    total_label="Total Amount",
)

ADMIN_OPERATING = Page(
    key="admin_operating",
    sheet_name="Admin Operating",
    title="Administrative Operations",
    stub_header="Administrative Operations",
    lines=(
        # --- Membership, the part that changes on reclassification --------
        Line("ncaa_dues", "NCAA Annual Dues", ("38",),
             note="FRS 38 covers all memberships and dues. It is shown here against NCAA "
                  "dues and the conference lines below carry the rest, so the base year "
                  "may need reallocating between these four rows by hand."),
        Line("new_conference_initiation", "New Conference Initiation Fee", (), starts_at_zero=True),
        Line("new_conference_dues", "New Conference Annual Dues", (), starts_at_zero=True),
        Line("current_conference_dues", "Current Conference Annual Dues", ()),
        Line("current_conference_exit", "Current Conference Exit Fees", (), starts_at_zero=True),
        Line("ncaa_application", "NCAA Application / Reclassification Fee", (), starts_at_zero=True),
        # --- The operating base ------------------------------------------
        Line("administrative", "Administrative", ("40",)),
        Line("fundraising_marketing", "Fund Raising, Marketing & Promotion", ("31",)),
        Line("game_guarantees_paid", "Guarantees Paid", ("21",)),
        Line("facilities_debt", "Athletic Facilities Debt Service, Leases & Rental", ("34",)),
        Line("facilities_maintenance", "Facilities Maintenance & Utilities", ("35A",)),
        Line("direct_overhead", "Direct Overhead & Administrative", ("35",)),
        Line("indirect_support", "Indirect Institutional Support", ("36",)),
        Line("medical", "Medical Expenses & Insurance", ("37",)),
        Line("meals", "Student-Athlete Meals (non-travel)", ("39",)),
        Line("spirit_groups", "Spirit Groups", ("33",)),
        Line("sports_camps", "Sports Camp Expenses", ("32",)),
        Line("severance", "Severance Payments", ("26",)),
        Line(
            "postseason_football",
            "Post-Season Football (Bowl) Expenses",
            ("41A",),
            note="FRS 41A, which is reported at institution level only - unlike 41 and 41B, "
                 "which are per-sport and sit in Football's operating budget instead.",
        ),
        Line(
            "postseason_ncaa",
            "NCAA Post-Season (non-football) Expenses",
            ("42A",),
            note="FRS 42A, institution level only - unlike 42 and 42B, which are per-sport.",
        ),
        Line(
            "nil_plan",
            "NIL / Revenue Share (planned)",
            (),
            note="Deliberately carries no FRS category. What the institution reported under "
                 "43 and 44 is per-sport and already in Sports Operations; claiming it here "
                 "as well would count it twice. This row is for the departmental NIL and "
                 "revenue-share commitment the scenario assumes going forward, which is a "
                 "decision rather than a reported figure - and at a reclassifying "
                 "institution it is usually the single largest new cost on the page.",
            starts_at_zero=True,
        ),
    ),
    total_label="Total Operations",
)

#: The administrative position scaffold, by department. CC's standard build
#: for a Division I department, which is what the pro forma is projecting
#: towards - not a list of who the institution employs today.
#:
#: The Salaries page writes every one of these with a blank base year and the
#: analyst fills in what exists; a position the institution does not have and
#: does not plan to add is deleted from the finished sheet. Starting from the
#: full scaffold rather than from the current staff is the point: the gap
#: between the two is the hiring plan the pro forma is there to cost.
ADMIN_DEPARTMENTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Administration", (
        "Director of Athletics",
        "Deputy / Executive Associate AD",
        "Senior Woman Administrator",
        "Administrative Assistant",
    )),
    ("Business Office", (
        "Associate AD - Business / Finance",
        "Business Manager",
    )),
    ("Internal Operations", (
        "Associate AD - Internal Operations",
        "Director of Facilities & Operations",
        "Facilities / Operations Coordinator",
        "Equipment Manager",
        "Facilities - GA",
    )),
    ("External Operations", (
        "Associate AD - External Operations",
        "Director of Development",
        "Assistant Director of Development",
        "Director of Marketing & Promotions",
        "Director of Ticketing",
        "Coordinator - Revenue Generation",
    )),
    ("Communications", (
        "Director of Athletics Communications",
        "Assistant Director of Athletics Communications",
        "Coordinator - Digital Media / Graphic Design",
        "Communications - GA",
    )),
    ("Compliance / Academics", (
        "Associate AD - Compliance",
        "Assistant Director - Compliance",
        "Associate AD - Academic Services",
        "Academic Services Coordinator",
        "Compliance / Academics - GA",
    )),
    ("Sports Medicine", (
        "Assistant AD - Sports Medicine",
        "Head Athletic Trainer",
        "Assistant Athletic Trainer #1",
        "Assistant Athletic Trainer #2",
        "Assistant Athletic Trainer #3",
        "Assistant Athletic Trainer #4",
    )),
    ("Strength & Conditioning", (
        "Director - Sports Performance",
        "Assistant Director - Sports Performance",
        "Strength & Conditioning - GA",
    )),
    ("Student-Athlete Welfare", (
        "Director of Student-Athlete Development",
        "Nutritionist",
        "Mental Health / Sport Psychologist",
    )),
)

#: The coaching position scaffold for one sport. Head coach, then assistants
#: to the sport's own staff limit, then graduate assistants - which is how
#: every one of CC's pro formas lays a sport out.
#:
#: Football is the only sport whose staff is large enough to need its own
#: shape; everything else takes the default and the analyst deletes the rows
#: it does not fill.
COACHING_STAFF_DEFAULT: tuple[str, ...] = (
    "Head Coach",
    "Assistant Coach #1",
    "Assistant Coach #2",
    "Assistant Coach - GA",
)

COACHING_STAFF_BY_SPORT: dict[str, tuple[str, ...]] = {
    "Football": (
        "Head Coach",
        "Offensive Coordinator",
        "Defensive Coordinator",
        "Assistant Coach #3",
        "Assistant Coach #4",
        "Assistant Coach #5",
        "Assistant Coach #6",
        "Assistant Coach #7",
        "Assistant Coach #8",
        "Assistant Coach #9",
        "Assistant Coach - GA #1",
        "Assistant Coach - GA #2",
        "Director of Football Operations",
    ),
    "Basketball": (
        "Head Coach",
        "Associate Head Coach",
        "Assistant Coach #2",
        "Assistant Coach #3",
        "Director of Basketball Operations",
    ),
    "Baseball": (
        "Head Coach",
        "Associate Head Coach",
        "Assistant Coach #2",
        "Assistant Coach - GA",
    ),
    "Softball": (
        "Head Coach",
        "Assistant Coach #1",
        "Assistant Coach #2",
        "Assistant Coach - GA",
    ),
    "Track and Field, X-Country": (
        "Head Coach",
        "Assistant Coach #1",
        "Assistant Coach #2",
        "Assistant Coach - GA",
    ),
}


def coaching_staff(sport: str) -> tuple[str, ...]:
    return COACHING_STAFF_BY_SPORT.get(sport, COACHING_STAFF_DEFAULT)


def sport_categories_without_admin_home() -> tuple[str, ...]:
    """Per-sport categories that no Admin Operating line claims.

    Every category in SPORT_OPERATING_CATEGORIES is reported twice in an FRS
    filing: once per sport, and once at institution level where the figure
    also carries what the form calls "Expenses Not Related to Specific
    Teams". Nine of them have an Admin Operating line, which takes that
    remainder (see _admin_remainder). The rest - recruiting, team travel,
    equipment, game expenses, the post-season and settlement categories - do
    not, and their remainder has nowhere to go.

    Left unclaimed it is simply lost: $1,016,778 at Clemson FY25 and
    $1,197,363 at Georgia Tech, mostly game expenses and equipment the
    institution runs centrally rather than charging to a sport. So the Sports
    Operations page carries one row for the lot, named as the form names it.

    One row rather than a line each because the alternative is ten more
    departmental lines that are zero at most institutions, and a page of
    zeroes is harder to read than a page with one honest residual on it.
    """
    claimed = {
        category
        for line in ADMIN_OPERATING.lines
        for category in line.frs_categories
    }
    return tuple(
        category
        for category in SPORT_OPERATING_CATEGORIES
        if category not in claimed
    )


SALARIES = Page(
    key="salaries",
    sheet_name="Salaries",
    title="Salaries",
    stub_header="Position",
)

EXPENSE_PAGES: tuple[Page, ...] = (
    SCHOLARSHIPS,
    SPORTS_OPERATIONS,
    ADMIN_OPERATING,
    SALARIES,
)

#: The expense Summary page, one row per detail page. The two salary rows
#: come off the same sheet at two different totals.
EXPENSE_SUMMARY_ROWS: tuple[tuple[str, str], ...] = (
    ("scholarships:*", "Scholarships"),
    ("sports_operations:*", "Sports Operations"),
    ("admin_operating:*", "Administrative Operations"),
    ("salaries:admin", "Administrative Salaries"),
    ("salaries:coaching", "Coaching Salaries"),
)

#: One-time and transition costs, shown on the expense Summary page under
#: its own header as a cash-expenditure block rather than inside the
#: operating total.
#:
#: They are kept out of the operating total deliberately. A reclassification
#: application fee is not an operating expense and including it makes the
#: year it falls in look structurally unaffordable; a board reading the two
#: together needs to see which costs recur and which are paid once.
TRANSITION_COST_LINES: tuple[Line, ...] = (
    Line(
        "ncaa_application", "NCAA Application / Reclassification Fee", (),
        starts_at_zero=True, recurring=False,
        note="Paid once. Enter it in the year it falls.",
    ),
    Line(
        "current_conference_exit", "Current Conference Exit Fees", (),
        starts_at_zero=True, recurring=False,
    ),
    Line(
        "new_conference_initiation", "New Conference Initiation Fee", (),
        starts_at_zero=True, recurring=False,
        note="Usually paid in instalments across the reclassification window - enter "
             "each year it is due, since the schedule is contractual rather than a trend.",
    ),
    # Dues recur, unlike everything else in this block, so an unentered year
    # carries forward from the year they start.
    Line("new_conference_dues", "New Conference Annual Dues", (), starts_at_zero=True),
    Line(
        "startup_capital", "Start-Up Capital & Facilities", (),
        starts_at_zero=True, recurring=False,
    ),
)


# ==========================================================================
# Lookups
# ==========================================================================

_PAGES_BY_KEY: dict[str, Page] = {
    page.key: page for page in (*REVENUE_PAGES, *EXPENSE_PAGES)
}


def page(key: str) -> Page:
    return _PAGES_BY_KEY[key]


def revenue_frs_categories() -> set[str]:
    """Every FRS revenue category the revenue book draws on.

    Used to check a parsed report against the schema: a category the report
    carries that no line claims is money going missing from the pro forma,
    and the builder says so rather than quietly dropping it.
    """
    claimed = {
        category
        for page_ in REVENUE_PAGES
        for line in page_.lines
        for category in line.frs_categories
    }
    # Per-sport pages claim the institution-level categories their sport rows
    # add up to, which no Line can carry because the rows are sports.
    return claimed | {"1", "7"}


def expense_frs_categories() -> set[str]:
    """Every FRS expense category the expense book draws on."""
    claimed = {
        category
        for page_ in EXPENSE_PAGES
        for line in page_.lines
        for category in line.frs_categories
    }
    return (
        claimed
        | set(SPORT_OPERATING_CATEGORIES)
        | {"20"}            # athletic student aid -> Scholar
        | {"22", "23"}      # coaching pay -> Salaries
        | {"24", "25"}      # administrative pay -> Salaries
    )
