#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["anki-eudic>=0.2.0,<0.3"]
# ///
"""Build an Anki deck of the roots and words of *Merriam-Webster's Vocabulary Builder*.

Reads ``roots.jsonl`` and ``words.jsonl`` written by ``extract.py`` and writes one
``.apkg`` with a subdeck per unit (``Vocabulary Builder::Unit 01`` ...), in book order:
each root card followed by the cards for its words.

- **Root** cards: the root -> its meaning, the book's introduction and its words.
- **Word** notes, with two cards each:
  - *Recognize*: the word and its pronunciation -> the book's definition, example
    sentence and notes, and the LDOCE5++ entry (IPA, audio, senses with Chinese
    translations).
  - *Recall*: the book's example sentence with the word blanked out, and the
    definition as a hint -> the word.

Pronunciations come from the Merriam-Webster respellings in Eudic's copy of the book
(``Merriam-Webster's Vocabulary Builder.mdx``). The LDOCE lookup and rendering come
from Anki-Eudic (https://github.com/Sorosliu1029/Anki-Eudic). The note types and
GUIDs are this deck's own, so re-importing a rebuilt deck updates cards in place and
keeps review progress.

Usage:
    uv run make_anki_deck.py -o vocabulary-builder.apkg
    uv run make_anki_deck.py --roots roots.jsonl --words words.jsonl --ldoce PATH --mw PATH
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import tempfile
import unicodedata
from pathlib import Path

import anki_eudic.mdict as mdict
import genanki
from anki_eudic.apkg import MODEL as EUDIC_MODEL
from anki_eudic.apkg import deck_id, media_name, render_entry
from anki_eudic.dictionary import DictionaryEntry, LdoceDictionary
from bs4 import BeautifulSoup

DECK_NAME = "Vocabulary Builder"
EUDIC_DIR = Path.home() / "Library" / "Eudb_en"
# Never change these: a new model ID or field list makes Anki import a second note
# type, and a new GUID namespace duplicates every note. Templates and CSS may change.
ROOT_MODEL_ID = 1_955_604_281
WORD_MODEL_ID = 1_955_604_282
ROOT_GUID_NAMESPACE = "mw-vocabulary-builder-root"
WORD_GUID_NAMESPACE = "mw-vocabulary-builder-word"

ROOT_FIELDS = ("Root", "Meaning", "Intro", "Words", "Unit")
WORD_FIELDS = (
    "Word",
    "Unit",
    "Section",
    "Pronunciation",
    "BookDefinition",
    "Example",
    "Cloze",
    "Notes",
    "PhoneticUK",
    "PhoneticUS",
    "AudioUK",
    "AudioUS",
    "PartOfSpeech",
    "Definition",
    "Extra",
)

_BADGE = '<div class="badge">Vocabulary Builder · Unit {{Unit}}</div>'
_ROOT_FRONT = _BADGE + """
<div class="kind">root</div>
<div class="word">{{Root}}</div>
"""
_ROOT_BACK = """\
{{FrontSide}}
<hr id="answer">
<div class="meaning">{{Meaning}}</div>
<div class="intro">{{Intro}}</div>
<div class="section-title">Words</div><ul class="words-list">{{Words}}</ul>
"""
_PRONUNCIATION = """\
<div class="phonetic">
{{#Pronunciation}}<span class="pron"><span class="accent">MW</span> {{Pronunciation}}</span>{{/Pronunciation}}
{{#PhoneticUK}}<span class="pron"><span class="accent">BrE</span> {{PhoneticUK}} {{AudioUK}}</span>{{/PhoneticUK}}
{{#PhoneticUS}}<span class="pron"><span class="accent">AmE</span> {{PhoneticUS}} {{AudioUS}}</span>{{/PhoneticUS}}
</div>
"""
_DETAILS = """\
<div class="book-def">{{BookDefinition}}</div>
<div class="example">• {{Example}}</div>
<div class="section-label">{{Section}}</div>
{{#Notes}}<details class="notes"><summary>About the word</summary>{{Notes}}</details>{{/Notes}}
{{#Definition}}<div class="section-title">LDOCE {{#PartOfSpeech}}· {{PartOfSpeech}}{{/PartOfSpeech}}</div>
<div class="definition" \
onclick="var p = event.target.closest('.reveal'); if (p) p.classList.toggle('shown');">\
{{Definition}}</div>{{/Definition}}
{{#Extra}}<div class="extra">{{Extra}}</div>{{/Extra}}
"""
_RECOGNIZE_FRONT = _BADGE + '\n<div class="word">{{Word}}</div>\n' + _PRONUNCIATION
_RECOGNIZE_BACK = "{{FrontSide}}\n<hr id=\"answer\">\n" + _DETAILS
# No audio or word on the front: it would give the answer away. Without a blankable
# example sentence the Cloze field is empty and Anki makes no Recall card.
_RECALL_FRONT = (
    "{{#Cloze}}" + _BADGE + """
<div class="kind">fill in the word</div>
<div class="cloze-sentence">{{Cloze}}</div>
<div class="hint">{{BookDefinition}}</div>
{{/Cloze}}"""
)
_RECALL_BACK = (
    "{{FrontSide}}\n<hr id=\"answer\">\n<div class=\"word\">{{Word}}</div>\n"
    + _PRONUNCIATION
    + _DETAILS
)
_CSS = (
    EUDIC_MODEL.css
    + """\
.badge { text-align: center; font-size: .7em; color: #999; letter-spacing: .05em; }
.kind { text-align: center; font-size: .75em; font-weight: 600; color: #b03060;
  text-transform: uppercase; letter-spacing: .08em; }
.meaning { font-size: 1.4em; font-weight: 600; text-align: center; margin: .3em 0; }
.intro { color: #444; margin: .5em 0; }
.book-def { background: #f6f3ea; border-radius: 6px; padding: .5em .8em; font-weight: 600; }
.example { font-style: italic; color: #444; margin: .5em 0; }
.section-label { display: inline-block; border: 1px solid #2a6db0; border-radius: 4px;
  padding: 0 .4em; color: #2a6db0; font-size: .85em; }
.cloze-sentence { font-size: 1.15em; margin: .8em 0; }
.cloze { color: #2a6db0; font-weight: 600; }
.hint { color: #777; text-align: center; }
.notes { margin-top: .8em; font-size: .9em; color: #444; }
.notes summary { cursor: pointer; color: #2a6db0; }
.section-title { margin-top: .8em; font-size: .8em; font-weight: 600; color: #2a6db0;
  text-transform: uppercase; letter-spacing: .05em; }
.words-list .gloss { color: #666; }
.nightMode .book-def { background: #2b2a26; }
.nightMode .badge, .nightMode .intro, .nightMode .example, .nightMode .hint,
.nightMode .notes, .nightMode .words-list .gloss { color: #aaa; }
"""
)

ROOT_MODEL = genanki.Model(
    ROOT_MODEL_ID,
    "Vocabulary Builder Root",
    fields=[{"name": name} for name in ROOT_FIELDS],
    templates=[{"name": "Root → Meaning", "qfmt": _ROOT_FRONT, "afmt": _ROOT_BACK}],
    css=_CSS,
)
WORD_MODEL = genanki.Model(
    WORD_MODEL_ID,
    "Vocabulary Builder Word",
    fields=[{"name": name} for name in WORD_FIELDS],
    templates=[
        {"name": "Recognize", "qfmt": _RECOGNIZE_FRONT, "afmt": _RECOGNIZE_BACK},
        {"name": "Recall", "qfmt": _RECALL_FRONT, "afmt": _RECALL_BACK},
    ],
    css=_CSS,
)


# --- dictionaries -------------------------------------------------------------------


_header_patched = False


def _allow_textual_encrypted_flag() -> None:
    """Let anki-eudic 0.2.0 open MDict files whose header says Encrypted="No"."""
    global _header_patched
    if _header_patched:
        return
    read_header = mdict.MdictFile._read_header

    def patched(self):
        header = read_header(self)
        flag = header.get("Encrypted", "").strip().lower()
        if flag in ("no", "yes"):
            header["Encrypted"] = "0" if flag == "no" else "1"
        return header

    mdict.MdictFile._read_header = patched
    _header_patched = True


class MerriamWebster:
    """Pronunciations from Eudic's copy of the book (``/ˌbe-nə-ˈdik-shən/``)."""

    def __init__(self, path: Path) -> None:
        _allow_textual_encrypted_flag()
        self._file = mdict.MdictFile(path)

    def pronunciation(self, word: str) -> str:
        records = self._file.lookup_text(word) or self._file.lookup_text(word.replace("’", "'"))
        if not records:
            return ""
        span = BeautifulSoup(records[0], "html.parser").find("span", class_="eI2")
        return re.sub(r"\s+", " ", span.get_text()).strip() if span else ""


def default_ldoce() -> str:
    """Return ``$ANKI_EUDIC_LDOCE`` or an LDOCE5++ ``.mdx`` in Eudic's macOS library."""
    if os.environ.get("ANKI_EUDIC_LDOCE"):
        return os.environ["ANKI_EUDIC_LDOCE"]
    found = sorted(EUDIC_DIR.glob("LDOCE5*.mdx"))
    return str(found[0]) if found else ""


def inflections(word: str) -> tuple[str, ...]:
    return (word, word + "s", word + "es", word + "ed", word + "d")


def ldoce_lookup(dictionary: LdoceDictionary, word: str) -> DictionaryEntry | None:
    """Look ``word`` up, falling back to inflected headwords LDOCE lists instead."""
    for candidate in inflections(word):
        entry = dictionary.lookup(candidate)
        if entry is not None and entry.homographs:
            return entry
    return None


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c)).strip()


def american(word: str) -> str:
    """Fold British spellings LDOCE uses as headwords: enamoured -> enamored."""
    for british, us in (("our", "or"), ("ise", "ize"), ("tre", "ter"), ("ae", "e"), ("oe", "e")):
        word = word.replace(british, us)
    return word


def same_headword(entry: DictionaryEntry, word: str) -> bool:
    """False when LDOCE redirected to a related word, whose pronunciation would mislead."""
    headword = american(normalize(entry.headword))
    return headword in {american(form) for form in inflections(normalize(word))}


# --- cards ----------------------------------------------------------------------------


def _text(value: str) -> str:
    return html.escape(value, quote=False)


LETTERS = r"[^\W\d_]"  # letters, accented ones included (vis-à-vis)


def _token_pattern(token: str) -> str:
    """A word as it may appear inflected: hector -> hectored, ostracize -> ostracized."""
    stem = token
    if len(token) > 6 and token[-2:] in ("um", "us", "on"):
        # Latin and Greek plurals: simulacrum -> simulacra (but centurion -/-> centuries)
        ending = re.escape(token[-2:])
        return f"{re.escape(token[:-2])}(?:{ending}{LETTERS}*|a|i)"
    if len(token) > 4 and token[-1] in "ey":
        stem = token[:-1]
    pattern = re.escape(stem)
    if stem != token and token.endswith("y"):
        pattern += "(?:y|i)"
    return pattern + LETTERS + "*"


def cloze(sentence: str, word: str) -> str:
    """The example sentence as HTML with the word blanked out, or '' if not found."""
    tokens = re.findall(LETTERS + "+", word)
    if not tokens:
        return ""
    # Multi-word headwords ("Achilles' heel"): every token, any punctuation between.
    body = r"[\W_]+".join(_token_pattern(t) for t in tokens)
    pattern = re.compile(rf"(?<!{LETTERS}){body}(?!{LETTERS})", re.IGNORECASE)
    parts, pos, found = [], 0, False
    for match in pattern.finditer(sentence):
        parts.append(_text(sentence[pos : match.start()]))
        parts.append('<span class="cloze">[…]</span>')
        pos, found = match.end(), True
    parts.append(_text(sentence[pos:]))
    return "".join(parts) if found else ""


def chapter_deck(unit: int) -> str:
    # Zero-padded so Anki lists Unit 10 after Unit 09, not after Unit 1.
    return f"{DECK_NAME}::Unit {unit:02d}"


def build(
    roots: list[dict],
    words: list[dict],
    destination: Path,
    dictionary: LdoceDictionary | None,
    merriam_webster: MerriamWebster | None,
    *,
    audio: bool = True,
) -> None:
    by_word = {w["word"]: w for w in words}
    first_word_root = {r["words"][0]: r for r in roots if r["words"]}
    decks: dict[int, genanki.Deck] = {}
    stats = {"ldoce": 0, "mw": 0, "cloze": 0}
    no_cloze: list[str] = []

    with tempfile.TemporaryDirectory(prefix="vocabulary-builder-") as media_dir:
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

        def deck(unit: int) -> genanki.Deck:
            if unit not in decks:
                decks[unit] = genanki.Deck(deck_id(chapter_deck(unit)), chapter_deck(unit))
            return decks[unit]

        for word in words:
            # Book order: each root's card comes right before its first word.
            root = first_word_root.get(word["word"])
            if root is not None:
                items = "".join(
                    f"<li><b>{_text(w)}</b> <span class=\"gloss\">— "
                    f"{_text(by_word[w]['definition'])}</span></li>"
                    for w in root["words"]
                )
                fields = {
                    "Root": _text(root["root"]),
                    "Meaning": _text(root["meaning"]),
                    "Intro": root["intro"],
                    "Words": items,
                    "Unit": str(root["unit"]),
                }
                deck(root["unit"]).add_note(
                    genanki.Note(
                        model=ROOT_MODEL,
                        fields=[fields[name] for name in ROOT_FIELDS],
                        # Two roots share a name (PRO "for", PRO "before"); their
                        # first words tell them apart.
                        guid=genanki.guid_for(ROOT_GUID_NAMESPACE, root["root"], root["words"][0]),
                        tags=[f"VocabularyBuilder::Unit_{root['unit']:02d}", "VocabularyBuilder::Root"],
                    )
                )

            section = word["section"]
            if word["kind"] == "root":
                root_meaning = next(r["meaning"] for r in roots if word["word"] in r["words"])
                section = f"{section} · {root_meaning}"
            fields: dict[str, str] = dict.fromkeys(WORD_FIELDS, "")
            fields.update(
                Word=_text(word["word"]),
                Unit=str(word["unit"]),
                Section=_text(section),
                BookDefinition=_text(word["definition"]),
                Example=_text(word["example"]),
                Cloze=cloze(word["example"], word["word"]),
                Notes=word["notes"],
            )
            if merriam_webster is not None:
                fields["Pronunciation"] = _text(merriam_webster.pronunciation(word["word"]))
            entry = ldoce_lookup(dictionary, word["word"].lower()) if dictionary else None
            if entry is not None:
                rendered = render_entry(entry)
                if same_headword(entry, word["word"]):
                    fields["AudioUK"] = sound(entry.pronunciation.audio_bre)
                    fields["AudioUS"] = sound(entry.pronunciation.audio_ame)
                else:
                    rendered["PhoneticUK"] = rendered["PhoneticUS"] = ""
                    rendered["PartOfSpeech"] = ", ".join(
                        p for p in (_text(entry.headword), rendered["PartOfSpeech"]) if p
                    )
                fields.update({k: v for k, v in rendered.items() if v})
            stats["ldoce"] += entry is not None
            stats["mw"] += bool(fields["Pronunciation"])
            stats["cloze"] += bool(fields["Cloze"])
            if not fields["Cloze"]:
                no_cloze.append(word["word"])
            deck(word["unit"]).add_note(
                genanki.Note(
                    model=WORD_MODEL,
                    fields=[fields[name] for name in WORD_FIELDS],
                    guid=genanki.guid_for(WORD_GUID_NAMESPACE, word["word"].lower()),
                    tags=[f"VocabularyBuilder::Unit_{word['unit']:02d}", "VocabularyBuilder::Word"],
                )
            )

        package = genanki.Package(
            [decks[n] for n in sorted(decks)], media_files=[str(p) for p in media.values()]
        )
        package.write_to_file(str(destination))

    print(
        f"Wrote {destination}: {len(roots)} root and {len(words)} word notes in "
        f"{len(decks)} units; {stats['cloze']} recall cards, {stats['mw']} MW "
        f"pronunciations, {stats['ldoce']} with LDOCE content, {len(media)} audio files",
        file=sys.stderr,
    )
    if no_cloze:
        print(f"No recall card (word not found in its example): {', '.join(no_cloze)}",
              file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--roots", default="roots.jsonl", help="roots JSONL from extract.py")
    parser.add_argument("--words", default="words.jsonl", help="words JSONL from extract.py")
    parser.add_argument("-o", "--output", default="vocabulary-builder.apkg", help="output .apkg")
    parser.add_argument(
        "--ldoce",
        default=default_ldoce(),
        help="LDOCE5++ .mdx (default: $ANKI_EUDIC_LDOCE or ~/Library/Eudb_en/LDOCE5*.mdx); "
        "audio comes from the .mdd next to it. Pass '' to skip it.",
    )
    parser.add_argument(
        "--mw",
        default=str(EUDIC_DIR / "Merriam-Webster's Vocabulary Builder.mdx"),
        help="Eudic's MDict copy of the book, for Merriam-Webster pronunciations. "
        "Pass '' to skip it.",
    )
    args = parser.parse_args()

    def load(path: str) -> list[dict]:
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    roots, words = load(args.roots), load(args.words)
    destination = Path(args.output).expanduser().with_suffix(".apkg")

    merriam_webster = None
    if args.mw and Path(args.mw).expanduser().exists():
        merriam_webster = MerriamWebster(Path(args.mw).expanduser())
    else:
        print("No Merriam-Webster dictionary; building without its pronunciations.",
              file=sys.stderr)

    if not args.ldoce:
        build(roots, words, destination, None, merriam_webster)
        return
    mdx = Path(args.ldoce).expanduser()
    mdd = mdx.with_suffix(".mdd")
    if not mdd.exists():
        print(f"No {mdd.name} next to the .mdx; building without audio.", file=sys.stderr)
    with LdoceDictionary(mdx, mdd if mdd.exists() else None) as dictionary:
        build(roots, words, destination, dictionary, merriam_webster, audio=mdd.exists())


if __name__ == "__main__":
    main()
