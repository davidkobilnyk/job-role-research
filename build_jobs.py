#!/usr/bin/env python3
"""
Build the analysis tables from a filtered raw_jobs table.

    python build_jobs.py --db jobs.sqlite [--table raw_jobs_filtered7]

Writes:
    jobs                one row per job with every tag (the analysis surface)
    job_sections        (job, section_index, section_type, heading, text)
    job_skills          (job, term, category, section_type)
    job_salary_ranges   (job, idx, min, max, currency, interval, source_field)

Requires tech_terms.py and jobtext.py alongside, plus title_norm and job_remote_us
in the database (built earlier). Everything is derived from the raw payload, so the
script is safe to re-run after changing a rule.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone

import jobtext
import tech_terms

# =========================================================================== #
# Schema
# =========================================================================== #

SCHEMA = """
drop table if exists jobs;
create table jobs (
    source            text not null,
    slug              text not null,
    job_id            text not null,
    company           text,
    title_raw         text,
    title_clean       text,
    levels            text,
    seniority         text not null,      -- intern | junior | mid | senior | staff_plus | management | unknown
    seniority_conf    text not null,      -- high | medium | low
    seniority_src     text,
    exclude_senior    integer not null,   -- 1 = confident senior+ (per the project rule)
    role_family       text not null,
    role_family_conf  text not null,
    employment_type   text not null,      -- full_time | part_time | contract | intern | temp | unknown
    is_ai_training_gig integer not null,
    is_freelance      integer not null,
    is_evergreen      integer not null,
    is_real_job       integer not null,   -- not gig, not freelance, not evergreen, not intern
    posted_at         text,               -- UTC ISO
    days_open         integer,
    first_seen_at     text,
    last_seen_at      text,
    remote_tier       integer,
    remote_signal     text,
    workplace_type    text,
    travel            text not null,      -- none | offsites | real | unknown
    state_list        text,               -- comma-separated state codes if a list was found
    timezone_req      text,
    sponsorship       text,               -- none_stated | no_sponsorship | sponsorship_ok
    clearance         integer not null,
    yoe_min           integer,
    yoe_max           integer,
    yoe_preferred     integer,
    salary_min        integer,
    salary_max        integer,
    salary_currency   text,
    salary_interval   text,               -- year | hour
    salary_source     text,               -- structured | text
    salary_n_ranges   integer not null,
    n_sections        integer not null,
    has_req_section   integer not null,
    desc_len          integer not null,
    dedupe_key        text not null,
    is_primary        integer not null,   -- 1 = the representative row for its dedupe group
    n_duplicates      integer not null,   -- rows in the group (1 = unique)
    primary key (source, slug, job_id)
);
create index idx_jobs_family on jobs(role_family, seniority);
create index idx_jobs_dedupe on jobs(dedupe_key);

drop table if exists job_sections;
create table job_sections (
    source text not null, slug text not null, job_id text not null,
    section_index integer not null, section_type text not null, heading text, text text,
    primary key (source, slug, job_id, section_index)
);

drop table if exists job_skills;
create table job_skills (
    source text not null, slug text not null, job_id text not null,
    term text not null, category text not null, section_type text not null,
    primary key (source, slug, job_id, term, section_type)
);
create index idx_job_skills_term on job_skills(term, section_type);

