"""Collegiate Consulting - Pro Forma Builder.

An internal web tool behind a shared team password: upload an institution's
NCAA FRS filings, set the horizon and the growth assumptions, pick the
sports, fill in the salary scaffold, and download the revenue and expense
pro formas as a pair.

Built to the same shape as the Net Tuition Revenue Calculator and the FRS
Workbook Builder - same sign-in, same branding, same step tracker, same
"here is what we could not read" honesty at the end - so an analyst who can
drive one can drive all three.

The password is read from Streamlit's secrets and never lives in this
repository - see pfa/web/auth.py and .streamlit/secrets.toml.example.

Run locally:      streamlit run app.py
Deploy:           see README.md (Streamlit Community Cloud)
"""

from __future__ import annotations

import io
import math
import tempfile
import zipfile
from pathlib import Path

import streamlit as st

from pfa.builders import checks
from pfa.builders.workbooks import Plan, assemble, build
from pfa.extraction import salaries as salary_input
from pfa.extraction.frs import build_seed, load_report
from pfa.model import schema
from pfa.model.proforma import DEFAULT_PAGE_TIERS, DEFAULT_TIERS
from pfa.sports import MASTER_SPORTS, form_order_key
from pfa.web.auth import PasswordNotConfigured, configured_password, verify
from pfa.web.branding import CSS, LOGO_URL, footer_html, header_html

st.set_page_config(
    page_title="Pro Forma Builder | Collegiate Consulting",
    page_icon="https://collegiateconsulting.com/wp-content/uploads/2023/03/CC_DES_LOGO_BLACK-300x300.png",
    layout="centered",
)
st.markdown(CSS, unsafe_allow_html=True)

GENDER_LABEL = {"male": "Men's", "female": "Women's"}

_DEFAULTS: dict[str, object] = {
    "unlocked": False,
    "step": 1,
    "uploads": [],          # [(filename, bytes)]
    "reports": [],          # [ReportedYear]
    "seed": None,           # ScenarioSeed
    "scenario_name": "",
    "entered_years": 1,
    "projected_years": 4,
    "benefits_rate": 30,
    "tier_rates": {tier.key: tier.rate for tier in DEFAULT_TIERS},
    "line_tiers": dict(DEFAULT_PAGE_TIERS),
    # Nothing excluded to begin with: the reports define the sports, and an
    # analyst narrows from there rather than building the list up.
    "excluded_sports": set(),
    "additions": set(),
    # Salaries. "skip" leaves the scaffold blank for the analyst to fill in
    # Excel, which is what the tool did before this step existed and is
    # still the right answer when the institution has not sent its payroll.
    "salary_mode": "skip",
    "salary_rows": None,       # [SourceRow] parsed from the upload
    "salary_review": None,     # [dict] the editable review table
    "salary_source": "",       # the filename it came from
    "salary_sheets": [],       # worksheets that could hold the salary list
    "salary_sheet": "",        # the one chosen
    "salary_error": "",
    "manual_salaries": {},     # slot key -> base-year figure
    "results": None,        # (zip bytes, filenames, warnings)
}
def _fresh(default: object) -> object:
    """A copy of a default, so the dict in _DEFAULTS is never the live one.

    Without this, resetting a project hands back the same dict or set object
    the last project spent its life mutating, and the "new" project starts
    with the old one's salary grid in it.
    """
    if isinstance(default, (dict, set, list)):
        return type(default)(default)
    return default


for key, default in _DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = _fresh(default)


def goto(step: int) -> None:
    st.session_state.step = step
    st.rerun()


