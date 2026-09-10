#!/usr/bin/env python3
"""Extract structured data from posts.jsonl using Anthropic SDK directly."""
import json, os, sys
from anthropic import Anthropic

REMOTE_ONLY = "--remote-only" in sys.argv
WORKERS = 4
MODEL = "claude-haiku-4-5-20251001"

PROMPT = """You extract structured data from a job post (provided on stdin).
Return ONLY a JSON object, no prose, no markdown fences. Keys (null when unknown):
company: string
company_summary: one sentence, what the company does
roles: array, one per distinct role, each with:
  title: string
  title_family: one of ["ai_app_eng","evals","data_eng","analytics_eng","product_eng","backend","frontend","mobile","fullstack","infra_devops","security","ml_eng","embedded","solutions_fde","other"]
  level: one of ["junior","mid","senior","staff_plus","unspecified"]
  remote: one of ["remote_global","remote_us","remote_region","hybrid","onsite","unspecified"]
  onsite_cadence: string or null
  required_skills: array of short strings
  nice_to_have: array of short strings
  languages: array of strings
  mentions: object of booleans {llm, rag_retrieval, evals, analytics_dashboards, data_pipelines, iot_sensors, agents}
  comp: string or null (as stated)
  interview_hints: string or null
company_size_guess: one of ["1-10","11-50","51-200","201-1000","1000+","unknown"]
industry: short string
one_line: one sentence, what these jobs actually are
"""

def extract(rec, client):
    try:
        msg = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": PROMPT + "\n\n" + rec["text"]}]
        )
        text = msg.content[0].text
        # Clean markdown fence if present
        raw = text.strip().removeprefix("```json").removesuffix("```").strip()
        data = json.loads(raw)
    except Exception as e:
        data = {"_error": str(e)}
    data.update(id=rec["id"], month=rec["month"], url=rec["url"])
    return data

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

done = set()
if os.path.exists("extracted.jsonl"):
    done = {json.loads(l)["id"] for l in open("extracted.jsonl")}

posts = [json.loads(l) for l in open("posts.jsonl")]
todo = [p for p in posts if p["id"] not in done and (p["remote_kw"] or not REMOTE_ONLY)]
print(f"{len(todo)} to extract ({len(done)} already done)", file=sys.stderr)

with open("extracted.jsonl", "a") as out:
    for i, rec in enumerate(todo, 1):
        data = extract(rec, client)
        out.write(json.dumps(data) + "\n"); out.flush()
        if i % 25 == 0:
            print(f"{i}/{len(todo)}", file=sys.stderr)
print("done -> extracted.jsonl", file=sys.stderr)
