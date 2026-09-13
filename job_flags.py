"""
Shared pieces for running the ai-job-fit-flags skill through `claude -p` (uses your Claude Code login).

  load_skill()                 -> (system_prompt, skill_hash, codes)
  build_prompt(title, text)    -> user message in the same shape as the evals
  flag_job(model, ...)         -> FlagResult
  bad_quote_count(flags, text) -> quotes not found verbatim in the job text
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from check_flags import INSTRUCTION, norm

SKILL_PATH = Path(__file__).with_name("ai-job-fit-flags-SKILL.md")
DEFAULT_MODEL = "claude-sonnet-5"
TIMEOUT_S = 600


def load_skill(path: Path = SKILL_PATH) -> tuple[str, str, list[str]]:
    raw = path.read_text()
    skill_hash = hashlib.sha256(raw.encode()).hexdigest()[:12]
    body = re.sub(r"\A---\n.*?\n---\n", "", raw, flags=re.S).strip()
    codes = re.findall(r"^\| `([A-Z]+)` \|", body, flags=re.M)
    return body, skill_hash, codes


def output_schema(codes: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "flags": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "enum": codes},
                        "quote": {"type": "string"},
                    },
                    "required": ["code", "quote"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["flags"],
        "additionalProperties": False,
    }


def build_prompt(title: str, text: str) -> str:
    return f"{INSTRUCTION}\n\n{title.strip()}\n\n{text.strip()}"


@dataclass
class FlagResult:
    status: str                         # ok | cli_error | bad_output
    output_text: str | None = None
    flags: list[dict] | None = None
    error: str | None = None
    usage: dict = field(default_factory=dict)


def flag_job(model: str, system: str, codes: list[str], prompt: str) -> FlagResult:
    cmd = [
        "claude", "-p",
        "--model", model,
        "--output-format", "json",
        "--no-session-persistence",
        "--tools", "",                  # no tools: read the text, answer
        "--strict-mcp-config",          # no MCP servers
        "--system-prompt", system,
        "--json-schema", json.dumps(output_schema(codes)),
    ]
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return FlagResult("cli_error", error=f"timed out after {TIMEOUT_S}s")
    except FileNotFoundError:
        return FlagResult("cli_error", error="`claude` not found on PATH")

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return FlagResult("cli_error", error=f"exit {proc.returncode}: {(proc.stderr or proc.stdout).strip()[:500]}")

    u = data.get("usage") or {}
    usage = {
        "input_tokens": u.get("input_tokens"),
        "output_tokens": u.get("output_tokens"),
        "cache_read_tokens": u.get("cache_read_input_tokens"),
    }
    if data.get("is_error"):
        return FlagResult("cli_error", error=str(data.get("result"))[:500], usage=usage)

    # With --json-schema the validated object should be in structured_output; fall back to the text result.
    if data.get("structured_output") is not None:
        text = json.dumps(data["structured_output"])
    else:
        text = (data.get("result") or "").strip()
        text = re.sub(r"\A```(?:json)?\s*|\s*```\Z", "", text)
    try:
        flags = json.loads(text)["flags"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        return FlagResult("bad_output", output_text=text, error=f"unparseable output: {e}", usage=usage)
    if not isinstance(flags, list) or not all(isinstance(f, dict) and f.get("code") in codes for f in flags):
        return FlagResult("bad_output", output_text=text, error="flags are not a list of known codes", usage=usage)

    # A code may appear at most once; keep the first quote and note what was dropped.
    seen, unique, dropped = set(), [], []
    for f in flags:
        if f["code"] in seen:
            dropped.append(f["code"])
        else:
            seen.add(f["code"])
            unique.append(f)
    note = f"removed duplicate codes: {', '.join(dropped)}" if dropped else None
    return FlagResult("ok", output_text=json.dumps({"flags": unique}), flags=unique, error=note, usage=usage)


def bad_quote_count(flags: list[dict], job_text: str) -> int:
    text = norm(job_text)
    return sum(1 for f in flags if not norm(f.get("quote") or "") or norm(f["quote"]) not in text)
