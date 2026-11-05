#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pymupdf>=1.24"]
# ///
"""Extract the roots and words taught in *Word Power Made Easy* into JSONL.

Writes two files:

``roots.jsonl``, one prefix, root or suffix per line, from the book's
"Review of Etymology" tables (variant spellings of one element are merged)::

    {"id": "anthropos", "display": "anthropos", "forms": ["anthropos"], "kind": "root",
     "meaning": "mankind", "chapter": 3, "examples": ["anthropologist"]}

``words.jsonl``, one word per line, from the "Can you pronounce the words?" lists::

    {"word": "misanthrope", "chapter": 3, "session": 1, "pronunciation": "MIS′-Ən-thrōp′",
     "definition": "hates people"}

``examples`` are the words the book itself pairs with a root ("Can you recognize
roots?"); ``definition`` is the answer to the book's matching exercises. Lewis's
pronunciation is left empty where the PDF lost a phonetic glyph.

Usage:
    uv run extract.py "Word Power Made Easy_ ... .pdf" -o .
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

import pymupdf

# Calibre mapped the ligatures of the source ePub to unrelated Greek glyphs.
LIGATURES = str.maketrans({"ϧ": "fi", "ϱ": "ff", "ϩ": "fl", "ϫ": "ffi", "Ϯ": "ffl"})

TOC_RE = re.compile(r"(\d+)\.\s+(How to [^(]+?)\s*\(\s*Sessions?\s+(\d+)(?:\s*–\s*(\d+))?\s*\)")
SESSION_RE = re.compile(r"^SESSION (\d+)$")
ROW_RE = re.compile(r"^(\d+)\.\s+(.+)$")
LETTER_RE = re.compile(r"^([a-z])\.\s+(.+)$")
KEY_PAIR_RE = re.compile(r"(\d+)\s*[–-]\s*([a-z])\b")
WORD_RE = re.compile(r"^[a-z][a-z’' -]*[a-z]$")
SECTION_HEADINGS = (
    "REVIEW OF ETYMOLOGY",
    "USING THE WORDS",
    "ORIGINS AND RELATED WORDS",
    "CHAPTER REVIEW",
    "TEASER",
    "BRIEF INTERMISSION",
    "IDEAS",
    "(End of Session",
    "Can you",
    "Do you",
    "Can the words",
    "KEY",
    "PART ",
)


def read_lines(pdf_path: str) -> list[str]:
    """Return the book's non-empty lines, with page breaks and ligatures removed."""
    lines = []
    with pymupdf.open(pdf_path) as doc:
        for page in doc:
            for line in page.get_text().translate(LIGATURES).splitlines():
                if line.strip():
                    lines.append(line.strip())
    return lines


def is_heading(line: str) -> bool:
    return line.startswith(SECTION_HEADINGS) or bool(SESSION_RE.match(line))


def parse_chapters(lines: list[str]) -> tuple[dict[int, dict], dict[int, int]]:
    """Read teaching chapters and their sessions from the table of contents."""
    chapters, session_chapter = {}, {}
    for line in lines[: lines.index("SESSION 1")]:
        match = TOC_RE.match(line)
        if not match:
            continue
        number, title = int(match.group(1)), match.group(2).strip()
        if "Comprehensive Test" in title or number in chapters:
            continue
        first = int(match.group(3))
        last = int(match.group(4) or first)
        title = re.sub(r"^How to (Talk About )?", "", title)
        chapters[number] = {"chapter": number, "title": title[0].upper() + title[1:]}
        for session in range(first, last + 1):
            session_chapter[session] = number
    return chapters, session_chapter


def sessions(lines: list[str]) -> list[int]:
    """Return the session number each line belongs to (0 before Session 1)."""
    current, result = 0, []
    for line in lines:
        match = SESSION_RE.match(line)
        if match:
            current = int(match.group(1))
        result.append(current)
    return result


def normalize(form: str) -> str:
    """Lowercase and drop diacritics: 'anthrōpos' -> 'anthropos'."""
    decomposed = unicodedata.normalize("NFKD", form.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c)).strip()


def element_forms(element: str) -> list[str]:
    """Split 'facio (fec-, fic-, fy)' or 'opsis, optikos' into its forms."""
    return [normalize(f) for f in re.split(r"[,()/]| or ", element) if f.strip()]


def element_kind(forms: list[str], meaning: str = "") -> str:
    first = forms[0]
    if first.startswith("-"):
        return "suffix"
    # "plac-" (to please) is a root's combining form, not a prefix.
    if first.endswith("-") and not meaning.startswith("to "):
        return "prefix"
    return "root"


