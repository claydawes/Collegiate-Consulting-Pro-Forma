"""Getting an institution's salary list into the workbook's position rows.

FRS gives one coaching total per sport and one support-staff total for the
department, and no positions at all. So the Salaries page writes CC's
standard Division I scaffold and somebody has to fill it in. This module is
the two ways of doing that without retyping a hundred figures: read the
institution's own salary file, or take a typed grid.

**The whole problem is that the titles never match.** The scaffold says
"Associate AD - External Operations"; the file says "Assoc. Athletic
Director, External Ops". It says "Head Coach" under a Sport column; the
scaffold says "Head Coach" under Men's Basketball. It says "Asst AT #2" and
"SID" and "S&C GA". A substring match gets perhaps half of these, and the
half it gets wrong it gets wrong *silently* - a $190,000 Director of
Athletics landing on the Nutritionist row produces a workbook that adds up
correctly and is nonsense.

So the matching here is deliberate about three things:

1. **Abbreviations are expanded before anything is compared.** A domain with
   "AD", "SWA", "SID", "ATC", "GA" and "Assoc." in every other title cannot
   be matched on raw strings.

2. **Rank is decisive and sport is a gate.** "Head Coach" and "Assistant
   Coach" are one token apart and never the same person, so disagreeing
   ranks are penalised hard. A row whose title or Sport column names a sport
   can only land on that sport's coaching rows - which is what stops six
   identical "Head Coach" rows piling onto whichever sport sorts first.

3. **The assignment is one-to-one, best-first.** Three rows reading
   "Assistant Coach" fill #1, #2 and #3 rather than all three landing on the
   same slot and overwriting each other. Greedy over a sorted score list,
   each row and each slot used once.

And then it is all put in front of the analyst to confirm, because none of
the above is good enough to trust unreviewed. Every row shows what it
matched, how confident the match is, and a dropdown of every other slot it
could go to. A row matched weakly is left unassigned rather than guessed at:
an empty row reads as "to confirm", and a wrong row reads as a fact.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from pfa import sports
from pfa.model import schema

# --- What a salary line can land on ------------------------------------


@dataclass(frozen=True)
class Slot:
    """One position row on the Salaries page."""

    key: str
    #: The position as the sheet labels it ("Assistant Coach #2").
    label: str
    #: The band it sits under ("Administration", "Men's Basketball").
    group: str
    #: "admin" or "coaching".
    kind: str
    gender: str | None = None
    sport: str | None = None

    @property
    def display(self) -> str:
        """How the slot is named in the review table."""
        return f"{self.group} › {self.label}"


def admin_slots() -> list[Slot]:
    return [
        Slot(
            key=f"{department}|{position}",
            label=position,
            group=department,
            kind="admin",
        )
        for department, positions in schema.ADMIN_DEPARTMENTS
        for position in positions
    ]


GENDER_LABEL = {"male": "Men's", "female": "Women's"}


def coaching_slots(selected: list[tuple[str, str]]) -> list[Slot]:
    """Coaching rows for the sports in the scenario, in page order.

    `selected` is (gender, sport) pairs. A sport run on both sides gets its
    own set of rows under each gender band, because the pay is different and
    the page is costing staff rather than counting sports.
    """
    slots: list[Slot] = []
    for gender, sport in selected:
        for position in schema.coaching_staff(sport):
            slots.append(Slot(
                key=f"{gender}|{sport}|{position}",
                label=position,
                group=f"{GENDER_LABEL[gender]} {sport}",
                kind="coaching",
                gender=gender,
                sport=sport,
            ))
    return slots


def all_slots(selected: list[tuple[str, str]]) -> list[Slot]:
    return admin_slots() + coaching_slots(selected)


# --- Reading the institution's file ------------------------------------


@dataclass(frozen=True)
class SourceRow:
    """One line of the uploaded salary file."""

    index: int
    title: str
    salary: float
    #: A Sport column's value, where the file had one.
    sport: str | None = None
    #: A Department column's value, where the file had one.
    department: str | None = None


class SalaryFileError(ValueError):
    """The file could not be read as a salary list."""


#: Header words that identify each column. Checked as substrings of the
#: normalized header, longest first, so "base salary" beats "salary".
_TITLE_HEADERS = (
    "position title", "job title", "position", "title", "job", "role", "employee",
)
_SALARY_HEADERS = (
    "base salary", "annual salary", "total compensation", "compensation",
    "salary", "base pay", "base", "amount", "pay", "wage", "budget", "cost",
)
_SPORT_HEADERS = ("sport", "team", "program")
_DEPARTMENT_HEADERS = ("department", "dept", "unit", "area", "division", "group")


def salary_sheets(filename: str, data: bytes) -> list[str]:
    """The worksheets that look like they hold a salary list.

    A conference salary survey arrives as one workbook with a sheet per
    institution, and reading "the first one with anything in it" silently
    builds the wrong school's pro forma. So the sheets are listed and the
    operator says which. Empty for a CSV, which has only one.
    """
    suffix = _suffix(filename)
    if suffix not in _WORKBOOK_SUFFIXES:
        return []
    import openpyxl

    workbook = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    return [
        worksheet.title
        for worksheet in workbook.worksheets
        if any(
            cell is not None
            for row in worksheet.iter_rows(max_row=40, values_only=True)
            for cell in row
        )
    ]


_WORKBOOK_SUFFIXES = ("xlsx", "xlsm", "xltx")


def _suffix(filename: str) -> str:
    return filename.lower().rsplit(".", 1)[-1] if "." in filename else ""


def read_salary_file(
    filename: str, data: bytes, *, sheet: str | None = None
) -> list[SourceRow]:
    """Rows out of an .xlsx, .csv or .tsv salary list.

    The header row is found rather than assumed. These come out of a payroll
    system or a conference survey form, and the top of the sheet is a title,
    a note about which year to use, and a blank line before anything useful
    - so reading row 1 as the header gets a column called "2026-2027
    SOUTHERN CONFERENCE ATHLETIC DIRECTORS SURVEY" and nothing else.
    """
    suffix = _suffix(filename)
    if suffix in _WORKBOOK_SUFFIXES:
        table = _rows_from_workbook(data, sheet)
    elif suffix in ("csv", "txt", "tsv"):
        table = _rows_from_delimited(data, tab=suffix == "tsv")
    else:
        raise SalaryFileError(
            f"{filename} is not a salary file this reads — it takes .xlsx, .csv or .tsv. "
            "Export the salary list from the payroll or budget system, or enter the "
            "figures by hand instead."
        )
    if not table:
        raise SalaryFileError(f"{filename} is empty.")
    return _rows_from_table(table, filename)


def _rows_from_workbook(data: bytes, sheet: str | None) -> list[list[object]]:
    import openpyxl

    workbook = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    if sheet is not None:
        if sheet not in workbook.sheetnames:
            raise SalaryFileError(
                f"The workbook has no sheet called {sheet!r}. It has: "
                + ", ".join(workbook.sheetnames)
            )
        return [list(row) for row in workbook[sheet].iter_rows(values_only=True)]
    # No sheet named: the first that holds anything. A workbook exported
    # from a payroll system often leads with a cover or parameters sheet.
    for worksheet in workbook.worksheets:
        rows = [list(row) for row in worksheet.iter_rows(values_only=True)]
        if any(cell is not None for row in rows for cell in row):
            return rows
    return []


def _rows_from_delimited(data: bytes, *, tab: bool) -> list[list[object]]:
    text = data.decode("utf-8-sig", errors="replace")
    delimiter = "\t" if tab else _sniff(text)
    return [list(row) for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


def _sniff(text: str) -> str:
    sample = "\n".join(text.splitlines()[:20])
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _rows_from_table(table: list[list[object]], filename: str) -> list[SourceRow]:
    header_index, columns = _find_header(table)
    if columns is None:
        columns = _infer_columns(table)
        header_index = -1
        if columns is None:
            raise SalaryFileError(
                f"{filename} has no column that reads as a position title and a salary. "
                "Name the columns (for instance “Position” and “Salary”) and upload it "
                "again, or enter the figures by hand."
            )

    title_col, salary_col, sport_col, department_col = columns
    rows: list[SourceRow] = []
    # The first section is usually named *above* the header row - a survey
    # form reads "Athletic Administration", then "Full Time with Benefits /
    # Base Salary", then the data - so starting the scan at the header loses
    # it, and every administrative row begins life with no context at all.
    section = _section_above(table, header_index, title_col, salary_col)
    for number, raw in enumerate(table[header_index + 1:], start=header_index + 2):
        title = _text(_at(raw, title_col))
        salary = _number(_at(raw, salary_col))

        if salary is None:
            # No figure. This is either noise or a section heading - and the
            # headings are worth having, because in a conference survey form
            # they are the only thing that says which sport a row of
            # identical "Asst. Coach" titles belongs to.
            heading = _heading(raw, title_col, salary_col)
            if heading:
                section = heading
            continue
        if not title or _looks_like_a_total(title):
            continue

        sport = _text(_at(raw, sport_col))
        department = _text(_at(raw, department_col))
        if not sport and not department and section:
            # The heading names a sport, or it names a part of the
            # department. Either way it is context the row itself lacks.
            if detect_sport(section)[1] is not None:
                sport = section
            else:
                department = section

        rows.append(SourceRow(
            index=number,
            title=title,
            salary=salary,
            sport=sport or None,
            department=department or None,
        ))
    if not rows:
        raise SalaryFileError(
            f"{filename} was read, but no row carried both a title and a figure."
        )
    return rows


def _section_above(
    table: list[list[object]], header_index: int, title_col: int, salary_col: int
) -> str:
    """The nearest section heading above the header row, if there is one."""
    for raw in reversed(table[:max(header_index, 0)]):
        heading = _heading(raw, title_col, salary_col)
        if heading:
            return heading
    return ""


def _heading(raw: list[object], title_col: int, salary_col: int) -> str:
    """A section heading, or "" if this row is not one.

    A heading is one piece of text on an otherwise empty row, at or to the
    left of the title column - "Baseball", "Men's Basketball", "Athletic
    Training, Strength & Conditioning". Anything with a second populated
    cell is a data row missing its figure, not a heading, and treating it as
    one would relabel every row beneath it.
    """
    populated = [
        (index, _text(value))
        for index, value in enumerate(raw)
        if _text(value) and index != salary_col
    ]
    if len(populated) != 1:
        return ""
    index, text = populated[0]
    if index > title_col or len(text) < 3 or text.replace(".", "").isdigit():
        return ""
    if _looks_like_a_total(text) or _looks_like_an_instruction(text):
        return ""
    return text


#: Form furniture. A survey template is half instructions, and every one of
#: them sits alone on its row looking exactly like a section heading.
_INSTRUCTION_WORDS = (
    "please", "note:", "instruction", "institution:", "name of person",
    "if you", "list the", "do not", "include", "complete",
)


def _looks_like_an_instruction(text: str) -> bool:
    lowered = text.strip().lower()
    return len(lowered) > 60 or lowered.startswith(_INSTRUCTION_WORDS)


def _at(row: list[object], column: int | None) -> object:
    if column is None or column >= len(row):
        return None
    return row[column]


def _find_header(table: list[list[object]]) -> tuple[int, tuple | None]:
    """The first row that names a salary column.

    The title column does not have to be named, and on a survey form it
    usually is not: the header row reads "Full Time with Benefits" over the
    numbering and "Base Salary" over the figures, with the column holding
    the actual job titles left blank. So a named salary column is enough,
    and the titles are taken from the text column nearest to its left.
    """
    for index, row in enumerate(table[:30]):
        headers = [_normalize(_text(cell)) for cell in row]
        salary_col = _best_header(headers, _SALARY_HEADERS)
        if salary_col is None:
            continue
        title_col = _best_header(headers, _TITLE_HEADERS)
        if title_col is None or title_col == salary_col:
            title_col = _text_column_before(table, index, salary_col)
        if title_col is None:
            continue
        return index, (
            title_col,
            salary_col,
            _best_header(headers, _SPORT_HEADERS),
            _best_header(headers, _DEPARTMENT_HEADERS),
        )
    return -1, None


def _text_column_before(
    table: list[list[object]], header_index: int, salary_col: int
) -> int | None:
    """The column holding job titles, where the header row doesn't say.

    The one nearest to the left of the salary column that carries real text
    in the body - which skips the numbering column that survey forms put in
    column A, and whose values ("1", "2", "50A") are text but never titles.
    """
    body = table[header_index + 1:]
    best = None
    for column in range(salary_col - 1, -1, -1):
        titles = sum(
            1 for row in body
            if (value := _text(_at(row, column))) and not _is_index_label(value)
        )
        if titles >= max(3, len(body) // 20):
            best = column
            break
    return best


_INDEX_LABEL = re.compile(r"^\d{1,3}[a-z]?$", re.IGNORECASE)


def _is_index_label(text: str) -> bool:
    """"1", "27", "50A" - a survey form's row numbering, not a job title."""
    return bool(_INDEX_LABEL.match(text.strip()))


