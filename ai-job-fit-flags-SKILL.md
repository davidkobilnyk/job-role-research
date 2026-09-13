---
name: ai-job-fit-flags
description: Screen an AI Engineer job description against a fixed candidate profile and return every alignment problem it matches, as JSON flag codes with a verbatim quote for each. Use this whenever the user gives a job description, job posting, or role text and asks to assess, screen, check, flag, evaluate, or review it for fit, alignment, red flags, or dealbreakers — even if they just paste a posting and say "check this one" or "what's wrong with this job." Also use when processing a batch of postings one at a time. This skill does not rank, score, recommend, or consider salary; it only reports matched flags.
---

# AI Job Fit Flags

Read one job description and report which alignment problems it matches, from the fixed list of codes below. Each flag comes with one short verbatim quote from the description so the human reviewer can verify it in seconds. The human reviews everything afterward, so bias strongly toward flagging when the text points at a rule; never flag based on silence. A missed flag costs more than an extra one: when you're unsure whether a rule applies, flag it.

Output is JSON only, so results can be loaded into a database without parsing prose.

The job title is the first line of the job text, followed by the description.

## The candidate

Evaluate every description against this profile. It does not change per request.

- 9 years of software engineering across data analysis, data engineering, iOS, and frontend web. Stack: Python, SQL, Databricks, data pipelines, JavaScript, Objective-C, Swift.
- A 4-year gap from the industry. Returning at **mid level** in spring 2027.
- **By application time** they will have: a production-style LLM application with a real data layer, an evals suite, deployment, and monitoring, built and documented; and a job-market data pipeline (ingestion, classification, analysis) as a second portfolio piece.
- No PhD or master's degree. Works in English only.
- Constraints: fully remote from **North Carolina**; no travel; not a customer-facing role; not a senior-titled role.
- Open to any industry or product domain. Unfamiliar domains are never a problem unless the description requires prior experience in them.

How to read requirements against this profile:

- "Familiarity with," "exposure to," "experience with" LLM frameworks, RAG, agents, prompt engineering, vector databases, evals → **met by the portfolio**. Do not flag.
- LLM or agent experience with **years attached** ("1+ years with LLMs") or the word **production** ("production LLM systems," "deployed agents in production") → not met. Flag `LLM`.
- Years specifically in ML, AI, or data science ("3+ years of ML engineering") → not met. Flag `MLY`. LLM or agent experience with a stated number of years ("1+ years building LLM agents") is also years in AI: flag `MLY` as well as `LLM`. General software engineering years are fine up to 7.
- Python, SQL, Databricks, data pipelines, general backend work → met.
- "Experience with," "hands-on experience with," "working knowledge of," "familiarity with" any tool or area (Docker, DevOps, Kubernetes, security, cloud, model training) with no years attached → met. A **stated number of years in a specific specialty or technology** outside the profile ("2+ years of GPU optimization," "5+ years of security engineering," "3+ years with cloud technologies such as AWS, SageMaker, or Kubernetes") → not met. Flag `SPC`.
- `LLM`, `MLY`, `DEG`, `LANG`, and the years part of `SR` come from what the candidate must bring (requirements, qualifications), not from what the job will do. A responsibility like "ensure ML models in production meet accuracy targets" is not a requirement.
- `LLM` needs LLMs or agents specifically. "Production machine learning systems" with no mention of LLMs or agents is not `LLM`.

## The flag codes

Flag a code when the description matches it. Quote the sentence or phrase that triggered it. If nothing in the text matches, don't flag; silence about travel, remote scope, or team structure is not a flag.

### Role type

| Code | Flag when the description says… |
|---|---|
| `CF` | The role is customer-facing at its core (implementation, solutions engineering, "technical face of," client onboarding, managing customer stakeholders), **or** it requires a customer-facing background (solutions architect, implementation engineer, TAM, consulting history). Building a customer-facing product (a chatbot, a support agent, customer-facing agents) is not `CF` by itself; the engineer has to work with customers or sit in a customer-facing org. |
| `CON` | It's a consulting or strategy role; production code is not expected ("you don't need to write production code," "present to C-suite," "advisor," "strategist"). |
| `IT` | It's internal IT or enterprise tooling work: low-code/no-code platforms, workflow automation tools, IT systems, enterprise productivity enablement. |
| `ORG` | The engineering role reports into a non-engineering org: Customer Success, Enterprise Tools, Sales, Marketing, Revenue Operations, Services. |

### Requirements

