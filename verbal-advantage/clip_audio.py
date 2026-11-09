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
# A spelled-out token such as "P-A-R-A" or "-S."
SPELLING_RE = re.compile(r"(-?[A-Z])+[.,]?")


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def transcribe(mp3: Path, cache: Path) -> list[dict]:
    """Return Whisper's words ({"word", "start", "end"}) for ``mp3``, cached as JSON."""
    if not cache.exists():
        import mlx_whisper

        print(f"Transcribing {mp3.name} ...", file=sys.stderr)
        result = mlx_whisper.transcribe(
            str(mp3), path_or_hf_repo=MODEL, word_timestamps=True, language="en"
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
    return list(zip(values[0::2], values[1::2]))


def find_markers(words: list[dict], first: int) -> dict[int, int]:
    """Map each keyword number to the index of its "Word N" in ``words``.

    Skips cross-references such as "Word 40 of level 2" and numbers out of
    sequence. The narrator counts either within the level (21, 22, ...) or
    within the file (1, 2, ...).
    """
    markers: dict[int, int] = {}
    for i in range(len(words) - 2):
        if norm(words[i]["word"]) != "word" or not norm(words[i + 1]["word"]).isdigit():
            continue
        if norm(words[i + 2]["word"]) == "of":
            continue
        n = int(norm(words[i + 1]["word"]))
        if n <= 10 and first > 10:
            n += first - 1
        if first <= n < first + 10 and n not in markers and all(n > m for m in markers):
            markers[n] = i
    return markers


def find_headword(words: list[dict], word: str, lo: int, hi: int) -> int | None:
    """Find the spoken keyword followed by its spelling between ``lo`` and ``hi``.

    Used when Whisper missed the "Word N" before it.
    """
    for i in range(lo, min(hi, len(words) - 1)):
        if norm(words[i]["word"]).startswith(norm(word)[:5]) and SPELLING_RE.fullmatch(
            words[i + 1]["word"].strip()
        ):
            return i
    return None


def clip_file(mp3: Path, entries: dict[int, dict], out: Path, cache: Path) -> list[dict]:
    match = FILE_RE.search(mp3.stem)
    level, first = int(match[1]), int(match[2])
    words = transcribe(mp3, cache / f"{mp3.stem}.json")
    gaps = silences(mp3)

    def snap_start(t: float) -> float:  # end of the silence just before t
        before = [e for s, e in gaps if e <= t + 0.15]
        return max(before) if before and t - max(before) < MAX_SNAP else t - 0.1

    def snap_end(t: float) -> float:  # start of the silence just after t
        after = [s for s, e in gaps if s >= t - 0.15]
        return min(after) if after and min(after) - t < MAX_SNAP else t + 0.1

    review = next(
        (i for i in range(len(words) - 1)
         if norm(words[i]["word"]) in ("lets", "let") and "review" in norm(words[i + 1]["word"])
         or norm(words[i]["word"]) == "letsreview"),
        None,
    )
    markers = find_markers(words, first)
    starts: dict[int, tuple[int, list[str]]] = {}
    for number in range(first, first + 10):
        if number in markers:
            starts[number] = (markers[number], [])
            continue
        lo = max([i for n, i in markers.items() if n < number], default=0)
        hi = min([i for n, i in markers.items() if n > number], default=review or len(words))
        head = find_headword(words, entries[number]["word"], lo + 1, hi)
        if head is not None:
            starts[number] = (head, ["no 'Word N' heard; clip starts at the keyword"])

    rows = []
    for number in range(first, first + 10):
        entry = entries[number]
        row = {"level": level, "number": number, "word": entry["word"], "source": mp3.name}
        if number not in starts:
            rows.append(row | {"file": None, "warnings": ["keyword not found in transcript"]})
            continue
        i, warnings = starts[number]
        later = [starts[n][0] for n in starts if n > number]
        end_index = min(later) if later else review
        if end_index is None:
            warnings.append("no 'Let's review' found; clip runs to the end of the file")
            end_index = len(words)
        if not any(norm(w["word"]).startswith(norm(entry["word"])[:4])
                   for w in words[i : i + 6]):
            warnings.append("keyword not heard right after 'Word N'")
        start = snap_start(words[i]["start"])
        end = snap_end(words[end_index - 1]["end"])
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
    rows = []
    for mp3 in mp3s:
        file_rows = clip_file(mp3, by_level[int(FILE_RE.search(mp3.stem)[1])], out, cache)
        for r in file_rows:
            if r["warnings"]:
                print(f"L{r['level']} #{r['number']} {r['word']}: {'; '.join(r['warnings'])}",
                      file=sys.stderr)
        rows += file_rows
    with open(out / "clips.jsonl", "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    clipped = sum(r["file"] is not None for r in rows)
    print(f"Wrote {clipped}/{len(rows)} clips to {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
