"""The fixed NCAA master sport list shown in the app's sport-selection step,
plus the matching logic that maps a user's selection onto the sport names
that actually appear inside NCAA FRS reports.

Why a matching layer is needed rather than plain string equality: the same
sport is named differently in different tables of the *same* report. Real
examples confirmed across the sampled FY24/FY25 reports:

    Sports Sponsorship table:   "Track, Indoor"   "Track, Outdoor"   "Cross Country"
    Athlete Scholarships table: "Track and Field, X-Country"  (all three combined)
    Coaching Expenses table:    "Track and Field, X-Country"  (all three combined)
    Athletics Participation:    "Track, Indoor"   "Track, Outdoor"   "Cross Country"

So a user who ticks "Cross Country" in the app must still get the combined
"Track and Field, X-Country" row on the Scholarships and Salaries sheets -
otherwise that data would silently vanish from those workbooks. That's what
_COMBINED_SPORT_MEMBERS below encodes: a combined row is included if *any*
of the individual sports it covers was selected.

MASTER_SPORTS is a fixed list (the client chose this over auto-deriving the
list from the uploaded PDFs), so it deliberately includes sports that no
uploaded report may sponsor. Selecting a sport that appears in no report is
harmless - it simply produces no column/row anywhere, and the app reports
that back rather than failing.
"""

from __future__ import annotations

import re

#: Every sport on the NCAA Sports Sponsorship form, in the form's own order,
#: with indoor/outdoor track listed separately (the client's Total Athletic
#: Budget template explicitly notes these count as individual sports).
MASTER_SPORTS: tuple[str, ...] = (
    "Acrobatics and Tumbling",
    "Baseball",
    "Basketball",
    "Beach Volleyball",
    "Bowling",
    "Cross Country",
    "Equestrian",
    "Fencing",
    "Field Hockey",
    "Football",
    "Golf",
    "Gymnastics",
    "Ice Hockey",
    "Lacrosse",
    "Rifle",
    "Rowing",
    "Rugby",
    "Skiing",
    "Soccer",
    "Softball",
    "Stunt",
    "Swimming and Diving",
    "Tennis",
    "Track, Indoor",
    "Track, Outdoor",
    "Triathlon",
    "Volleyball",
    "Water Polo",
    "Wrestling",
)

#: A report row that covers several master-list sports at once -> the set of
#: master-list sports it covers. Keyed by normalized name.
_COMBINED_SPORT_MEMBERS: dict[str, frozenset[str]] = {
    "track and field x country": frozenset({"Cross Country", "Track, Indoor", "Track, Outdoor"}),
    "track and field xcountry": frozenset({"Cross Country", "Track, Indoor", "Track, Outdoor"}),
    "track and field cross country": frozenset({"Cross Country", "Track, Indoor", "Track, Outdoor"}),
    "track field x country": frozenset({"Cross Country", "Track, Indoor", "Track, Outdoor"}),
    "track and field": frozenset({"Track, Indoor", "Track, Outdoor"}),
}

#: Alternate spellings of a single master-list sport, keyed by normalized name.
_ALIASES: dict[str, str] = {
    "track indoor": "Track, Indoor",
    "indoor track": "Track, Indoor",
    "track and field indoor": "Track, Indoor",
    "track outdoor": "Track, Outdoor",
    "outdoor track": "Track, Outdoor",
    "track and field outdoor": "Track, Outdoor",
    "x country": "Cross Country",
    "xcountry": "Cross Country",
    "crosscountry": "Cross Country",
    "swimming diving": "Swimming and Diving",
    "swimming and diving": "Swimming and Diving",
    "beach volleyball": "Beach Volleyball",
    "sand volleyball": "Beach Volleyball",
    "acrobatics tumbling": "Acrobatics and Tumbling",
    "water polo": "Water Polo",
    "field hockey": "Field Hockey",
    "ice hockey": "Ice Hockey",
}


