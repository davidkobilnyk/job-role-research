#!/usr/bin/env python3
"""
Count words and bigrams across job descriptions.

Reports document frequency (how many jobs mention the term at least once)
rather than raw occurrences, since "python appears in 4,000 jobs" is the
number that matters and repeats within one posting are noise.

Usage:
    python description_words.py --db jobs.sqlite [--table raw_jobs_filtered3] [--out-dir .] [--min-jobs 5]

Writes:
    desc_words.csv     word, jobs, pct_of_jobs
    desc_bigrams.csv   bigram, jobs, pct_of_jobs
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

# --------------------------------------------------------------------------- #
# Text extraction per source
# --------------------------------------------------------------------------- #

TAG = re.compile(r"<[^>]+>")
WS = re.compile(r"\s+")


def strip_html(s: str) -> str:
    s = html.unescape(s or "")
    s = html.unescape(s)            # Greenhouse content is double-escaped in places
    s = TAG.sub(" ", s)
    return WS.sub(" ", s)


def description(source: str, payload: dict) -> str:
    if source == "greenhouse":
        return strip_html(payload.get("content") or "")
    if source == "ashby":
        return payload.get("descriptionPlain") or strip_html(payload.get("descriptionHtml") or "")
    if source == "lever":
        parts = [payload.get("descriptionPlain") or "", payload.get("additionalPlain") or ""]
        for lst in payload.get("lists") or []:
            parts.append(lst.get("text") or "")
            parts.append(strip_html(lst.get("content") or ""))
        return " ".join(parts)
    return ""


# --------------------------------------------------------------------------- #
# Tokenization
# --------------------------------------------------------------------------- #

# Keeps tech-flavored tokens intact: c++, c#, .net, node.js, ci/cd, scikit-learn, s3.
# A token starts with a letter or digit (or '.' for .net) and may contain + # . / - inside.
TOKEN = re.compile(r"(?:\.net|[a-z0-9][a-z0-9+#./-]*[a-z0-9+#]|[a-z])")
PURE_NUMBER = re.compile(r"^\d+([./-]\d+)*$")


def tokens(text: str) -> list[str]:
    out = []
    for t in TOKEN.findall(text.lower()):
        t = t.rstrip("./-").lstrip("/-")      # keep the leading dot in ".net"
        if not t or PURE_NUMBER.match(t):
            continue
        out.append(t)
    return out


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def run(db: str, table: str, out_dir: Path, min_jobs: int) -> None:
    conn = sqlite3.connect(db)
    word_df: Counter = Counter()
    bigram_df: Counter = Counter()
    n_jobs = 0
    n_empty = 0

    for source, payload in conn.execute(f"select source, payload from {table}"):
        text = description(source, json.loads(payload))
        toks = tokens(text)
        n_jobs += 1
        if not toks:
            n_empty += 1
            continue
        word_df.update(set(toks))
        bigram_df.update(set(zip(toks, toks[1:])))

    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / "desc_words.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["word", "jobs", "pct_of_jobs"])
        for word, n in word_df.most_common():
            if n < min_jobs:
                break
            w.writerow([word, n, round(100 * n / n_jobs, 2)])

    with open(out_dir / "desc_bigrams.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["bigram", "jobs", "pct_of_jobs"])
        for (a, b), n in bigram_df.most_common():
            if n < min_jobs:
                break
            w.writerow([f"{a} {b}", n, round(100 * n / n_jobs, 2)])

    print(f"{n_jobs:,} jobs read from {table} ({n_empty:,} with empty descriptions)")
    print(f"{len(word_df):,} distinct words, {len(bigram_df):,} distinct bigrams")
    print(f"wrote desc_words.csv and desc_bigrams.csv (terms in >= {min_jobs} jobs) to {out_dir}")
    conn.close()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    p.add_argument("--table", default="raw_jobs_filtered3")
    p.add_argument("--out-dir", default=".")
    p.add_argument("--min-jobs", type=int, default=5, help="drop terms seen in fewer jobs than this")
    args = p.parse_args()
    run(args.db, args.table, Path(args.out_dir), args.min_jobs)


if __name__ == "__main__":
    main()