| Code | Flag when the description requires… |
|---|---|
| `LLM` | Production LLM/agent experience, or LLM/agent experience with a stated number of years. |
| `MLY` | A stated number of years specifically in AI, ML, data science, or ML engineering, including years with LLMs or agents. |
| `SPC` | Specialist depth outside the profile, in either form: (1) a stated number of years in a specific specialty or technology outside the profile ("2+ years of GPU optimization," "5+ years of security engineering"); or (2) the role's core work is a specialist area: GPU/kernel optimization, computer vision, sensor fusion, distributed ML training infrastructure, robotics, model training at scale, research-grade ML. "Experience with," "hands-on experience with," "working knowledge of," or "familiarity with" a tool or area (Docker, DevOps, security engineering, model training) with no years attached is not `SPC`. Years in cloud infrastructure or ML platforms (AWS, SageMaker, Bedrock, Kubernetes) are outside the profile and count. |
| `SR` | The title is Senior (including Sr or Sr.), Staff, Principal, Lead, Distinguished, Architect, Head of, Director, or Manager; or the title carries a senior level designation (Engineer III or higher, IV, V, Level 5+/L5+); or the description states the role is senior level; or 8+ years of experience are required. Title words count only in the job title line; a role name in the body ("As an Agent Architect at…") is not `SR` unless the description also states the role is senior. |
| `SRX` | The role carries senior expectations: mentoring others, owning a roadmap, setting standards, being "the go-to expert," advising leadership. |
| `DEG` | A PhD or master's degree is required, or is the stated minimum. Alternatives that accept a bachelor's ("BS, MS, or PhD," "or equivalent experience") do not count, and neither does an advanced degree listed only as preferred or a plus. |
| `LANG` | Proficiency in a spoken language other than English is required: bilingual, fluent, or native-level Spanish, German, Japanese, and so on. Programming languages don't count, and neither does a language listed only as preferred or a plus. |

### Environment

| Code | Flag when the description signals… |
|---|---|
| `SOLO` | A first or only hire in the function: "this function doesn't exist yet," "you'll be first," "define the role," "build the team from scratch." |
| `AMB` | Ambiguity as a feature: "thrives in ambiguity," "build the playbook as you go," "blank canvas," "wear many hats," "figure it out," "no playbook." |
| `AGG` | Aggressive delivery expectations during ramp-up: ship to production in 30/60/90 days, "impact from day one," "hit the ground running." |
| `LAB` | A research-lab environment: "frontier scale," "world-class scientists," PhD-typical work, papers, "talent-dense," "state of the art" research. |
| `IND` | Working independently from day one: "self-directed," "comfortable working independently," "minimal supervision," "autonomy from day one." |
| `CUL` | High-pressure culture language: "owners not passengers," "never settle," "no excuses," "not for everyone," "relentless," "demanding," "constantly raising the bar," "hunger." |

### Logistics

| Code | Flag when the description says… |
|---|---|
| `TRV` | Real travel is required: customer or client sites, industry events, a travel percentage, "willing to travel," "travel is required." Annual offsites, team gatherings, or "occasional travel to meet the team" do **not** count. |
| `NRM` | The role is not clearly fully remote: hybrid, "remote-friendly," on-site perks (catered lunch, gym, office), "near one of our offices," travel to hub-city offices or working groups with no fully-remote statement, "remote with in-office days." Not `NRM`: *optional* office access on a remote-first or fully remote role, onsite or in-person interviews, or "hybrid" describing a mix of skills ("a hybrid role requiring a blend of engineering and consulting"). |
| `GEO` | A location restriction that excludes North Carolina: a list of eligible US states without NC, "must be in [specific metro/state]," US-timezone restrictions to Pacific-only, non-US location, Canada-only. Restrictions that still include NC (e.g., "anywhere in the US except SF/NYC/DC metros," "US Eastern time") do not count. |

## Output

Return only this JSON, nothing before or after it:

```json
{"flags": [
  {"code": "CF",  "quote": "the technical face of the services team"},
  {"code": "AMB", "quote": "build the playbook as you go"}
]}
```

- One object per flagged code. A code appears at most once; pick the single clearest quote.
- Flag every code that applies. One sentence can support several codes, and the same quote may be used for more than one code.
- Quotes are verbatim from the description, trimmed to the shortest span that shows the match (a phrase or one sentence, under ~25 words). No paraphrasing, no ellipses inside the quote.
- If nothing matches: `{"flags": []}`.
- No commentary, no severity, no score, no recommendation. The reviewer decides what to do with the flags.

## Worked examples

Short excerpts, each followed by the expected output.

**Example 1 — a consulting role.** Excerpts: "We're an AI services firm that helps companies figure out where and how to use AI." / "You'll partner with executives and their teams… present recommendations to C-suite stakeholders." / "4–7 years of experience; strong preference for top-tier strategy consulting." / "you don't need to write production code." / "Self-directed, thrives in ambiguity, and excited by the pace of an early-stage, fast-growing firm." / "Fully remote, US/Canada based, with occasional client travel."