def normalize(name: str) -> str:
    """Lowercases and strips punctuation/extra whitespace so that
    "Track, Indoor", "Track Indoor" and "track  indoor" all compare equal.
    """
    cleaned = re.sub(r"[^a-z0-9\s]", " ", name.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _squash(normalized_name: str) -> str:
    """Normalized form with the spaces taken out as well.

    Some institutions' reports produce a PDF whose text extracts with no
    spaces between words at all - "Track and Field, X-Country" arrives as
    "TrackandField,X-Country", and partial variants like "Trackand
    Field,X-Country" turn up too. That's the same row, not an unknown sport,
    and now that an unrecognized name is dropped rather than carried through
    (see is_selected) failing to match it would lose a real sport's data.
    """
    return normalized_name.replace(" ", "")


_COMBINED_SPORT_MEMBERS_SQUASHED: dict[str, frozenset[str]] = {
    _squash(key): value for key, value in _COMBINED_SPORT_MEMBERS.items()
}
_ALIASES_SQUASHED: dict[str, str] = {_squash(key): value for key, value in _ALIASES.items()}
_MASTER_SQUASHED: dict[str, str] = {_squash(normalize(m)): m for m in MASTER_SPORTS}


def combined_members(report_sport_name: str) -> frozenset[str] | None:
    """The master-list sports a combined report row covers, or None if this
    name isn't one of the known combined rows.
    """
    normalized = normalize(report_sport_name)
    if normalized in _COMBINED_SPORT_MEMBERS:
        return _COMBINED_SPORT_MEMBERS[normalized]
    return _COMBINED_SPORT_MEMBERS_SQUASHED.get(_squash(normalized))


def is_selected(report_sport_name: str, selected_sports: set[str]) -> bool:
    """True if a sport row from an FRS report should be included, given the
    user's master-list selection.

    Handles three cases:
      1. Exact/alias match      - "Track, Indoor" <- user ticked "Track, Indoor"
      2. Combined-row match     - "Track and Field, X-Country" is kept if the
                                  user ticked ANY of Cross Country / Track
                                  Indoor / Track Outdoor (see module docstring)
      3. Unrecognized row name  - dropped.

    Case 3 used to be *kept*, on the reasoning that an unfamiliar name meant
    this matcher's alias table was incomplete and losing real data was worse
    than carrying an extra row. Measured against the report corpus that
    reasoning didn't hold: the names arriving here are not unlisted sports,
    they're artefacts of reading a PDF table - the aid table's own "Others"
    aggregate row, repeated column headers ("Sport Athletic", "Equivalency"),
    page furniture ("NCAA Membership Financial"), and two real sports whose
    rows got glued together ("Basketball Beach Volleyball"). None of them is
    a sport, none can be ticked on the selection page, and a workbook column
    headed "Sport Athletic" is worse than no column at all.

    Genuinely losing data to this is now a parser bug to fix at the source,
    not something to paper over here - so the callers report what was
    dropped rather than letting it through silently.
    """
    if not selected_sports:
        return False

    members = combined_members(report_sport_name)
    if members is not None:
        return bool(members & selected_sports)

    canonical = canonical_name(report_sport_name)
    if canonical is not None:
        return canonical in selected_sports

    return False  # case 3 - see docstring


def canonical_name(report_sport_name: str) -> str | None:
    """Maps a report's sport name onto its MASTER_SPORTS entry, or None if
    it isn't recognized (an unknown name, or a combined multi-sport row).
    """
    normalized = normalize(report_sport_name)
    if normalized in _ALIASES:
        return _ALIASES[normalized]
    for master in MASTER_SPORTS:
        if normalize(master) == normalized:
            return master
    # Space-removed fallback, for reports whose PDFs extract without spaces
    # between words - see _squash.
    squashed = _squash(normalized)
    if squashed in _ALIASES_SQUASHED:
        return _ALIASES_SQUASHED[squashed]
    return _MASTER_SQUASHED.get(squashed)


#: The selection the app hands to the builders: one set of master-list sport
#: names per gender. A sport sponsored for both (Basketball, Golf, Soccer)
#: appears on both sides of the picker and can be selected independently -
#: a project might benchmark women's basketball without men's.
SelectionByGender = dict  # {"male": set[str], "female": set[str]}


def empty_selection() -> dict[str, set[str]]:
    return {"male": set(), "female": set()}


def all_selected() -> dict[str, set[str]]:
    return {"male": set(MASTER_SPORTS), "female": set(MASTER_SPORTS)}


def for_gender(selection, gender: str) -> set[str]:
    """Reads one side of a gendered selection.

    Tolerates a plain set as well, so any caller still passing the old
    ungendered form keeps working rather than failing obscurely.
    """
    if isinstance(selection, dict):
        return selection.get(gender, set())
    return set(selection)


#: The label a combined row is shown under, keyed by the set of master-list
#: sports it covers. Without this the sheet is headed with whichever spelling
#: the first institution's PDF happened to produce.
_COMBINED_DISPLAY_NAMES: dict[frozenset[str], str] = {
    frozenset({"Cross Country", "Track, Indoor", "Track, Outdoor"}): "Track and Field, X-Country",
    frozenset({"Track, Indoor", "Track, Outdoor"}): "Track and Field",
}


def display_name(report_sport_name: str) -> str | None:
    """The single label this sport is shown under across every institution,
    or None if it isn't a sport at all.

    Report rows for the same sport don't arrive spelled the same way: one
    institution's PDF gives "Track and Field, X-Country" and another's
    "Trackand Field,X-Country". Keying a sheet's columns on the raw text puts
    those in two different columns, each populated for only the schools that
    spell it that way - which on a 50-institution project reads as two
    half-empty columns for one sport. Everything is mapped through here so a
    sport occupies exactly one column, headed by its master-list name.
    """
    members = combined_members(report_sport_name)
    if members is not None:
        return _COMBINED_DISPLAY_NAMES.get(members, report_sport_name)
    return canonical_name(report_sport_name)


def is_recognized(report_sport_name: str) -> bool:
    """False for a sport name this module can't place on the master list -
    used by the app to warn that a row was included by fallback rather than
    by a confident match.
    """
    return combined_members(report_sport_name) is not None or canonical_name(report_sport_name) is not None


def form_order_key(sport: str) -> tuple[int, str]:
    """Sorts sports into the order the NCAA sponsorship form lists them.

    A combined row sits where the first of its members does - so "Track and
    Field, X-Country" lands at Cross Country's position rather than at the
    end under T - and anything unrecognized falls to the end alphabetically.

    Used by the pro forma pages, which list sports down the rows rather than
    across the columns. Alphabetical order would be defensible on its own,
    but every other deliverable CC produces from these reports is in form
    order, and a client reading a pro forma beside a benchmarking workbook
    should find the sports in the same places.
    """
    members = combined_members(sport)
    candidates = sorted(members) if members else [canonical_name(sport) or sport]
    for candidate in candidates:
        if candidate in MASTER_SPORTS:
            return (MASTER_SPORTS.index(candidate), sport)
    return (len(MASTER_SPORTS), sport)
