#!/usr/bin/env python3
"""
Run the ai-job-fit-flags skill (via `claude -p`) over the non-senior ml_ai jobs and store results in job_eval.

    python3 eval_jobs.py --dry-run          # count jobs, show one prompt, no model calls
    python3 eval_jobs.py --limit 20         # trial run
    python3 eval_jobs.py                    # everything not yet evaluated

Re-runnable: a job is skipped when it already has completed = 1 for the current skill file
(by hash) and model. Editing the skill file makes every job eligible again.

Job text is the stored payload from raw_jobs_filtered7 (application form questions aren't in it).
"""

import argparse
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import jobtext
from job_flags import DEFAULT_MODEL, bad_quote_count, build_prompt, flag_job, load_skill

SELECT_JOBS = """
select j.source, j.slug, j.job_id, j.url, j.title_raw, f.payload
from jobs j
join raw_jobs_filtered7 f on f.source = j.source and f.slug = j.slug and f.job_id = j.job_id
where j.role_family = 'ml_ai'
  and j.is_real_job = 1
  and j.is_primary = 1
  and j.seniority not in ('senior', 'staff_plus', 'management')
  and not exists (
      select 1 from job_eval e
      where e.source = j.source and e.slug = j.slug and e.job_id = j.job_id
        and e.completed = 1 and e.skill_hash = ? and e.model = ?)
order by j.source, j.slug, j.job_id
"""


def ensure_table(db: sqlite3.Connection, codes: list[str]) -> None:
    db.execute("""
        create table if not exists job_eval (
            source            text not null,
            slug              text not null,
            job_id            text not null,
            url               text,
            title             text,
            completed         integer not null,   -- 1 = model returned valid flags JSON for this job
            status            text not null,      -- ok | empty_text | cli_error | bad_output
            error             text,
            flags_json        text,               -- the model's {"flags": [...]} output
            n_flags           integer,
            bad_quotes        integer,            -- flags whose quote isn't verbatim in the job text
            skill_hash        text not null,      -- first 12 hex chars of sha256(ai-job-fit-flags-SKILL.md)
            model             text not null,
            input_chars       integer,
            input_tokens      integer,
            output_tokens     integer,
            cache_read_tokens integer,
            evaluated_at      text not null,      -- UTC ISO
            primary key (source, slug, job_id)
        )""")
    # One 0/1 column per flag code (null when not completed); new codes in the skill get added here.
    have = {r[1] for r in db.execute("pragma table_info(job_eval)")}
    for code in codes:
        if code not in have:
            db.execute(f'alter table job_eval add column "{code}" integer')
    db.commit()


def save(db, codes, job, skill_hash, model, text, result=None, status=None, error=None):
    source, slug, job_id, url, title, _ = job
    row = {
        "source": source, "slug": slug, "job_id": job_id, "url": url, "title": title,
        "completed": 0, "status": status or result.status, "error": error,
        "flags_json": None, "n_flags": None, "bad_quotes": None,
        "skill_hash": skill_hash, "model": model, "input_chars": len(text),
        "input_tokens": None, "output_tokens": None, "cache_read_tokens": None,
        "evaluated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    row.update({c: None for c in codes})
    if result is not None:
        row.update(result.usage)
        row["error"] = result.error
        row["flags_json"] = result.output_text
        if result.status == "ok":
            got = {f["code"] for f in result.flags}
            # Quotes may come from the title line too, so check against title + description (what the model saw).
            row.update(completed=1, n_flags=len(result.flags), bad_quotes=bad_quote_count(result.flags, f"{title or ''}\n{text}"))
            row.update({c: int(c in got) for c in codes})
    cols = ", ".join(f'"{k}"' for k in row)
    db.execute(f"insert or replace into job_eval ({cols}) values ({', '.join('?' * len(row))})", list(row.values()))
    db.commit()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data.db")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--limit", type=int)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    system, skill_hash, codes = load_skill()
    db = sqlite3.connect(args.db)
    ensure_table(db, codes)
    jobs = db.execute(SELECT_JOBS, (skill_hash, args.model)).fetchall()
    if args.limit:
        jobs = jobs[: args.limit]
    print(f"skill {skill_hash} ({len(codes)} codes), model {args.model}: {len(jobs)} jobs to evaluate")

    prompts = []
    for job in jobs:
        source, _, _, _, title, payload = job
        text = jobtext.full_text(source, json.loads(payload))
        prompts.append((job, text, build_prompt(title or "", text)))

    if args.dry_run:
        if prompts:
            print("\n--- first prompt ---\n" + prompts[0][2][:3000])
        return 0

    todo = []
    for job, text, prompt in prompts:
        if text.strip():
            todo.append((job, text, prompt))
        else:
            save(db, codes, job, skill_hash, args.model, text, status="empty_text", error="no description text in payload")

    statuses = {}
    with ThreadPoolExecutor(args.workers) as pool:
        futures = {pool.submit(flag_job, args.model, system, codes, prompt): (job, text)
                   for job, text, prompt in todo}
        for fut in as_completed(futures):
            job, text = futures[fut]
            result = fut.result()
            save(db, codes, job, skill_hash, args.model, text, result=result)
            statuses[result.status] = statuses.get(result.status, 0) + 1
            if result.status != "ok":
                print(f"  {job[0]}/{job[1]}/{job[2]}: {result.status}: {result.error}")
            done = sum(statuses.values())
            if done % 10 == 0 or done == len(todo):
                print(f"{done}/{len(todo)} {statuses}")

    print("done:", statuses)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
