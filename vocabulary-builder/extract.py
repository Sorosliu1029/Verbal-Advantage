#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["mobi>=0.3"]
# ///
"""Extract the roots and words of *Merriam-Webster's Vocabulary Builder* into JSONL.

Reads the Kindle ``.mobi`` and writes, in book order:

``roots.jsonl``, one root per line::

    {"root": "BENE", "unit": 1, "order": 1, "meaning": "Latin, “well”",
     "intro": "<b>BENE</b> is Latin for “well.” ...", "words": ["benediction", ...]}

``words.jsonl``, one word per line::

    {"word": "benediction", "unit": 1, "order": 1, "section": "BENE", "kind": "root",
     "definition": "A prayer that asks for God's blessing, ...",
     "example": "The moment the bishop had finished his benediction, ...",
     "notes": "In <i>benediction</i>, the <i>bene</i> root is joined by ..."}

``kind`` is "root" for words under a root, or "special" for the sections such as
"Words from Mythology" (then ``section`` is that title). ``intro`` and ``notes`` are
HTML with only ``<i>`` and ``<b>`` kept. Quizzes are skipped. The book draws its
pronunciations as images, so they are not extracted here.

Usage:
    uv run extract.py "Merriam-Webster's Vocabulary Builder - Mary Wood Cornog.mobi" -o .
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
from pathlib import Path

import mobi

UNIT_RE = re.compile(r"<h1>Unit (\d+)</h1>\s*<div[^>]*>(.*?)</div>", re.S)
NAV_RE = re.compile(r'<a href="#filepos(\d+)"\s*>(.*?)</a>', re.S)
PARAGRAPH_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.S)
TAG_RE = re.compile(r"<[^>]+>")
KEEP_TAGS_RE = re.compile(r"</?(i|b)>")
LANGUAGES = ("Latin", "Greek", "French", "Old English", "Middle English", "Italian",
             "Spanish", "German", "Arabic", "Hebrew", "Sanskrit")


def read_book(mobi_path: str) -> str:
    """Unpack the .mobi and return its HTML (Kindle 7 format, windows-1252)."""
    temp_dir, html_path = mobi.extract(mobi_path)
    try:
        return Path(html_path).read_bytes().decode("cp1252", errors="replace")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def text(fragment: str) -> str:
    """Plain text of an HTML fragment."""
    return re.sub(r"\s+", " ", html.unescape(TAG_RE.sub("", fragment))).strip()


def clean_html(fragment: str) -> str:
    """Escape an HTML fragment's text but keep its <i> and <b> tags."""
    parts = []
    pos = 0
    for match in TAG_RE.finditer(fragment):
        parts.append(html.escape(html.unescape(fragment[pos : match.start()]), quote=False))
        if KEEP_TAGS_RE.fullmatch(match.group(0)):
            parts.append(match.group(0))
        pos = match.end()
    parts.append(html.escape(html.unescape(fragment[pos:]), quote=False))
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def sections(book: str) -> list[dict]:
    """Every root and special section, with the HTML between its anchor and the next.

    Each unit opens with a list of links to its sections and quizzes; the anchors
    those links point to cut the book into sections.
    """
    targets = []
    for unit_match in UNIT_RE.finditer(book):
        unit = int(unit_match.group(1))
        for filepos, label in NAV_RE.findall(unit_match.group(2)):
            label = text(label)
            if label.startswith(("Quiz", "Review Quizzes")):
                kind = "quiz"
            elif label.upper() == label:
                kind = "root"  # roots are written in capitals: BENE, CRAC/CRAT
            else:
                kind = "special"  # "Words from Mythology and History"
            anchor = book.find(f'<a id="filepos{filepos}"')
            if anchor >= 0:
                targets.append({"unit": unit, "label": label, "kind": kind, "start": anchor})
    # Unit 28 links twice to one anchor for its two PRO roots; keep one section.
    targets = list({t["start"]: t for t in sorted(targets, key=lambda t: -t["start"])}.values())
    targets.sort(key=lambda t: t["start"])
    end_of_units = book.find("<h1>Answers</h1>")
    for i, target in enumerate(targets):
        end = targets[i + 1]["start"] if i + 1 < len(targets) else end_of_units
        target["html"] = book[target["start"] : end]
    return [t for t in targets if t["kind"] != "quiz"]