drop table if exists job_salary_ranges;
create table job_salary_ranges (
    source text not null, slug text not null, job_id text not null, idx integer not null,
    min integer, max integer, currency text, interval text, source_field text,
    primary key (source, slug, job_id, idx)
);
"""

# =========================================================================== #
# 1. Seniority
# =========================================================================== #

MGMT_RX = re.compile(r"\b(manager|director|vp|vice president|head of|chief|cto|cio|ciso|engineering lead|team lead)\b")
SENIOR_LEVELS = {"senior", "lead", "3", "4"}
STAFF_LEVELS = {"staff", "principal", "distinguished", "fellow", "5"}
JUNIOR_LEVELS = {"junior", "entry", "1"}
MID_LEVELS = {"mid", "2"}

# "3-5 years of professional experience", "5+ years' DevOps experience", "8 yrs exp".
# Up to four words may sit between "years (of)" and "experience"; "age" is excluded so
# "18 years of age ... experience" doesn't match.
YOE_RX = re.compile(
    r"(?<![\d.])(\d{1,2})\s*(?:\+|plus)?\s*(?:(?:-|–|to)\s*(\d{1,2})\s*\+?)?\s*(?:years?|yrs?)(?:['’]s?)?\s+"
    r"(?:of\s+)?(?!age\b)(?:(?!age\b)[\w+#./&-]+\s+){0,4}?(?:experience|exp\b)", re.I)


def extract_yoe(text: str) -> list[tuple[int, int | None]]:
    out = []
    for m in YOE_RX.finditer(text):
        lo = int(m.group(1))
        hi = int(m.group(2)) if m.group(2) else None
        if 0 <= lo <= 25:
            out.append((lo, hi))
    return out


def classify_seniority(title_clean: str, levels: str, yoe_req: list, yoe_pref: list) -> tuple[str, str, str]:
    lv = set(levels.split("+")) if levels else set()
    if "intern" in lv:
        return "intern", "high", "title"
    if MGMT_RX.search(title_clean):
        return "management", "high", "title"
    if lv & STAFF_LEVELS:
        return "staff_plus", "high", "title"
    if lv & SENIOR_LEVELS:
        return "senior", "high", "title"
    if lv & JUNIOR_LEVELS:
        return "junior", "high", "title"
    if lv & MID_LEVELS:
        return "mid", "high", "title"
    if re.search(r"\b(architect)\b", title_clean):
        return "senior", "medium", "title:architect"
    yoe = yoe_req or yoe_pref
    if yoe:
        lo = min(y[0] for y in yoe)
        src = "yoe:req" if yoe_req else "yoe:pref"
        if lo >= 8:
            return "senior", "medium", src
        if lo >= 6:
            return "senior", "low", src
        if lo >= 3:
            return "mid", "medium", src
        return "junior", "medium", src
    return "unknown", "low", "none"


# =========================================================================== #
# 2. Exclusions / employment type
# =========================================================================== #

EVERGREEN_RX = re.compile(
    r"talent (community|network|pool|pipeline)|general (application|interest)|future (opportunit|opening|role)|"
    r"open application|evergreen|speculative|join our (talent|team!?$)|don't see (a|the) (role|fit)|"
    r"expression of interest|pipeline req|prospective", re.I)
INTERN_RX = re.compile(r"\b(intern|internship|co-op|coop|apprentice)\b", re.I)
CONTRACT_RX = re.compile(r"\b(contract|contractor|freelance|1099|c2c|corp[- ]to[- ]corp|temporary|temp\b|fixed[- ]term)", re.I)
PART_TIME_RX = re.compile(r"\bpart[- ]time\b", re.I)


def employment_type(source: str, payload: dict, title: str, text_head: str) -> str:
    v = ""
    if source == "ashby":
        v = (payload.get("employmentType") or "").lower()
    elif source == "lever":
        v = ((payload.get("categories") or {}).get("commitment") or "").lower()
    elif source == "greenhouse":
        for m in payload.get("metadata") or []:
            if "employment" in (m.get("name") or "").lower() or "job type" in (m.get("name") or "").lower():
                v = str(m.get("value") or "").lower()
    probe = f"{v} {title}"
    if INTERN_RX.search(probe):
        return "intern"
    if PART_TIME_RX.search(probe):
        return "part_time"
    if CONTRACT_RX.search(probe) or "temporary" in v:
        return "contract" if "temp" not in v else "temp"
    if "full" in v or "fulltime" in v.replace("-", "").replace(" ", ""):
        return "full_time"
    if v:
        return "unknown"
    if PART_TIME_RX.search(text_head) or CONTRACT_RX.search(text_head):
        return "contract" if not PART_TIME_RX.search(text_head) else "part_time"
    return "unknown"


# =========================================================================== #
# 4. Dates
# =========================================================================== #

def posted_at(source: str, payload: dict) -> datetime | None:
    try:
        if source == "greenhouse":
            v = payload.get("first_published") or payload.get("updated_at")
            return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(timezone.utc) if v else None
        if source == "ashby":
            v = payload.get("publishedAt")
            return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(timezone.utc) if v else None
        if source == "lever":
            v = payload.get("createdAt")
            return datetime.fromtimestamp(v / 1000, tz=timezone.utc) if v else None
    except (ValueError, TypeError, AttributeError):
        return None
    return None


# =========================================================================== #
# 5. Role family
# =========================================================================== #

FAMILY_RULES = [
    ("management", re.compile(r"\b(engineering manager|manager|director|head of|vp|vice president|chief)\b")),
    ("ml_ai", re.compile(r"\b(machine learning|ml|ai|artificial intelligence|llm|nlp|computer vision|deep learning|"
                         r"applied scientist|research (engineer|scientist)|mlops|genai|generative)\b")),
    ("data_engineering", re.compile(r"\b(data engineer|data platform|data infrastructure|etl|pipeline|analytics engineer|"
                                    r"data architect|database|dba|data warehouse)\b")),
    ("analytics", re.compile(r"\b(data analyst|analytics|business intelligence|bi\b|data scientist|data science|"
                             r"reporting|insights|statistic)")),
    ("devops_sre", re.compile(r"\b(devops|sre|site reliability|platform engineer|infrastructure|cloud engineer|"
                              r"systems engineer|release engineer|build engineer|observability|kubernetes)\b")),
    ("security", re.compile(r"\b(security|appsec|infosec|cybersecurity|penetration|threat|iam)\b")),
    ("qa", re.compile(r"\b(qa|quality assurance|sdet|test engineer|test automation|quality engineer|tester)\b")),
    ("mobile", re.compile(r"\b(ios|android|mobile|react native|flutter|swift|kotlin)\b")),
    ("frontend", re.compile(r"\b(front[- ]?end|frontend|ui engineer|web developer|react|angular|vue|javascript|"
                            r"typescript|ux engineer|design engineer)\b")),
    ("fullstack", re.compile(r"\b(full[- ]?stack|fullstack)\b")),
    ("backend", re.compile(r"\b(back[- ]?end|backend|api|server|distributed systems|java|python|golang|\.net|c#|"
                           r"ruby|rails|node|php|scala|rust|c\+\+)\b")),
    ("solutions_support", re.compile(r"\b(solutions? (engineer|architect)|sales engineer|forward deployed|customer engineer|"
                                     r"support engineer|implementation|technical account|pre[- ]?sales|integration engineer)\b")),
    ("embedded", re.compile(r"\b(embedded|firmware|fpga|rtos|hardware)\b")),
]
GENERIC_SWE_RX = re.compile(r"\b(software engineer|software developer|swe|sde|engineer|developer|programmer|"
                            r"member of technical staff|founding engineer|technologist)\b")


def classify_family(title_clean: str, cat_counts: Counter) -> tuple[str, str]:
    for fam, rx in FAMILY_RULES:
        if rx.search(title_clean):
            return fam, "high"
    if GENERIC_SWE_RX.search(title_clean):
        # tiebreak generic "software engineer" by what the description talks about
        dm, wf, inf = cat_counts.get("data_ml", 0), cat_counts.get("web_framework", 0), cat_counts.get("cloud_infra", 0)
        if dm >= 3 and dm > wf:
            return "backend_data_leaning", "medium"
        if wf >= 2 and wf > dm:
            return "fullstack", "medium"
        return "backend", "low"
    return "other", "low"


# =========================================================================== #
# 7. Salary
# =========================================================================== #

SALARY_RX = re.compile(
    r"(?:\$|usd\s?)\s?(\d{2,3}(?:,\d{3})+|\d{2,3}(?:\.\d)?k)\s*(?:-|–|—|to|and)\s*(?:\$|usd\s?)?\s?"
    r"(\d{2,3}(?:,\d{3})+|\d{2,3}(?:\.\d)?k)", re.I)
HOURLY_HINT = re.compile(r"per hour|/\s?hour|/\s?hr\b|hourly|an hour", re.I)


def _num(s: str) -> int:
    s = s.lower().replace(",", "")
    return int(float(s[:-1]) * 1000) if s.endswith("k") else int(s)


def salary_ranges(source: str, payload: dict, text: str) -> list[tuple]:
    out: list[tuple] = []
    if source == "lever" and isinstance(payload.get("salaryRange"), dict):
        sr = payload["salaryRange"]
        if sr.get("min") or sr.get("max"):
            interval = "hour" if "hour" in str(sr.get("interval", "")).lower() else "year"
            out.append((sr.get("min"), sr.get("max"), sr.get("currency") or "USD", interval, "lever.salaryRange"))
    if source == "ashby":
        comp = payload.get("compensation") or {}
        for c in comp.get("summaryComponents") or []:
            if (c.get("compensationType") or "").lower() == "salary" and (c.get("minValue") or c.get("maxValue")):
                interval = "hour" if "hour" in str(c.get("interval", "")).lower() else "year"
                out.append((c.get("minValue"), c.get("maxValue"), c.get("currencyCode") or "USD", interval, "ashby.compensation"))
    for m in SALARY_RX.finditer(text):
        lo, hi = _num(m.group(1)), _num(m.group(2))
        if lo > hi:
            lo, hi = hi, lo
        window = text[max(0, m.start() - 60): m.end() + 60]
        interval = "hour" if HOURLY_HINT.search(window) else "year"
        if interval == "year" and not (15_000 <= lo <= 1_000_000):
            continue
        if interval == "hour" and not (5 <= lo <= 500):
            continue
        out.append((lo, hi, "USD", interval, "text"))
    return out


# =========================================================================== #
# 8. Fit tags
# =========================================================================== #

TRAVEL_NONE = re.compile(r"no travel|travel is not required|not required to travel|no (regular|significant) travel|minimal travel|little to no travel", re.I)
TRAVEL_REAL = re.compile(r"(\d{1,3}\s?%|percent)[^.\n]{0,40}travel|travel[^.\n]{0,40}(\d{1,3}\s?%|percent)|"
                         r"(frequent|regular|extensive|significant|heavy|substantial|weekly|monthly)[^.\n]{0,20}travel|"
                         r"(willing|ability|able|required|expected|must) to travel|travel (is )?required|"
                         r"travel to (customer|client|site)", re.I)
TRAVEL_OFFSITE = re.compile(r"(occasional|periodic|infrequent|some|limited|light)[^.\n]{0,20}travel|"
                            r"travel[^.\n]{0,60}(offsite|off-site|team (event|gathering|meetup|summit)|"
                            r"once or twice|twice a year|a few times|quarterly|annual|per year|a year|onsite)|"
                            r"(offsite|off-site|team (gathering|meetup|summit))s?[^.\n]{0,60}travel", re.I)
TRAVEL_PRODUCT = re.compile(r"travel (software|industry|platform|company|booking|tech|agency|management|& expense|and expense)|"
                            r"business travel (platform|software|company)", re.I)
TRAVEL_WORD = re.compile(r"\btravel", re.I)


def travel_tag(text: str) -> str:
    if not TRAVEL_WORD.search(text):
        return "none"
    if TRAVEL_NONE.search(text):
        return "none"
    if TRAVEL_REAL.search(text):
        return "real"
    if TRAVEL_OFFSITE.search(text):
        return "offsites"
    stripped = TRAVEL_PRODUCT.sub("", text)
    return "unknown" if TRAVEL_WORD.search(stripped) else "none"


US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}
STATE_NAME_RX = re.compile(r"\b(" + "|".join(re.escape(s) for s in sorted(US_STATES, key=len, reverse=True)) + r")\b", re.I)
STATE_CODE_RX = re.compile(r"\b(" + "|".join(US_STATES.values()) + r")\b")
STATE_LIST_ANCHOR = re.compile(r"(following states|these states|eligible states|approved states|reside in|located in|"
                               r"based in|hire in|hiring in|open to candidates in|residents of)", re.I)


def state_list(text: str) -> str | None:
    found: list[str] = []
    for m in STATE_LIST_ANCHOR.finditer(text):
        window = text[m.end(): m.end() + 600]
        codes = [US_STATES[s.lower()] for s in STATE_NAME_RX.findall(window)]
        codes += STATE_CODE_RX.findall(window)
        if len(set(codes)) >= 3:
            found = sorted(set(codes))
            break
    if not found:
        names = STATE_NAME_RX.findall(text)
        if len(set(n.lower() for n in names)) >= 8:
            found = sorted({US_STATES[n.lower()] for n in names})
    return ",".join(found) if found else None


TZ_NAME_RX = re.compile(r"\b((eastern|central|mountain|pacific)\s+(time|standard time|tz|time zone)|us time ?zones?)\b", re.I)
TZ_ABBR_RX = re.compile(r"\b(EST|EDT|CST|CDT|MST|MDT|PST|PDT|CET|CEST|GMT|BST|IST|AEST)\b")   # case-sensitive on purpose


def timezone_tag(text: str) -> str | None:
    m = TZ_NAME_RX.search(text) or TZ_ABBR_RX.search(text)
    return m.group(0) if m else None
NO_SPONSOR_RX = re.compile(r"(not|unable|cannot|can't|won't|will not|do not|does not|don't)\s+(to\s+|be able to\s+)?"
                           r"(offer|provide|sponsor|support)[^.\n]{0,30}(sponsor|visa)|without (visa )?sponsorship|"
                           r"no (visa )?sponsorship|sponsorship (is )?not available|not (currently )?sponsor", re.I)
SPONSOR_OK_RX = re.compile(r"(will|can|able to|happy to|open to)\s+sponsor|sponsorship (is )?available|visa sponsorship", re.I)
CLEARANCE_RX = re.compile(r"security clearance|ts/sci|top secret|secret clearance|public trust|clearance (is )?required", re.I)


def sponsorship_tag(text: str) -> str:
    if NO_SPONSOR_RX.search(text):
        return "no_sponsorship"
    if SPONSOR_OK_RX.search(text):
        return "sponsorship_ok"
    return "none_stated"


# =========================================================================== #
# 6. Skills matcher (same approach as description_terms.py)
# =========================================================================== #

def build_matcher():
    lookup = {}
    for form, canonical, category in tech_terms.surface_forms():
        lookup.setdefault(form, (canonical, category))
    forms = sorted(lookup, key=len, reverse=True)
    rx = re.compile(r"(?<![\w+#.])(" + "|".join(re.escape(f) for f in forms) + r")(?![\w+#])", re.I)
    return rx, lookup


# =========================================================================== #
# 3. Dedupe key
# =========================================================================== #

NON_ALNUM = re.compile(r"[^a-z0-9]+")


def company_key(source: str, slug: str, payload: dict) -> str:
    name = payload.get("company_name") if source == "greenhouse" else None
    base = (name or slug or "").lower()
    base = re.sub(r"\b(inc|llc|ltd|corp|corporation|co|company|holdings|technologies|technology|labs|group)\b", "", base)
    return NON_ALNUM.sub("", base)


def dedupe_key(company: str, title_clean: str, text: str) -> str:
    head = NON_ALNUM.sub("", text[:300].lower())
    return f"{company}|{title_clean}|{hashlib.sha1(head.encode()).hexdigest()[:10]}"


# =========================================================================== #
# Main
# =========================================================================== #

def run(db: str, table: str) -> None:
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    rx, lookup = build_matcher()
    now = datetime.now(timezone.utc)

    titles = {(s, sl, j): (raw, clean, lv) for s, sl, j, raw, clean, lv in
              conn.execute("select source, slug, job_id, raw_title, clean_title, levels from title_norm")}
    remote = {(s, sl, j): (t, sig) for s, sl, j, t, sig in
              conn.execute("select source, slug, job_id, tier, signal from job_remote_us")}
    cat_counts: dict[tuple, Counter] = defaultdict(Counter)
    for s, sl, j, cat, n in conn.execute("select source, slug, job_id, category, count(*) from job_terms group by 1,2,3,4"):
        cat_counts[(s, sl, j)][cat] = n
    gig_signals: dict[tuple, set] = defaultdict(set)
    for s, sl, j, t in conn.execute("select source, slug, job_id, term from job_terms where category = 'gig_signal'"):
        gig_signals[(s, sl, j)].add(t)

    job_rows, section_rows, skill_rows, salary_rows = [], [], [], []
    groups: dict[str, list[tuple]] = defaultdict(list)
    n = 0

    for source, slug, job_id, first_seen, last_seen, payload_s in conn.execute(
            f"select source, slug, job_id, first_seen_at, last_seen_at, payload from {table}"):
        n += 1
        payload = json.loads(payload_s)
        key = (source, slug, job_id)
        title_raw, title_clean, levels = titles.get(key, (payload.get("title") or payload.get("text") or "", "", ""))
        title_clean = title_clean or (title_raw or "").lower()

        lines = jobtext.description_lines(source, payload)
        sections = jobtext.split_sections(lines)
        text = "\n".join(t for t, _ in lines)
        text_l = text.lower()

        # sections + skills per section
        by_type: dict[str, list[str]] = defaultdict(list)
        for sec in sections:
            section_rows.append((source, slug, job_id, sec.index, sec.section_type, sec.heading, sec.text))
            by_type[sec.section_type].append(sec.text)
        if not sections:
            by_type["unsectioned"].append(text)
        seen_skill = set()
        for stype, texts in by_type.items():
            for m in rx.finditer("\n".join(texts)):
                canonical, category = lookup[m.group(1).lower()]
                if (canonical, stype) not in seen_skill:
                    seen_skill.add((canonical, stype))
                    skill_rows.append((source, slug, job_id, canonical, category, stype))

        # YOE from requirements first, preferred second, whole text as fallback
        yoe_req = extract_yoe("\n".join(by_type.get("requirements", []) + by_type.get("unsectioned", [])))
        yoe_pref = extract_yoe("\n".join(by_type.get("preferred", [])))
        if not yoe_req and not yoe_pref:
            yoe_req = extract_yoe(text)
        yoe_min = min((y[0] for y in yoe_req), default=None)
        yoe_max = max((y[1] or y[0] for y in yoe_req), default=None)
        yoe_pref_min = min((y[0] for y in yoe_pref), default=None)

        seniority, sconf, ssrc = classify_seniority(title_clean, levels, yoe_req, yoe_pref)
        exclude_senior = 1 if seniority in ("senior", "staff_plus", "management") and sconf == "high" else 0

        emp = employment_type(source, payload, title_raw or "", text_l[:1500])
        gig = cat_counts[key]
        gig_terms = gig_signals.get(key, set())
        is_gig = 1 if "AI training gig" in gig_terms else 0
        is_freelance = 1 if ("Freelance / hourly" in gig_terms or emp in ("contract", "part_time", "temp")) else 0
        posted = posted_at(source, payload)
        days_open = (now - posted).days if posted else None
        is_evergreen = 1 if (EVERGREEN_RX.search(title_raw or "") or "Not a real opening" in gig_terms
                             or (days_open is not None and days_open > 365)) else 0
        is_real = 1 if not (is_gig or is_freelance or is_evergreen or emp == "intern") else 0

        family, fconf = classify_family(title_clean, gig)

        ranges = salary_ranges(source, payload, text)
        for i, r in enumerate(ranges):
            salary_rows.append((source, slug, job_id, i, *r))
        primary = next((r for r in ranges if r[3] == "year"), ranges[0] if ranges else None)

        tier, signal = remote.get(key, (None, None))
        wt = (payload.get("workplaceType") or "").lower() or None

        company = company_key(source, slug, payload)
        dk = dedupe_key(company, title_clean, text)
        groups[dk].append((posted.isoformat() if posted else "9999", key))

        job_rows.append(dict(
            source=source, slug=slug, job_id=job_id, company=payload.get("company_name") or slug,
            title_raw=title_raw, title_clean=title_clean, levels=levels,
            seniority=seniority, seniority_conf=sconf, seniority_src=ssrc, exclude_senior=exclude_senior,
            role_family=family, role_family_conf=fconf, employment_type=emp,
            is_ai_training_gig=is_gig, is_freelance=is_freelance, is_evergreen=is_evergreen, is_real_job=is_real,
            posted_at=posted.isoformat(timespec="seconds") if posted else None, days_open=days_open,
            first_seen_at=first_seen, last_seen_at=last_seen,
            remote_tier=tier, remote_signal=signal, workplace_type=wt,
            travel=travel_tag(text), state_list=state_list(text),
            timezone_req=timezone_tag(text),
            sponsorship=sponsorship_tag(text), clearance=1 if CLEARANCE_RX.search(text) else 0,
            yoe_min=yoe_min, yoe_max=yoe_max, yoe_preferred=yoe_pref_min,
            salary_min=primary[0] if primary else None, salary_max=primary[1] if primary else None,
            salary_currency=primary[2] if primary else None, salary_interval=primary[3] if primary else None,
            salary_source=("structured" if primary and primary[4] != "text" else "text") if primary else None,
            salary_n_ranges=len(ranges),
            n_sections=len(sections), has_req_section=1 if "requirements" in by_type else 0,
            desc_len=len(text), dedupe_key=dk, is_primary=0, n_duplicates=1,
        ))
        if n % 5000 == 0:
            print(f"  {n:,} jobs processed")

    # 3. dedupe: earliest posted row in each group is primary
    primary_of = {}
    for dk, members in groups.items():
        members.sort()
        primary_of[dk] = (members[0][1], len(members))
    for row in job_rows:
        pk, size = primary_of[row["dedupe_key"]]
        row["is_primary"] = 1 if (row["source"], row["slug"], row["job_id"]) == pk else 0
        row["n_duplicates"] = size

    cols = list(job_rows[0].keys()) if job_rows else []
    conn.executemany(f"insert into jobs ({','.join(cols)}) values ({','.join('?' * len(cols))})",
                     [tuple(r[c] for c in cols) for r in job_rows])
    conn.executemany("insert or ignore into job_sections values (?,?,?,?,?,?,?)", section_rows)
    conn.executemany("insert or ignore into job_skills values (?,?,?,?,?,?)", skill_rows)
    conn.executemany("insert or ignore into job_salary_ranges values (?,?,?,?,?,?,?,?,?)", salary_rows)
    conn.commit()

    # summary
    print(f"\n{n:,} jobs -> jobs table; {len(section_rows):,} sections; {len(skill_rows):,} skill rows; "
          f"{len(salary_rows):,} salary ranges")
    for label, q in [
        ("seniority", "select seniority, seniority_conf, count(*) from jobs group by 1,2 order by 3 desc"),
        ("role_family", "select role_family, count(*) from jobs group by 1 order by 2 desc"),
        ("real jobs", "select is_real_job, count(*) from jobs group by 1"),
        ("dedupe", "select sum(is_primary) as primaries, count(*) as rows_ from jobs"),
        ("has requirements section", "select has_req_section, count(*) from jobs group by 1"),
        ("salary present", "select salary_source, count(*) from jobs group by 1"),
        ("travel", "select travel, count(*) from jobs group by 1"),
        ("yoe stated", "select yoe_min is not null, count(*) from jobs group by 1"),
    ]:
        print(f"\n{label}:")
        for r in conn.execute(q):
            print("  ", r)
    conn.close()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    p.add_argument("--table", default="raw_jobs_filtered7")
    args = p.parse_args()
    run(args.db, args.table)


if __name__ == "__main__":
    main()
