# Merriam-Webster's Vocabulary Builder

Turns Mary Wood Cornog's *Merriam-Webster's Vocabulary Builder* (2nd edition, Kindle
`.mobi`) into an Anki deck of its roots and words. Both scripts run with
[uv](https://docs.astral.sh/uv/), which installs their dependencies from the inline
metadata.

## 1. Extract roots and words

```sh
uv run extract.py "Merriam-Webster's Vocabulary Builder - Mary Wood Cornog.mobi" -o .
```

This writes two JSONL files in book order (quizzes are skipped):

- `roots.jsonl`: 259 roots, each with its meaning (e.g. BENE → Latin, "well"), the
  book's introduction and its words.
- `words.jsonl`: all 1,200 words, each with its unit, its root or special section
  ("Words from Mythology", "Latin Borrowings", ...), definition, example sentence and
  the book's notes on the word.

The ebook draws pronunciations as images, so they come from the dictionary below
instead.

## 2. Build the deck

```sh
uv run make_anki_deck.py -o vocabulary-builder.apkg
```

The deck has a subdeck per unit (`Vocabulary Builder::Unit 01` ... `Unit 30`), in book
order: each root card, then its words.

- **Root cards**: the root → its meaning, the book's introduction, and its words with
  their definitions.
- **Word notes**, two cards each:
  - *Recognize*: the word and its pronunciation → the definition, the example
    sentence, the root, the book's notes (collapsible) and the LDOCE5++ entry with
    click-to-reveal Chinese translations.
  - *Recall*: the example sentence with the word blanked out, with the definition as
    a hint → the word. Inflected forms are blanked too (*hector* → "hectored"). Only
    *metaphorical*, whose example uses "metaphors", gets no Recall card.

Dictionaries are read from Eudic's folder, `~/Library/Eudb_en`:

- `--mw`: Eudic's MDict copy of the book, `Merriam-Webster's Vocabulary Builder.mdx`,
  for the Merriam-Webster respellings (`/ˌbe-nə-ˈdik-shən/`). It has them for 1,134
  words.
- `--ldoce`: LDOCE5++ `.mdx` (default `$ANKI_EUDIC_LDOCE` or `LDOCE5*.mdx`), with IPA
  and audio from the `.mdd` next to it. It covers 1,069 words. The lookup and rendering
  come from [Anki-Eudic](https://github.com/Sorosliu1029/Anki-Eudic).

Pass `''` to either option to build without it. 28 words have a pronunciation in
neither dictionary.

Import the `.apkg` in Anki with **File → Import**. The note types ("Vocabulary Builder
Root", "Vocabulary Builder Word") and note IDs are this deck's own, so it doesn't touch
the other decks, and re-importing a rebuilt deck updates the cards without resetting
review progress.