#: A header is a label, not a sentence. Survey forms are half instructions,
#: and "Please list the title of ALL your athletic administrative staff..."
#: contains "title" as surely as a column called "Title" does.
_HEADER_MAX = 40


def _best_header(headers: list[str], wanted: tuple[str, ...]) -> int | None:
    """The column whose header matches the most specific wanted word.

    Ordered by specificity rather than by column, so a sheet carrying both
    "Base Salary" and "Salary Grade" picks the first. Matched on whole words
    inside short cells only: a bare substring search over whatever text a
    cell happens to hold found "Base Salary" in a cell naming the person who
    filled the form in, and read their job title as the salary column.
    """
    short = [
        header if header and len(header) <= _HEADER_MAX else ""
        for header in headers
    ]
    for word in wanted:
        for index, header in enumerate(short):
            if header == word:
                return index
    for word in wanted:
        pattern = re.compile(rf"\b{re.escape(word)}\b")
        for index, header in enumerate(short):
            if header and pattern.search(header):
                return index
    return None


#: A salary is a salary. Below this a column of numbers is a row count, a
#: head count, an FTE or a percentage - and a survey form's numbering column
#: is forty small integers that will otherwise be read as forty salaries,
#: silently, producing a workbook where the Director of Athletics earns $1.
_SALARY_FLOOR = 1_000.0


