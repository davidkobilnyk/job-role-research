#!/usr/bin/env python3
"""
Normalize job titles from raw_jobs so the long tail collapses into
reviewable roles. Writes:

  title_norm table   one row per job: raw title, cleaned title, level tokens
  titles.csv         cleaned titles by frequency, with company count and examples
  title_words.csv    word frequency across cleaned titles

Usage:
    python normalize_titles.py --db jobs.sqlite [--out-dir .]

Cleaning is deliberately lossy: it exists to group titles for review and to
feed the role-family rules, not to be the display title. The raw title is
kept alongside it.
"""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

# --------------------------------------------------------------------------- #
# Cleaning rules
# --------------------------------------------------------------------------- #

# Leading bracketed / hash-numbered junk: "[Job - 31247] ", "(Req 4421) ", "#1234 - "
LEADING_JUNK = re.compile(r"^\s*(\[[^\]]*\]|\([^)]*\)|#\s*\d+)\s*[-–—:|]?\s*")

# Trailing req ids: "... (R-12345)", "... - JR123456", "... #12345"
TRAILING_ID = re.compile(r"\s*[-–—(#]\s*(req(uisition)?\s*#?\s*)?[a-z]{0,3}-?\d{3,}\)?\s*$", re.I)

# Anything after these separators is usually location, team, or tech stack.
CUT_SEPARATORS = re.compile(r"\s+[-–—|]\s+|\s*\(|\s*\[|\s+@\s+")

# Comma segments that look like locations get dropped; others are kept.
US_STATES = (
    "al ak az ar ca co ct de fl ga hi id il in ia ks ky la me md ma mi mn ms mo mt ne nv nh nj nm ny nc nd "
    "oh ok or pa ri sc sd tn tx ut vt va wa wv wi wy dc"
).split()
LOCATION_WORDS = {
    "remote", "hybrid", "onsite", "on-site", "in-office", "us", "usa", "u.s.", "united states", "uk", "canada",
    "emea", "apac", "latam", "europe", "north america", "global", "worldwide", "anywhere",
    "new york", "nyc", "san francisco", "sf", "bay area", "los angeles", "seattle", "austin", "denver",
    "boston", "chicago", "atlanta", "london", "berlin", "toronto", "india", "germany", "france", "brazil",
    "mexico", "poland", "spain", "netherlands", "ireland", "australia", "singapore", "japan", "israel",
}
LOCATION_SEGMENT = re.compile(
    r"^(?:" + "|".join(re.escape(w) for w in LOCATION_WORDS) + r"|[a-z]{2})$"   # [a-z]{2} catches state abbrs
)

# Level / seniority tokens are pulled out into their own column.
LEVEL_TOKENS = {
    "senior": "senior", "sr": "senior", "sr.": "senior", "snr": "senior",
    "junior": "junior", "jr": "junior", "jr.": "junior",
    "lead": "lead", "staff": "staff", "principal": "principal", "distinguished": "distinguished",
    "fellow": "fellow", "intern": "intern", "internship": "intern", "co-op": "intern",
    "entry-level": "entry", "mid-level": "mid",
    "i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5",
    "1": "1", "2": "2", "3": "3", "4": "4", "5": "5",
}
# "entry", "mid", "associate" on their own are words, not levels ("Entry Door Installer",
# "Mid-Market AE", "Sales Associate"); only the explicit hyphenated forms count.
BIGRAM_LEVELS = re.compile(r"\b(entry|mid)[\s-]+level\b")
# "i" and single digits are only levels when standalone at the end or before a comma/slash,
# otherwise "Data Analyst I" vs "I" in some other position. Handled in extract_levels.
STANDALONE_ONLY = {"i", "v", "1", "2", "3", "4", "5"}

WHITESPACE = re.compile(r"\s+")
PUNCT = re.compile(r"[^\w\s+#/&.-]")           # keep c++, c#, ci/cd, r&d, .net