```json
{"flags": [
  {"code": "CF",  "quote": "You'll partner with executives and their teams"},
  {"code": "CON", "quote": "you don't need to write production code"},
  {"code": "SRX", "quote": "present recommendations to C-suite stakeholders"},
  {"code": "AMB", "quote": "thrives in ambiguity"},
  {"code": "IND", "quote": "Self-directed"},
  {"code": "TRV", "quote": "occasional client travel"}
]}
```

`CF` is flagged for the client-facing work with executives. `CON` is flagged too, because the role is also a consulting role that doesn't expect production code; both codes apply. `SRX` is flagged because presenting recommendations to the C-suite is advising leadership. `TRV` is flagged because it's client travel, not a team offsite. Not `GEO`: US/Canada includes North Carolina.

**Example 2 — a near-fit engineering role with a few real problems.** Excerpts: "As an embedded AI Engineer in Customer Success, you'll design, build, test, deploy, and continuously maintain agentic solutions." / "This is a remote position open to candidates residing in the US except the San Francisco Bay Metro Area, NYC Metro Area, and Washington, D.C. Metro Area." / "Hands-on experience building and maintaining production LLM-based agents or automations." / "3+ years of software engineering or technical experience, with at least 1 year working hands-on with LLMs or AI agents in a production environment." / "In your first 90 days: Ship the first production agentic solution." / "You'll be first: This function doesn't exist yet. You'll define the role, the standard, and the playbook." / "Be the go-to expert at Samsara for customer-facing agentic AI development — advising Customer Success leadership." / "We want owners, not passengers."

```json
{"flags": [
  {"code": "CF",   "quote": "embedded AI Engineer in Customer Success"},
  {"code": "ORG",  "quote": "embedded AI Engineer in Customer Success"},
  {"code": "LLM",  "quote": "Hands-on experience building and maintaining production LLM-based agents or automations"},
  {"code": "MLY",  "quote": "at least 1 year working hands-on with LLMs or AI agents"},
  {"code": "SRX",  "quote": "Be the go-to expert at Samsara for customer-facing agentic AI development"},
  {"code": "SOLO", "quote": "This function doesn't exist yet"},
  {"code": "AMB",  "quote": "You'll define the role, the standard, and the playbook"},
  {"code": "AGG",  "quote": "In your first 90 days: Ship the first production agentic solution"},
  {"code": "CUL",  "quote": "We want owners, not passengers"}
]}
```

`CF` is flagged because the engineer sits inside Customer Success and advises its leadership, which points at customer-facing work even though the description never says "customer-facing role." The customer-facing agents alone would not be `CF`; the Customer Success placement is what triggers it. The same quote supports `CF` and `ORG`. `MLY` is flagged alongside `LLM` because "at least 1 year" is a stated number of years in AI. `SOLO` and `AMB` are both flagged: the function doesn't exist yet, and the playbook has to be defined. Not flagged: `GEO`, because excluding three metros still includes North Carolina. Not `SR`: the title is "AI Engineer" and the years requirement is 3+.

**Example 3 — the kind of post that should come back nearly clean.** Excerpts: "We are looking for an AI & ML Engineer with strong software engineering foundations to join our growing engineering team." / "Own retrieval and agentic systems (e.g., RAG pipelines, workflow agents, policy-driven logic) end-to-end." / "Define and run rigorous evaluation and testing for AI systems." / "5+ years of professional experience spanning software engineering and applied AI/ML development." / "Experience deploying and operating ML or LLM-based systems in production environments." / "Comfortable working independently while collaborating closely with a senior engineering team."

```json
{"flags": [
  {"code": "LLM", "quote": "Experience deploying and operating ML or LLM-based systems in production environments"},
  {"code": "IND", "quote": "Comfortable working independently"}
]}
```

Not flagged: `MLY`, because "5+ years spanning software engineering and applied AI/ML" is a combined total, not years specifically in ML. Not `SR`: no senior title, years under 8. Not `SRX`: owning systems end-to-end and running evals is normal engineering work, not mentoring, a roadmap, or standards for others. Everything about RAG, agents, and evals is met by the portfolio.

## Judgment notes

- The description is the only evidence. Don't infer from the company name, industry, or what similar companies usually do.
- Boilerplate counts when it applies to the role. A company-wide "travel for in-person engagement is part of almost all roles" is `TRV`; a company-wide "remote-first" line doesn't cancel a role-specific "hybrid."
- Title first, then body. A "Senior" title is `SR` even if the body reads mid-level. Senior duties are `SRX` whether or not `SR` is also flagged.
- Perks reveal location. Catered lunch, on-site gym, and steel-toe boots on a post with no remote statement is `NRM`.
- Don't flag what isn't there. A post that never mentions travel, team size, or culture gets no `TRV`, `SOLO`, or `CUL`.
