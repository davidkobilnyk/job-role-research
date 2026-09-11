#!/usr/bin/env python3
"""
ATS board fetcher: Greenhouse, Lever, Ashby.

Reads slugs from the `tenants` table, fetches each board's open jobs with
full descriptions, and upserts the raw job objects into `raw_jobs`.
Nothing is normalized here; each payload is stored exactly as returned.

Adding a platform means writing one small Adapter subclass (URL, how to
pull the job list out of the response, how to read a job's id) and
registering it. Retry, backoff, storage, and the run loop are shared.

Usage:
    python fetch_ats.py --db jobs.sqlite --source greenhouse --test-set
    python fetch_ats.py --db jobs.sqlite --source lever --limit 50
    python fetch_ats.py --db jobs.sqlite --source all
    python fetch_ats.py --db jobs.sqlite --source ashby --slug openai -v
    python fetch_ats.py --db jobs.sqlite --source all --force

Assumed `tenants` columns (edit TENANT_QUERY if yours differ):
    source TEXT, slug TEXT, test_set INTEGER, last_attempted TEXT,
    last_status TEXT, job_count INTEGER
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sqlite3
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

USER_AGENT = "job-market-research/0.2 (personal, low-volume)"

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})   # transient: back off and retry
SKIP_STATUSES = frozenset({400, 401, 403, 404})          # permanent for this slug: log, move on

MAX_RETRIES = 5
BASE_BACKOFF = 2.0        # seconds; doubles per retry, plus jitter
REQUEST_TIMEOUT = 30      # seconds
DEFAULT_DELAY = 0.5       # polite pause between boards
RECENT_HOURS = 20         # skip slugs fetched successfully within this window unless --force

log = logging.getLogger("fetch_ats")

Json = dict[str, Any]


# --------------------------------------------------------------------------- #
# Adapters: the only part that knows anything platform-specific
# --------------------------------------------------------------------------- #

class AdapterError(ValueError):
    """Raised when a 200 response doesn't have the shape the adapter expects."""


class Adapter(ABC):
    """One ATS platform. Subclasses are stateless; register instances in ADAPTERS."""

    name: str                # value stored in raw_jobs.source and matched against tenants.source

    @abstractmethod
    def board_url(self, slug: str) -> str:
        """URL that returns every open job on this board, descriptions included."""

    @abstractmethod
    def extract_jobs(self, data: Any) -> list[Json]:
        """Pull the list of job objects out of a parsed 200 response."""

    def job_id(self, job: Json) -> str | None:
        """Stable identifier for a job within its board. Default: the 'id' field."""
        value = job.get("id")
        return None if value is None else str(value)


class Greenhouse(Adapter):
    name = "greenhouse"

    def board_url(self, slug: str) -> str:
        # content=true is what includes the description; without it you get titles only.
        return f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"

    def extract_jobs(self, data: Any) -> list[Json]:
        jobs = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(jobs, list):
            raise AdapterError("response missing 'jobs' list")
        return jobs


class Lever(Adapter):
    name = "lever"

    def board_url(self, slug: str) -> str:
        # mode=json returns the full postings with descriptionPlain, lists[], etc.
        return f"https://api.lever.co/v0/postings/{slug}?mode=json"

    def extract_jobs(self, data: Any) -> list[Json]:
        # Lever returns a bare list. An unknown site is usually a 404, but an
        # empty list is also a valid "no open postings" answer, so accept it.
        if not isinstance(data, list):
            raise AdapterError("expected a JSON list of postings")
        return data


class Ashby(Adapter):
    name = "ashby"

    def board_url(self, slug: str) -> str:
        # includeCompensation=true adds the structured compensation block.
        return f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"

    def extract_jobs(self, data: Any) -> list[Json]:
        jobs = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(jobs, list):
            raise AdapterError("response missing 'jobs' list")
        return jobs


ADAPTERS: dict[str, Adapter] = {a.name: a for a in (Greenhouse(), Lever(), Ashby())}


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #

SCHEMA = """
create table if not exists raw_jobs (
    source        text not null,
    slug          text not null,
    job_id        text not null,
    first_seen_at text not null,
    last_seen_at  text not null,
    fetched_at    text not null,
    payload       text not null,
    primary key (source, slug, job_id)
);

create table if not exists fetch_log (
    source      text not null,
    slug        text not null,
    fetched_at  text not null,
    http_status integer,
    job_count   integer,
    error       text,
    elapsed_ms  integer
);

create index if not exists idx_raw_jobs_slug on raw_jobs(source, slug);
create index if not exists idx_fetch_log_slug on fetch_log(source, slug, fetched_at);
"""

