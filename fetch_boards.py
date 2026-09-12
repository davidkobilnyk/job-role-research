#!/usr/bin/env python3
"""
ATS job board fetcher (Greenhouse, Ashby, Lever).

Reads slugs from the `tenants` table, fetches each board's open jobs with
full descriptions, and upserts the raw job objects into `raw_jobs`.
Nothing is normalized here; the payload is stored exactly as returned.

Usage:
    python fetch_greenhouse.py --db data.db --test-set          # the hand-picked 30
    python fetch_greenhouse.py --db data.db --source ashby      # Ashby boards (tenants.source = 'ashby')
    python fetch_greenhouse.py --db data.db --source lever      # Lever boards
    python fetch_greenhouse.py --db data.db --limit 100         # first 100 pending slugs
    python fetch_greenhouse.py --db data.db                     # everything pending
    python fetch_greenhouse.py --db data.db --slug stripe       # one board
    python fetch_greenhouse.py --db data.db --force             # ignore "recently fetched" skip

Assumed `tenants` columns (adjust TENANT_QUERY below if yours differ):
    slug TEXT, source TEXT, test_set INTEGER, last_attempted TEXT,
    last_status TEXT, job_count INTEGER
"""

import argparse
import json
import logging
import random
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

BOARD_URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
    "ashby":      "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true",
    "lever":      "https://api.lever.co/v0/postings/{slug}?mode=json",
}
SOURCE = "greenhouse"     # overridden by --source
USER_AGENT = "job-market-research/0.1 (personal, low-volume, contact via GitHub)"

# HTTP status handling
RETRY_STATUSES = {429, 500, 502, 503, 504}     # transient: back off and retry
SKIP_STATUSES = {400, 401, 403, 404}            # permanent for this slug: log and move on

MAX_RETRIES = 5
BASE_BACKOFF = 2.0        # seconds; doubles each retry, with jitter
REQUEST_TIMEOUT = 30      # seconds
DEFAULT_DELAY = 0.5       # polite pause between boards
RECENT_HOURS = 20         # skip slugs fetched successfully within this window unless --force

log = logging.getLogger("greenhouse")


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

