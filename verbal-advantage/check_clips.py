#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["mlx-whisper"]
# ///
"""Check the clips written by ``clip_audio.py`` by transcribing each clip on its own.

Whisper runs again on the cut files (without conditioning on earlier text, unlike
the clipping pass), and each clip is scored against the book:

- it opens with "Word N" for its own number, and the keyword is spelled out;
- the book's definition is heard in the first 45 seconds;
- how much of the book's discussion, and of its last paragraph, is heard;
- no "Word N+1" or "Let's review" from what follows leaked in;
- the first and last moments are silent, so no word is cut off.

Writes one JSON line per clip to ``<audio>/check.jsonl`` (an interrupted run
resumes where it stopped) and prints the clips that fail a check. The audiobook
sometimes rewords or skips the book's text, so look at or listen to a flagged
clip before treating it as wrong. Needs ffmpeg and an Apple Silicon Mac.

Usage:
    uv run check_clips.py words.jsonl -a audio            # every clip
    uv run check_clips.py words.jsonl -a audio --sample 5  # 5 random clips per level
"""

from __future__ import annotations

import argparse
import difflib
import json
import random
import re
import subprocess
import sys
from pathlib import Path

MODEL = "mlx-community/whisper-large-v3-turbo"
SPELLING_RE = re.compile(r"-?[A-Za-z](-[A-Za-z])*[.,]?")
NUMBER_WORDS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                "fourteen fifteen sixteen seventeen eighteen nineteen twenty").split()
# Common words left out when comparing the clip's words with the book's.
STOP = set("this that with from have which their there about would these those other were been "
           "they them into also more most such than then when what your will only some very word "
           "words means meaning".split())
# Below these scores a clip is flagged. A clip edge louder than SILENCE_DB has
# speech in it; the room tone in the audiobook's pauses peaks around -33 dB.
MIN_DEFINITION, MIN_CONTENT, MIN_LAST_PARAGRAPH = 0.5, 0.6, 0.5
SILENCE_DB = -30.0


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def content_words(text: str) -> set[str]:
    return {norm(w) for w in text.split() if len(norm(w)) >= 5 and norm(w) not in STOP}


def coverage(book: str, heard: set[str]) -> float:
    words = content_words(book)
    return round(len(words & heard) / len(words), 2) if words else 1.0


def says_number(token: str, number: int) -> bool:
    """Whether Whisper's ``token`` is the N of "Word N" (counted in the level or the file)."""
    t = norm(token)
    names = {str(number), str((number - 1) % 10 + 1)}
    names |= {NUMBER_WORDS[n] for n in (number, (number - 1) % 10 + 1) if n < len(NUMBER_WORDS)}
    if "four" in names:
        names.add("for")
    return t in names


def edge_db(path: Path, at_end: bool) -> float | None:
    """Peak level of the clip's first or last 50 ms, which ``clip_audio.py`` cuts in silence."""
    duration = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True).stdout)
    start = max(duration - 0.1, 0) if at_end else 0
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-t", "0.05", "-i", str(path),
         "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr
    match = re.search(r"max_volume: ([-0-9.]+) dB", out)
    return float(match[1]) if match else None