def reset_project() -> None:
    for key in (
        "uploads", "reports", "seed", "scenario_name", "results",
        "salary_mode", "salary_rows", "salary_review", "salary_source",
        "salary_sheets", "salary_sheet", "salary_error", "manual_salaries",
    ):
        st.session_state[key] = _fresh(_DEFAULTS[key])
    st.session_state.excluded_sports = set()
    st.session_state.additions = set()
    # The sport checkboxes are keyed widgets holding their own state, so
    # clearing the sets above doesn't untick them - without this, a new
    # project comes back with the last one's choices still made.
    for gender in GENDER_LABEL:
        for sport in MASTER_SPORTS:
            st.session_state.pop(f"add_{gender}_{sport}", None)
            st.session_state.pop(f"keep_{gender}_{sport}", None)
    # Same for the two salary grids, which hold their edits under their keys.
    st.session_state.pop("salary_editor", None)
    st.session_state.pop("manual_salary_editor", None)
    st.session_state.step = 2


# ---------------------------------------------------------------------------
# Step 1 - sign in
# ---------------------------------------------------------------------------


def render_sign_in() -> None:
    st.image(LOGO_URL, width=260)
    st.markdown(header_html("Internal platform"), unsafe_allow_html=True)
    st.title("Pro forma builder")
    st.write(
        "Turn an institution's NCAA Financial Reporting System filings into the revenue "
        "and expense pro formas, with the reported years filled in and the projection "
        "running off assumptions you set on the page."
    )

    try:
        expected = configured_password(st.secrets)
    except PasswordNotConfigured as problem:
        # Fails closed: without a configured password nobody gets in, and the
        # message is aimed at whoever administers the app rather than at the
        # person being turned away.
        st.error(
            f"This platform isn't available right now — {problem} "
            "Set it in the app's Settings → Secrets on Streamlit Cloud.",
            icon="🔒",
        )
        st.markdown(footer_html(), unsafe_allow_html=True)
        return

    with st.form("sign_in"):
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary")

    if submitted:
        if verify(password, expected):
            st.session_state.unlocked = True
            goto(2)
        # The only message this screen ever shows, and the same one every
        # time however many attempts have been made. Nothing here counts
        # attempts or escalates: a message that changes as you retry tells
        # whoever is guessing that someone is watching, without doing
        # anything to stop them.
        st.error("Password Incorrect")

    st.markdown(footer_html(), unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Step 2 - upload
# ---------------------------------------------------------------------------


def render_upload() -> None:
    st.header("Step 1 · Upload the institution's FRS filings")
    st.write(
        "One PDF per fiscal year, for the **same** institution. Upload two or three: "
        "each becomes a reported column, and a board judging a six-year projection wants "
        "to see the years behind it."
    )

    files = st.file_uploader(
        "NCAA FRS PDF reports",
        type=["pdf"],
        accept_multiple_files=True,
        help="The most recent filing becomes the base year the projection runs from.",
    )

    if files:
        st.session_state.uploads = [(f.name, f.getvalue()) for f in files]
        st.success(f"{len(files)} file{'s' if len(files) != 1 else ''} ready.")
        for name, data in st.session_state.uploads:
            st.write(f"• {name}  ·  {len(data) / 1_000_000:.1f} MB")

    back, forward = st.columns(2)
    with back:
        if st.button("← Sign out"):
            for key, default in _DEFAULTS.items():
                st.session_state[key] = default
            st.rerun()
    with forward:
        if st.button("Read filings →", type="primary", disabled=not st.session_state.uploads):
            read_uploaded_reports()


def read_uploaded_reports() -> None:
    """Parses each upload once, up front.

    Doing it here rather than at build time means the next step can show real
    institution names and fiscal years instead of filenames, and an
    unreadable file is caught while the operator is still looking at the
    uploader rather than halfway through generating a workbook.
    """
    uploads = st.session_state.uploads
    parsed: list = []
    failures: list[tuple[str, str]] = []
    progress = st.progress(0.0, text="Reading filings…")

    with tempfile.TemporaryDirectory() as tmpdir:
        for index, (name, data) in enumerate(uploads, start=1):
            progress.progress((index - 1) / len(uploads), text=f"Reading {name}…")
            path = Path(tmpdir) / name
            path.write_bytes(data)
            try:
                report = load_report(path)
                # The temp directory disappears; keep the display name so the
                # source notes in the finished workbook can cite the file.
                report.path = Path(name)
                parsed.append(report)
            except Exception as exc:  # noqa: BLE001 - reported, not raised
                failures.append((name, str(exc)))
    progress.empty()

    for name, message in failures:
        st.error(
            f"**{name}** could not be read as an NCAA FRS report — skipped.\n\n`{message}`"
        )

    if not parsed:
        st.error("None of the uploaded files could be read. Check they're NCAA FRS PDFs.")
        return

    st.session_state.reports = parsed
    st.session_state.seed = build_seed(parsed)
    goto(3)


# ---------------------------------------------------------------------------
# Step 3 - the scenario
# ---------------------------------------------------------------------------


def render_scenario() -> None:
    seed = st.session_state.seed
    st.header("Step 2 · The scenario")

    st.write(
        f"**{seed.institution}** — reported years "
        f"{', '.join(seed.reported_labels)}, base year **{seed.base.label}**."
    )
    for warning in seed.warnings:
        st.warning(warning, icon="⚠️")

    st.subheader("What is being projected")
    st.write(
        "Appears in every sheet title and in the header of each summary — usually the "
        "conference or division the institution is moving to."
    )
    st.session_state.scenario_name = st.text_input(
        "Scenario name",
        value=st.session_state.scenario_name,
        placeholder="e.g. ASUN, SoCon, or Division I",
        label_visibility="collapsed",
    ).strip()
    if not st.session_state.scenario_name:
        st.warning(
            "Enter a scenario name so the workbooks don't ship with an unlabelled summary.",
            icon="⚠️",
        )

    st.divider()
    st.subheader("Horizon")
    entered, projected = st.columns(2)
    with entered:
        st.session_state.entered_years = st.number_input(
            "Years you'll enter by hand", min_value=0, max_value=3,
            value=st.session_state.entered_years,
            help=(
                "The years between the last filing and the projection — usually one, the "
                "year the institution is budgeting now. These are seeded with the escalated "
                "figure so the model stays whole, and you overwrite them in the workbook."
            ),
        )
    with projected:
        st.session_state.projected_years = st.number_input(
            "Projected years", min_value=1, max_value=10,
            value=st.session_state.projected_years,
            help="Escalated off the year to the left at the rates below.",
        )

    horizon = seed.horizon(
        entered=st.session_state.entered_years,
        projected=st.session_state.projected_years,
    )
    st.caption(
        "Columns: "
        + " · ".join(
            f"**{year.label}**" if year.reported else
            (f"_{year.label}_" if year.entered else year.label)
            for year in horizon.years
        )
        + "  —  **bold** as filed, _italic_ entered by you, plain projected."
    )

    st.divider()
    st.subheader("Escalation assumptions")
    st.write(
        "Each line of each page escalates on one of these tiers. They are written into "
        "the workbook as editable cells above every table, so these are a starting point, "
        "not a commitment — changing one in Excel moves every line on it."
    )
    columns = st.columns(len(DEFAULT_TIERS))
    for column, tier in zip(columns, DEFAULT_TIERS):
        with column:
            st.session_state.tier_rates[tier.key] = st.number_input(
                tier.label,
                min_value=0.0, max_value=100.0, step=0.5,
                value=float(st.session_state.tier_rates[tier.key] * 100),
                key=f"tier_{tier.key}",
            ) / 100

    st.divider()
    st.subheader("Benefits load")
    st.session_state.benefits_rate = st.number_input(
        "Employer benefits as a share of salary (%)",
        min_value=0.0, max_value=60.0, step=0.25,
        value=float(st.session_state.benefits_rate),
        help=(
            "Applied to both salary blocks and stated on the row, so it can be argued "
            "with. The client's own pro formas use 30–32%."
        ),
    )

    back, forward = st.columns(2)
    with back:
        if st.button("← Back"):
            goto(2)
    with forward:
        if st.button(
            "Choose sports →", type="primary",
            disabled=not st.session_state.scenario_name,
        ):
            goto(4)


# ---------------------------------------------------------------------------
# Step 4 - sports
# ---------------------------------------------------------------------------


def render_sports() -> None:
    seed = st.session_state.seed
    st.header("Step 3 · Sports")
    st.write(
        "The filings carry these sports. Untick one to leave it off the Scholar, Sports "
        "Operations and coaching pages."
    )

    carried = sorted(
        {(plan.gender, plan.sport) for plan in seed.sports},
        key=lambda pair: (pair[0] != "male", form_order_key(pair[1])),
    )
    if not carried:
        st.error(
            "The filings carried no per-sport figures at all. The pro formas will build "
            "with empty sport pages — check the uploads are complete FRS reports."
        )

    for gender in ("male", "female"):
        sports = [sport for side, sport in carried if side == gender]
        if not sports:
            continue
        st.markdown(f"**{GENDER_LABEL[gender]} sports**")
        for row in _chunk(sports, 3):
            for column, sport in zip(st.columns(3), row):
                with column:
                    keep = st.checkbox(
                        sport,
                        value=(gender, sport) not in st.session_state.excluded_sports,
                        key=f"keep_{gender}_{sport}",
                    )
                    if keep:
                        st.session_state.excluded_sports.discard((gender, sport))
                    else:
                        st.session_state.excluded_sports.add((gender, sport))

    st.divider()
    st.subheader("Sports being added")
    st.write(
        "Sports the institution does not sponsor today but will under this scenario. "
        "They get a row with zeroed reported years, a note on the page saying the figures "
        "are projections rather than reported, and a seeded scholarship cost based on what "
        "an equivalency costs here."
    )
    with st.expander("Add sports"):
        for gender in ("male", "female"):
            existing = {sport for side, sport in carried if side == gender}
            available = [sport for sport in MASTER_SPORTS if sport not in existing]
            st.markdown(f"**{GENDER_LABEL[gender]}**")
            for row in _chunk(available, 3):
                for column, sport in zip(st.columns(3), row):
                    with column:
                        if st.checkbox(
                            sport,
                            value=(gender, sport) in st.session_state.additions,
                            key=f"add_{gender}_{sport}",
                        ):
                            st.session_state.additions.add((gender, sport))
                        else:
                            st.session_state.additions.discard((gender, sport))

    if st.session_state.additions:
        st.info(
            "Adding: "
            + ", ".join(
                f"{GENDER_LABEL[gender]} {sport}"
                for gender, sport in sorted(st.session_state.additions)
            ),
            icon="➕",
        )

    back, forward = st.columns(2)
    with back:
        if st.button("← Back"):
            goto(3)
    with forward:
        if st.button("Salaries →", type="primary"):
            # The sport list decides which coaching rows exist, so anything
            # already matched against the old list is stale.
            st.session_state.salary_review = None
            goto(5)


def _chunk(items: list, size: int) -> list[list]:
    return [items[index : index + size] for index in range(0, len(items), size)]


# ---------------------------------------------------------------------------
# Step 5 - salaries
# ---------------------------------------------------------------------------

SKIP = "— leave blank —"


def selected_sports() -> list[tuple[str, str]]:
    """The (gender, sport) pairs the workbook will carry, in page order."""
    seed = st.session_state.seed
    carried = {(plan.gender, plan.sport) for plan in seed.sports}
    chosen = (carried - set(st.session_state.excluded_sports)) | set(
        st.session_state.additions
    )
    return sorted(chosen, key=lambda pair: (pair[0] != "male", form_order_key(pair[1])))


def render_salaries() -> None:
    seed = st.session_state.seed
    base = seed.base.label
    slots = salary_input.all_slots(selected_sports())

    st.header("Step 4 · Salaries")
    st.write(
        "FRS reports one coaching total per sport and one support-staff total for the "
        f"department — it never reports positions. This fills in **{base}** against the "
        "scaffold; every later year escalates off it. Leave it blank and the rows go out "
        "empty for someone to fill in Excel, which is a perfectly good answer when the "
        "institution has not sent its payroll yet."
    )
    _reported_totals(seed, base)

    mode = st.radio(
        "How are the salaries coming in?",
        options=("skip", "upload", "manual"),
        format_func=lambda key: {
            "skip": "Leave the scaffold blank",
            "upload": "Upload the institution's salary file",
            "manual": "Type them in here",
        }[key],
        index=("skip", "upload", "manual").index(st.session_state.salary_mode),
        horizontal=True,
    )
    st.session_state.salary_mode = mode

    if mode == "upload":
        _render_salary_upload(slots, base)
    elif mode == "manual":
        _render_salary_manual(slots, base)

    st.divider()
    back, forward = st.columns(2)
    with back:
        if st.button("← Back"):
            goto(4)
    with forward:
        if st.button("Build pro formas →", type="primary"):
            run_build()


def _reported_totals(seed, base: str) -> None:
    """What the filing says the two blocks cost, so the analyst can see how
    close the scaffold is getting.

    This is the figure the deleted reconciliation row used to carry, and it
    belongs here rather than on the sheet - it is only useful to the person
    doing the filling in, and this is the screen where they do it.
    """
    reported = seed.reconciliation if hasattr(seed, "reconciliation") else {}
    admin = (reported.get("admin_salaries") or {}).get(base)
    coaching = (reported.get("coaching_salaries") or {}).get(base)
    if admin is None and coaching is None:
        return
    left, right = st.columns(2)
    with left:
        st.metric(
            f"Support staff, {base} (FRS 24 + 25)",
            f"${admin:,.0f}" if admin is not None else "not reported",
        )
    with right:
        st.metric(
            f"Coaching, {base} (FRS 22 + 23)",
            f"${coaching:,.0f}" if coaching is not None else "not reported",
        )
    st.caption(
        "These are totals including benefits as the institution filed them. The scaffold "
        "takes salary only — the workbook adds the benefits load on top — so expect what "
        "you enter to come in under these."
    )


def _render_salary_upload(slots: list, base: str) -> None:
    upload = st.file_uploader(
        "Salary file",
        type=["xlsx", "xlsm", "csv", "tsv", "txt"],
        help=(
            "Any export with a position column and a salary column. A Sport column is "
            "used where there is one; it is what tells six identical “Head Coach” rows "
            "apart."
        ),
    )
    if upload is not None and upload.name != st.session_state.salary_source:
        st.session_state.salary_source = upload.name
        st.session_state.salary_sheets = salary_input.salary_sheets(
            upload.name, upload.getvalue()
        )
        st.session_state.salary_sheet = ""
        st.session_state.salary_review = None
        st.session_state.salary_error = ""

    if upload is not None:
        _choose_sheet(upload, slots)

    if st.session_state.salary_error:
        st.error(st.session_state.salary_error, icon="⚠️")
        return
    if not st.session_state.salary_review:
        return

    _render_salary_review(slots, base)


def _choose_sheet(upload, slots: list) -> None:
    """Which worksheet to read, when there is more than one.

    A conference salary survey is one workbook with a sheet per member
    institution. Taking the first one silently builds a pro forma out of a
    rival's payroll, and every figure on it looks perfectly reasonable.
    """
    sheets = st.session_state.salary_sheets
    chosen = None
    if len(sheets) > 1:
        st.info(
            f"{upload.name} has {len(sheets)} sheets — this looks like a conference "
            "survey with one per institution. Pick the one you are building.",
            icon="📑",
        )
        chosen = st.selectbox(
            "Worksheet", options=sheets,
            index=sheets.index(st.session_state.salary_sheet)
            if st.session_state.salary_sheet in sheets else 0,
        )
    elif sheets:
        chosen = sheets[0]

    if chosen != st.session_state.salary_sheet or st.session_state.salary_review is None:
        st.session_state.salary_sheet = chosen or ""
        _read_salary_upload(upload, slots, chosen)


def _read_salary_upload(upload, slots: list, sheet: str | None) -> None:
    st.session_state.salary_error = ""
    st.session_state.salary_review = None
    try:
        rows = salary_input.read_salary_file(upload.name, upload.getvalue(), sheet=sheet)
    except salary_input.SalaryFileError as error:
        st.session_state.salary_error = str(error)
        return

    by_key = {slot.key: slot for slot in slots}
    assignments = salary_input.match(rows, slots)
    st.session_state.salary_rows = rows
    # The Sport column is folded into the title rather than given a column
    # of its own. Five columns do not fit the page's width, and the one that
    # gets pushed off the right is whichever matters most to whoever is
    # reading - the flag, or the dropdown they came here to use.
    st.session_state.salary_review = [
        {
            "Match": _confidence_label(assignment),
            "From the file": _source_label(assignment.row),
            "Salary": float(assignment.row.salary),
            "Goes to": (
                by_key[assignment.slot_key].display if assignment.slot_key else SKIP
            ),
        }
        for assignment in assignments
    ]


def _source_label(row) -> str:
    return f"{row.title} · {row.sport}" if row.sport else row.title


def _confidence_label(assignment) -> str:
    return {
        "strong": "✓ clear",
        "likely": "~ check",
        "weak": "? check",
        "unmatched": "— pick one",
    }[assignment.confidence]


def _render_salary_review(slots: list, base: str) -> None:
    """The review table. This is the point of the whole step.

    Nothing is taken on trust: every row shows what it matched and can be
    sent anywhere else, and a row the matcher was not confident about comes
    in unassigned rather than guessed at. A wrong guess here is a $190,000
    Director of Athletics sitting on the Nutritionist row in a workbook that
    adds up perfectly.
    """
    review = st.session_state.salary_review
    unmatched = sum(1 for entry in review if entry["Goes to"] == SKIP)
    checks_needed = sum(1 for entry in review if entry["Match"] != "✓ clear")

    source = st.session_state.salary_source
    if st.session_state.salary_sheet and len(st.session_state.salary_sheets) > 1:
        source = f"{source} · {st.session_state.salary_sheet}"
    st.subheader(f"Check the matches · {len(review)} row(s) from {source}")
    if checks_needed:
        st.warning(
            f"{checks_needed} row(s) need a look — {unmatched} could not be matched at "
            "all. Titles never line up exactly, so the ones marked “check” are a guess "
            "or a blank, not a result. Everything here can be re-pointed.",
            icon="👀",
        )
    else:
        st.success("Every row matched cleanly. Worth a glance anyway.", icon="✅")

    options = [SKIP] + [slot.display for slot in slots]
    edited = st.data_editor(
        review,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        column_config={
            "Match": st.column_config.TextColumn(disabled=True, width="small"),
            "From the file": st.column_config.TextColumn(disabled=True, width="medium"),
            "Salary": st.column_config.NumberColumn(format="$%d", width="small"),
            "Goes to": st.column_config.SelectboxColumn(
                options=options, required=False, width="large"
            ),
        },
        key="salary_editor",
    )
    st.session_state.salary_review = list(edited)

    duplicates = _duplicate_targets(st.session_state.salary_review)
    if duplicates:
        st.info(
            "Two or more rows point at the same position — they will be added together, "
            "which is right for a base salary and a supplement listed separately: "
            + ", ".join(sorted(duplicates)),
            icon="➕",
        )
    entered = sum(
        float(entry["Salary"])
        for entry in st.session_state.salary_review
        if entry["Goes to"] != SKIP and _entered(entry["Salary"])
    )
    st.caption(f"**${entered:,.0f}** of salary will be written into {base}.")


def _duplicate_targets(review: list[dict]) -> set[str]:
    seen: set[str] = set()
    twice: set[str] = set()
    for entry in review:
        target = entry["Goes to"]
        if target == SKIP:
            continue
        if target in seen:
            twice.add(target)
        seen.add(target)
    return twice


def _render_salary_manual(slots: list, base: str) -> None:
    """The typed grid: the scaffold itself, with a column to fill in.

    No matching needed here - the rows *are* the scaffold, so there is
    nothing to get wrong. Which is why it is worth offering even though the
    upload path is faster: an analyst with the figures in front of them on
    paper should not have to build a spreadsheet to import one.
    """
    st.subheader(f"Enter {base} salaries")
    st.write(
        "One row per position the workbook carries. Leave a position blank and its row "
        "goes out blank — delete the ones the institution does not fill when you get to "
        "Excel."
    )
    stored = st.session_state.manual_salaries
    # NaN rather than None for an empty cell. A column of Nones is inferred
    # as text and the grid prints the word "None" down all hundred and fifty
    # rows; NaN keeps the column numeric and renders as the blank it is.
    grid = [
        {
            "Block": slot.group,
            "Position": slot.label,
            "Salary": float(stored.get(slot.key, math.nan)),
        }
        for slot in slots
    ]
    edited = st.data_editor(
        grid,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        height=520,
        column_config={
            "Block": st.column_config.TextColumn(disabled=True, width="medium"),
            "Position": st.column_config.TextColumn(disabled=True, width="large"),
            "Salary": st.column_config.NumberColumn(format="$%d", min_value=0),
        },
        key="manual_salary_editor",
    )
    st.session_state.manual_salaries = {
        slot.key: float(entry["Salary"])
        for slot, entry in zip(slots, edited)
        if _entered(entry.get("Salary"))
    }
    entered = sum(st.session_state.manual_salaries.values())
    st.caption(
        f"**${entered:,.0f}** across "
        f"{len(st.session_state.manual_salaries)} of {len(slots)} positions."
    )


def _entered(value) -> bool:
    """A cell the analyst actually filled in.

    None is an untouched cell, NaN is a cleared one, and 0 is a position
    the institution does not fund - all three mean "no figure", and none of
    them should take up a scaffold row.
    """
    if value is None:
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0


def salary_figures(slots: list, base: str):
    """(admin, coaching) figures for the Plan, from whichever path was used."""
    mode = st.session_state.salary_mode
    if mode == "manual":
        return salary_input.manual_figures(
            list(st.session_state.manual_salaries.items()), slots, base
        )
    if mode == "upload" and st.session_state.salary_review:
        by_display = {slot.display: slot.key for slot in slots}
        assignments = [
            salary_input.Assignment(
                row=salary_input.SourceRow(
                    index=index,
                    title=entry["From the file"],
                    salary=float(entry["Salary"] or 0),
                ),
                slot_key=by_display.get(entry["Goes to"]),
                score=1.0,
            )
            for index, entry in enumerate(st.session_state.salary_review)
            if entry["Goes to"] != SKIP and _entered(entry["Salary"])
        ]
        return salary_input.to_plan_figures(assignments, slots, base)
    return {}, {}


# ---------------------------------------------------------------------------
# Step 5 - build and download
# ---------------------------------------------------------------------------


def run_build() -> None:
    seed = st.session_state.seed
    tiers = tuple(
        tier.__class__(tier.key, tier.label, st.session_state.tier_rates[tier.key])
        for tier in DEFAULT_TIERS
    )
    carried = {(plan.gender, plan.sport) for plan in seed.sports}
    slots = salary_input.all_slots(selected_sports())
    admin_salaries, coaching_salaries = salary_figures(slots, seed.base.label)
    plan = Plan(
        scenario_name=st.session_state.scenario_name,
        entered_years=int(st.session_state.entered_years),
        projected_years=int(st.session_state.projected_years),
        benefits_rate=st.session_state.benefits_rate / 100,
        tiers=tiers,
        line_tiers=dict(st.session_state.line_tiers),
        sports=carried - set(st.session_state.excluded_sports),
        additions=frozenset(st.session_state.additions),
        admin_salaries=admin_salaries,
        coaching_salaries=coaching_salaries,
    )

    with st.spinner("Building both workbooks…"):
        scenario = assemble(seed, plan)
        with tempfile.TemporaryDirectory() as tmpdir:
            result = build(scenario, Path(tmpdir))
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in (result.revenue, result.expense):
                    archive.write(path, arcname=path.name)
            files = [
                (path.name, path.read_bytes()) for path in (result.revenue, result.expense)
            ]

    st.session_state.results = (buffer.getvalue(), files, result.warnings)
    goto(6)


def render_results() -> None:
    zipped, files, warnings = st.session_state.results
    seed = st.session_state.seed
    st.header("Step 4 · Download")
    st.success(f"Both pro formas built for **{seed.institution}**.")

    st.download_button(
        "Download both workbooks (ZIP)",
        data=zipped,
        file_name=f"{seed.institution} - Pro Formas - {st.session_state.scenario_name}.zip",
        mime="application/zip",
        type="primary",
    )
    st.write("Or one at a time:")
    for name, data in files:
        st.download_button(
            name, data=data, file_name=name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"download_{name}",
        )

    st.divider()

    # Two classes of message, kept apart on purpose. A tie-out failure says
    # the workbook does not add up to the filing it was built from, which is
    # the one thing here that makes the deliverable wrong rather than
    # unfinished. It used to be a red row on the sheet itself; now it is
    # this, and burying it in a list of sixty blank-scaffold notes would be
    # the same as deleting it.
    failures = [warning for warning in warnings if isinstance(warning, checks.TieOut)]
    notes = [warning for warning in warnings if not isinstance(warning, checks.TieOut)]

    if failures:
        st.subheader("The base year does not tie")
        st.write(
            "Check these before the workbook goes anywhere. Either a figure the filing "
            "carried has not landed on the page, or the filing disagrees with itself — "
            "and the second happens often enough to be worth looking at rather than "
            "assuming."
        )
        for failure in failures:
            st.error(failure, icon="🚩")

    st.subheader("What still needs filling in")
    if notes:
        st.write(
            f"{len(notes)} note(s). None of these stopped the build — each one names a "
            "row that is on the page and blank, which is the honest rendering of a figure "
            "the filings didn't carry. Work through them in Excel before sending."
        )
        for note in notes:
            st.write(f"• {note}")
    else:
        st.write("Nothing — every row the schema defines has a figure.")

    st.divider()
    st.caption(
        "Reported years come straight from the uploaded filings. A blank cell means the "
        "filing didn't carry that figure — deliberately left blank rather than zeroed, "
        "since a real $0 and “not reported” aren't the same thing. The Salaries page's "
        "positions are Collegiate Consulting's standard build, not reported detail: FRS "
        "gives one coaching total per sport and one administrative total, and both are "
        "shown on the page as reconciliation rows. Spot-check the variance rows before "
        "this goes to a client."
    )

    if st.button("Start another pro forma"):
        reset_project()
        st.rerun()


# ---------------------------------------------------------------------------
# Shell
# ---------------------------------------------------------------------------


def render_sidebar() -> None:
    with st.sidebar:
        st.write("**Signed in**")
        if st.button("Sign out"):
            for key, default in _DEFAULTS.items():
                st.session_state[key] = _fresh(default)
            st.rerun()
        st.divider()
        labels = ["Upload filings", "Scenario", "Sports", "Salaries", "Download"]
        for index, label in enumerate(labels, start=2):
            marker = (
                "▶" if st.session_state.step == index
                else ("✓" if st.session_state.step > index else "○")
            )
            st.write(f"{marker}  {label}")
        st.divider()
        st.caption(
            "Builds: **Revenue** — Totals · "
            + " · ".join(page.sheet_name for page in schema.REVENUE_PAGES)
        )
        st.caption(
            "**Expense** — Summary · "
            + " · ".join(page.sheet_name for page in schema.EXPENSE_PAGES)
        )


def main() -> None:
    if not st.session_state.unlocked:
        render_sign_in()
        return

    render_sidebar()
    step = st.session_state.step
    if step == 2:
        render_upload()
    elif step == 3:
        render_scenario()
    elif step == 4:
        render_sports()
    elif step == 5:
        render_salaries()
    elif step == 6:
        render_results()
    else:
        goto(2)
    st.markdown(footer_html(), unsafe_allow_html=True)


main()