def parse_entry(paragraph: str) -> dict | None:
    """Split a word paragraph into headword, definition, example and notes."""
    head = re.match(r"\s*<b>(.*?)</b>", paragraph, re.S)
    if not head or "•" not in paragraph:
        return None
    rest = re.sub(r"<img[^>]*>", "", paragraph[head.end() :])  # pronunciation glyphs
    parts = [p for p in re.split(r"<br\s*/?>\s*<br\s*/?>", rest) if text(p)]
    bullet = next((i for i, p in enumerate(parts) if "•" in p), None)
    if bullet is None:
        return None
    return {
        "word": text(head.group(1)),
        "definition": text(" ".join(parts[:bullet])),
        "example": text(parts[bullet]).lstrip("• ").strip(),
        "notes": [clean_html(p) for p in parts[bullet + 1 :]],
    }


def root_meaning(intro: str) -> str:
    """'BENE is Latin for “well.”' -> 'Latin, “well”'."""
    end = re.search(r"[.!?][”\"]?\s", intro)
    first = intro[: end.end()].strip() if end else intro
    language = next((lang for lang in LANGUAGES if lang in first), "")
    gloss = re.search(r"“([^”]+?)[,.]?”", first) or re.search(r"“([^”]+?)[,.]?”", intro)
    meaning = gloss.group(1) if gloss else ""
    if language and meaning:
        return f"{language}, “{meaning}”"
    return meaning or first


def extract(book: str) -> tuple[list[dict], list[dict]]:
    roots, words = [], []
    seen: set[str] = set()
    for section in sections(book):
        root = None
        current = None
        for paragraph in PARAGRAPH_RE.findall(section["html"]):
            entry = parse_entry(paragraph)
            intro = re.match(r"\s*<b>([A-Z][A-Z/ -]*)</b>", paragraph)
            if section["kind"] == "root" and entry is None and intro:
                # A root's introduction. One section can hold two (PRO "for", PRO "before").
                root = {
                    "root": section["label"],
                    "unit": section["unit"],
                    "order": len(roots) + 1,
                    "meaning": root_meaning(text(paragraph)),
                    "intro": clean_html(paragraph),
                    "words": [],
                }
                roots.append(root)
                current = None
            elif entry is not None:
                if entry["word"].lower() in seen:
                    current = None
                    continue
                seen.add(entry["word"].lower())
                current = {
                    "word": entry["word"],
                    "unit": section["unit"],
                    "order": len(words) + 1,
                    "section": section["label"],
                    "kind": section["kind"],
                    "definition": entry["definition"],
                    "example": entry["example"],
                    "notes": entry["notes"],
                }
                words.append(current)
                if root is not None:
                    root["words"].append(entry["word"])
            elif current is not None and text(paragraph):
                current["notes"].append(clean_html(paragraph))  # a notes paragraph of its own
    for word in words:
        word["notes"] = "".join(f"<p>{p}</p>" for p in word["notes"])
    return roots, words


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("mobi", help="path to the Vocabulary Builder .mobi")
    parser.add_argument("-o", "--output-dir", default=".", help="where to write the JSONL files")
    args = parser.parse_args()

    roots, words = extract(read_book(args.mobi))
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, records in (("roots.jsonl", roots), ("words.jsonl", words)):
        with open(out / name, "w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    units = {w["unit"] for w in words}
    special = sum(w["kind"] == "special" for w in words)
    print(
        f"{len(units)} units; {len(roots)} roots; {len(words)} words "
        f"({len(words) - special} under roots, {special} in special sections)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
