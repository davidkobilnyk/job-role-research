#!/usr/bin/env python3
"""Aggregate extracted.jsonl into a clean JSON array, filtering errors."""
import json, sys

results = []
errors = 0
with open("extracted.jsonl") as f:
    for line in f:
        rec = json.loads(line)
        if "_error" in rec:
            errors += 1
        else:
            # Extract just the structured data, drop metadata
            data = {k: v for k, v in rec.items() if k not in ["id", "month", "url", "_error", "_stdout", "_stderr"]}
            if data.get("company"):  # Only include if company was extracted
                results.append(data)

print(f"Extracted {len(results)} valid records ({errors} errors)", file=sys.stderr)
print(json.dumps(results, indent=2))
