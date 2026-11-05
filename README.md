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
