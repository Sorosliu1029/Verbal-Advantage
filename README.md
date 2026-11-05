# Verbal-Advantage

## Extract the keywords to JSONL

`extract_words.py` pulls the 500 keywords (10 levels × 50 words) out of the
*Verbal Advantage* PDF, one JSON object per line:

```json
{"level": 1, "number": 1, "word": "PARAPHRASE", "pronunciation": "PAR-uh-frayz", "definition": "To restate, put what someone else has expressed into different words.", "content": "The noun a paraphrase is ...", "page": 11}
```

- `definition` is the short definition printed right under the headword.
- `content` is the rest of the discussion, with paragraphs separated by `\n\n`.
- `page` is the PDF page where the entry starts.

```sh
pip install pypdf
python extract_words.py "Verbal Advantage_ 10 Steps to a Powerful V - Charles Harrington Elster.pdf" -o words.jsonl
```

## Build an Anki deck

`make_anki_deck.py` turns `words.jsonl` into an Anki package with one subdeck per
level (`Verbal Advantage::Level 01` ... `Level 10`). Each card's front shows the word
with British and American pronunciations and audio. The back shows the book's
pronunciation, definition and (collapsible) discussion, followed by the
*Longman Dictionary of Contemporary English* (LDOCE5++) entry: senses with Chinese
translations (click a definition to reveal it), examples, collocations and origin.

The LDOCE5++ lookup comes from [Anki-Eudic](https://github.com/Sorosliu1029/Anki-Eudic),
which [uv](https://docs.astral.sh/uv/) installs from the script's inline metadata:

```sh
uv run make_anki_deck.py words.jsonl -o verbal-advantage.apkg
```

The dictionary defaults to `$ANKI_EUDIC_LDOCE` or `~/Library/Eudb_en/LDOCE5*.mdx`
(pass `--ldoce PATH`, or `--ldoce ''` to build from the book only); audio comes from
the `.mdd` next to it. About 50 of the rarest words, such as *deipnosophist*, are not
in LDOCE, so their cards carry the book's content only.

Import the `.apkg` in Anki with **File → Import**. The deck has its own note type
("Verbal Advantage Word") and note IDs, so it never touches cards exported by
Anki-Eudic, and re-importing a rebuilt deck updates cards without resetting progress.
