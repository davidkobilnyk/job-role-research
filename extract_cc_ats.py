"""Extract structured fields from a JSONL of job posts using Claude Code headless mode.
Usage: python extract_cc.py posts.jsonl [--remote-only]      -> posts.extracted.jsonl
       python extract_cc.py ats_jobs.jsonl                    -> ats_jobs.extracted.jsonl
Run from a PLAIN terminal (not inside a Claude Code session), or rely on the env strip below.
Resumable: skips ids already in the output file.
"""
import json, os, subprocess, sys
from concurrent.futures import ThreadPoolExecutor

IN = sys.argv[1]
OUT = IN.replace(".jsonl", ".extracted.jsonl")
REMOTE_ONLY = "--remote-only" in sys.argv
WORKERS = 4
MODEL = "haiku"      # switch to "sonnet" if output is sloppy

PROMPT = """You extract structured data from a job post (provided on stdin).
Return ONLY a JSON object, no prose, no markdown fences. Keys (null when unknown):
company: string
company_summary: one sentence, what the company does
roles: array, one per distinct role, each with:
  title: string
  title_family: one of ["ai_app_eng","evals","data_eng","analytics_eng","product_eng",
      "backend","frontend","mobile","fullstack","infra_devops","security","ml_eng",
      "embedded","solutions_fde","other"]
  level: one of ["junior","mid","senior","staff_plus","unspecified"]
  years_required: integer or null (minimum years stated)
  remote: one of ["remote_global","remote_us","remote_region","hybrid","onsite","unspecified"]
  onsite_cadence: string or null
  required_skills: array of short strings
  nice_to_have: array of short strings
  languages: array of strings
  mentions: object of booleans {llm, rag_retrieval, evals, analytics_dashboards,
      data_pipelines, iot_sensors, agents, ai_tools_expected}
  comp: string or null (as stated)
  interview_hints: string or null
company_size_guess: one of ["1-10","11-50","51-200","201-1000","1000+","unknown"]
industry: short string
one_line: one sentence, what these jobs actually are
"""

NO_TOOLS = "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,Task"
ENV = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

def extract(rec):
    proc = subprocess.run(
        ["claude", "-p", PROMPT, "--output-format", "json",
         "--model", MODEL, "--disallowedTools", NO_TOOLS],
        input=rec["text"][:12000], capture_output=True, text=True, timeout=180, env=ENV,
    )
    try:
        result = json.loads(proc.stdout)["result"]
        raw = result.strip().removeprefix("```json").removesuffix("```").strip()
        data = json.loads(raw)
    except Exception as e:
        data = {"_error": str(e), "_stdout": proc.stdout[:500], "_stderr": proc.stderr[:300]}
    for k in ("url", "month", "company", "category", "title", "location", "source"):
        if k in rec and k not in data:
            data[k] = rec[k]
    data["id"] = rec["id"]
    return data

done = set()
if os.path.exists(OUT):
    done = {json.loads(l)["id"] for l in open(OUT)}

recs = [json.loads(l) for l in open(IN)]
todo = [r for r in recs if r["id"] not in done and (r.get("remote_kw", True) or not REMOTE_ONLY)]
print(f"{len(todo)} to extract ({len(done)} already done) -> {OUT}")

with open(OUT, "a") as out, ThreadPoolExecutor(max_workers=WORKERS) as ex:
    for i, data in enumerate(ex.map(extract, todo), 1):
        out.write(json.dumps(data) + "\n"); out.flush()
        if i % 25 == 0:
            print(f"{i}/{len(todo)}")
print("done")
