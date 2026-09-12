#!/usr/bin/env python3
"""
Match job descriptions against the tech vocabulary in tech_terms.py.

Reports document frequency per canonical term and per category, and writes a
job_terms table (one row per job x canonical term) so the density signal
("how many distinct tech terms does this post mention") is a group-by away.

Usage:
    python description_terms.py --db jobs.sqlite [--table raw_jobs_filtered3] [--out-dir .]

Writes:
    term_counts.csv       canonical term, category, jobs, pct_of_jobs
    category_counts.csv   category, jobs mentioning >= 1 term in it
    job_terms table       source, slug, job_id, term, category
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

import tech_terms

# --------------------------------------------------------------------------- #
# Text extraction (same as description_words.py)
# --------------------------------------------------------------------------- #

TAG = re.compile(r"<[^>]+>")
WS = re.compile(r"\s+")


def strip_html(s: str) -> str:
    s = html.unescape(html.unescape(s or ""))
    return WS.sub(" ", TAG.sub(" ", s))


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
# Matcher: one alternation regex over every surface form, boundary-aware
# --------------------------------------------------------------------------- #

# A "boundary" here means: not preceded or followed by a word character or one of
# the symbols that appear inside tech names. This lets "c++" and ".net" match at
# the end of a word while keeping "java" from matching inside "javascript".
LEFT = r"(?<![\w+#.])"
RIGHT = r"(?![\w+#])"


def build_matcher() -> tuple[re.Pattern, dict[str, tuple[str, str]]]:
    lookup: dict[str, tuple[str, str]] = {}
    for form, canonical, category in tech_terms.surface_forms():
        lookup.setdefault(form, (canonical, category))     # first category wins on duplicates
    # Longest forms first so "asp.net core" beats "asp.net" beats ".net".
    forms = sorted(lookup, key=len, reverse=True)
    pattern = LEFT + "(" + "|".join(re.escape(f) for f in forms) + ")" + RIGHT
    return re.compile(pattern, re.IGNORECASE), lookup


def match_terms(text: str, matcher: re.Pattern, lookup: dict) -> set[tuple[str, str]]:
    return {lookup[m.group(1).lower()] for m in matcher.finditer(text)}


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

SCHEMA = """
drop table if exists job_terms;
create table job_terms (
    source   text not null,
    slug     text not null,
    job_id   text not null,
    term     text not null,
    category text not null,
    primary key (source, slug, job_id, term)
);
create index idx_job_terms_term on job_terms(term);
"""


def run(db: str, table: str, out_dir: Path) -> None:
    matcher, lookup = build_matcher()
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)

    term_df: Counter = Counter()
    cat_df: Counter = Counter()
    density: Counter = Counter()
    rows: list[tuple] = []
    n_jobs = 0

    for source, slug, job_id, payload in conn.execute(f"select source, slug, job_id, payload from {table}"):
        n_jobs += 1
        hits = match_terms(description(source, json.loads(payload)), matcher, lookup)
        density[len(hits)] += 1
        for canonical, category in hits:
            term_df[(canonical, category)] += 1
            rows.append((source, slug, job_id, canonical, category))
        for category in {c for _, c in hits}:
            cat_df[category] += 1
        if len(rows) >= 50_000:
            conn.executemany("insert or ignore into job_terms values (?, ?, ?, ?, ?)", rows)
            rows.clear()
    conn.executemany("insert or ignore into job_terms values (?, ?, ?, ?, ?)", rows)
    conn.commit()

    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "term_counts.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["term", "category", "jobs", "pct_of_jobs"])
        for (term, category), n in term_df.most_common():
            w.writerow([term, category, n, round(100 * n / n_jobs, 2)])

    with open(out_dir / "category_counts.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["category", "jobs_with_any_term", "pct_of_jobs"])
        for category, n in cat_df.most_common():
            w.writerow([category, n, round(100 * n / n_jobs, 2)])

    print(f"{n_jobs:,} jobs read from {table}; {len(term_df):,} distinct terms matched")
    print("distinct terms per job:")
    for k in sorted(density):
        bar = "#" * min(60, int(60 * density[k] / n_jobs))
        print(f"  {k:3d}  {density[k]:7,}  {bar}")
    print(f"wrote term_counts.csv, category_counts.csv to {out_dir}; table job_terms populated")
    conn.close()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    p.add_argument("--table", default="raw_jobs_filtered3")
    p.add_argument("--out-dir", default=".")
    args = p.parse_args()
    run(args.db, args.table, Path(args.out_dir))


if __name__ == "__main__":
    main()
