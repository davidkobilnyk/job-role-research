"""Extract structured fields from posts.jsonl -> extracted.jsonl using Claude Code headless mode.
Usage: python extract_cc.py [--remote-only]
Needs: Claude Code installed and logged in (`claude` on PATH). No API key.
Resumable: skips ids already present in extracted.jsonl.
"""
import json, os, subprocess, sys
from concurrent.futures import ThreadPoolExecutor

REMOTE_ONLY = "--remote-only" in sys.argv
WORKERS = 4          # parallel claude processes; lower if you hit rate limits
MODEL = "haiku"      # alias; use "sonnet" if quality is lacking

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
  remote: one of ["remote_global","remote_us","remote_region","hybrid","onsite","unspecified"]
  onsite_cadence: string or null
  required_skills: array of short strings
  nice_to_have: array of short strings
  languages: array of strings
  mentions: object of booleans {llm, rag_retrieval, evals, analytics_dashboards,
      data_pipelines, iot_sensors, agents}
  comp: string or null (as stated)
  interview_hints: string or null
company_size_guess: one of ["1-10","11-50","51-200","201-1000","1000+","unknown"]
industry: short string
one_line: one sentence, what these jobs actually are
"""

NO_TOOLS = "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,Task"

def extract(rec):
    proc = subprocess.run(
        ["claude", "-p", PROMPT, "--output-format", "json",
         "--model", MODEL, "--disallowedTools", NO_TOOLS],
        input=rec["text"], capture_output=True, text=True, timeout=180,
    )
    try:
        result = json.loads(proc.stdout)["result"]
        raw = result.strip().removeprefix("```json").removesuffix("```").strip()
        data = json.loads(raw)
    except Exception as e:
        data = {"_error": str(e), "_stdout": proc.stdout[:500], "_stderr": proc.stderr[:300]}
    data.update(id=rec["id"], month=rec["month"], url=rec["url"])
    return data

done = set()
if os.path.exists("extracted.jsonl"):
    done = {json.loads(l)["id"] for l in open("extracted.jsonl")}

posts = [json.loads(l) for l in open("posts.jsonl")]
todo = [p for p in posts if p["id"] not in done and (p["remote_kw"] or not REMOTE_ONLY)]
print(f"{len(todo)} to extract ({len(done)} already done)")

with open("extracted.jsonl", "a") as out, ThreadPoolExecutor(max_workers=WORKERS) as ex:
    for i, data in enumerate(ex.map(extract, todo), 1):
        out.write(json.dumps(data) + "\n"); out.flush()
        if i % 25 == 0:
            print(f"{i}/{len(todo)}")
print("done -> extracted.jsonl")
