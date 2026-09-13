#!/usr/bin/env python3
"""
Score agent outputs against evals.json.

Usage:
    python check_flags.py evals.json results.json [results2.json ...]

Each results file is a list of {"id": <eval id>, "output": <the agent's JSON output as an object or string>}.
Pass several results files (repeated runs) to see how stable each flag is.
Codes in expected_flags must appear; codes in optional_flags may appear; anything else is a false positive.
Every quote must appear verbatim in the job text (insensitive to whitespace, curly quotes, dash style, and letter case).
An output that isn't a valid {"flags": [...]} object counts every expected code for that eval as missed.
"""

import json
import re
import sys
from collections import Counter, defaultdict

INSTRUCTION = "Assess this job description for fit and return the flags."
TYPOGRAPHY = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", " ": " ",
})


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.translate(TYPOGRAPHY)).strip().casefold()


def job_text(prompt: str) -> str:
    """The prompt minus the instruction line, so a quote can't match the instruction."""
    return prompt[len(INSTRUCTION):] if prompt.startswith(INSTRUCTION) else prompt


def parse_output(out):
    """Return (flags, error)."""
    if isinstance(out, str):
        try:
            out = json.loads(out)
        except json.JSONDecodeError:
            return None, "output is not valid JSON"
    if not isinstance(out, dict) or set(out) != {"flags"} or not isinstance(out["flags"], list):
        return None, "output shape is not {'flags': [...]}"
    if not all(isinstance(f, dict) for f in out["flags"]):
        return None, "a flag entry is not an object"
    return out["flags"], None


def main(evals_path: str, results_paths: list[str]) -> int:
    evals = {e["id"]: e for e in json.load(open(evals_path))["evals"]}
    tp = fp = fn = bad_quotes = bad_shape = 0
    problems = []
    per_code = defaultdict(Counter)              # code -> {hit, missed, unexpected}
    freq = defaultdict(Counter)                  # eval id -> code -> runs flagged
    runs_per_eval = Counter()

    for path in results_paths:
        results = json.load(open(path))
        tag = f"{path}: " if len(results_paths) > 1 else ""
        seen = set()
        for r in results:
            e = evals.get(r.get("id"))
            if e is None:
                problems.append(f"{tag}result has unknown eval id {r.get('id')!r}")
                continue
            seen.add(e["id"])
            runs_per_eval[e["id"]] += 1
            expected = set(e["expected_flags"])
            optional = set(e.get("optional_flags", []))

            flags, err = parse_output(r.get("output"))
            if err:
                bad_shape += 1
                fn += len(expected)
                for c in expected:
                    per_code[c]["missed"] += 1
                problems.append(f"{tag}[{e['name']}] {err}")
                continue

            got = Counter(f.get("code") for f in flags)
            dupes = [c for c, n in got.items() if n > 1]
            if dupes:
                problems.append(f"{tag}[{e['name']}] duplicate codes: {dupes}")
            got_set = set(got)
            for c in got_set:
                freq[e["id"]][c] += 1

            missing = expected - got_set
            extra = got_set - expected - optional
            hit = got_set & expected
            tp += len(hit)
            fn += len(missing)
            fp += len(extra)
            for c in hit:
                per_code[c]["hit"] += 1
            for c in missing:
                per_code[c]["missed"] += 1
            for c in extra:
                per_code[c]["unexpected"] += 1
            if missing:
                problems.append(f"{tag}[{e['name']}] missing: {sorted(missing)}")
            if extra:
                problems.append(f"{tag}[{e['name']}] unexpected: {sorted(extra)}")

            text = norm(job_text(e["prompt"]))
            for f in flags:
                q = norm(str(f.get("quote") or ""))
                if not q or q not in text:
                    bad_quotes += 1
                    problems.append(f"{tag}[{e['name']}] {f.get('code')}: quote not verbatim: {q[:60]!r}")

        for eid in sorted(set(evals) - seen):
            problems.append(f"{tag}no result for eval {eid} [{evals[eid]['name']}]")

    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    print(f"results files: {len(results_paths)}  |  expected codes hit: {tp}  missed: {fn}  unexpected: {fp}")
    print(f"precision {prec:.2f}  recall {rec:.2f}  |  bad quotes: {bad_quotes}  bad shape: {bad_shape}")

    if per_code:
        print("\nper code (hit / missed / unexpected):")
        for c in sorted(per_code):
            k = per_code[c]
            print(f"  {c:<5} {k['hit']:>3} / {k['missed']:>3} / {k['unexpected']:>3}")

    if len(results_paths) > 1:
        print("\nflag frequency per eval (runs flagged / runs):")
        for eid in sorted(freq):
            e = evals[eid]
            n = runs_per_eval[eid]
            parts = []
            for c in sorted(freq[eid], key=lambda c: (-freq[eid][c], c)):
                kind = "exp" if c in e["expected_flags"] else "opt" if c in e.get("optional_flags", []) else "UNEXPECTED"
                parts.append(f"{c} {freq[eid][c]}/{n} {kind}")
            print(f"  [{e['name']}] " + ", ".join(parts))

    if problems:
        print("\nproblems:")
        for p in problems:
            print("  -", p)
    return 0 if not problems else 1


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2:]))