def _infer_columns(table: list[list[object]]) -> tuple | None:
    """No usable header: take the first mostly-text column as the title and
    the first column of plausible money as the salary.

    A last resort, and it is worth having - plenty of these files are a
    two-column paste with no header at all, and refusing them would send the
    analyst back to typing.
    """
    width = max((len(row) for row in table), default=0)
    # Chosen by weight of evidence rather than first-past-the-post. A survey
    # form's column A holds a numbering ("1", "87A") and seventeen section
    # headings, which is enough text to win a "first column with some text
    # in it" race against the column holding ninety job titles.
    best_text = (0, None)
    money: list[int] = []
    for column in range(width):
        values = [_at(row, column) for row in table]
        texts = sum(
            1 for value in values
            if (text := _text(value)) and _number(value) is None
            and not _is_index_label(text)
        )
        if texts > best_text[0]:
            best_text = (texts, column)
        amounts = [
            number for value in values if (number := _number(value)) is not None
        ]
        if _looks_like_money(amounts, len(table)):
            money.append(column)
    text_col = best_text[1]
    if text_col is None or not money:
        return None
    # The money column to the right of the titles where there is one; these
    # read left to right, and a figure to the left of the title is usually
    # an identifier that happens to be large.
    to_the_right = [column for column in money if column > text_col]
    return text_col, (to_the_right or money)[0], None, None


