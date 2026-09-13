#!/usr/bin/env python3
"""
Run the ai-job-fit-flags skill over the eval set (via `claude -p`) and score it with check_flags.py.

Usage:
    python3 run_evals.py [--model claude-sonnet-5] [--runs 3] [--workers 5]

Writes one results file per run to eval_runs/<model>_<skill hash>_run<N>.json,
then prints the combined check_flags.py report.
"""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import check_flags
from job_flags import DEFAULT_MODEL, flag_job, load_skill

EVALS_PATH = Path(__file__).with_name("ai-job-fit-flags-evals.json")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--runs", type=int, default=1)
    p.add_argument("--workers", type=int, default=5)
    p.add_argument("--out-dir", default="eval_runs")
    args = p.parse_args()

    system, skill_hash, codes = load_skill()
    evals = json.load(open(EVALS_PATH))["evals"]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(exist_ok=True)

    paths = []
    for run in range(1, args.runs + 1):
        with ThreadPoolExecutor(args.workers) as pool:
            results = list(pool.map(lambda e: flag_job(args.model, system, codes, e["prompt"]), evals))
        rows = []
        for e, r in zip(evals, results):
            if r.status != "ok":
                print(f"run {run} [{e['name']}] {r.status}: {r.error}")
            rows.append({"id": e["id"], "output": r.output_text if r.output_text is not None else "", "usage": r.usage})
        path = out_dir / f"{args.model}_{skill_hash}_run{run}.json"
        path.write_text(json.dumps(rows, indent=2))
        paths.append(str(path))
        print(f"run {run}: wrote {path}")

    print()
    return check_flags.main(str(EVALS_PATH), paths)


if __name__ == "__main__":
    raise SystemExit(main())
