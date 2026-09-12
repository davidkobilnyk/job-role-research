#!/usr/bin/env python3
"""
Build the geo_lookup table used to resolve location parts in SQL.

    pip install pycountry geonamescache

Usage:
    python build_geo_lookup.py --db jobs.sqlite [--aliases aliases.csv] [--min-pop 15000]

Table:
    geo_lookup(term, kind, country, us_flag, ambiguous, population)
      term       lowercase string as it would appear in job_location_parts.part
      kind       country | country_code | us_state | us_state_code | city | region | remote | timezone | nongeo
      country    ISO-2 where known
      us_flag    us | non_us | includes_us | unknown
      ambiguous  1 if the same term names places in more than one country (or a US and a non-US place)
      population for cities, to let SQL prefer the bigger one when it must guess

Primary key is (term, kind): "in" is both an ISO country code (India) and a US state code
(Indiana). The SQL picks the kind from context (source_field, position in the string).

Optional aliases.csv (term,kind,country,us_flag) is loaded last and overrides everything.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
from collections import defaultdict
from pathlib import Path

SCHEMA = """
drop table if exists geo_lookup;
create table geo_lookup (
    term       text not null,
    kind       text not null,
    country    text,
    us_flag    text not null,
    ambiguous  integer not null default 0,
    population integer,
    primary key (term, kind)
);
create index idx_geo_lookup_term on geo_lookup(term);
"""

# --------------------------------------------------------------------------- #
# Hand-maintained entries. These win over package data.
# --------------------------------------------------------------------------- #

US_COUNTRY_ALIASES = [
    "united states", "united states of america", "usa", "u.s.", "u.s", "us", "u.s.a.", "u.s.a",
    "america", "the us", "the united states", "usa only", "us only", "us-based", "us based",
    "puerto rico", "guam", "us virgin islands", "u.s. virgin islands",
]

COUNTRY_ALIASES = {   # alias -> ISO-2
    "uk": "GB", "united kingdom": "GB", "england": "GB", "scotland": "GB", "wales": "GB",
    "northern ireland": "GB", "britain": "GB", "great britain": "GB", "u.k.": "GB",
    "south korea": "KR", "korea": "KR", "russia": "RU", "vietnam": "VN", "iran": "IR",
    "czech republic": "CZ", "czechia": "CZ", "the netherlands": "NL", "holland": "NL",
    "uae": "AE", "united arab emirates": "AE", "dubai": "AE", "hong kong": "HK", "macau": "MO",
    "taiwan": "TW", "türkiye": "TR", "turkey": "TR", "brasil": "BR", "méxico": "MX",
    "deutschland": "DE", "españa": "ES", "italia": "IT", "sverige": "SE", "norge": "NO",
    "danmark": "DK", "suomi": "FI", "polska": "PL", "schweiz": "CH", "österreich": "AT",
    "ivory coast": "CI", "cote d'ivoire": "CI", "bolivia": "BO", "venezuela": "VE", "tanzania": "TZ",
    "laos": "LA", "syria": "SY", "moldova": "MD", "north macedonia": "MK", "kosovo": "XK",
}

REGIONS = {   # term -> us_flag
    "worldwide": "includes_us", "global": "includes_us", "anywhere": "includes_us",
    "international": "includes_us", "americas": "includes_us", "north america": "includes_us",
    "the americas": "includes_us", "amer": "includes_us",
    "emea": "non_us", "emeia": "non_us", "apac": "non_us", "apj": "non_us", "latam": "non_us",
    "latin america": "non_us", "south america": "non_us", "central america": "non_us",
    "eu": "non_us", "europe": "non_us", "european union": "non_us", "eastern europe": "non_us",
    "western europe": "non_us", "central europe": "non_us", "nordics": "non_us", "scandinavia": "non_us",
    "dach": "non_us", "benelux": "non_us", "uk&i": "non_us", "uki": "non_us", "asia": "non_us",
    "southeast asia": "non_us", "south asia": "non_us", "east asia": "non_us", "middle east": "non_us",
    "mena": "non_us", "africa": "non_us", "sub-saharan africa": "non_us", "oceania": "non_us",
    "anz": "non_us", "australia and new zealand": "non_us", "cee": "non_us", "gcc": "non_us",
    "caribbean": "non_us",
}

REMOTE_WORDS = [
    "remote", "remote work", "remote-first", "fully remote", "100% remote", "hybrid", "onsite", "on-site",
    "on site", "in-office", "in office", "distributed", "flexible", "work from home", "wfh", "telecommute",
    "virtual", "remote-friendly", "remote friendly", "remote optional", "remote-optional", "home based",
    "home-based", "unspecified", "remote us", "us remote", "remote usa", "usa remote", "remote united states",
]
# Two-letter time zone abbreviations (ET, CT, MT, PT) are deliberately absent: they collide
# with ISO country codes (PT = Portugal, ET = Ethiopia, MT = Malta). "est" still collides with
# Estonia's alpha-3 code; the SQL should only read country_code rows from Lever's `country` field.
# Some remote words carry geography: treat those as US.
REMOTE_WORDS_US = {"remote us", "us remote", "remote usa", "usa remote", "remote united states"}

NONGEO = [
    "n/a", "na", "tbd", "tba", "multiple locations", "multiple", "various", "various locations", "other",
    "hq", "headquarters", "office", "offices", "any", "any location", "open", "flexible location",
    "no location", "none", "all", "all locations", "global remote", "location", "anywhere in the world",
]

US_METRO_ALIASES = {   # term -> (state ISO-2 suffix or None)
    "nyc": "NY", "new york city": "NY", "manhattan": "NY", "brooklyn": "NY", "tri-state": "NY",
    "tri-state area": "NY", "greater new york": "NY", "new york metro": "NY", "new york area": "NY",
    "sf": "CA", "sf bay area": "CA", "san francisco bay area": "CA", "bay area": "CA", "silicon valley": "CA",
    "socal": "CA", "norcal": "CA", "la": "CA", "los angeles area": "CA", "greater los angeles": "CA",
    "south bay": "CA", "east bay": "CA", "peninsula": "CA", "orange county": "CA",
    "dc": "DC", "d.c.": "DC", "d.c": "DC", "washington dc": "DC", "washington d.c.": "DC", "washington, d.c.": "DC",
    "dmv": "DC", "dc metro": "DC", "dc metro area": "DC", "greater washington": "DC", "nova": "VA",
    "northern virginia": "VA", "greater boston": "MA", "boston area": "MA", "greater boston area": "MA",
    "chicagoland": "IL", "greater chicago": "IL", "dfw": "TX", "dallas-fort worth": "TX", "dallas/fort worth": "TX",
    "twin cities": "MN", "research triangle": "NC", "rtp": "NC", "the triangle": "NC", "raleigh-durham": "NC",
    "greater seattle": "WA", "seattle area": "WA", "puget sound": "WA", "greater denver": "CO", "denver metro": "CO",
    "greater atlanta": "GA", "metro atlanta": "GA", "greater philadelphia": "PA", "philly": "PA",
    "greater houston": "TX", "austin area": "TX", "greater phoenix": "AZ", "phoenix metro": "AZ",
    "greater miami": "FL", "south florida": "FL", "tampa bay": "FL", "salt lake": "UT", "silicon slopes": "UT",
    "greater portland": "OR", "greater detroit": "MI", "metro detroit": "MI", "pittsburgh area": "PA",
    "new england": None, "midwest": None, "pacific northwest": None, "pnw": None, "east coast": None,
    "west coast": None, "southeast": None, "southwest": None, "northeast": None, "mid-atlantic": None,
    "sunbelt": None, "continental us": None, "contiguous us": None, "lower 48": None, "conus": None,
    "us time zones": None, "united states (remote)": None, "any us location": None,
}

TIMEZONES = {   # term -> us_flag
    "est": "us", "edt": "us", "cst": "us", "cdt": "us", "mst": "us", "mdt": "us", "pst": "us", "pdt": "us",
    "eastern time": "us", "central time": "us",
    "mountain time": "us", "pacific time": "us", "eastern time zone": "us", "pacific time zone": "us",
    "us eastern": "us", "us pacific": "us", "us central": "us", "us hours": "us", "us business hours": "us",
    "cet": "non_us", "cest": "non_us", "gmt": "non_us", "bst": "non_us", "ist": "non_us", "aest": "non_us",
    "aedt": "non_us", "eet": "non_us", "wet": "non_us", "jst": "non_us", "sgt": "non_us", "hkt": "non_us",
    "european hours": "non_us", "uk hours": "non_us", "european time zones": "non_us", "emea hours": "non_us",
    "utc": "unknown", "utc-5": "us", "utc-6": "us", "utc-7": "us", "utc-8": "us", "utc+1": "non_us",
    "utc+2": "non_us", "utc+0": "non_us", "utc+5:30": "non_us", "utc+8": "non_us",
}

# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #

Row = tuple  # (term, kind, country, us_flag, ambiguous, population)


def hand_rows() -> list[Row]:
    rows: list[Row] = []
    for t in US_COUNTRY_ALIASES:
        rows.append((t, "country", "US", "us", 0, None))
    for t, iso in COUNTRY_ALIASES.items():
        rows.append((t, "country", iso, "non_us", 0, None))
    for t, flag in REGIONS.items():
        rows.append((t, "region", None, flag, 0, None))
    for t in REMOTE_WORDS:
        rows.append((t, "remote", "US" if t in REMOTE_WORDS_US else None,
                     "us" if t in REMOTE_WORDS_US else "unknown", 0, None))
    for t in NONGEO:
        rows.append((t, "nongeo", None, "unknown", 0, None))
    for t, state in US_METRO_ALIASES.items():
        rows.append((t, "us_metro", "US", "us", 0, None))
    for t, flag in TIMEZONES.items():
        rows.append((t, "timezone", "US" if flag == "us" else None, flag, 0, None))
    return rows


def country_rows() -> list[Row]:
    import pycountry
    rows: list[Row] = []
    for c in pycountry.countries:
        iso = c.alpha_2
        flag = "us" if iso == "US" else "non_us"
        names = {c.name}
        for attr in ("official_name", "common_name"):
            v = getattr(c, attr, None)
            if v:
                names.add(v)
        for n in names:
            rows.append((n.lower(), "country", iso, flag, 0, None))
        rows.append((iso.lower(), "country_code", iso, flag, 0, None))
        rows.append((c.alpha_3.lower(), "country_code", iso, flag, 0, None))
    return rows


def us_state_rows() -> list[Row]:
    import pycountry
    rows: list[Row] = []
    for s in pycountry.subdivisions.get(country_code="US"):
        code = s.code.split("-", 1)[1]          # "US-NC" -> "NC"
        rows.append((s.name.lower(), "us_state", "US", "us", 0, None))
        rows.append((code.lower(), "us_state_code", "US", "us", 0, None))
    rows.append(("washington dc", "us_state", "US", "us", 0, None))
    rows.append(("washington, d.c.", "us_state", "US", "us", 0, None))
    return rows


def city_rows(min_pop: int) -> list[Row]:
    """
    Collapse geonamescache cities by lowercase name. If a name exists in more than one
    country, keep the most populous and mark it ambiguous; us_flag follows the winner.
    """
    import geonamescache
    gc = geonamescache.GeonamesCache()
    by_name: dict[str, list[dict]] = defaultdict(list)
    for city in gc.get_cities().values():
        if city.get("population", 0) < min_pop:
            continue
        by_name[city["name"].lower()].append(city)
        for alt in city.get("alternatenames", []) or []:
            if alt and alt.isascii() and len(alt) > 2:
                by_name[alt.lower()].append(city)

    rows: list[Row] = []
    for name, cities in by_name.items():
        winner = max(cities, key=lambda c: c.get("population", 0))
        countries = {c["countrycode"] for c in cities}
        ambiguous = 1 if len(countries) > 1 else 0
        iso = winner["countrycode"]
        flag = "us" if iso == "US" else "non_us"
        rows.append((name, "city", iso, flag, ambiguous, winner.get("population")))
    return rows


def alias_file_rows(path: Path) -> list[Row]:
    rows: list[Row] = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append((r["term"].strip().lower(), r["kind"].strip(), (r.get("country") or "").strip() or None,
                         r["us_flag"].strip(), int(r.get("ambiguous") or 0), None))
    return rows


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def run(db: str, aliases: Path | None, min_pop: int) -> None:
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)

    def load(label: str, rows: list[Row], replace: bool) -> None:
        verb = "insert or replace" if replace else "insert or ignore"
        conn.executemany(f"{verb} into geo_lookup values (?, ?, ?, ?, ?, ?)", rows)
        print(f"  {label:<14} {len(rows):>7,} rows")

    # Order matters: earlier loads win on (term, kind) collisions; hand entries and the
    # alias file use replace so they override package data.
    load("countries", country_rows(), replace=False)
    load("us states", us_state_rows(), replace=False)
    load("cities", city_rows(min_pop), replace=False)
    load("hand entries", hand_rows(), replace=True)
    if aliases:
        load("alias file", alias_file_rows(aliases), replace=True)

    conn.commit()
    n = conn.execute("select count(*) from geo_lookup").fetchone()[0]
    amb = conn.execute("select count(*) from geo_lookup where ambiguous = 1").fetchone()[0]
    print(f"geo_lookup: {n:,} rows ({amb:,} ambiguous city names)")
    conn.close()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    p.add_argument("--aliases", type=Path, help="CSV of term,kind,country,us_flag[,ambiguous] that overrides everything")
    p.add_argument("--min-pop", type=int, default=15000, help="minimum city population to include")
    args = p.parse_args()
    run(args.db, args.aliases, args.min_pop)


if __name__ == "__main__":
    main()