# A slug is pending when: never attempted; succeeded but not recently; or failed
# transiently (network error, 5xx). Permanent failures (4xx: dead or private
# board) are skipped on later runs unless --force, so dead slugs don't cost a
# request every night.
TENANT_QUERY = """
select slug from tenants
where source = :source
  and (:force = 1
       or last_attempted is null
       or (last_status = 'ok' and last_attempted < :cutoff)
       or (last_status != 'ok' and last_status not like 'http\\_4%' escape '\\'))
order by slug
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# HTTP: platform-agnostic fetch with retry
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class BoardResult:
    """Outcome of fetching one board."""
    status: int | None
    jobs: list[Json] | None
    error: str | None
    elapsed_ms: int

    @property
    def ok(self) -> bool:
        return self.jobs is not None

    @property
    def skipped(self) -> bool:
        return self.status in SKIP_STATUSES


def fetch_board(session: requests.Session, adapter: Adapter, slug: str) -> BoardResult:
    """
    Fetch one board. Retries transient failures with exponential backoff and
    honors Retry-After; returns immediately on permanent failures (404 etc.).
    """
    url = adapter.board_url(slug)
    started = time.monotonic()
    label = f"{adapter.name}/{slug}"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            if attempt == MAX_RETRIES:
                return BoardResult(None, None, f"request failed: {exc}", _ms(started))
            _sleep_backoff(attempt, None, f"{label}: {exc}")
            continue

        status = resp.status_code

        if status == 200:
            try:
                jobs = adapter.extract_jobs(resp.json())
            except (ValueError, AdapterError) as exc:     # ValueError covers bad JSON
                return BoardResult(status, None, str(exc), _ms(started))
            return BoardResult(status, jobs, None, _ms(started))

        if status in SKIP_STATUSES:
            return BoardResult(status, None, f"http {status}", _ms(started))

        if status in RETRY_STATUSES:
            if attempt == MAX_RETRIES:
                return BoardResult(status, None, f"http {status} after {attempt} attempts", _ms(started))
            _sleep_backoff(attempt, resp.headers.get("Retry-After"), f"{label}: http {status}")
            continue

        return BoardResult(status, None, f"unexpected http {status}", _ms(started))

    return BoardResult(None, None, "exhausted retries", _ms(started))


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _sleep_backoff(attempt: int, retry_after: str | None, reason: str) -> None:
    wait: float | None = None
    if retry_after:
        try:
            wait = float(retry_after)
        except ValueError:
            pass
    if wait is None:
        wait = BASE_BACKOFF * (2 ** (attempt - 1)) + random.uniform(0, 1)
    log.warning("%s — retrying in %.1fs (attempt %d/%d)", reason, wait, attempt, MAX_RETRIES)
    time.sleep(wait)


# --------------------------------------------------------------------------- #
# Storage: platform-agnostic, keyed by adapter.name
# --------------------------------------------------------------------------- #

def upsert_jobs(conn: sqlite3.Connection, adapter: Adapter, slug: str,
                jobs: list[Json], fetched_at: str) -> int:
    """
    Insert new jobs, refresh existing ones. first_seen_at is preserved across
    runs; last_seen_at and payload are updated each time a job is seen. Jobs
    that vanish from a board are never deleted; their last_seen_at just stops
    advancing, which is how "closed" gets inferred downstream.
    """
    rows = []
    for job in jobs:
        job_id = adapter.job_id(job)
        if job_id is None:
            log.warning("%s/%s: job without id, skipping: %r", adapter.name, slug, job.get("title"))
            continue
        rows.append((
            adapter.name, slug, job_id,
            fetched_at, fetched_at, fetched_at,
            json.dumps(job, ensure_ascii=False, sort_keys=True),
        ))

    conn.executemany(
        """
        insert into raw_jobs (source, slug, job_id, first_seen_at, last_seen_at, fetched_at, payload)
        values (?, ?, ?, ?, ?, ?, ?)
        on conflict(source, slug, job_id) do update set
            last_seen_at = excluded.last_seen_at,
            fetched_at   = excluded.fetched_at,
            payload      = excluded.payload
        """,
        rows,
    )
    return len(rows)


def record_fetch(conn: sqlite3.Connection, adapter: Adapter, slug: str,
                 fetched_at: str, result: BoardResult, job_count: int) -> None:
    """Append to fetch_log (history) and update the tenants row (current state)."""
    conn.execute(
        "insert into fetch_log (source, slug, fetched_at, http_status, job_count, error, elapsed_ms) "
        "values (?, ?, ?, ?, ?, ?, ?)",
        (adapter.name, slug, fetched_at, result.status, job_count, result.error, result.elapsed_ms),
    )
    if result.ok:
        status = "ok"
    elif result.status is not None:
        status = f"http_{result.status}"
    else:
        status = "error"
    conn.execute(
        "update tenants set last_attempted = ?, last_status = ?, job_count = ? "
        "where source = ? and slug = ?",
        (fetched_at, status, job_count if result.ok else None, adapter.name, slug),
    )


# --------------------------------------------------------------------------- #
# Run loop
# --------------------------------------------------------------------------- #

@dataclass
class RunTotals:
    boards: int = 0
    ok: int = 0
    skipped: int = 0
    failed: int = 0
    jobs: int = 0

    def add(self, other: RunTotals) -> None:
        for field in ("boards", "ok", "skipped", "failed", "jobs"):
            setattr(self, field, getattr(self, field) + getattr(other, field))


def select_slugs(conn: sqlite3.Connection, adapter: Adapter, args: argparse.Namespace) -> list[str]:
    if args.slug:
        return [args.slug]
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=RECENT_HOURS)).isoformat(timespec="seconds")
    params = {
        "source": adapter.name,
        "test_set": 1 if args.test_set else 0,
        "force": 1 if args.force else 0,
        "cutoff": cutoff,
    }
    slugs = [row[0] for row in conn.execute(TENANT_QUERY, params)]
    return slugs[: args.limit] if args.limit else slugs


def run_source(conn: sqlite3.Connection, session: requests.Session,
               adapter: Adapter, args: argparse.Namespace) -> RunTotals:
    totals = RunTotals()
    slugs = select_slugs(conn, adapter, args)
    if not slugs:
        log.info("%s: nothing to fetch (all recently fetched? try --force)", adapter.name)
        return totals
    log.info("%s: fetching %d board(s)", adapter.name, len(slugs))

    for i, slug in enumerate(slugs, 1):
        fetched_at = now_iso()
        result = fetch_board(session, adapter, slug)
        job_count = 0
        tag = f"[{adapter.name} {i}/{len(slugs)}] {slug:<30}"

        if result.ok:
            job_count = upsert_jobs(conn, adapter, slug, result.jobs, fetched_at)
            totals.ok += 1
            totals.jobs += job_count
            log.info("%s %4d jobs  %5dms", tag, job_count, result.elapsed_ms)
        elif result.skipped:
            totals.skipped += 1
            log.info("%s skip (%s)", tag, result.error)
        else:
            totals.failed += 1
            log.error("%s FAILED (%s)", tag, result.error)

        record_fetch(conn, adapter, slug, fetched_at, result, job_count)
        conn.commit()            # per board: a crash loses at most one
        totals.boards += 1

        if i < len(slugs):
            time.sleep(args.delay)

    log.info("%s: %d boards, %d ok, %d skipped, %d failed, %d jobs upserted",
             adapter.name, totals.boards, totals.ok, totals.skipped, totals.failed, totals.jobs)
    return totals


def run(args: argparse.Namespace) -> int:
    adapters = list(ADAPTERS.values()) if args.source == "all" else [ADAPTERS[args.source]]
    if args.slug and len(adapters) != 1:
        log.error("--slug requires a single --source")
        return 2

    conn = sqlite3.connect(args.db)
    conn.executescript(SCHEMA)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    grand = RunTotals()
    for adapter in adapters:
        grand.add(run_source(conn, session, adapter, args))

    if len(adapters) > 1:
        log.info("all sources: %d boards, %d ok, %d skipped, %d failed, %d jobs upserted",
                 grand.boards, grand.ok, grand.skipped, grand.failed, grand.jobs)
    conn.close()
    return 0 if grand.failed == 0 else 1


def main() -> int:
    p = argparse.ArgumentParser(description="Fetch ATS boards into raw_jobs.")
    p.add_argument("--db", required=True, help="path to the SQLite database")
    p.add_argument("--source", choices=[*ADAPTERS, "all"], default="all",
                   help="which platform(s) to fetch (default: all)")
    p.add_argument("--test-set", action="store_true", help="only slugs with tenants.test_set = 1")
    p.add_argument("--limit", type=int, default=0, help="stop after N slugs per source")
    p.add_argument("--slug", help="fetch one board, bypassing the tenants table (needs a single --source)")
    p.add_argument("--force", action="store_true", help="re-fetch even if recently fetched")
    p.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="seconds between boards")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