def clean_title(raw: str) -> tuple[str, str]:
    """Return (cleaned_title, level_tokens) for a raw title."""
    t = WHITESPACE.sub(" ", raw or "").strip()
    t = LEADING_JUNK.sub("", t)
    t = TRAILING_ID.sub("", t)
    t = t.lower()

    # Cut at the first separator that usually introduces location/team/stack.
    t = CUT_SEPARATORS.split(t, maxsplit=1)[0]

    # Drop comma segments that look like locations; keep the rest in order.
    segments = [s.strip() for s in t.split(",")]
    kept = [s for s in segments if s and not LOCATION_SEGMENT.match(s)]
    if not kept:                       # everything looked like a location; keep the first segment
        kept = segments[:1]
    t = ", ".join(kept)

    t = PUNCT.sub(" ", t)
    t = WHITESPACE.sub(" ", t).strip(" .-/")
    t = BIGRAM_LEVELS.sub(r"\1-level", t)

    t, levels = extract_levels(t)
    return t, levels


def extract_levels(t: str) -> tuple[str, str]:
    words = t.split(" ")
    levels: list[str] = []
    kept: list[str] = []
    n = len(words)
    for i, w in enumerate(words):
        key = w.strip(".,")
        if key in LEVEL_TOKENS:
            if key in STANDALONE_ONLY and i != n - 1:
                kept.append(w)             # "i"/"v"/digits only count as levels at the end
                continue
            levels.append(LEVEL_TOKENS[key])
        else:
            kept.append(w)
    cleaned = WHITESPACE.sub(" ", " ".join(kept)).strip(" ,.-/")
    return cleaned, "+".join(sorted(set(levels)))


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

SCHEMA = """
drop table if exists title_norm;
create table title_norm (
    source      text not null,
    slug        text not null,
    job_id      text not null,
    raw_title   text,
    clean_title text,
    levels      text,
    primary key (source, slug, job_id)
);
create index idx_title_norm_clean on title_norm(clean_title);
"""

SELECT = """
select source, slug, job_id,
       coalesce(json_extract(payload, '$.title'),   -- greenhouse, ashby
                json_extract(payload, '$.text'))    -- lever
from raw_jobs_filtered5
"""


def run(db: str, out_dir: Path) -> None:
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)

    rows = []
    freq: dict[str, Counter] = defaultdict(Counter)       # clean -> Counter(raw)
    companies: dict[str, set] = defaultdict(set)
    words = Counter()

    for source, slug, job_id, raw in conn.execute(SELECT):
        clean, levels = clean_title(raw or "")
        rows.append((source, slug, job_id, raw, clean, levels))
        freq[clean][raw] += 1
        companies[clean].add(f"{source}/{slug}")
        words.update(clean.split())

    conn.executemany("insert into title_norm values (?, ?, ?, ?, ?, ?)", rows)
    conn.commit()

    with open(out_dir / "titles.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["clean_title", "n", "companies", "n_raw_variants", "example_raw_titles"])
        for clean, raws in sorted(freq.items(), key=lambda kv: -sum(kv[1].values())):
            examples = " || ".join(r for r, _ in raws.most_common(3))
            w.writerow([clean, sum(raws.values()), len(companies[clean]), len(raws), examples])

    with open(out_dir / "title_words.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["word", "n"])
        for word, n in words.most_common():
            w.writerow([word, n])

    total = len(rows)
    distinct_raw = len({r[3] for r in rows})
    distinct_clean = len(freq)
    singletons = sum(1 for c in freq.values() if sum(c.values()) == 1)
    print(f"{total:,} jobs")
    print(f"{distinct_raw:,} distinct raw titles -> {distinct_clean:,} cleaned ({singletons:,} still singletons)")
    print(f"wrote {out_dir / 'titles.csv'} and {out_dir / 'title_words.csv'}; table title_norm populated")
    conn.close()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    p.add_argument("--out-dir", default=".")
    args = p.parse_args()
    run(args.db, Path(args.out_dir))


if __name__ == "__main__":
    main()
