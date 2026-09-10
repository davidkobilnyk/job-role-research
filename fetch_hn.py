"""Fetch top-level posts from HN "Who is hiring" threads -> posts.jsonl
Usage: python fetch_hn.py            (needs: pip install requests)
"""
import html, json, re, requests

THREADS = {  # month: Algolia story id
    "2026-09": 49522897,
    "2026-08": 49156683,
    "2026-07": 48747976,
    "2026-06": 48357725,
}

def clean(raw):
    t = html.unescape(raw or "")
    t = re.sub(r"<p>", "\n", t)
    t = re.sub(r"<br\s*/?>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()

out = open("posts.jsonl", "w")
n = 0
for month, sid in THREADS.items():
    data = requests.get(f"https://hn.algolia.com/api/v1/items/{sid}", timeout=60).json()
    for c in data["children"]:
        text = clean(c.get("text"))
        if len(text) < 40:
            continue
        rec = {
            "source": "hn",
            "id": c["id"],
            "month": month,
            "url": f"https://news.ycombinator.com/item?id={c['id']}",
            "created_at": c["created_at"],
            "remote_kw": bool(re.search(r"\bremote\b", text, re.I)),
            "text": text,
        }
        out.write(json.dumps(rec) + "\n")
        n += 1
    print(f"{month}: {len(data['children'])} top-level, kept so far {n}")
out.close()
print(f"wrote {n} posts to posts.jsonl")