def check(clip: dict, entry: dict, path: Path) -> dict:
    import mlx_whisper

    result = mlx_whisper.transcribe(str(path), path_or_hf_repo=MODEL, word_timestamps=True,
                                    language="en", condition_on_previous_text=False)
    words = [w for s in result["segments"] for w in s["words"]]
    tokens = [w["word"].strip() for w in words]
    head = [w for w in words if w["start"] < 25]
    opening = [w for w in words if w["start"] < 45]
    closing = [w for w in words if words and w["start"] > words[-1]["end"] - 60]

    target = norm(entry["word"])
    letters = norm("".join(w["word"] for w in head if SPELLING_RE.fullmatch(w["word"].strip())))
    spelled = target in letters or difflib.SequenceMatcher(None, target, letters[: len(target) + 3]).ratio() >= 0.8
    paragraphs = [p for p in entry["content"].split("\n\n") if p.strip()]

    intrusions = []
    for i in range(len(words) - 1):
        if norm(tokens[i]) == "word" and words[i]["start"] > 5 and says_number(tokens[i + 1], clip["number"] + 1):
            intrusions.append(f"'Word {tokens[i + 1]}' at {words[i]['start']:.0f}s")
        if norm(tokens[i]) in ("lets", "let") and "review" in norm(tokens[i + 1]):
            intrusions.append(f"'Let's review' at {words[i]['start']:.0f}s")

    return {
        "level": clip["level"], "number": clip["number"], "word": entry["word"], "file": clip["file"],
        "starts_with_word_n": len(tokens) > 1 and norm(tokens[0]) == "word" and says_number(tokens[1], clip["number"]),
        "first_words": " ".join(tokens[:3]),
        "keyword_spelled": spelled,
        "keyword_said": target[:5] in norm("".join(w["word"] for w in head)),
        "definition_coverage": coverage(entry["definition"], content_words(" ".join(w["word"] for w in opening))),
        "content_coverage": coverage(entry["content"], content_words(" ".join(tokens))),
        "last_paragraph_coverage": coverage(paragraphs[-1] if paragraphs else "",
                                            content_words(" ".join(w["word"] for w in closing))),
        "intrusions": intrusions,
        "start_edge_db": edge_db(path, at_end=False),
        "end_edge_db": edge_db(path, at_end=True),
        "tail": " ".join(tokens[-25:]),
    }


def problems(r: dict) -> list[str]:
    found = []
    if not r["starts_with_word_n"]:
        found.append(f"starts with '{r['first_words']}'")
    if not r["keyword_spelled"]:
        found.append("spelling not heard")
    if r["intrusions"]:
        found.append("contains " + ", ".join(r["intrusions"]))
    if r["definition_coverage"] < MIN_DEFINITION:
        found.append(f"definition {r['definition_coverage']:.0%}")
    if r["content_coverage"] < MIN_CONTENT:
        found.append(f"discussion {r['content_coverage']:.0%}")
    if r["last_paragraph_coverage"] < MIN_LAST_PARAGRAPH:
        found.append(f"last paragraph {r['last_paragraph_coverage']:.0%}")
    for edge in ("start_edge_db", "end_edge_db"):
        if r[edge] is not None and r[edge] > SILENCE_DB:
            found.append(f"{edge.split('_')[0]} not silent ({r[edge]:.0f} dB)")
    return found


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("words", help="JSONL written by extract_words.py")
    parser.add_argument("-a", "--audio", default="audio", help="folder written by clip_audio.py")
    parser.add_argument("--sample", type=int, metavar="N", help="check N random clips per level")
    parser.add_argument("--seed", type=int, default=0, help="random seed for --sample")
    args = parser.parse_args()

    audio = Path(args.audio)
    with open(args.words, encoding="utf-8") as f:
        entries = {(e["level"], e["number"]): e for e in map(json.loads, f)}
    with open(audio / "clips.jsonl", encoding="utf-8") as f:
        clips = [c for c in map(json.loads, f) if c["file"]]
    if args.sample:
        rng = random.Random(args.seed)
        clips = [c for level in sorted({c["level"] for c in clips})
                 for c in rng.sample([c for c in clips if c["level"] == level], args.sample)]

    output = audio / "check.jsonl"
    done = {}
    if output.exists():
        with open(output, encoding="utf-8") as f:
            done = {r["file"]: r for r in map(json.loads, f)}
    for n, clip in enumerate(clips, 1):
        if clip["file"] in done:
            continue
        print(f"[{n}/{len(clips)}] {clip['file']}", file=sys.stderr)
        result = check(clip, entries[(clip["level"], clip["number"])], audio / clip["file"])
        done[clip["file"]] = result
        with open(output, "a", encoding="utf-8") as f:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")

    results = [done[c["file"]] for c in clips]
    flagged = [(r, problems(r)) for r in results if problems(r)]
    for r, found in flagged:
        print(f"L{r['level']} #{r['number']} {r['word']}: {'; '.join(found)}")
    print(f"Checked {len(results)} clips; {len(flagged)} flagged (see {output})", file=sys.stderr)


if __name__ == "__main__":
    main()
