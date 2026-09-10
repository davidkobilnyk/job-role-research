"""Probe Greenhouse / Lever / Ashby for each company in companies.csv, pull all jobs
with full descriptions -> ats_jobs.jsonl. Prints which companies resolved and which didn't.
Usage: python fetch_ats.py            (needs: pip install requests)
"""
import csv, html, json, re, time, requests

UA = {"User-Agent": "Mozilla/5.0 (job-market-research script)"}
KEYWORDS = re.compile(
    r"engineer|developer|data|analytics|ai\b|ml\b|machine learning|product|solutions|platform",
    re.I)

def clean(raw):
    t = html.unescape(raw or "")
    t = re.sub(r"</?(p|div|li|br|h\d|ul|ol)[^>]*>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()

def greenhouse(slug):
    r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
                     params={"content": "true"}, headers=UA, timeout=30)
    if r.status_code != 200: return None
    jobs = r.json().get("jobs", [])
    return [{"ats": "greenhouse", "id": j["id"], "title": j["title"],
             "location": (j.get("location") or {}).get("name"),
             "url": j.get("absolute_url"), "updated": j.get("updated_at"),
             "text": clean(j.get("content"))} for j in jobs]

def lever(slug):
    r = requests.get(f"https://api.lever.co/v0/postings/{slug}",
                     params={"mode": "json"}, headers=UA, timeout=30)
    if r.status_code != 200: return None
    jobs = r.json()
    if not isinstance(jobs, list): return None
    return [{"ats": "lever", "id": j["id"], "title": j["text"],
             "location": (j.get("categories") or {}).get("location"),
             "url": j.get("hostedUrl"), "updated": j.get("createdAt"),
             "text": clean(j.get("descriptionPlain") or j.get("description"))
                     + "\n" + "\n".join(clean(l.get("text", "") + ": " + l.get("content", ""))
                                        for l in j.get("lists", []))}
            for j in jobs]

def ashby(slug):
    r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
                     params={"includeCompensation": "true"}, headers=UA, timeout=30)
    if r.status_code != 200: return None
    jobs = r.json().get("jobs")
    if jobs is None: return None
    return [{"ats": "ashby", "id": j["id"], "title": j["title"],
             "location": j.get("location"), "remote": j.get("isRemote"),
             "url": j.get("jobUrl"), "updated": j.get("publishedAt"),
             "comp": (j.get("compensation") or {}).get("compensationTierSummary"),
             "text": clean(j.get("descriptionHtml") or j.get("descriptionPlain"))}
            for j in jobs]

resolved, unresolved, n = [], [], 0
with open("ats_jobs.jsonl", "w") as out:
    for row in csv.DictReader(open("companies.csv")):
        found = None
        for slug in row["slugs"].split("|"):
            for fn in (greenhouse, lever, ashby):
                try:
                    jobs = fn(slug.strip())
                except Exception:
                    jobs = None
                if jobs is not None:
                    found = (fn.__name__, slug, jobs); break
            if found: break
            time.sleep(0.2)
        if not found:
            unresolved.append(row["company"]); print(f"  ?? {row['company']}"); continue
        ats, slug, jobs = found
        kept = 0
        for j in jobs:
            if not KEYWORDS.search(j["title"] or ""): continue
            j.update(company=row["company"], category=row["category"], source=f"{ats}:{slug}")
            out.write(json.dumps(j) + "\n"); kept += 1; n += 1
        resolved.append(row["company"])
        print(f"  ok {row['company']:<28} {ats:<10} {len(jobs):>3} jobs, {kept:>3} kept")

print(f"\n{len(resolved)} companies resolved, {n} jobs written to ats_jobs.jsonl")
print(f"{len(unresolved)} unresolved (find their careers page and add the right slug/ATS):")
print("  " + ", ".join(unresolved))