def _looks_like_money(amounts: list[float], rows: int) -> bool:
    if len(amounts) < max(2, rows // 20):
        return False
    ordered = sorted(abs(amount) for amount in amounts)
    median = ordered[len(ordered) // 2]
    return median >= _SALARY_FLOOR


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


_MONEY = re.compile(r"[^0-9.\-]")


def _number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) or None
    text = str(value).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    cleaned = _MONEY.sub("", text)
    if not cleaned or cleaned in ("-", ".", "-."):
        return None
    try:
        number = float(cleaned)
    except ValueError:
        return None
    if not number:
        return None
    return -number if negative else number


_TOTAL_WORDS = ("total", "subtotal", "grand total", "sum of", "all positions")


def _looks_like_a_total(title: str) -> bool:
    normalized = _normalize(title)
    return any(normalized.startswith(word) or normalized == word for word in _TOTAL_WORDS)


# --- Normalising a title -----------------------------------------------

#: Abbreviations as athletics departments actually write them. Expanded
#: token by token before anything is compared, because this is a domain
#: where "Assoc AD - Ext Ops" and "Associate Athletics Director, External
#: Operations" are the same job and share almost no characters.
_ABBREVIATIONS: dict[str, str] = {
    "ad": "athletics director",
    "ads": "athletics director",
    "aad": "associate athletics director",
    "saad": "senior associate athletics director",
    "swa": "senior woman administrator",
    "sid": "athletics communications director",
    "atc": "athletic trainer",
    "at": "athletic trainer",
    "ga": "graduate assistant",
    "gas": "graduate assistant",
    "sc": "sports performance",
    "assoc": "associate",
    "assc": "associate",
    "asst": "assistant",
    "assist": "assistant",
    "asst2": "assistant",
    "ass": "assistant",
    "dir": "director",
    "drctr": "director",
    "coord": "coordinator",
    "coor": "coordinator",
    "ops": "operations",
    "op": "operations",
    "admin": "administrative",
    "administration": "administrative",
    "mgr": "manager",
    "mgmt": "management",
    "comm": "communications",
    "comms": "communications",
    "info": "information",
    "rel": "relations",
    "dev": "development",
    "mktg": "marketing",
    "mkt": "marketing",
    "exec": "executive",
    "sr": "senior",
    "jr": "junior",
    "vp": "vice president",
    "svp": "senior vice president",
    "cfo": "business finance",
    "coo": "internal operations",
    "hr": "human resources",
    "sa": "student athlete",
    "sas": "student athlete services",
    "saac": "student athlete development",
    "nil": "name image likeness",
    "strength": "sports performance",
    "conditioning": "sports performance",
    "trainer": "athletic trainer",
    "training": "athletic trainer",
    "sports information": "athletics communications",
    "media relations": "athletics communications",
    "equip": "equipment",
    "facil": "facilities",
    "fac": "facilities",
    "acad": "academic",
    "svcs": "services",
    "svc": "services",
    "facility": "facilities",
    "compl": "compliance",
    "psych": "psychologist",
    "counselor": "psychologist",
    "counsellor": "psychologist",
    "counseling": "psychologist",
    "therapist": "psychologist",
    "wellness": "mental health",
    "nutrition": "nutritionist",
    "dietitian": "nutritionist",
    "promo": "promotions",
    "promos": "promotions",
    "tix": "ticketing",
    "tickets": "ticketing",
    "ticket": "ticketing",
    "bus": "business",
    "fin": "finance",
    "financial": "finance",
    "asso": "associate",
}

#: Whole-phrase rewrites, applied before tokenising. These are the ones
#: where the words themselves are right but point at a different scaffold
#: label than a token match would reach.
_PHRASES: tuple[tuple[str, str], ...] = (
    ("strength and conditioning", "sports performance"),
    # "S&C" survives punctuation stripping as "s and c", so it has to be
    # caught as a phrase - by the time it is three tokens no abbreviation
    # lookup will reach it, and it otherwise lands on whichever other "GA"
    # row sorts first.
    ("s and c", "sports performance"),
    ("strength and cond", "sports performance"),
    ("sports info", "athletics communications"),
    ("sports information", "athletics communications"),
    ("media relations", "athletics communications"),
    ("public relations", "athletics communications"),
    ("athletic trainer", "athletic trainer"),
    ("athletic training", "athletic trainer"),
    ("sports medicine", "sports medicine"),
    ("director of athletics", "athletics director"),
    ("athletic director", "athletics director"),
    ("athletics director", "athletics director"),
    # At a private institution the athletics director is often a vice
    # president and the title says so. Left alone, "VP of Intercollegiate
    # Athletics" shares not one word with "Director of Athletics" once the
    # stopwords are gone, and the highest-paid person in the file lands
    # nowhere.
    ("vp of intercollegiate athletics", "athletics director"),
    ("vice president of intercollegiate athletics", "athletics director"),
    ("vice president for intercollegiate athletics", "athletics director"),
    ("vice president of athletics", "athletics director"),
    ("vice chancellor and athletics director", "athletics director"),
    ("head athletic trainer", "head athletic trainer"),
    ("graduate assistant", "graduate assistant"),
    ("grad assistant", "graduate assistant"),
    ("student athlete", "student athlete"),
    ("student-athlete", "student athlete"),
    ("mens basketball", "men basketball"),
    ("womens basketball", "women basketball"),
    ("cross country", "x country"),
    ("t and f", "track field"),
    ("tf", "track field"),
    ("track field", "track x country"),
)

_PUNCTUATION = re.compile(r"[^a-z0-9#\s]")
_SPACES = re.compile(r"\s+")

#: Compiled longest-first and anchored on word boundaries. Both matter:
#: a plain substring pass turned "sports information director" into
#: "athletics communicationsrmation director", because "sports info" is a
#: prefix of "sports information" and fired inside it.
_PHRASE_PATTERNS: tuple[tuple[re.Pattern, str], ...] = tuple(
    (re.compile(rf"\b{re.escape(phrase)}\b"), replacement)
    for phrase, replacement in sorted(
        _PHRASES, key=lambda pair: len(pair[0]), reverse=True
    )
)


def _normalize(text: str) -> str:
    """Lowercase, de-punctuated, abbreviation-expanded."""
    lowered = text.lower().replace("&", " and ")
    # Apostrophes close up rather than split. CC's own books are full of
    # "Ass't Director" and "Ass't SID", and splitting on the apostrophe
    # leaves "ass" and a stray "t" instead of the "asst" the abbreviation
    # table is waiting for.
    lowered = lowered.replace("'", "").replace("’", "")
    lowered = _PUNCTUATION.sub(" ", lowered)
    lowered = _SPACES.sub(" ", lowered).strip()
    for pattern, replacement in _PHRASE_PATTERNS:
        lowered = pattern.sub(replacement, lowered)
    tokens = [_ABBREVIATIONS.get(token, token) for token in lowered.split()]
    return _SPACES.sub(" ", " ".join(tokens)).strip()


#: Noise words that carry no distinguishing signal and drag every score
#: towards every other score.
_STOPWORDS = frozenset({
    "of", "the", "for", "and", "to", "a", "an", "in", "at", "on", "department",
    "athletics", "athletic", "staff", "intercollegiate", "university", "college",
})


def _tokens(normalized: str) -> frozenset[str]:
    """Meaning-bearing words only.

    Single characters are dropped. "Asst. M & W Cross Country/T&F Coach"
    leaves "m", "w", "t" and "f" behind once the sport and gender words are
    accounted for, and those four letters then read as the subject of the
    job - disagreeing with every scaffold row and sinking a coach who was
    otherwise an exact match.
    """
    return frozenset(
        token for token in normalized.split()
        if len(token) > 1 and token not in _STOPWORDS
    )


def _strip(normalized: str, drop: frozenset[str]) -> str:
    if not drop:
        return normalized
    return " ".join(token for token in normalized.split() if token not in drop)


# --- Rank, number, sport -----------------------------------------------

#: Seniority, most specific first. Two titles whose ranks disagree are
#: almost never the same job, however many other words they share - "Head
#: Coach" and "Assistant Coach" differ by one token and by a person.
_RANKS: tuple[tuple[str, frozenset[str]], ...] = (
    ("graduate assistant", frozenset({"graduate"})),
    ("associate head", frozenset({"associate", "head"})),
    ("head", frozenset({"head"})),
    ("senior associate", frozenset({"senior", "associate"})),
    ("associate", frozenset({"associate"})),
    ("deputy", frozenset({"deputy"})),
    ("executive", frozenset({"executive"})),
    ("assistant", frozenset({"assistant"})),
    ("director", frozenset({"director"})),
    ("coordinator", frozenset({"coordinator"})),
    ("manager", frozenset({"manager"})),
)

#: "#2", "No. 2", "II" - and a bare trailing digit, which is how CC's own
#: books write it ("Assistant Trainer 1", "Assistant Trainer 2"). Without
#: that last form those rows are numbered in the file and unnumbered to the
#: matcher, and trainer 1 lands on trainer 4.
_NUMBER = re.compile(
    r"#\s*(\d+)|\b(?:number|no)\s*(\d+)\b|\b([ivx]+)\b|\s(\d{1,2})\s*$"
)
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8}


