#!/usr/bin/env python3
"""Extract the keywords of each level from *Verbal Advantage* into JSONL.

Each output line is one keyword:
    {"level": 1, "number": 1, "word": "PARAPHRASE", "pronunciation": "PAR-uh-frayz",
     "definition": "...", "content": "...", "page": 11}

Usage:
    pip install pypdf
    python extract_words.py "Verbal Advantage_ ... .pdf" -o words.jsonl
"""

import argparse
import json
import re
import sys

from pypdf import PdfReader

LEVEL_RE = re.compile(r"^Level\s+(\d+)\s*$")
# "Word 1:" alone, or "Word 1: ADVOCATE (AD-vuh-kayt)" on one line.
WORD_RE = re.compile(r"^Word\s+(\d+):\s*(.*)$")
# Page numbers and the watermark the ePub-to-PDF converter stamped on pages.
PAGE_FOOTER_RE = re.compile(r"^(Page\s+\d+|ABC Amber ePub Converter Trial version.*)\s*$")
# A keyword's discussion ends where the review quiz for its group begins.
SECTION_END_RE = re.compile(r"^Let's\s+review\b", re.IGNORECASE)
# Body lines are justified, which pads them with runs of spaces. The last line
# of a paragraph is not justified, so a line that ends a sentence and is either
# short or free of padding is treated as the end of a paragraph.
PARAGRAPH_END_MAX_LEN = 60
SENTENCE_END_RE = re.compile(r"[.!?:”’)]$")


def read_lines(pdf_path):
    """Yield (page_number, line) for every non-footer line in the book."""
    reader = PdfReader(pdf_path)
    for page_no, page in enumerate(reader.pages, start=1):
        for line in (page.extract_text() or "").splitlines():
            if not PAGE_FOOTER_RE.match(line.strip()):
                yield page_no, line


def clean(text):
    """Collapse justification spaces and italics artifacts like 'word ,'."""
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r" ([,.;:!?)’”])", r"\1", text)


def to_paragraphs(lines):
    paragraphs, current = [], []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        current.append(stripped)
        unjustified = "  " not in stripped or len(clean(stripped)) < PARAGRAPH_END_MAX_LEN
        if unjustified and SENTENCE_END_RE.search(stripped):
            paragraphs.append(clean(" ".join(current)))
            current = []
    if current:
        paragraphs.append(clean(" ".join(current)))
    return paragraphs


def split_header(text):
    """Split 'ADVOCATE (AD-vuh-kayt) rest...' into (word, pronunciation, rest)."""
    match = re.match(r"^\s*([^()]+?)\s*\(([^)]*)\)\s*(.*)$", text, re.DOTALL)
    if not match:
        return clean(text), "", ""
    return clean(match.group(1)), clean(match.group(2)), match.group(3)


def parse_entry(level, number, page, header_rest, body_lines):
    # The headword and pronunciation may sit on the "Word N:" line or on the
    # lines that follow it; join them until the closing parenthesis.
    head = [header_rest] if header_rest.strip() else []
    remaining = list(body_lines)
    while remaining and ")" not in " ".join(head):
        head.append(remaining.pop(0))
        if len(head) > 4:  # no pronunciation found; stop looking
            break
    word, pronunciation, after = split_header("\n".join(head))
    if after.strip():
        remaining = after.splitlines() + remaining

    # The definition always ends at a line break, but its lines are sometimes
    # padded like justified text, so end it at the first finished sentence.
    remaining = [line for line in remaining if line.strip()]
    definition = []
    while remaining:
        definition.append(remaining.pop(0).strip())
        if SENTENCE_END_RE.search(definition[-1]):
            break

    return {
        "level": level,
        "number": number,
        "word": word,
        "pronunciation": pronunciation,
        "definition": clean(" ".join(definition)),
        "content": "\n\n".join(to_paragraphs(remaining)),
        "page": page,
    }


def extract(pdf_path):
    entries = []
    level = None
    seen_levels = set()
    current = None  # [number, page, header_rest, body_lines]

    def flush():
        if current is not None:
            entries.append(parse_entry(level, *current))

    for page, line in read_lines(pdf_path):
        stripped = line.strip()

        level_match = LEVEL_RE.match(stripped)
        if level_match:
            n = int(level_match.group(1))
            # The table of contents lists "Level 1".."Level 10" before the
            # real chapters; a level only starts for real once it repeats.
            if n in seen_levels:
                flush()
                current = None
                level = n
            seen_levels.add(n)
            continue

        if level is None:
            continue

        word_match = WORD_RE.match(stripped)
        if word_match:
            flush()
            current = [int(word_match.group(1)), page, word_match.group(2), []]
            continue

        if current is not None:
            if SECTION_END_RE.match(stripped):
                flush()
                current = None
            else:
                current[3].append(line)

    flush()
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pdf", help="path to the Verbal Advantage PDF")
    parser.add_argument("-o", "--output", default="-", help="output JSONL file (default: stdout)")
    args = parser.parse_args()

    entries = extract(args.pdf)
    out = sys.stdout if args.output == "-" else open(args.output, "w", encoding="utf-8")
    with out:
        for entry in entries:
            out.write(json.dumps(entry, ensure_ascii=False) + "\n")

    counts = {}
    for entry in entries:
        counts[entry["level"]] = counts.get(entry["level"], 0) + 1
    summary = ", ".join(f"L{lvl}: {n}" for lvl, n in sorted(counts.items()))
    print(f"Extracted {len(entries)} words ({summary})", file=sys.stderr)


if __name__ == "__main__":
    main()
