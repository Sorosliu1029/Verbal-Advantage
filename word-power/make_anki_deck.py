#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["anki-eudic>=0.2.0,<0.3"]
# ///
"""Build an Anki deck of the roots and words of *Word Power Made Easy*.

Reads ``roots.jsonl`` and ``words.jsonl`` written by ``extract.py`` and writes one
``.apkg`` with a subdeck per chapter (``Word Power::Chapter 03 · Personality Types``).
Each chapter has two kinds of cards:

- **Root** cards (prefix, root or suffix -> meaning), listing the book's words built on it.
- **Word** cards (word -> meaning), showing the roots the word is built from, the
  book's definition, the LDOCE5++ entry (IPA, audio, senses with Chinese translations)
  and the word's history from etymonline (``etym2023.mdx``).

Words are linked to roots from three sources, strongest first: the book's own root
examples, etymonline naming the root in the word's history, and, for words etymonline
lacks, the root's stem in a word of the same chapter. Derived words also inherit the
roots of the book words they share a stem with (misogynistic <- misogynist).

The LDOCE lookup and rendering come from Anki-Eudic
(https://github.com/Sorosliu1029/Anki-Eudic). The note types and GUIDs are this deck's
own, so re-importing a rebuilt deck updates cards in place and keeps review progress.

Usage:
    uv run make_anki_deck.py -o word-power.apkg
    uv run make_anki_deck.py --roots roots.jsonl --words words.jsonl --ldoce PATH --etym PATH
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
from bs4.element import NavigableString, Tag

DECK_NAME = "Word Power"
EUDIC_DIR = Path.home() / "Library" / "Eudb_en"
# Never change these: a new model ID or field list makes Anki import a second note
# type, and a new GUID namespace duplicates every note. Templates and CSS may change.
ROOT_MODEL_ID = 1_902_337_415
WORD_MODEL_ID = 1_902_337_416
ROOT_GUID_NAMESPACE = "word-power-root"
WORD_GUID_NAMESPACE = "word-power-word"

ROOT_FIELDS = ("Root", "Kind", "Hint", "Meaning", "Examples", "Chapter", "ChapterTitle")
WORD_FIELDS = (
    "Word",
    "Chapter",
    "ChapterTitle",
    "Session",
    "BookPronunciation",
    "BookDefinition",
    "Roots",
    "Etymology",
    "PhoneticUK",
    "PhoneticUS",
    "AudioUK",
    "AudioUS",
    "PartOfSpeech",
    "Definition",
    "Extra",
)
MAX_EXAMPLES = 8
MAX_ETYMOLOGY_CHARS = 900

_BADGE = '<div class="badge">Word Power · Chapter {{Chapter}} · {{ChapterTitle}}</div>'
_ROOT_FRONT = (
    _BADGE
    + """