#: Seniority, where a disagreement means a different person. "Head Coach"
#: and "Assistant Coach" differ by one token and by a salary band.
_SENIORITY = frozenset({
    "head", "associate head", "associate", "senior associate", "deputy",
    "executive", "assistant", "graduate assistant",
})

#: Role nouns, where a disagreement means nothing at all. One institution's
#: "Manager - Athletics Communication" is another's "Director of Athletics
#: Communications" and a third's "Coordinator"; penalising those against
#: each other left four perfectly ordinary communications rows unmatched in
#: CC's own ASUN book.
_TITLE_WORDS = frozenset({"director", "coordinator", "manager"})

#: Everything above, plus the rest of the vocabulary that appears in every
#: second title and so distinguishes nothing.
_GENERIC = _SENIORITY | _TITLE_WORDS | frozenset({"graduate", "junior", "coach"})


def _fold(tokens: frozenset[str]) -> frozenset[str]:
    """Singular and plural are the same word here."""
    return frozenset(
        token[:-1] if len(token) > 4 and token.endswith("s") else token
        for token in tokens
    )


def _subject(tokens: frozenset[str]) -> frozenset[str]:
    """What the job is actually about, once the role words are removed.

    Compliance, facilities, ticketing, marketing, nutrition. This is the
    part that decides whether two titles are the same job, and weighting it
    like any other token is why "Ass't Director - Facilities" went to
    "Assistant Director - Compliance" with no hesitation: two of three
    tokens matched, and the one that did not was the only one that meant
    anything.
    """
    return tokens - _GENERIC


def _ranks(tokens: frozenset[str]) -> frozenset[str]:
    """Every seniority word present, not just the most specific one.

    A scaffold label often names alternatives - "Deputy / Executive
    Associate AD" is one row covering three titles. Taking only the first
    rank that matched made that row read as "associate", which then
    disagreed with an incoming "Deputy AD" and was penalised as if the two
    were different people. Comparing the sets instead, they overlap on
    "deputy" and agree, while "Head Coach" and "Assistant Coach" stay
    disjoint and are still penalised.
    """
    return frozenset(
        name for name, required in _RANKS if required <= tokens
    )


def _number_in(normalized: str) -> int | None:
    match = _NUMBER.search(normalized)
    if not match:
        return None
    hashed, worded, roman, trailing = match.groups()
    if hashed:
        return int(hashed)
    if worded:
        return int(worded)
    if roman and roman in _ROMAN:
        return _ROMAN[roman]
    if trailing:
        return int(trailing)
    return None


