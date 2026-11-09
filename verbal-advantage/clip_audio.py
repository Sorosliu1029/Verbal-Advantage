#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["mlx-whisper"]
# ///
"""Cut each keyword's discussion out of the *Verbal Advantage* audiobook MP3s.

Every file ``Verbal-Advantage-level<L>_<AA>-<BB>*.mp3`` holds ten keywords, each
announced as "Word N. <keyword>." and spelled out, followed by a review quiz that
starts with "Let's review". Whisper transcribes each file with word timestamps;
a keyword's clip runs from its "Word N" to the next keyword's (or to the review),
with both edges moved into the nearest silence that ffmpeg finds.

Writes ``L<level>_<number>_<word>.mp3`` (mono, 48 kbps) into the output folder,
plus ``clips.jsonl`` with each clip's source file, times and any warnings.
Transcripts are cached in ``<output>/transcripts`` so reruns skip Whisper.
Needs ffmpeg and an Apple Silicon Mac (mlx-whisper).

Usage:
    uv run clip_audio.py words.jsonl --mp3-dir . -o audio
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
import sys
from pathlib import Path

MODEL = "mlx-community/whisper-large-v3-turbo"
FILE_RE = re.compile(r"level(\d+)_(\d+)-(\d+)")
# Silence below -35 dB for at least 0.12 s separates spoken words.
SILENCE_FILTER = "silencedetect=noise=-35dB:d=0.12"
# Only snap an edge to a silence this close to Whisper's timestamp (seconds).
MAX_SNAP = 0.8
# Look for the pause before an entry this far before and after Whisper's start.
# Whisper often starts the "Word" at the start of the pause, so look further ahead.
PAUSE_WINDOW = (2.0, 3.5)
# A stretch with no transcribed words longer than this (seconds) is speech Whisper
# skipped, not a pause; entry starts within HOLE_WINDOW of one are re-transcribed.
MAX_HOLE = 6.0
HOLE_WINDOW = 10.0
# A spelled-out token such as "P-A-R-A", "-S." or "b"; a spelling is a run of
# at least three letters, so the words "a" and "I" don't count.
SPELLING_RE = re.compile(r"-?[A-Za-z](-[A-Za-z])*[.,]?")
MIN_SPELLING = 3
NOT_HEARD = "keyword not heard; clip starts before its spelling"
# How closely the spelled letters must match the keyword (difflib ratio). Lower
# than 1 because Whisper sometimes drops a letter; high enough to skip related
# words spelled in the same entry, like "minacious" next to "minatory".
SPELLING_MATCH = 0.8
# How Whisper writes the N of "Word N": digits, or words (and "for" for "four").
NUMBERS = {str(n) for n in range(1, 51)} | set(
    "one two three four for five six seven eight nine ten".split()
) | {t + u for t in ("twenty", "thirty", "forty", "fifty") for u in ("", "one", "two", "three",
     "four", "five", "six", "seven", "eight", "nine")}


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def transcribe(
    mp3: Path, cache: Path, span: tuple[float, float] | None = None, prompt: str | None = None
) -> list[dict]:
    """Return Whisper's words ({"word", "start", "end"}) for ``mp3``, cached as JSON.

    With ``span``, transcribe only that stretch, without conditioning on earlier
    text (which is what lets Whisper fall into a repetition loop and lose a
    stretch of speech), and with ``prompt`` to prime it for rare keywords.
    """
    if not cache.exists():
        import mlx_whisper

        print(f"Transcribing {mp3.name}{f' {span[0]:.0f}-{span[1]:.0f}s' if span else ''} ...",
              file=sys.stderr)
        options = {}
        if span:
            options = {"clip_timestamps": [span[0], span[1]], "condition_on_previous_text": False,
                       "initial_prompt": prompt}
        result = mlx_whisper.transcribe(
            str(mp3), path_or_hf_repo=MODEL, word_timestamps=True, language="en", **options
        )
        segments = [
            {"start": s["start"], "end": s["end"], "text": s["text"], "words": s["words"]}
            for s in result["segments"]
        ]
        cache.write_text(json.dumps({"segments": segments}), encoding="utf-8")
    segments = json.loads(cache.read_text(encoding="utf-8"))["segments"]
    return [w for s in segments for w in s["words"]]


def silences(mp3: Path) -> list[tuple[float, float]]:
    """Return the (start, end) of every silent stretch in ``mp3``."""
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(mp3), "-af", SILENCE_FILTER,
         "-f", "null", "-"],
        capture_output=True, text=True, check=True,
    ).stderr
    values = [float(v) for v in re.findall(r"silence_(?:start|end): ([-0-9.]+)", out)]
    # ffmpeg can report one pause as back-to-back pieces; join them.
    gaps: list[tuple[float, float]] = []
    for s, e in zip(values[0::2], values[1::2]):
        if gaps and s - gaps[-1][1] < 0.01:
            gaps[-1] = (gaps[-1][0], e)
        else:
            gaps.append((s, e))
    return gaps


def find_spelling(words: list[dict], word: str, lo: int) -> int | None:
    """Return the index where ``word`` is spelled out ("P-A-R-A-...") after ``lo``.

    Whisper transcribes the letters reliably even when it garbles the spoken
    keyword or the "Word N" before it.
    """
    target = norm(word)
    i = lo
    while i < len(words):
        j = i
        while j < len(words) and SPELLING_RE.fullmatch(words[j]["word"].strip()):
            j += 1
        letters = norm("".join(w["word"] for w in words[i:j]))
        # "the noun colloquy, spelled C-O-L-..." spells a related word, not the keyword.
        mention = i > 0 and norm(words[i - 1]["word"]) == "spelled"
        if (len(letters) >= MIN_SPELLING and not mention and letters[:3] == target[:3]
                and difflib.SequenceMatcher(None, letters, target).ratio() >= SPELLING_MATCH):
            return i
        i = max(j, i + 1)
    return None


def find_marker(words: list[dict], number: int, first: int, lo: int) -> int | None:
    """Return the index of "Word <number>" after ``lo``.

    The narrator counts either within the level (21, 22, ...) or within the file
    (1, 2, ...). Skips cross-references such as "word 40 of level 2".
    """
    for i in range(lo, len(words) - 2):
        if norm(words[i]["word"]) != "word" or not norm(words[i + 1]["word"]).isdigit():
            continue
        if norm(words[i + 2]["word"]) in ("of", "in", "from", "on"):
            continue
        if int(norm(words[i + 1]["word"])) in (number, number - first + 1):
            return i
    return None


def entry_start(words: list[dict], spelling: int) -> tuple[int, float, list[str]]:
    """Return the index and time where the entry spelled at ``spelling`` begins.

    That is the "Word N" just before the spoken keyword, or the keyword itself
    when Whisper didn't hear the "Word N". When Whisper heard neither, the entry
    begins right after the sentence before the spelling.
    """
    window = range(max(spelling - 6, 0), spelling)
    said = [i for i in window if norm(words[i]["word"]) == "word"]
    # Whisper sometimes writes "Word 27. Word 27." for one "Word 27"; the first is right.
    numbered = [i for i in said if i + 1 < spelling and norm(words[i + 1]["word"]) in NUMBERS]
    if numbered or said:
        i = (numbered or said[-1:])[0]
        return i, words[i]["start"], []
    if spelling > 0 and words[spelling - 1]["word"].strip().endswith((".", "?", "!")):
        return spelling, words[spelling - 1]["end"], [NOT_HEARD]
    i = spelling - 1
    # Walk back over a keyword Whisper split or heard twice, up to the last sentence.
    while i > spelling - 3 and i > 0 and not words[i - 1]["word"].strip().endswith((".", "?", "!")):
        i -= 1
    return i, words[i]["start"], ["no 'Word N' heard; clip starts at the keyword"]


def find_starts(
    words: list[dict], entries: dict[int, dict], first: int
) -> dict[int, tuple[int, float, list[str]]]:
    """Map each keyword number to the (index, time) where its entry begins, in order."""
    starts: dict[int, tuple[int, float, list[str]]] = {}
    lo = 0
    for number in range(first, first + 10):
        spelling = find_spelling(words, entries[number]["word"], lo)
        marker = find_marker(words, number, first, lo)
        if spelling is not None and (marker is None or marker > spelling or spelling - marker <= 6):
            starts[number] = entry_start(words, spelling)
        elif marker is not None:
            # Whisper missed the spelling, or the one it heard belongs to a later mention.
            starts[number] = (marker, words[marker]["start"], [])
        else:
            continue
        lo = starts[number][0] + 1
    return starts


def transcript_hole(words: list[dict], t: float) -> tuple[float, float] | None:
    """Return a stretch near ``t`` with no transcribed words for longer than any pause.

    Whisper sometimes skips half a minute of speech and then stamps the next
    words at the wrong time, so an entry start next to such a hole can't be trusted.
    """
    times = [(w["start"], w["end"]) for w in words]
    for (_, a), (b, _) in zip(times, times[1:]):
        if b - a > MAX_HOLE and a - HOLE_WINDOW <= t <= b + HOLE_WINDOW:
            return a, b
    return None


def find_review(words: list[dict], lo: int) -> int | None:
    """Return the index of the "Let's review" that starts the quiz, after ``lo``."""
    for i in range(lo, len(words) - 1):
        w = norm(words[i]["word"])
        if w == "letsreview" or w in ("lets", "let") and "review" in norm(words[i + 1]["word"]):
            return i
    return None


def retranscribe_missing(
    mp3: Path, words: list[dict], entries: dict[int, dict], first: int, cache: Path
) -> list[dict]:
    """Re-transcribe each stretch where keywords are missing, and splice it in.

    The stretch runs between the neighbouring keywords that were found (or the
    review quiz), and Whisper is primed with the missing keywords' spellings.
    """
    starts = find_starts(words, entries, first)
    # Also redo stretches where Whisper skipped speech next to an entry's start:
    # only the spelling was heard, or a hole in the transcript is close by.
    missing = [n for n in range(first, first + 10) if n not in starts
               or NOT_HEARD in starts[n][2] or transcript_hole(words, starts[n][1])]
    starts = {n: s for n, s in starts.items() if n not in missing}
    while missing:
        n = missing[0]
        run = [m for m in missing if all(k in missing for k in range(n, m + 1))]
        before = [starts[k][1] for k in starts if k < n]
        after = [starts[k][1] for k in starts if k > run[-1]]
        review = find_review(words, max((starts[k][0] for k in starts if k < n), default=0))
        lo = max(before, default=0.0)
        hi = min(after, default=words[review]["start"] if review is not None else words[-1]["end"])
        prompt = " ".join(
            f"Word {k}. {entries[k]['word'].capitalize()}. {'-'.join(norm(entries[k]['word']).upper())}."
            for k in run
        )
        patch = transcribe(mp3, cache.with_suffix(f".{lo:.0f}-{hi:.0f}.json"), (lo, hi), prompt)
        words = ([w for w in words if w["end"] <= lo] + [w for w in patch if lo <= w["start"] < hi]
                 + [w for w in words if w["start"] >= hi])
        missing = missing[len(run):]
    return words


def clip_file(mp3: Path, entries: dict[int, dict], out: Path, cache: Path) -> list[dict]:
    match = FILE_RE.search(mp3.stem)
    level, first = int(match[1]), int(match[2])
    transcript = cache / f"{mp3.stem}.json"
    words = retranscribe_missing(mp3, transcribe(mp3, transcript), entries, first, transcript)
    gaps = silences(mp3)

    # Whisper's word times can fall inside the pause around the word; snap them to its edge.
    def snap_end(t: float) -> float:  # start of the silence just after t
        inside = [s for s, e in gaps if s <= t <= e]
        if inside:
            return min(inside)
        after = [s for s, e in gaps if s >= t - 0.15]
        return min(after) if after and min(after) - t < MAX_SNAP else t + 0.1

    def pause_before(t: float) -> tuple[float, float] | None:
        """The pause that leads into the entry Whisper starts at ``t``.

        Entries are separated by the longest pause around them (about two
        seconds), and Whisper's time for the "Word" after it can be off by more
        than a second either way.
        """
        near = [g for g in gaps if t - PAUSE_WINDOW[0] <= g[1] <= t + PAUSE_WINDOW[1]]
        return max(near, key=lambda g: g[1] - g[0], default=None)

    starts = find_starts(words, entries, first)
    review = find_review(words, max((i for i, _, _ in starts.values()), default=0))

    rows = []
    for number in range(first, first + 10):
        entry = entries[number]
        row = {"level": level, "number": number, "word": entry["word"], "source": mp3.name}
        if number not in starts:
            rows.append(row | {"file": None, "warnings": ["keyword not found in transcript"]})
            continue
        _, begin, warnings = starts[number]
        later = [starts[n][1] for n in starts if n > number]
        if later:
            until = min(later)
        elif review is not None:
            until = words[review]["start"]
        else:
            warnings.append("no 'Let's review' found; clip runs to the end of the file")
            until = words[-1]["end"] + 1
        if number + 1 < first + 10 and number + 1 not in starts:
            warnings.append("next keyword not found, so this clip may run into it")
        if hole := transcript_hole(words, begin):
            warnings.append(f"Whisper skipped {hole[0]:.0f}-{hole[1]:.0f}s near the start; "
                            "check this clip")
        pause = pause_before(begin)
        start = pause[1] if pause else begin - 0.1
        if later or review is not None:
            # End where the pause before the next entry begins, so the clip never
            # catches its "Word"; Whisper's end time for the last word can run past it.
            pause = pause_before(until)
            end = pause[0] if pause and pause[0] > start else until - 0.15
        else:
            end = snap_end(max(w["end"] for w in words if w["end"] <= until + 0.05))
        name = f"L{level:02d}_{number:03d}_{norm(entry['word'])}.mp3"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-ss", f"{start - 0.08:.3f}", "-to",
             f"{end + 0.12:.3f}", "-i", str(mp3), "-af", "afade=t=in:d=0.02", "-ac", "1",
             "-c:a", "libmp3lame", "-b:a", "48k", str(out / name)],
            check=True,
        )
        rows.append(row | {"file": name, "start": round(start, 2), "end": round(end, 2),
                           "warnings": warnings})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("words", help="JSONL written by extract_words.py")
    parser.add_argument("--mp3-dir", default=".", help="folder with the audiobook MP3s")
    parser.add_argument("-o", "--output", default="audio", help="output folder")
    parser.add_argument(
        "--only", nargs="+", metavar="PART",
        help="only (re)cut MP3s whose name contains one of these, e.g. level3_01-10",
    )
    args = parser.parse_args()

    with open(args.words, encoding="utf-8") as f:
        entries = [json.loads(line) for line in f if line.strip()]
    by_level: dict[int, dict[int, dict]] = {}
    for e in entries:
        by_level.setdefault(e["level"], {})[e["number"]] = e
    out = Path(args.output)
    cache = out / "transcripts"
    cache.mkdir(parents=True, exist_ok=True)

    mp3s = sorted(
        (p for p in Path(args.mp3_dir).glob("Verbal-Advantage-level*.mp3") if FILE_RE.search(p.stem)),
        key=lambda p: tuple(map(int, FILE_RE.search(p.stem).groups())),
    )
    if args.only:
        mp3s = [p for p in mp3s if any(part in p.name for part in args.only)]
    index = out / "clips.jsonl"
    # Keep the rows of files not cut this time.
    done = {p.name for p in mp3s}
    rows = []
    if index.exists():
        with open(index, encoding="utf-8") as f:
            rows = [r for r in map(json.loads, f) if r["source"] not in done]
    for mp3 in mp3s:
        file_rows = clip_file(mp3, by_level[int(FILE_RE.search(mp3.stem)[1])], out, cache)
        for r in file_rows:
            if r["warnings"]:
                print(f"L{r['level']} #{r['number']} {r['word']}: {'; '.join(r['warnings'])}",
                      file=sys.stderr)
        rows += file_rows
    rows.sort(key=lambda r: (r["level"], r["number"]))
    with open(index, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    clipped = sum(r["file"] is not None for r in rows)
    print(f"Wrote {clipped}/{len(rows)} clips to {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
