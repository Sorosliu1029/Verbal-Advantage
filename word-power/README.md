# Word Power Made Easy

Turns Norman Lewis's *Word Power Made Easy* into an Anki deck of the roots, prefixes
and suffixes it teaches and the words built on them. Both scripts run with
[uv](https://docs.astral.sh/uv/), which installs their dependencies from the inline
metadata.

## 1. Extract roots and words

```sh
uv run extract.py "Word Power Made Easy_ The Complete Handboo - Norman Lewis.pdf" -o .
```

This writes two JSONL files from the 12 teaching chapters (the three comprehensive
tests are skipped):

- `roots.jsonl`: 302 prefixes, roots and suffixes from the "Review of Etymology"
  tables, with their meanings. Variant spellings are merged (`paidos (ped-)`), while
  homographs stay apart (`in-` "in" vs `in-` "not"). `examples` holds the words the
  book pairs with each root in its chapter reviews.
- `words.jsonl`: 968 words from the "Can you pronounce the words?" lists, with the
  session they're taught in, Lewis's pronunciation and a short definition taken from
  the answers to the matching exercises (about 680 words have one). The pronunciation
  is left empty for the ~80 words where the PDF lost a phonetic symbol.

## 2. Build the deck

```sh
uv run make_anki_deck.py -o word-power.apkg
```

The deck has a subdeck per chapter (`Word Power::Chapter 03 · Personality Types`), with
the chapter's root cards first and then its word cards:

- **Root cards**: the root and whether it's a prefix, root or suffix on the front; the
  meaning and up to eight words from the book built on it on the back.
- **Word cards**: the word and its pronunciation (LDOCE audio when available) on the
  front. The back has the book's definition, the roots the word is built from, the
  LDOCE5++ entry (senses with click-to-reveal Chinese translations) and the word's
  history from etymonline.

Dictionaries are read from Eudic's folder, `~/Library/Eudb_en`:

- `--ldoce`: LDOCE5++ `.mdx` (default `$ANKI_EUDIC_LDOCE` or `LDOCE5*.mdx`), with audio
  from the `.mdd` next to it. The lookup and rendering come from
  [Anki-Eudic](https://github.com/Sorosliu1029/Anki-Eudic).
- `--etym`: etymonline's `etym2023.mdx`, used to link words to roots and for the
  etymology section.

Pass `''` to either option to build without it.

### How words are linked to roots

Each word gets the roots it's built from, using three sources, strongest first:

1. the book's own root examples ("Can you recognize roots?"),
2. etymonline naming the root in the word's history (*misanthrope*: from *misein* "to
   hate" + *anthrōpos* "man"), where the root's stem also appears in the word at the
   right place (prefixes at the start, suffixes at the end),
3. for words etymonline lacks, a root from the same chapter whose stem is in the word.

Derived forms also inherit the roots of book words that share their stem
(*misogynistic* from *misogynist*), and words with no definition from any source
borrow their base word's (*monogamist* → "related to *monogamy*").

About 89% of the words get at least one root. Spot checks find roughly one wrong link
in twenty, usually a root whose letters happen to appear in an unrelated word
(*chivalrous* → *vale*).

Import the `.apkg` in Anki with **File → Import**. The note types ("Word Power Root",
"Word Power Word") and note IDs are this deck's own, so re-importing a rebuilt deck
updates the cards without resetting review progress.