_GENDER_WORDS = {
    "men": "male", "mens": "male", "man": "male", "boys": "male", "m": "male",
    "women": "female", "womens": "female", "woman": "female", "girls": "female",
    "w": "female", "ladies": "female",
}

#: Sport shorthands the master list does not carry.
_SPORT_SHORTHAND = {
    "mbb": ("male", "Basketball"),
    "wbb": ("female", "Basketball"),
    "mbball": ("male", "Basketball"),
    "wbball": ("female", "Basketball"),
    "xc": (None, "Track and Field, X-Country"),
    "tfxc": (None, "Track and Field, X-Country"),
    "mvb": ("male", "Volleyball"),
    "wvb": ("female", "Volleyball"),
    "mgolf": ("male", "Golf"),
    "wgolf": ("female", "Golf"),
    "msoc": ("male", "Soccer"),
    "wsoc": ("female", "Soccer"),
}


def detect_sport(text: str) -> tuple[str | None, str | None]:
    """The (gender, sport) a title or Sport column names, where it names one.

    Returns (None, None) for an administrative title. The gender half can be
    None on its own - "Head Soccer Coach" at an institution that runs one
    soccer programme names a sport without naming a side, and the caller
    resolves that against the sports actually in the scenario.
    """
    if not text:
        return None, None
    normalized = _normalize(text)
    words = normalized.split()

    for word in words:
        if word in _SPORT_SHORTHAND:
            return _SPORT_SHORTHAND[word]

    # A section headed "Men's & Women's Golf" names both, so it names
    # neither: taking the first one found sent every women's golf coach to
    # the men's rows, which is a quiet way to lose a programme.
    sides = {_GENDER_WORDS[word] for word in words if word in _GENDER_WORDS}
    gender = sides.pop() if len(sides) == 1 else None

    # Longest master-list name first, so "Track and Field, X-Country" is
    # found before the bare "Track" inside it.
    for candidate in sorted(sports.MASTER_SPORTS, key=len, reverse=True):
        normalized_sport = _normalize(candidate)
        if normalized_sport and normalized_sport in normalized:
            return gender, candidate
    for candidate in sorted(sports.MASTER_SPORTS, key=len, reverse=True):
        head = _normalize(candidate).split()[0]
        if len(head) > 4 and head in words:
            return gender, candidate
    return gender, None


# --- Scoring -----------------------------------------------------------

#: What an exactly-equal pair scores. Deliberately above anything the
#: weighted score can reach, so an exact match can never lose a tie-break.
#:
#: It used to score 1.0 like everything else that saturated, and
#: "Director - Sports Performance" lost its own identically-named row to
#: "Assistant Director - Sports Performance": both hit the ceiling, and the
#: tie broke alphabetically. A ceiling destroys the ordering it is being
#: asked to preserve, which is why the score is no longer capped above.
EXACT = 2.0

#: Below this a pairing is not offered at all.
FLOOR = 0.34
#: At or above this the match is taken as read.
STRONG = 0.78
#: Between FLOOR and this, the row is left unassigned for the analyst.
LIKELY = 0.56


def similarity(
    source_title: str,
    slot_label: str,
    *,
    drop: frozenset[str] = frozenset(),
) -> float:
    """How alike two position titles are, 0 to 1.

    `drop` removes tokens from both sides before comparing. It carries the
    sport and gender words once the sport gate has already used them: the
    file says "Head Men's Basketball Coach" and the scaffold says "Head
    Coach", and those are the same job with three redundant tokens between
    them. Comparing them intact scores 0.61 - a review-me - for a pairing
    there is nothing uncertain about, and a reviewer who is sent twelve of
    those stops reading the ones that matter.
    """
    left = _strip(_normalize(source_title), drop)
    right = _strip(_normalize(slot_label), drop)
    if not left or not right:
        return 0.0
    if left == right:
        return EXACT

    left_tokens, right_tokens = _tokens(left), _tokens(right)
    if not left_tokens or not right_tokens:
        # Everything that distinguished them was dropped as context - two
        # bare "coach" rows, say. Fall back to the full titles rather than
        # calling them identical.
        left, right = _normalize(source_title), _normalize(slot_label)
        left_tokens, right_tokens = _tokens(left), _tokens(right)
    # Compared on the subject nouns where both sides have one, and on the
    # whole title otherwise. This is the difference between "Assistant AD -
    # Academics" reaching Academic Services and reaching Athletics
    # Communications: "assistant director" matches the second exactly, and
    # on whole-token overlap that outweighed the only word in either title
    # that said what the job was. Coaching rows have no subject once "coach"
    # is removed, so they fall through to the whole title, which is what
    # keeps Head and Assistant apart.
    left_subject = _fold(_subject(left_tokens))
    right_subject = _fold(_subject(right_tokens))
    if left_subject and right_subject:
        compare_left, compare_right = left_subject, right_subject
    else:
        compare_left, compare_right = left_tokens, right_tokens

    if compare_left and compare_right:
        shared = len(compare_left & compare_right)
        overlap = shared / len(compare_left | compare_right)
        # How much of the *file's* title the scaffold label accounts for.
        # Symmetric overlap alone cannot separate a label that carries the
        # distinguishing word from a shorter one that drops it: "Deputy AD"
        # overlaps "Director of Athletics" and "Deputy / Executive Associate
        # AD" equally, and only coverage notices that the first leaves
        # "deputy" unexplained - which is the whole job.
        covered = shared / len(compare_left)
    else:
        overlap = covered = 0.0
    sequence = SequenceMatcher(None, left, right).ratio()
    score = 0.40 * overlap + 0.28 * covered + 0.32 * sequence

    left_ranks = _ranks(left_tokens) & _SENIORITY
    right_ranks = _ranks(right_tokens) & _SENIORITY
    if left_ranks and right_ranks:
        score += 0.16 if left_ranks & right_ranks else -0.26
    elif left_ranks or right_ranks:
        score -= 0.04

    # The subject nouns decide it. Agreeing on them is the strongest signal
    # in a title; disagreeing on them means different jobs, whatever the
    # role words have in common.
    #
    # Folded for plurals: one book writes "Facility Director" and the
    # scaffold says "Director of Facilities & Operations", and treating
    # those as unrelated subjects is how a facilities director ends up
    # unmatched.
    if left_subject and right_subject:
        if left_subject == right_subject:
            score += 0.12
        elif left_subject & right_subject:
            score += 0.06
        else:
            # Disjoint subjects already score near nothing, since the
            # overlap above is computed on the subjects themselves. This
            # pushes them below FLOOR as well, which keeps them out of the
            # analyst's dropdown: without it "Ass't Director - Facilities"
            # offers five unrelated "Assistant Director of ..." rows
            # alongside the three facilities ones, and a candidate list
            # padded with plausible wrong answers is worse than a short one.
            score -= 0.30

    # A graduate assistant is not an assistant, whatever the token overlap
    # says. The two labels differ by one word and by about $45,000, and
    # "Assistant Coach" would otherwise settle happily onto the GA row once
    # the numbered slots were taken - understating a real position and
    # overstating a graduate one in the same stroke.
    #
    # Heavy enough to push the pairing below the threshold rather than merely
    # flag it. In CC's own SoCon book a second "S&C GA" was settling onto
    # "Assistant Director - Sports Performance" and displacing the "S&C
    # Coach" who belonged there; with the GA declined, both land correctly.
    if ("graduate" in left_tokens) != ("graduate" in right_tokens):
        score -= 0.45

    left_number, right_number = _number_in(left), _number_in(right)
    if left_number and right_number:
        score += 0.10 if left_number == right_number else -0.12

    # Floored but not capped. Two near-matches that both saturate are
    # indistinguishable, and the tie then falls to whichever slot sorts
    # first - which is how an exact match loses to a longer label that
    # merely contains it.
    return max(0.0, score)