# Which tenants to fetch. Edit here if your column names differ.
TENANT_QUERY = """
select slug from tenants
where source = :source
  and (:force = 1
       or last_status is null
       or last_status != 'ok'
       or last_attempted is null
       or last_attempted < :cutoff)
order by slug
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

class BoardResult:
    """Outcome of fetching one board."""

    def __init__(self, status: int | None, jobs: list | None, error: str | None, elapsed_ms: int):
        self.status = status
        self.jobs = jobs
        self.error = error
        self.elapsed_ms = elapsed_ms

    @property
    def ok(self) -> bool:
        return self.jobs is not None


def fetch_board(session: requests.Session, slug: str) -> BoardResult:
    """
    Fetch one Greenhouse board. Retries transient failures with exponential
    backoff; returns immediately on permanent failures (404 etc.).
    """
    url = BOARD_URLS[SOURCE].format(slug=slug)
    started = time.monotonic()

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            # Network-level failure: treat like a transient server error.
            if attempt == MAX_RETRIES:
                return BoardResult(None, None, f"request failed: {exc}", _ms(started))
            _sleep_backoff(attempt, None, f"{slug}: {exc}")
            continue

        status = resp.status_code

        if status == 200:
            try:
                data = resp.json()
            except ValueError as exc:
                return BoardResult(status, None, f"invalid JSON: {exc}", _ms(started))
            # Greenhouse and Ashby wrap the list in {"jobs": [...]}; Lever returns a bare array.
            jobs = data if SOURCE == "lever" else (data.get("jobs") if isinstance(data, dict) else None)
            if not isinstance(jobs, list):
                return BoardResult(status, None, "response missing job list", _ms(started))
            return BoardResult(status, jobs, None, _ms(started))

        if status in SKIP_STATUSES:
            return BoardResult(status, None, f"http {status}", _ms(started))

        if status in RETRY_STATUSES:
            if attempt == MAX_RETRIES:
                return BoardResult(status, None, f"http {status} after {attempt} attempts", _ms(started))
            _sleep_backoff(attempt, resp.headers.get("Retry-After"), f"{slug}: http {status}")
            continue

        # Anything else: unexpected, don't retry.
        return BoardResult(status, None, f"unexpected http {status}", _ms(started))

    return BoardResult(None, None, "exhausted retries", _ms(started))


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _sleep_backoff(attempt: int, retry_after: str | None, reason: str) -> None:
    """Honor Retry-After if present, otherwise exponential backoff with jitter."""
    wait = None
    if retry_after:
        try:
            wait = float(retry_after)
        except ValueError:
            wait = None
    if wait is None:
        wait = BASE_BACKOFF * (2 ** (attempt - 1)) + random.uniform(0, 1)
    log.warning("%s — retrying in %.1fs (attempt %d/%d)", reason, wait, attempt, MAX_RETRIES)
    time.sleep(wait)


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #

def upsert_jobs(conn: sqlite3.Connection, slug: str, jobs: list, fetched_at: str) -> int:
    """
    Insert new jobs, refresh existing ones. first_seen_at is preserved across
    runs; last_seen_at and payload are updated every time the job is seen.
    Jobs that disappear from the board are NOT deleted; their last_seen_at
    simply stops advancing, which is how "closed" gets inferred later.
    """
    rows = []
    for job in jobs:
        job_id = job.get("id")
        if job_id is None:
            log.warning("%s: job without id, skipping: %s", slug, job.get("title") or job.get("text"))
            continue
        rows.append((
            SOURCE, slug, str(job_id),
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


def record_fetch(conn: sqlite3.Connection, slug: str, fetched_at: str, result: BoardResult, job_count: int) -> None:
    conn.execute(
        "insert into fetch_log (source, slug, fetched_at, http_status, job_count, error, elapsed_ms) "
        "values (?, ?, ?, ?, ?, ?, ?)",
        (SOURCE, slug, fetched_at, result.status, job_count, result.error, result.elapsed_ms),
    )
    status = "ok" if result.ok else (f"http_{result.status}" if result.status else "error")
    conn.execute(
        "update tenants set last_attempted = ?, last_status = ?, job_count = ? "
        "where source = ? and slug = ?",
        (fetched_at, status, job_count if result.ok else None, SOURCE, slug),
    )


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #

def select_slugs(conn: sqlite3.Connection, args: argparse.Namespace) -> list[str]:
    if args.slug:
        return [args.slug]
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=RECENT_HOURS)).isoformat(timespec="seconds")
    params = {
        "source": SOURCE,
        "test_set": 1 if args.test_set else 0,
        "force": 1 if args.force else 0,
        "cutoff": cutoff,
    }
    slugs = [r[0] for r in conn.execute(TENANT_QUERY, params)]
    if args.limit:
        slugs = slugs[: args.limit]
    return slugs


def run(args: argparse.Namespace) -> int:
    conn = sqlite3.connect(args.db)
    conn.executescript(SCHEMA)

    slugs = select_slugs(conn, args)
    if not slugs:
        log.info("nothing to fetch (all slugs recently fetched? try --force)")
        return 0
    log.info("fetching %d board(s)", len(slugs))

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    session.headers["Accept"] = "application/json"

    totals = {"boards": 0, "ok": 0, "skipped": 0, "failed": 0, "jobs": 0}

    for i, slug in enumerate(slugs, 1):
        fetched_at = now_iso()
        result = fetch_board(session, slug)
        job_count = 0

        if result.ok:
            job_count = upsert_jobs(conn, slug, result.jobs, fetched_at)
            totals["ok"] += 1
            totals["jobs"] += job_count
            log.info("[%d/%d] %-30s %4d jobs  %5dms", i, len(slugs), slug, job_count, result.elapsed_ms)
        elif result.status in SKIP_STATUSES:
            totals["skipped"] += 1
            log.info("[%d/%d] %-30s skip (%s)", i, len(slugs), slug, result.error)
        else:
            totals["failed"] += 1
            log.error("[%d/%d] %-30s FAILED (%s)", i, len(slugs), slug, result.error)

        record_fetch(conn, slug, fetched_at, result, job_count)
        conn.commit()          # commit per board so a crash loses at most one
        totals["boards"] += 1

        if i < len(slugs):
            time.sleep(args.delay)

    log.info(
        "done: %d boards, %d ok, %d skipped, %d failed, %d jobs upserted",
        totals["boards"], totals["ok"], totals["skipped"], totals["failed"], totals["jobs"],
    )
    conn.close()
    return 0 if totals["failed"] == 0 else 1


def main() -> int:
    p = argparse.ArgumentParser(description="Fetch Greenhouse / Ashby / Lever boards into raw_jobs.")
    p.add_argument("--db", required=True, help="path to the SQLite database")
    p.add_argument("--source", default="greenhouse", choices=sorted(BOARD_URLS),
                   help="which ATS to fetch (tenants.source must match)")
    p.add_argument("--test-set", action="store_true", help="only slugs with tenants.test_set = 1")
    p.add_argument("--limit", type=int, default=0, help="stop after N slugs")
    p.add_argument("--slug", help="fetch a single board, bypassing the tenants table")
    p.add_argument("--force", action="store_true", help="re-fetch even if recently fetched")
    p.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="seconds between boards")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    global SOURCE
    SOURCE = args.source

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
