"""Reading an FRS PDF into positioned words, grouped into visual rows.

Every table parser in this package works the same way: pull each word plus
its x/y position out of the whole document, group words into visual rows, and
then read a particular table out of those rows by finding its heading. The
detail parsers (sponsorship, scholarships, participation, coaching, and
later the sport-expense and other-reporting-items readers) each used to carry
their own private copy of the first two steps.

Those copies were byte-for-byte identical in behaviour - verified against the
report corpus, `_extract_word_stream` and `_group_into_rows` returned equal
results in all four - so reading one report meant doing the same 1.7s of work
once per parser. Only ~1ms per parser is actually specific to its table.

At a handful of institutions that waste is invisible. At fifty it is most of
the runtime, and on a shared-CPU host it is the difference between a report
set that builds and one that appears to hang. Hence one implementation here,
called once per PDF by `load_institution`, with the result handed to every
parser.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pdfplumber

from pfa.core.domain.exceptions import ExtractionError

#: Word 'top' values within this many points are treated as the same visual
#: row. Rows in these reports are ~14pt apart, so 2pt only ever merges the
#: sub-pixel jitter within a single rendered line.
ROW_MERGE_TOLERANCE_PT = 2.0

#: How many pages to look at before deciding a document has no text layer.
#: Page 1 of an FRS report always carries the "Reporting Institution:" header,
#: so this only has to be deep enough to survive an unexpected cover page.
_TEXT_LAYER_PROBE_PAGES = 5

#: A pdfplumber word reduced to what the parsers use:
#: {"page", "top", "x0", "x1", "text"}.
#:
#: x1 (the right edge) matters for the per-sport expense tables, whose figures
#: are right-aligned: a column's x0 moves with the width of the number in it,
#: while its x1 is identical down the whole column.
Word = dict


def require_text_layer(pdf_path: Path | str) -> None:
    """Fails fast on a PDF with no extractable text.

    A scanned or photographed report is a valid PDF full of pictures of a
    table, with no text objects to read. Every parser here works from word
    positions, so there is nothing to find - but discovering that by walking
    the document is ruinously expensive: the two such files in the report
    corpus are ~18MB of page images each, and reading them to completion took
    104 seconds and several GB of memory before failing anyway. On a
    memory-capped host that doesn't fail the file, it kills the whole app,
    taking every other report in the batch with it.

    Probing a few pages costs a fraction of a second and turns that into an
    immediate, accurate message.
    """
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages[:_TEXT_LAYER_PROBE_PAGES]:
            has_chars = bool(page.chars)
            page.flush_cache()
            if has_chars:
                return
    raise ExtractionError(
        f"{Path(pdf_path).name} has no readable text - it looks like a scanned or "
        "photographed report rather than the original PDF from the FRS system. "
        "Ask the institution for the original file, or export it from FRS again."
    )


def extract_word_stream(pdf_path: Path | str) -> list[Word]:
    """Every word in the document, with the page and position needed to place
    it in a table column.
    """
    stream: list[Word] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_index, page in enumerate(pdf.pages):
            for word in page.extract_words():
                stream.append(
                    {
                        "page": page_index,
                        "top": word["top"],
                        "x0": word["x0"],
                        "x1": word["x1"],
                        "text": word["text"],
                    }
                )
            # Released as we go. pdfplumber caches every page's parsed objects
            # on the page, so without this the whole document stays resident
            # and memory scales with page count rather than staying flat.
            page.flush_cache()
    return stream


def group_into_rows(stream: list[Word]) -> list[list[Word]]:
    """Groups words into visual rows: same page, 'top' within tolerance.

    Words are bucketed by rounded 'top', then adjacent buckets on the same
    page within the tolerance are merged into one row. Buckets are visited in
    document order, so each group is anchored on its topmost bucket and a run
    of buckets 1pt apart can't chain into one oversized row.

    Each row is returned sorted left-to-right, which is what lets the callers
    read a value by which column it sits in rather than by its position in
    the line.
    """
    by_key: dict[tuple[int, int], list[Word]] = defaultdict(list)
    for word in stream:
        by_key[(word["page"], round(word["top"]))].append(word)

    keys = sorted(by_key)
    rows: list[list[Word]] = []
    index = 0
    while index < len(keys):
        page, top = keys[index]
        group = list(by_key[keys[index]])
        next_index = index + 1
        while next_index < len(keys):
            other_page, other_top = keys[next_index]
            if other_page != page or other_top - top > ROW_MERGE_TOLERANCE_PT:
                break
            group.extend(by_key[keys[next_index]])
            next_index += 1
        group.sort(key=lambda w: w["x0"])
        rows.append(group)
        index = next_index
    return rows


def read_rows(pdf_path: Path | str) -> list[list[Word]]:
    """The whole document as grouped rows - the input every detail parser wants."""
    return group_into_rows(extract_word_stream(pdf_path))