# --- Assignment --------------------------------------------------------


@dataclass
class Assignment:
    """One source row and where it is going."""

    row: SourceRow
    slot_key: str | None
    score: float
    #: Candidate slot keys worth offering, best first.
    candidates: list[str] = field(default_factory=list)

    @property
    def confidence(self) -> str:
        if self.slot_key is None:
            return "unmatched"
        if self.score >= STRONG:
            return "strong"
        if self.score >= LIKELY:
            return "likely"
        return "weak"


def match(rows: list[SourceRow], slots: list[Slot]) -> list[Assignment]:
    """Assign salary rows to scaffold positions, one to one, best first.

    Greedy rather than optimal. A full assignment problem would buy very
    little here - the scores that matter are the near-ones and the analyst
    reviews those anyway - and greedy has the property that the obviously
    right pairings are made first and cannot be displaced by a marginal
    pairing elsewhere, which is what a reviewer expects to see.
    """
    by_key = {slot.key: slot for slot in slots}
    pairs: list[tuple[float, int, str]] = []
    candidates: dict[int, list[tuple[float, str]]] = {index: [] for index in range(len(rows))}

    for index, row in enumerate(rows):
        allowed, gender, sport = _allowed_slots(row, slots)
        drop = _context_tokens(gender, sport)
        for slot in allowed:
            score = similarity(row.title, slot.label, drop=drop)
            score = _adjust_for_context(score, row, slot)
            if score < FLOOR:
                continue
            pairs.append((score, index, slot.key))
            candidates[index].append((score, slot.key))

    pairs.sort(key=lambda pair: (-pair[0], pair[1], pair[2]))
    taken_rows: set[int] = set()
    taken_slots: set[str] = set()
    chosen: dict[int, tuple[str, float]] = {}
    for score, index, key in pairs:
        if index in taken_rows or key in taken_slots:
            continue
        if score < LIKELY:
            # Left for the analyst. A weak guess costs more than a blank:
            # a blank row reads as "to confirm" and a filled one reads as a
            # fact, and nobody re-checks a fact.
            continue
        taken_rows.add(index)
        taken_slots.add(key)
        chosen[index] = (key, score)

    assignments: list[Assignment] = []
    for index, row in enumerate(rows):
        key, score = chosen.get(index, (None, 0.0))
        offered = [
            slot_key for _, slot_key in sorted(
                candidates[index], key=lambda pair: (-pair[0], pair[1])
            )
        ]
        if key and key in offered:
            offered.remove(key)
        if key:
            offered.insert(0, key)
        assignments.append(Assignment(
            row=row, slot_key=key, score=score, candidates=offered[:12],
        ))
    _ = by_key
    return assignments


def _resolve_sport(sport: str | None, slots: list[Slot]) -> str | None:
    """The sport as the scaffold names it, given the sport as the file does.

    A survey form has sections for "Men's & Women's Cross Country" and
    "Men's & Women's Track", and the pro forma carries one combined row
    called "Track and Field, X-Country" - because that is how the FRS
    report files them. Matched name-for-name, every track and cross-country
    coach in the file lands nowhere.

    Resolved against the rows that actually exist rather than through a
    table of its own, so it follows whatever the scenario carries.
    """
    if sport is None:
        return None
    available = {slot.sport for slot in slots if slot.sport}
    if sport in available:
        return sport
    for candidate in sorted(available):
        members = sports.combined_members(candidate)
        if members and sport in members:
            return candidate
    return sport


