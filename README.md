# Vocabulary decks

Scripts that turn three vocabulary-building books into Anki decks, enriched with the
*Longman Dictionary of Contemporary English* (LDOCE5++): British and American IPA and
audio, senses with Chinese translations, collocations and word origins.

| Folder | Book | Deck |
| --- | --- | --- |
| [`verbal-advantage/`](verbal-advantage/) | *Verbal Advantage*, Charles Harrington Elster | 500 words in 10 levels |
| [`word-power/`](word-power/) | *Word Power Made Easy*, Norman Lewis | 302 roots and 968 words linked to them, in 12 chapters |
| [`vocabulary-builder/`](vocabulary-builder/) | *Merriam-Webster's Vocabulary Builder*, Mary Wood Cornog | 259 roots and 1,200 words, with fill-in-the-blank cards, in 30 units |

Each folder has two scripts and a README with the details:

1. `extract*.py` reads the book and writes its content to JSONL.
2. `make_anki_deck.py` builds an `.apkg` from that JSONL to import in Anki
   (**File → Import**).

## Requirements

- [uv](https://docs.astral.sh/uv/): the scripts declare their dependencies inline and
  run with `uv run`.
- The books themselves, which aren't included. Put each one in its folder;
  `md5.txt` lists the checksums of the copies the scripts were written against.
- For the dictionary content, Eudic's dictionaries in `~/Library/Eudb_en` (macOS): LDOCE5++,
  plus etymonline for Word Power and Eudic's copy of the Merriam-Webster book for
  Vocabulary Builder. The LDOCE lookup comes from
  [Anki-Eudic](https://github.com/Sorosliu1029/Anki-Eudic). The decks can also be built
  without them.

Each deck has its own note types, so importing one never changes another, and
re-importing a rebuilt deck updates its cards without resetting review progress.

The books, the extracted JSONL and the generated decks are kept out of git.