def parse_etymology_tables(lines, line_sessions, session_chapter) -> list[dict]:
    """Read every row of the 'Review of Etymology' tables."""
    rows = []
    i = 0
    while i < len(lines):
        if lines[i] != "REVIEW OF ETYMOLOGY":
            i += 1
            continue
        # Skip the intro ("Can you recall a word ...?") up to the column headers.
        header = next((j for j in range(i, min(i + 12, len(lines))) if lines[j] == "MEANING"), i)
        i = header + 1
        while i < len(lines) and not is_heading(lines[i]):
            match = ROW_RE.match(lines[i])
            if match:
                element = match.group(2).strip()
                meaning_lines = []
                i += 1
                while i < len(lines) and "___" not in lines[i] and not ROW_RE.match(lines[i]):
                    meaning_lines.append(lines[i])
                    i += 1
                chapter = session_chapter.get(line_sessions[i - 1])
                if chapter and meaning_lines:
                    rows.append(
                        {"element": element, "meaning": " ".join(meaning_lines), "chapter": chapter}
                    )
                continue
            i += 1
    return rows


def parse_root_examples(lines: list[str]) -> list[tuple[str, str]]:
    """Read the (root, example word) pairs of the 'Can you recognize roots?' reviews."""
    pairs = []
    i = 0
    while i < len(lines):
        if "Can you recognize roots" not in lines[i]:
            i += 1
            continue
        i += 1
        element = None
        while i < len(lines) and not lines[i].startswith(("KEY", "C. ", "CHAPTER REVIEW")):
            match = ROW_RE.match(lines[i])
            if match:
                element = match.group(2).strip()
            elif lines[i].startswith("EXAMPLE") and element:
                example = lines[i].removeprefix("EXAMPLE").strip().lower()
                if example:
                    pairs.append((element, example))
                element = None
            i += 1
    return pairs


# Grammar words in glosses such as "noun suffix attached to adjectives". ("in" is
# kept: it tells the prefix "in-" (in) from "in-" (not).)
MEANING_STOPWORDS = {
    "a", "an", "the", "to", "of", "or", "and", "for", "etc", "prefix", "suffix", "suffixes",
    "noun", "nouns", "adjective", "adjectives", "verb", "verbs", "adverb", "added", "aded",
    "attached", "ending", "ate",
}


def meaning_words(meaning: str) -> set[str]:
    return set(re.findall(r"[a-z]+", meaning.lower())) - MEANING_STOPWORDS


def compatible(a: str, b: str) -> bool:
    """Whether two glosses of one affix can describe the same element.

    A generic gloss ("noun suffix") fits any other; otherwise they must share a word.
    """
    words_a, words_b = meaning_words(a), meaning_words(b)
    return not words_a or not words_b or bool(words_a & words_b)


def merge_meanings(old: str, new: str) -> str:
    """Add only the phrases of ``new`` that say something new.

    'suffering, disease' + 'disease, suffering, feeling' -> 'suffering, disease, feeling';
    'noun suffix added to verbs' + 'noun suffix attached to verbs' -> unchanged.
    """
    known = meaning_words(old)
    added = []
    for phrase in re.split(r"[;,](?![^(]*\))", new):  # not inside parentheses
        phrase = phrase.strip()
        fresh = meaning_words(phrase) - known
        if phrase and fresh:
            added.append(phrase)
            known |= fresh
    return ", ".join([old, *added])


def merge_roots(rows: list[dict], examples: list[tuple[str, str]]) -> list[dict]:
    """Merge table rows that share a form (e.g. 'paidos (ped-)' and 'paidos')."""
    roots: list[dict] = []
    by_form: dict[str, list[dict]] = {}
    by_bare: dict[str, list[dict]] = {}  # forms without hyphens
    for row in rows:
        forms = element_forms(row["element"])
        if not forms:
            continue
        # Affixes like "in-" mean both "in" and "not", so they only merge with an entry
        # whose meaning shares a word ("not; negative prefix" ~ "negative prefix").
        is_affix = element_kind(forms, row["meaning"]) != "root"
        root = next(
            (
                candidate
                for f in forms
                for candidate in by_form.get(f, [])
                if not is_affix or compatible(candidate["meaning"], row["meaning"])
            ),
            None,
        ) or next(
            # The book writes some affixes both ways, e.g. "anti-" and "anti".
            (
                candidate
                for f in forms
                for candidate in by_bare.get(f.strip("-"), [])
                if compatible(candidate["meaning"], row["meaning"])
            ),
            None,
        )
        is_merge = root is not None
        if root is None:
            ids = {r["id"] for r in roots}
            root_id = forms[0] if forms[0] not in ids else f"{forms[0]} ({row['meaning']})"
            root = {
                "id": root_id,
                "display": row["element"],
                "forms": [],
                "kind": element_kind(forms, row["meaning"]),
                "meaning": row["meaning"],
                "chapter": row["chapter"],
                "examples": [],
            }
            roots.append(root)
        else:
            # Keep the fullest spelling of the same element ("paidos (ped-)" over
            # "paidos"), but not a cross-reference such as "fic- (facio)".
            same_head = forms[0] == root["forms"][0]
            if same_head and len(row["element"]) > len(root["display"]):
                root["display"] = row["element"]
            root["meaning"] = merge_meanings(root["meaning"], row["meaning"])
        for form in forms:
            # A merged row may not take another element's form: the book's slip
            # "ana-, a-" (negative prefix) must not make "ana-" a spelling of "a-".
            owned = [r for r in by_form.get(form, []) if r is not root]
            if form not in root["forms"] and not (is_merge and owned):
                root["forms"].append(form)
            if root not in by_form.setdefault(form, []):
                by_form[form].append(root)
            if root not in by_bare.setdefault(form.strip("-"), []):
                by_bare[form.strip("-")].append(root)

    # The book once glosses the prefix "in-" as a "negative suffix"; drop such slips.
    wrong = {"prefix": "suffix", "suffix": "prefix"}
    for root in roots:
        if root["kind"] in wrong:
            phrases = re.split(r", (?![^(]*\))", root["meaning"])
            kept = [p for p in phrases if wrong[root["kind"]] not in p]
            root["meaning"] = ", ".join(kept) or root["meaning"]

    for element, example in examples:
        root = next((by_form[f][0] for f in element_forms(element) if f in by_form), None)
        if root is not None and example not in root["examples"]:
            root["examples"].append(example)
    return roots