def _context_tokens(gender: str | None, sport: str | None) -> frozenset[str]:
    """The words the sport gate has already accounted for.

    Once a row is known to be about Men's Basketball, "men", "mens" and
    "basketball" carry no further information - every slot it can still
    reach is also Men's Basketball. Dropping them from both sides of the
    comparison leaves the part that actually distinguishes a Head Coach
    from a Director of Operations.
    """
    if sport is None:
        return frozenset()
    tokens = set(_normalize(sport).split())
    tokens.update(word for word, side in _GENDER_WORDS.items() if side == gender or gender is None)
    tokens.discard("")
    return frozenset(tokens)


def _allowed_slots(
    row: SourceRow, slots: list[Slot]
) -> tuple[list[Slot], str | None, str | None]:
    """Which slots this row is even allowed to compete for, and the
    (gender, sport) context that decided it.

    The sport gate is what makes the coaching side work at all. Without it
    every "Head Coach" row in the file competes for every sport's Head Coach
    slot and the winner is decided by nothing - the titles are identical.
    With it, a row that names Baseball can only land on Baseball.
    """
    # The sport comes from whichever names one; the gender is the row's own
    # word first. A row reading "Head Women's Golf Coach" under a section
    # headed "Men's & Women's Golf" is women's golf, and letting the section
    # answer for it put her on the men's row.
    column_gender, column_sport = detect_sport(row.sport or "")
    title_gender, title_sport = detect_sport(row.title)
    sport = column_sport or title_sport
    gender = title_gender or column_gender
    sport = _resolve_sport(sport, slots)

    admin = [slot for slot in slots if slot.kind == "admin"]
    if sport is None:
        # No sport named anywhere: administrative, unless the title is
        # plainly a sport-coaching one, in which case it still has to be
        # reviewed by hand because nothing says which sport.
        #
        # "Coach" alone does not make it one. A strength coach is
        # administrative staff, and gating "S&C Coach" out of the admin
        # slots left CC's own SoCon book with its strength coach unmatched
        # and the row they belonged in taken by a graduate assistant. So the
        # gate only closes on a title with no administrative subject - which
        # is exactly what a bare "Head Coach" or "Assistant Coach" is.
        subject = _fold(_subject(_tokens(_normalize(row.title))))
        if "coach" in _normalize(row.title).split() and not any(
            subject & _fold(_subject(_tokens(_normalize(slot.label))))
            for slot in admin
        ):
            return [], None, None
        return admin, None, None

    matching = [
        slot for slot in slots
        if slot.kind == "coaching"
        and slot.sport == sport
        and (gender is None or slot.gender == gender)
    ]
    # A sport named in the file that the scenario does not run leaves the row
    # with nowhere to go. Returning the admin slots instead would be worse
    # than returning nothing: it would offer a dropdown of plausible-looking
    # wrong answers.
    return matching, gender, sport


def _adjust_for_context(score: float, row: SourceRow, slot: Slot) -> float:
    """Nudges from the columns beside the title."""
    if slot.kind == "admin" and row.department:
        department = _normalize(row.department)
        group = _normalize(slot.group)
        if department and group:
            if department == group:
                score += 0.12
            elif _tokens(department) & _tokens(group):
                score += 0.06
    return max(0.0, score)


# --- Turning assignments into the Plan's shape -------------------------


def to_plan_figures(
    assignments: list[Assignment],
    slots: list[Slot],
    base_year: str,
) -> tuple[dict[str, dict[str, float]], dict[tuple[str, str], dict[str, dict[str, float]]]]:
    """(admin_salaries, coaching_salaries) as workbooks.Plan wants them.

    Only the base year is set. Every later year escalates off it against the
    line's tier, which is the whole point of entering one year rather than
    six - and an analyst who wants a step change in 2027 types it into the
    cell, where it reads as a decision rather than as a trend.

    Two rows assigned to the same slot are added together rather than one
    silently replacing the other. That happens legitimately: a file that
    lists base pay and a supplement on separate lines, or a split
    appointment, is one person on one row of the page.
    """
    by_key = {slot.key: slot for slot in slots}
    admin: dict[str, dict[str, float]] = {}
    coaching: dict[tuple[str, str], dict[str, dict[str, float]]] = {}

    for assignment in assignments:
        if not assignment.slot_key:
            continue
        slot = by_key.get(assignment.slot_key)
        if slot is None:
            continue
        if slot.kind == "admin":
            figures = admin.setdefault(slot.key, {})
            figures[base_year] = figures.get(base_year, 0.0) + assignment.row.salary
        else:
            sport_figures = coaching.setdefault((slot.gender, slot.sport), {})
            figures = sport_figures.setdefault(slot.label, {})
            figures[base_year] = figures.get(base_year, 0.0) + assignment.row.salary

    return admin, coaching


def manual_figures(
    entries: list[tuple[str, float]],
    slots: list[Slot],
    base_year: str,
) -> tuple[dict[str, dict[str, float]], dict[tuple[str, str], dict[str, dict[str, float]]]]:
    """The same, from a typed grid of (slot key, salary)."""
    rows = [
        SourceRow(index=index, title="", salary=salary)
        for index, (_, salary) in enumerate(entries)
    ]
    assignments = [
        Assignment(row=row, slot_key=key, score=1.0)
        for row, (key, _) in zip(rows, entries)
    ]
    return to_plan_figures(assignments, slots, base_year)
