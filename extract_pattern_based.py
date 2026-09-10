#!/usr/bin/env python3
"""Extract job data using pattern matching (no API needed)."""
import json, re, sys

ROLE_FAMILIES = {
    r"(ai|machine learning|llm|neural|deep learning)": "ai_app_eng",
    r"eval": "evals",
    r"data engineer": "data_eng",
    r"analytics engineer": "analytics_eng",
    r"product": "product_eng",
    r"backend": "backend",
    r"frontend|react|vue|angular": "frontend",
    r"mobile|ios|android": "mobile",
    r"fullstack|full.?stack": "fullstack",
    r"(devops|infrastructure|sre)": "infra_devops",
    r"security": "security",
    r"ml engineer": "ml_eng",
    r"embedded": "embedded",
    r"(solutions|field).*engineer": "solutions_fde",
}

SKILL_PATTERNS = {
    "python": r"\bpython\b",
    "javascript": r"\b(js|javascript)\b",
    "typescript": r"\btypescript\b",
    "go": r"\bgo\b",
    "rust": r"\brust\b",
    "java": r"\bjava\b",
    "react": r"\breact\b",
    "aws": r"\baws\b",
    "gcp": r"\b(gcp|google cloud)\b",
    "kubernetes": r"\bk8s|kubernetes\b",
    "docker": r"\bdocker\b",
    "sql": r"\bsql\b",
    "postgres": r"\bpostgres\b",
}

def extract(rec):
    text = rec["text"].lower()

    # Extract company (often at start)
    lines = rec["text"].split("\n")
    company = None
    for line in lines[:3]:
        if len(line) > 3 and len(line) < 80 and not any(c in line for c in ["(", ")", ";"]):
            company = line.strip()
            break

    # Detect role families
    roles = []
    seen_families = set()
    for pattern, family in ROLE_FAMILIES.items():
        if re.search(pattern, text, re.I) and family not in seen_families:
            roles.append({
                "title": pattern.replace(r"\b", "").split("|")[0],
                "title_family": family,
                "level": "unspecified",
                "remote": "remote_global" if "remote" in text else "onsite",
                "required_skills": [s for s, p in SKILL_PATTERNS.items() if re.search(p, text)],
                "nice_to_have": [],
                "languages": extract_languages(text),
                "mentions": {
                    "llm": bool(re.search(r"(llm|language model)", text)),
                    "rag_retrieval": bool(re.search(r"rag|retrieval", text)),
                    "evals": bool(re.search(r"eval", text)),
                    "analytics_dashboards": bool(re.search(r"analytics|dashboard|metrics", text)),
                    "data_pipelines": bool(re.search(r"pipeline|etl|data flow", text)),
                    "iot_sensors": bool(re.search(r"iot|sensor|edge", text)),
                    "agents": bool(re.search(r"agent|autonomous", text)),
                },
                "comp": extract_comp(rec["text"]),
                "interview_hints": None,
            })
            seen_families.add(family)

    if not roles:
        roles = [{"title": "Unknown", "title_family": "other", "level": "unspecified",
                  "remote": "remote_global" if "remote" in text else "onsite",
                  "required_skills": [], "nice_to_have": [], "languages": [],
                  "mentions": {k: False for k in ["llm", "rag_retrieval", "evals", "analytics_dashboards",
                                                   "data_pipelines", "iot_sensors", "agents"]},
                  "comp": None, "interview_hints": None}]

    return {
        "company": company,
        "company_summary": None,
        "roles": roles,
        "company_size_guess": "unknown",
        "industry": extract_industry(text),
        "one_line": f"Hiring for {', '.join(r['title_family'] for r in roles[:2])}",
        "text": rec["text"],
    }

def extract_languages(text):
    langs = []
    for lang in ["python", "javascript", "typescript", "go", "rust", "java", "c++", "c#", "ruby", "php"]:
        if re.search(rf"\b{lang}\b", text, re.I):
            langs.append(lang)
    return langs[:3]

def extract_comp(text):
    match = re.search(r"\$[\d,]+k?(?:\s*[-–]\s*\$[\d,]+k?)?|\$\d+(?:,\d{3})*(?:\.\d{2})?", text)
    return match.group(0) if match else None

def extract_industry(text):
    industries = {
        r"(ai|machine learning|ml|nlp)": "AI/ML",
        r"fintech|finance|banking": "FinTech",
        r"healthcare|medical|pharma": "Healthcare",
        r"e-commerce|retail": "E-commerce",
        r"saas": "SaaS",
    }
    for pattern, industry in industries.items():
        if re.search(pattern, text, re.I):
            return industry
    return "Tech"

done = set()
if sys.argv[1:2] == ["--remote-only"]:
    posts = [json.loads(l) for l in open("posts.jsonl") if json.loads(l).get("remote_kw")]
else:
    posts = [json.loads(l) for l in open("posts.jsonl")]

results = []
for i, rec in enumerate(posts, 1):
    data = extract(rec)
    results.append(data)
    if i % 100 == 0:
        print(f"Extracted {i}/{len(posts)}", file=sys.stderr)

print(json.dumps(results, indent=2))