def parse_pronunciations(lines, line_sessions, session_chapter) -> list[dict]:
    """Read every 'Can you pronounce the words?' list."""
    words: dict[str, dict] = {}
    i = 0
    while i < len(lines):
        if not lines[i].startswith("Can you pronounce"):
            i += 1
            continue
        i += 1
        while i < len(lines) and not is_heading(lines[i]):
            match = ROW_RE.match(lines[i])
            if match and i + 1 < len(lines):
                word = normalize(match.group(2)).removeprefix("to ")
                pron = lines[i + 1]
                after = lines[i + 2] if i + 2 < len(lines) else ""
                # Each item is a word line and a pronunciation line. A phonetic glyph
                # the PDF lost splits the pronunciation, leaving extra lines behind.
                broken = not (ROW_RE.match(after) or is_heading(after))
                chapter = session_chapter.get(line_sessions[i])
                if chapter and WORD_RE.match(word) and word not in words:
                    words[word] = {
                        "word": word,
                        "chapter": chapter,
                        "session": line_sessions[i],
                        "pronunciation": "" if broken or ROW_RE.match(pron) else pron,
                        "definition": "",
                    }
                i += 2
                continue
            i += 1
    return list(words.values())


def parse_definitions(lines: list[str], known: set[str]) -> dict[str, str]:
    """Answer the 'Can you work with the words?' matching exercises with their KEYs."""
    definitions: dict[str, str] = {}
    numbered: dict[int, str] = {}
    lettered: dict[str, str] = {}
    last: dict | None = None
    active = False
    for line in lines:
        if line.startswith("Can you work with the words"):
            active, numbered, lettered, last = True, {}, {}, None
            continue
        if not active:
            continue
        if line.startswith("KEY"):
            pairs = [(int(n), letter) for n, letter in KEY_PAIR_RE.findall(line)]
            number_side = sum(numbered.get(n, "") in known for n, _ in pairs)
            letter_side = sum(lettered.get(letter, "") in known for _, letter in pairs)
            for n, letter in pairs:
                left, right = numbered.get(n, ""), lettered.get(letter, "")
                word, meaning = (left, right) if number_side >= letter_side else (right, left)
                if word in known and meaning and word not in definitions:
                    definitions[word] = meaning
            numbered, lettered, last = {}, {}, None
            continue
        if is_heading(line):
            active = False
            continue
        if match := ROW_RE.match(line):
            numbered[int(match.group(1))] = normalize(match.group(2))
            last = None  # a numbered item is a word; never continue it
        elif match := LETTER_RE.match(line):
            lettered[match.group(1)] = match.group(2).strip()
            last = {"letter": match.group(1)}
        elif last is not None:
            lettered[last["letter"]] += " " + line  # a wrapped definition
    return definitions


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("pdf", help="path to the Word Power Made Easy PDF")
    parser.add_argument("-o", "--output-dir", default=".", help="where to write the JSONL files")
    args = parser.parse_args()

    lines = read_lines(args.pdf)
    chapters, session_chapter = parse_chapters(lines)
    line_sessions = sessions(lines)

    rows = parse_etymology_tables(lines, line_sessions, session_chapter)
    roots = merge_roots(rows, parse_root_examples(lines))
    words = parse_pronunciations(lines, line_sessions, session_chapter)
    definitions = parse_definitions(lines, {w["word"] for w in words})
    for word in words:
        word["definition"] = definitions.get(word["word"], "")
        word["chapter_title"] = chapters[word["chapter"]]["title"]
    for root in roots:
        root["chapter_title"] = chapters[root["chapter"]]["title"]

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, records in (("roots.jsonl", roots), ("words.jsonl", words)):
        with open(out / name, "w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(
        f"{len(chapters)} chapters; {len(rows)} etymology rows -> {len(roots)} roots "
        f"({sum(bool(r['examples']) for r in roots)} with book examples); "
        f"{len(words)} words ({sum(bool(w['definition']) for w in words)} with book definitions, "
        f"{sum(bool(w['pronunciation']) for w in words)} with pronunciation)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