<div class="kind">{{Kind}}</div>
<div class="word">{{Root}}</div>
{{#Hint}}<div class="hint">{{Hint}}</div>{{/Hint}}
"""
)
_ROOT_BACK = """\
{{FrontSide}}
<hr id="answer">
<div class="meaning">{{Meaning}}</div>
{{#Examples}}<div class="section-title">Words</div><ul class="examples-list">{{Examples}}</ul>{{/Examples}}
"""
_WORD_FRONT = (
    _BADGE
    + """
<div class="word">{{Word}}</div>
<div class="phonetic">
{{#PhoneticUK}}<span class="pron"><span class="accent">BrE</span> {{PhoneticUK}} {{AudioUK}}</span>{{/PhoneticUK}}
{{#PhoneticUS}}<span class="pron"><span class="accent">AmE</span> {{PhoneticUS}} {{AudioUS}}</span>{{/PhoneticUS}}
{{^PhoneticUK}}{{#BookPronunciation}}<span class="pron">{{BookPronunciation}}</span>{{/BookPronunciation}}{{/PhoneticUK}}
</div>
"""
)
_WORD_BACK = """\
{{FrontSide}}
<hr id="answer">
{{#BookDefinition}}<div class="book-def">{{BookDefinition}}</div>{{/BookDefinition}}
{{#Roots}}<div class="roots">{{Roots}}</div>{{/Roots}}
{{#Definition}}<div class="section-title">LDOCE {{#PartOfSpeech}}· {{PartOfSpeech}}{{/PartOfSpeech}}</div>
<div class="definition" \
onclick="var p = event.target.closest('.reveal'); if (p) p.classList.toggle('shown');">\
{{Definition}}</div>{{/Definition}}
{{#Extra}}<div class="extra">{{Extra}}</div>{{/Extra}}
{{#Etymology}}<details class="etymology"><summary>Etymology</summary>{{Etymology}}</details>{{/Etymology}}
"""
# Reuse Anki-Eudic's styling for the LDOCE part so all decks look alike.
_CSS = (
    EUDIC_MODEL.css
    + """\
.badge { text-align: center; font-size: .7em; color: #999; letter-spacing: .05em; }
.kind { text-align: center; font-size: .75em; font-weight: 600; color: #b03060;
  text-transform: uppercase; letter-spacing: .08em; }
.hint { text-align: center; color: #888; font-style: italic; }
.meaning { font-size: 1.4em; font-weight: 600; text-align: center; margin: .3em 0; }
.book-def { background: #f6f3ea; border-radius: 6px; padding: .5em .8em; font-weight: 600; }
.book-def .related { font-weight: normal; color: #777; }
.roots { margin: .6em 0; text-align: center; }
.root { display: inline-block; border: 1px solid #2a6db0; border-radius: 4px;
  padding: 0 .4em; margin: .1em; }
.root b { color: #2a6db0; }
.root-meaning { color: #555; }
.section-title { margin-top: .8em; font-size: .8em; font-weight: 600; color: #2a6db0;
  text-transform: uppercase; letter-spacing: .05em; }
.examples-list { margin: .3em 0; }
.examples-list .gloss { color: #666; }
.etymology { margin-top: .8em; font-size: .85em; color: #444; }
.etymology summary { cursor: pointer; color: #2a6db0; }
.nightMode .book-def { background: #2b2a26; }
.nightMode .badge, .nightMode .root-meaning, .nightMode .examples-list .gloss,
.nightMode .etymology { color: #aaa; }
"""
)

ROOT_MODEL = genanki.Model(
    ROOT_MODEL_ID,
    "Word Power Root",
    fields=[{"name": name} for name in ROOT_FIELDS],
    templates=[{"name": "Root → Meaning", "qfmt": _ROOT_FRONT, "afmt": _ROOT_BACK}],
    css=_CSS,
)
WORD_MODEL = genanki.Model(
    WORD_MODEL_ID,
    "Word Power Word",
    fields=[{"name": name} for name in WORD_FIELDS],
    templates=[{"name": "Word → Meaning", "qfmt": _WORD_FRONT, "afmt": _WORD_BACK}],
    css=_CSS,
)


# --- dictionaries -------------------------------------------------------------------


def _allow_textual_encrypted_flag() -> None:
    """Let anki-eudic open etym2023.mdx, whose header says Encrypted="No".

    anki-eudic 0.2.0 only accepts a number there and raises ValueError otherwise.
    """
    read_header = mdict.MdictFile._read_header

    def patched(self):
        header = read_header(self)
        flag = header.get("Encrypted", "").strip().lower()
        if flag in ("no", "yes"):
            header["Encrypted"] = "0" if flag == "no" else "1"
        return header

    mdict.MdictFile._read_header = patched


class Etymology:
    """Look words up in etymonline (``etym2023.mdx``)."""

    def __init__(self, path: Path) -> None:
        _allow_textual_encrypted_flag()
        self._file = mdict.MdictFile(path)
        self._cache: dict[str, BeautifulSoup | None] = {}

    def _soup(self, word: str) -> BeautifulSoup | None:
        if word not in self._cache:
            records = self._file.lookup_text(word)
            self._cache[word] = BeautifulSoup(records[0], "html.parser") if records else None
        return self._cache[word]

    def text(self, word: str) -> str | None:
        """The entry's text, including the entries it links to; None if missing."""
        soup = self._soup(word)
        return normalize(soup.get_text(" ")) if soup is not None else None

    def article_html(self, word: str) -> str:
        """The entry's own text as HTML, with foreign forms in italics."""
        soup = self._soup(word)
        main = soup.find("div", class_="word") if soup else None
        if main is None:
            return ""
        paragraphs, length = [], 0
        for section in main.find_all("section", class_="defination"):
            for p in section.find_all("p"):
                text = _render_etymology(p).strip()
                if not text or length > MAX_ETYMOLOGY_CHARS:
                    continue
                paragraphs.append(f"<p>{text}</p>")
                length += len(p.get_text())
        return "".join(paragraphs)


def _render_etymology(node: Tag) -> str:
    parts = []
    for child in node.children:
        if isinstance(child, NavigableString):
            parts.append(html.escape(str(child), quote=False))
        elif isinstance(child, Tag):
            inner = _render_etymology(child)
            classes = child.get("class") or []
            if "foreign" in classes or "crossreference" in classes:
                parts.append(f"<i>{inner}</i>")
            else:
                parts.append(inner)
    return "".join(parts)


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


def same_headword(entry: DictionaryEntry, word: str) -> bool:
    """False when LDOCE redirected elsewhere, e.g. misanthrope -> misanthropist."""
    return normalize(entry.headword).strip() in inflections(word)


# --- linking words to roots ---------------------------------------------------------

EVIDENCE_RANK = {"book": 0, "etymology": 1, "chapter": 2, "family": 3}
# Latin and Greek dictionary-form endings: verto -> vert, anthropos -> anthrop.
ENDINGS = ("ein", "ere", "are", "ire", "or", "os", "on", "us", "um", "is", "es", "as",
           "ex", "ix", "er", "o", "e", "a", "y")
# Verb and noun endings whose Latin source ends in -atus, which hides false roots:
# "recapitulate" is not built on latus (side).
ATE_ENDINGS = ("ation", "ative", "ator", "ated", "ates", "ate")


def normalize(text: str) -> str:
    """Lowercase and drop diacritics: 'anthrōpos' -> 'anthropos'."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def stems(form: str) -> set[str]:
    """Spellings a root form takes inside English words (kardia -> cardi, card...)."""
    bare = form.strip("-").replace(" ", "")
    found = {bare}
    for ending in ENDINGS:
        if bare.endswith(ending) and len(bare) - len(ending) >= 3:
            found.add(bare[: -len(ending)])
    for stem in list(found):  # Greek spellings in Latin dress
        found.add(stem.replace("k", "c").replace("ai", "e").replace("oi", "e").replace("ou", "u"))
    return {stem for stem in found if len(stem) >= 2}


def root_span(root: dict, word: str) -> tuple[int, int] | None:
    """Where the root sits in the word: prefixes at the start, suffixes at the end."""
    best = None
    for stem in set().union(*(stems(form) for form in root["forms"])):
        if root["kind"] == "prefix":
            start = 0 if word.startswith(stem) else -1
        elif root["kind"] == "suffix":
            start = len(word) - len(stem) if word.endswith(stem) else -1
            if start < 0 and word.endswith(stem + "s"):
                start = len(word) - len(stem) - 1
        else:
            start = word.find(stem) if len(stem) >= 3 else -1
            ending = next((e for e in ATE_ENDINGS if word.endswith(e)), "")
            if start >= 0 and len(stem) <= 5 and ending and start + len(stem) > len(word) - len(ending):
                start = -1
        if start >= 0 and (best is None or len(stem) > best[1] - best[0]):
            best = (start, start + len(stem))
    return best


def named_in_etymology(root: dict, text: str, tokens: set[str]) -> bool:
    """Whether etymonline's account of a word mentions the root (misein, anthrōpos...)."""
    for form in root["forms"]:
        bare = form.strip("-")
        if len(bare) <= 2 and root["kind"] != "root":
            # "al", "a", "in" occur everywhere; short affixes count only as "al-", "-y".
            spelled = f"{bare}-" if root["kind"] == "prefix" else f"-{bare}"
            if re.search(rf"(?<![a-z]){re.escape(spelled)}(?![a-z])", text):
                return True
            continue
        if bare in tokens:
            return True
        # Inside compounds too ("monogamos" contains "gamos"), if long enough to be safe.
        for stem in stems(form) | {bare}:
            if len(stem) >= 4 and any(stem in token for token in tokens):
                return True
    return False


def resolve(links: list[tuple[str, str, tuple | None]], roots: dict[str, dict], chapter: int) -> list:
    """Keep the strongest link per spot in the word (gnostos over the nostos inside it).

    Of homograph affixes, the sense taught most recently up to the word's chapter wins.
    """

    def recency(root_id: str) -> int:
        taught = roots[root_id]["chapter"]
        return chapter - taught if taught <= chapter else 100 + taught

    kept, taken = [], []
    ordered = sorted(
        links,
        key=lambda l: (
            EVIDENCE_RANK[l[1]],
            -(l[2][1] - l[2][0]) if l[2] else 0,
            recency(l[0]),
        ),
    )
    for root_id, evidence, span in ordered:
        # Overlap of 2+ letters drops a match; a one- or two-letter match (a-, in-)
        # may not overlap at all.
        if span and any(
            min(span[1], t[1]) - max(span[0], t[0]) >= min(2, span[1] - span[0]) for t in taken
        ):
            continue
        # One sense per homograph affix ("dis-" against / negative / pejorative).
        head = roots[root_id]["forms"][0]
        if any(roots[k]["forms"][0] == head for k, _, _ in kept):
            continue
        kept.append((root_id, evidence, span))
        if span:
            taken.append(span)
    return kept


def shares_stem(a: str, b: str) -> bool:
    """misogynist ~ misogynistic, monogamy ~ monogamist."""
    n = 0
    while n < min(len(a), len(b)) and a[n] == b[n]:
        n += 1
    return n >= 6 and n >= min(len(a), len(b)) - 2


def link_words(words: list[dict], roots: list[dict], etymology: Etymology | None) -> None:
    """Set ``word["roots"]`` to the IDs of the roots it is built on, in word order."""
    by_id = {root["id"]: root for root in roots}
    links: dict[str, list] = {}
    for word in words:
        text = word["word"]
        history = etymology.text(text) if etymology else None
        tokens = set(re.findall(r"[a-z]+", history)) if history is not None else set()
        found = []
        for root in roots:
            span = root_span(root, text)
            same_chapter = root["chapter"] == word["chapter"]
            if text in root["examples"] or text in (f.strip("-") for f in root["forms"]):
                found.append((root["id"], "book", span))
            elif span is None:
                continue
            elif history is not None:
                if named_in_etymology(root, history, tokens):
                    found.append((root["id"], "etymology", span))
            # Without etymonline, a root taught in the word's chapter whose stem is in
            # the word; 3-letter stems are too common to trust ("mis" in bigamist).
            elif same_chapter and root["kind"] == "root" and span[1] - span[0] >= 4:
                found.append((root["id"], "chapter", span))
        links[text] = resolve(found, by_id, word["chapter"])

    # Derived words inherit the roots (not suffixes) of book words sharing their stem.
    names = sorted(links)
    chapters = {word["word"]: word["chapter"] for word in words}
    for a in names:
        inherited = [
            (root_id, "family", span)
            for b in names
            if a != b and shares_stem(a, b)
            for root_id, evidence, _ in links[b]
            if evidence != "family"
            and by_id[root_id]["kind"] != "suffix"
            and (span := root_span(by_id[root_id], a))
        ]
        if inherited:
            links[a] = resolve(links[a] + inherited, by_id, chapters[a])

    for word in words:
        ordered = sorted(links[word["word"]], key=lambda l: l[2][0] if l[2] else 99)
        word["roots"] = [root_id for root_id, _, _ in ordered]


# --- cards ----------------------------------------------------------------------------


def _text(value: str) -> str:
    return html.escape(value, quote=False)


def chapter_deck(chapter: int, title: str) -> str:
    return f"{DECK_NAME}::Chapter {chapter:02d} · {title}"


def root_examples(root: dict, words: list[dict]) -> list[dict | str]:
    """The book's examples first, then other words built on the root, same chapter first."""
    by_word = {w["word"]: w for w in words}
    chosen: list[dict | str] = [by_word[e] if e in by_word else e for e in root["examples"]]
    built = [w for w in words if root["id"] in w["roots"] and w["word"] not in root["examples"]]
    built.sort(key=lambda w: (w["chapter"] != root["chapter"], w["chapter"], len(w["word"])))
    chosen += built
    return chosen[:MAX_EXAMPLES]


def render_examples(examples: list[dict | str]) -> str:
    items = []
    for example in examples:
        if isinstance(example, str):
            items.append(f"<li>{_text(example)}</li>")
        else:
            gloss = example["definition"]
            gloss_html = f' <span class="gloss">— {_text(gloss)}</span>' if gloss else ""
            items.append(f"<li><b>{_text(example['word'])}</b>{gloss_html}</li>")
    return "".join(items)


def render_roots(root_ids: list[str], roots: dict[str, dict]) -> str:
    chips = []
    for root_id in root_ids:
        root = roots[root_id]
        chips.append(
            f'<span class="root"><b>{_text(root["display"])}</b> '
            f'<span class="root-meaning">{_text(root["meaning"])}</span></span>'
        )
    return " + ".join(chips)


def build(
    roots: list[dict],
    words: list[dict],
    destination: Path,
    dictionary: LdoceDictionary | None,
    etymology: Etymology | None,
    *,
    audio: bool = True,
) -> None:
    link_words(words, roots, etymology)
    by_id = {root["id"]: root for root in roots}
    heads: dict[str, int] = {}
    for root in roots:
        heads[root["display"]] = heads.get(root["display"], 0) + 1

    titles = {w["chapter"]: w["chapter_title"] for w in words}
    titles.update({r["chapter"]: r["chapter_title"] for r in roots})
    decks = {n: genanki.Deck(deck_id(chapter_deck(n, t)), chapter_deck(n, t)) for n, t in titles.items()}

    with tempfile.TemporaryDirectory(prefix="word-power-") as media_dir:
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

        # Roots first so each chapter introduces its roots before the words built on them.
        for root in roots:
            examples = root_examples(root, words)
            hint = ""
            if heads[root["display"]] > 1:  # e.g. "in-" (in) vs "in-" (not)
                first = next((e if isinstance(e, str) else e["word"] for e in examples), "")
                hint = f"as in <i>{_text(first)}</i>" if first else f"as taught in Chapter {root['chapter']}"
            fields = {
                "Root": _text(root["display"]),
                "Kind": root["kind"],
                "Hint": hint,
                "Meaning": _text(root["meaning"]),
                "Examples": render_examples(examples),
                "Chapter": str(root["chapter"]),
                "ChapterTitle": _text(root["chapter_title"]),
            }
            decks[root["chapter"]].add_note(
                genanki.Note(
                    model=ROOT_MODEL,
                    fields=[fields[name] for name in ROOT_FIELDS],
                    guid=genanki.guid_for(ROOT_GUID_NAMESPACE, root["id"]),
                    tags=[f"WordPower::Chapter_{root['chapter']:02d}", "WordPower::Root"],
                )
            )

        enriched = 0
        for word in words:
            fields: dict[str, str] = dict.fromkeys(WORD_FIELDS, "")
            fields.update(
                Word=_text(word["word"]),
                Chapter=str(word["chapter"]),
                ChapterTitle=_text(word["chapter_title"]),
                Session=str(word["session"]),
                BookPronunciation=_text(word["pronunciation"]),
                BookDefinition=_text(word["definition"]),
                Roots=render_roots(word["roots"], by_id),
                Etymology=etymology.article_html(word["word"]) if etymology else "",
            )
            entry = ldoce_lookup(dictionary, word["word"]) if dictionary else None
            if entry is not None:
                rendered = render_entry(entry)
                if same_headword(entry, word["word"]):
                    fields["AudioUK"] = sound(entry.pronunciation.audio_bre)
                    fields["AudioUS"] = sound(entry.pronunciation.audio_ame)
                else:
                    # The entry is for a related word; its pronunciation would mislead.
                    rendered["PhoneticUK"] = rendered["PhoneticUS"] = ""
                    rendered["PartOfSpeech"] = ", ".join(
                        p for p in (_text(entry.headword), rendered["PartOfSpeech"]) if p
                    )
                fields.update({k: v for k, v in rendered.items() if v})
                enriched += 1
            if not (fields["BookDefinition"] or fields["Definition"] or fields["Etymology"]):
                # Derived forms (monogamist) borrow the gloss of their base (monogamy).
                base = next(
                    (
                        w
                        for w in words
                        if w["definition"] and w is not word and shares_stem(w["word"], word["word"])
                    ),
                    None,
                )
                if base is not None:
                    fields["BookDefinition"] = (
                        f"<span class=\"related\">related to <i>{_text(base['word'])}</i>:</span> "
                        f"{_text(base['definition'])}"
                    )
            decks[word["chapter"]].add_note(
                genanki.Note(
                    model=WORD_MODEL,
                    fields=[fields[name] for name in WORD_FIELDS],
                    guid=genanki.guid_for(WORD_GUID_NAMESPACE, word["word"]),
                    tags=[f"WordPower::Chapter_{word['chapter']:02d}", "WordPower::Word"],
                )
            )

        package = genanki.Package(
            [decks[n] for n in sorted(decks)], media_files=[str(p) for p in media.values()]
        )
        package.write_to_file(str(destination))

    linked = sum(bool(w["roots"]) for w in words)
    print(
        f"Wrote {destination}: {len(roots)} root and {len(words)} word notes in "
        f"{len(decks)} chapters; {linked} words linked to roots, {enriched} with LDOCE "
        f"content, {len(media)} audio files",
        file=sys.stderr,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--roots", default="roots.jsonl", help="roots JSONL from extract.py")
    parser.add_argument("--words", default="words.jsonl", help="words JSONL from extract.py")
    parser.add_argument("-o", "--output", default="word-power.apkg", help="output .apkg")
    parser.add_argument(
        "--ldoce",
        default=default_ldoce(),
        help="LDOCE5++ .mdx (default: $ANKI_EUDIC_LDOCE or ~/Library/Eudb_en/LDOCE5*.mdx); "
        "audio comes from the .mdd next to it. Pass '' to skip it.",
    )
    parser.add_argument(
        "--etym",
        default=str(EUDIC_DIR / "etym2023.mdx"),
        help="etymonline .mdx used to link words to roots (default: "
        "~/Library/Eudb_en/etym2023.mdx). Pass '' to skip it.",
    )
    args = parser.parse_args()

    def load(path: str) -> list[dict]:
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    roots, words = load(args.roots), load(args.words)
    destination = Path(args.output).expanduser().with_suffix(".apkg")

    etymology = None
    if args.etym and Path(args.etym).expanduser().exists():
        etymology = Etymology(Path(args.etym).expanduser())
    else:
        print("No etymonline dictionary; linking words to roots from the book only.",
              file=sys.stderr)

    if not args.ldoce:
        build(roots, words, destination, None, etymology)
        return
    mdx = Path(args.ldoce).expanduser()
    mdd = mdx.with_suffix(".mdd")
    if not mdd.exists():
        print(f"No {mdd.name} next to the .mdx; building without audio.", file=sys.stderr)
    with LdoceDictionary(mdx, mdd if mdd.exists() else None) as dictionary:
        build(roots, words, destination, dictionary, etymology, audio=mdd.exists())


if __name__ == "__main__":
    main()
