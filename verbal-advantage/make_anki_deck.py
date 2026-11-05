#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["anki-eudic>=0.2.0,<0.3"]
# ///
"""Build an Anki deck of the Verbal Advantage keywords, enriched from LDOCE5++.

Reads the JSONL written by ``extract_words.py`` and writes one ``.apkg`` with a
subdeck per level (``Verbal Advantage::Level 01`` ...). Each card shows the
book's pronunciation, definition and discussion next to the LDOCE5++ entry:
BrE/AmE IPA and audio, senses with Chinese translations, collocations and origin.

The LDOCE lookup and rendering come from Anki-Eudic
(https://github.com/Sorosliu1029/Anki-Eudic). The deck has its own note type and
GUIDs, so importing it never touches notes exported by Anki-Eudic itself.
Re-importing a rebuilt deck updates the notes in place and keeps review progress.

Usage:
    uv run make_anki_deck.py words.jsonl -o verbal-advantage.apkg
    uv run make_anki_deck.py words.jsonl --ldoce "~/Library/Eudb_en/LDOCE5++ V 1-35.mdx"
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import tempfile
from pathlib import Path

import genanki
from anki_eudic.apkg import MODEL as EUDIC_MODEL
from anki_eudic.apkg import deck_id, media_name, render_entry
from anki_eudic.dictionary import DictionaryEntry, LdoceDictionary

DECK_NAME = "Verbal Advantage"
# Never change these: a new model ID or field list makes Anki import a second note
# type, and a new GUID namespace duplicates every note. Templates and CSS may change.
MODEL_ID = 1_843_520_917
GUID_NAMESPACE = "verbal-advantage"
FIELDS = (
    "Word",
    "Level",
    "Number",
    "BookPronunciation",
    "BookDefinition",
    "BookDiscussion",
    "PhoneticUK",
    "PhoneticUS",
    "AudioUK",
    "AudioUS",
    "PartOfSpeech",
    "Definition",
    "Extra",
)

_FRONT = """\
<div class="badge">Verbal Advantage · Level {{Level}} · Word {{Number}}</div>
<div class="word">{{Word}}</div>
<div class="phonetic">
<span class="pron">{{#PhoneticUK}}<span class="accent">BrE</span> {{PhoneticUK}}{{/PhoneticUK}} \
{{AudioUK}}</span>
<span class="pron">{{#PhoneticUS}}<span class="accent">AmE</span> {{PhoneticUS}}{{/PhoneticUS}} \
{{AudioUS}}</span>
</div>
"""
_BACK = """\
{{FrontSide}}
<hr id="answer">
<div class="book">
<div class="book-pron">({{BookPronunciation}})</div>
<div class="book-def">{{BookDefinition}}</div>
{{#BookDiscussion}}<details class="book-disc"><summary>Discussion</summary>\
{{BookDiscussion}}</details>{{/BookDiscussion}}
</div>
{{#Definition}}<div class="section-title">LDOCE {{#PartOfSpeech}}· {{PartOfSpeech}}{{/PartOfSpeech}}</div>
<div class="definition" \
onclick="var p = event.target.closest('.reveal'); if (p) p.classList.toggle('shown');">\
{{Definition}}</div>{{/Definition}}
{{#Extra}}<div class="extra">{{Extra}}</div>{{/Extra}}
"""
# Reuse Anki-Eudic's styling for the LDOCE part so both decks look alike.
_CSS = (
    EUDIC_MODEL.css
    + """\
.badge { text-align: center; font-size: .7em; color: #999; letter-spacing: .05em; }
.book { background: #f6f3ea; border-radius: 6px; padding: .6em .8em; }
.book-pron { color: #666; font-size: .9em; }
.book-def { font-weight: 600; margin: .2em 0; }
.book-disc summary { cursor: pointer; color: #2a6db0; font-size: .9em; }
.book-disc p { margin: .5em 0; }
.section-title { margin-top: .8em; font-size: .8em; font-weight: 600; color: #2a6db0;
  text-transform: uppercase; letter-spacing: .05em; }
.nightMode .book { background: #2b2a26; }
.nightMode .book-pron, .nightMode .badge { color: #aaa; }
"""
)

MODEL = genanki.Model(
    MODEL_ID,
    "Verbal Advantage Word",
    fields=[{"name": name} for name in FIELDS],
    templates=[{"name": "Recognize", "qfmt": _FRONT, "afmt": _BACK}],
    css=_CSS,
    sort_field_index=0,
)


def default_ldoce() -> str:
    """Return ``$ANKI_EUDIC_LDOCE`` or an LDOCE5++ ``.mdx`` in Eudic's macOS library."""
    if os.environ.get("ANKI_EUDIC_LDOCE"):
        return os.environ["ANKI_EUDIC_LDOCE"]
    found = sorted((Path.home() / "Library" / "Eudb_en").glob("LDOCE5*.mdx"))
    return str(found[0]) if found else ""


def level_deck(level: int) -> str:
    # Zero-padded so Anki lists Level 10 after Level 09, not after Level 1.
    return f"{DECK_NAME}::Level {level:02d}"


def lookup(dictionary: LdoceDictionary, word: str) -> DictionaryEntry | None:
    """Look ``word`` up, falling back to inflected headwords LDOCE lists instead.

    The book teaches some words in a form LDOCE only has inflected, e.g. it
    lists "vicissitudes", "machinations" and "protracted".
    """
    for candidate in (word, word + "s", word + "es", word + "ed", word + "d"):
        entry = dictionary.lookup(candidate)
        if entry is not None and entry.homographs:
            return entry
    return None


def _text(value: str) -> str:
    return html.escape(value, quote=False)


def _paragraphs(text: str) -> str:
    return "".join(f"<p>{_text(p)}</p>" for p in text.split("\n\n") if p.strip())


def build(
    entries: list[dict],
    destination: Path,
    dictionary: LdoceDictionary | None,
    *,
    audio: bool = True,
) -> None:
    decks: dict[int, genanki.Deck] = {}
    missing: list[str] = []
    with tempfile.TemporaryDirectory(prefix="verbal-advantage-") as media_dir:
        media: dict[str, Path] = {}

        def sound(resource: str) -> str:
            if not resource or dictionary is None or not audio:
                return ""
            name = media_name(resource)
            if name not in media:
                data = dictionary.resource(resource)
                if data is None:
                    return ""
                media[name] = Path(media_dir, name)
                media[name].write_bytes(data)
            return f"[sound:{name}]"

        for item in entries:
            word = item["word"].lower()
            fields: dict[str, str] = dict.fromkeys(FIELDS, "")
            fields.update(
                Word=_text(word),
                Level=str(item["level"]),
                Number=str(item["number"]),
                BookPronunciation=_text(item["pronunciation"]),
                BookDefinition=_text(item["definition"]),
                BookDiscussion=_paragraphs(item["content"]),
            )
            entry = lookup(dictionary, word) if dictionary is not None else None
            if entry is not None:
                fields.update({k: v for k, v in render_entry(entry).items() if v})
                fields["AudioUK"] = sound(entry.pronunciation.audio_bre)
                fields["AudioUS"] = sound(entry.pronunciation.audio_ame)
            elif dictionary is not None:
                missing.append(word)

            level = item["level"]
            if level not in decks:
                decks[level] = genanki.Deck(deck_id(level_deck(level)), level_deck(level))
            decks[level].add_note(
                genanki.Note(
                    model=MODEL,
                    fields=[fields[name] for name in FIELDS],
                    guid=genanki.guid_for(GUID_NAMESPACE, word),
                    tags=[f"VerbalAdvantage::Level_{level:02d}"],
                )
            )

        package = genanki.Package(
            [decks[level] for level in sorted(decks)],
            media_files=[str(p) for p in media.values()],
        )
        package.write_to_file(str(destination))

    enriched = len(entries) - len(missing) if dictionary is not None else 0
    print(
        f"Wrote {destination}: {len(entries)} notes in {len(decks)} decks, "
        f"{enriched} with LDOCE content, {len(media)} audio files",
        file=sys.stderr,
    )
    if missing:
        print(f"Not found in LDOCE ({len(missing)}): {', '.join(missing)}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("words", help="JSONL written by extract_words.py")
    parser.add_argument("-o", "--output", default="verbal-advantage.apkg", help="output .apkg")
    parser.add_argument(
        "--ldoce",
        default=default_ldoce(),
        help="LDOCE5++ .mdx (default: $ANKI_EUDIC_LDOCE or ~/Library/Eudb_en/LDOCE5*.mdx); "
        "audio comes from the .mdd next to it. Pass '' to build without the dictionary.",
    )
    args = parser.parse_args()

    with open(args.words, encoding="utf-8") as f:
        entries = [json.loads(line) for line in f if line.strip()]
    destination = Path(args.output).expanduser().with_suffix(".apkg")

    if not args.ldoce:
        print("No LDOCE5++ dictionary given; building from the book only.", file=sys.stderr)
        build(entries, destination, None)
        return
    mdx = Path(args.ldoce).expanduser()
    mdd = mdx.with_suffix(".mdd")
    if not mdd.exists():
        print(f"No {mdd.name} next to the .mdx; building without audio.", file=sys.stderr)
    with LdoceDictionary(mdx, mdd if mdd.exists() else None) as dictionary:
        build(entries, destination, dictionary, audio=mdd.exists())


if __name__ == "__main__":
    main()
