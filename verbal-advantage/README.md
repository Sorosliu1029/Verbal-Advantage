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

## Cut the audiobook into one clip per word

`clip_audio.py` finds each keyword's discussion in the *Verbal Advantage* audiobook
(`Verbal-Advantage-level<L>_<AA>-<BB>*.mp3`, ten keywords per file) and cuts it into
its own MP3: from "Word N. *keyword*", through the spelling, definition and
discussion, up to the next keyword or the review quiz. Whisper transcribes each file
with word timestamps, and the cuts are moved into the nearest silence.

```sh
uv run clip_audio.py words.jsonl --mp3-dir . -o audio
```

It writes `audio/L01_001_paraphrase.mp3` ... (mono, 48 kbps), `audio/clips.jsonl`
with each clip's source file and times, and caches transcripts in
`audio/transcripts/` so a rerun only re-cuts. It prints a warning for any keyword it
couldn't place cleanly. It needs ffmpeg and an Apple Silicon Mac (it runs
[mlx-whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper));
transcribing all 50 files takes a few hours.

To check the clips, `check_clips.py` transcribes each clip again on its own and
compares it with the book: does it open with its own "Word N" and spelling, is the
book's definition and discussion in it, and did nothing from the next keyword or the
review quiz leak in. It writes `audio/check.jsonl` and prints the clips that fail a
check (`--sample 5` checks five random clips per level instead of all 500):

```sh
uv run check_clips.py words.jsonl -a audio
```

The audiobook sometimes rewords or skips a paragraph of the book, and the narrator
cross-references other keywords ("word 29 of Level 7"), so listen to a flagged clip
before treating it as wrong. On the clips as cut now, all 500 open with their own
"Word N" and none contains the next keyword or the review; the 12 it flags are such
cases.

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

If `audio/clips.jsonl` exists (or the folder passed with `--audio`), the back of each
card also gets a player for the word's audiobook clip, under the book's definition.
It doesn't autoplay, since the clips run from half a minute to several minutes.

Import the `.apkg` in Anki with **File → Import**. The deck has its own note type
("Verbal Advantage Word") and note IDs, so it never touches cards exported by
Anki-Eudic, and re-importing a rebuilt deck updates cards without resetting progress.
