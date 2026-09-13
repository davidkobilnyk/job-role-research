"""
Text utilities shared by the normalization pipeline.

  html_to_lines(html)      -> list[str]   block-aware HTML to text lines
  description_lines(src, payload) -> list[(heading_or_None, line)]
  split_sections(lines)    -> list[Section]

Sections are classified by heading keywords into:
  requirements | preferred | responsibilities | about | benefits | other
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

BLOCK_TAGS = {"p", "div", "li", "ul", "ol", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table", "section", "article"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
STRONG_TAGS = {"strong", "b"}
WS = re.compile(r"[ \t\u00a0]+")


class _LineParser(HTMLParser):
    """Emit one text line per block element. Marks lines that are headings or entirely bold."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[tuple[str, bool]] = []     # (text, is_heading)
        self._buf: list[str] = []
        self._heading_depth = 0
        self._strong_depth = 0
        self._strong_chars = 0
        self._total_chars = 0
        self._skip = 0

    def _flush(self) -> None:
        text = WS.sub(" ", "".join(self._buf)).strip()
        if text:
            all_bold = self._total_chars > 0 and self._strong_chars >= self._total_chars * 0.9
            self.lines.append((text, self._heading_depth > 0 or (all_bold and len(text) <= 80)))
        self._buf, self._strong_chars, self._total_chars = [], 0, 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "iframe"):
            self._skip += 1
        elif tag in BLOCK_TAGS:
            self._flush()
            if tag in HEADING_TAGS:
                self._heading_depth += 1
        elif tag in STRONG_TAGS:
            self._strong_depth += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "iframe"):
            self._skip = max(0, self._skip - 1)
        elif tag in BLOCK_TAGS:
            self._flush()
            if tag in HEADING_TAGS:
                self._heading_depth = max(0, self._heading_depth - 1)
        elif tag in STRONG_TAGS:
            self._strong_depth = max(0, self._strong_depth - 1)

    def handle_data(self, data):
        if self._skip:
            return
        self._buf.append(data)
        n = len(data.strip())
        self._total_chars += n
        if self._strong_depth:
            self._strong_chars += n

    def close(self):
        super().close()
        self._flush()


def html_to_lines(raw: str) -> list[tuple[str, bool]]:
    """Unescape (twice: Greenhouse double-escapes) and parse into (line, is_heading) pairs."""
    s = html.unescape(html.unescape(raw or ""))
    if "<" not in s:
        return [(ln.strip(), False) for ln in s.splitlines() if ln.strip()]
    p = _LineParser()
    p.feed(s)
    p.close()
    return p.lines


def plain_to_lines(text: str) -> list[tuple[str, bool]]:
    """Ashby descriptionPlain: blank-line separated blocks; short lines w/o terminal period are headings."""
    out = []
    for ln in (text or "").splitlines():
        ln = WS.sub(" ", ln).strip()
        if ln:
            out.append((ln, False))
    return out


# --------------------------------------------------------------------------- #
# Heading classification
# --------------------------------------------------------------------------- #

SECTION_RULES = [
    ("preferred", re.compile(
        r"nice[- ]to[- ]have|preferred|bonus|plus(es)?\b|ideally|extra credit|would be great|great if|good to have|"
        r"stand out|even better|desirable|additional (skills|qualifications)|not required", re.I)),
    ("requirements", re.compile(
        r"requirement|qualification|what you('ll| will)? (bring|need|have)|what we('re| are) looking for|"
        r"must[- ]have|you have|about you|who you are|what you bring|your (background|experience|profile)|"
        r"skills|experience|you are|you should|minimum|basic qual|the ideal candidate|we('re| are) looking for|"
        r"what it takes|you('ll| will) need|required", re.I)),
    ("responsibilities", re.compile(
        r"responsibilit|what you('ll| will) do|what you('ll| will) be doing|the role|your role|in this role|"
        r"day[- ]to[- ]day|you will|duties|what you('ll| will) work on|the opportunity|the job|the position|"
        r"about the (role|job|position|team)|your impact|what you('ll| will) own|key outcomes", re.I)),
    ("benefits", re.compile(
        r"benefit|perk|compensation|salary|pay (range|band)|what we offer|why join|why you('ll| will) love|"
        r"total rewards|we offer|equity|what('s| is) in it for you", re.I)),
    ("about", re.compile(
        r"about (us|the company|[A-Z]\w+)|who we are|our (mission|story|company|team)|company overview|"
        r"the company|overview|introduction|our values|culture", re.I)),
]

TERMINAL_PUNCT = re.compile(r"[.!?]\s*$")


def classify_heading(text: str) -> str | None:
    t = text.strip().rstrip(":").strip()
    for name, rx in SECTION_RULES:
        if rx.search(t):
            return name
    return None


def looks_like_heading(text: str, flagged: bool) -> bool:
    t = text.strip()
    if flagged:
        return len(t) <= 80
    if t.endswith(":") and len(t) <= 80:
        return True
    words = t.split()
    return 1 <= len(words) <= 7 and not TERMINAL_PUNCT.search(t) and classify_heading(t) is not None


@dataclass
class Section:
    index: int
    section_type: str
    heading: str
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def split_sections(lines: list[tuple[str, bool]]) -> list[Section]:
    sections: list[Section] = []
    current = Section(0, "other", "")
    for text, flagged in lines:
        if looks_like_heading(text, flagged):
            if current.lines:
                sections.append(current)
            current = Section(len(sections), classify_heading(text) or "other", text.rstrip(":").strip())
        else:
            current.lines.append(text)
    if current.lines:
        sections.append(current)
    return sections


# --------------------------------------------------------------------------- #
# Per-source assembly
# --------------------------------------------------------------------------- #

def description_lines(source: str, payload: dict) -> list[tuple[str, bool]]:
    if source == "greenhouse":
        return html_to_lines(payload.get("content") or "")
    if source == "ashby":
        if payload.get("descriptionHtml"):
            return html_to_lines(payload["descriptionHtml"])
        return plain_to_lines(payload.get("descriptionPlain") or "")
    if source == "lever":
        # Lever splits the description into an optional intro (opening) and the body; descriptionPlain is
        # both concatenated. Include the intro and the body; don't stop at the intro.
        out: list[tuple[str, bool]] = []
        if payload.get("openingPlain"):
            out.append(("About", True))
            out += plain_to_lines(payload["openingPlain"])
        if payload.get("descriptionBody"):
            out += html_to_lines(payload["descriptionBody"])
        elif payload.get("descriptionBodyPlain"):
            out += plain_to_lines(payload["descriptionBodyPlain"])
        elif not payload.get("openingPlain") and payload.get("descriptionPlain"):
            out.append(("About", True))
            out += plain_to_lines(payload["descriptionPlain"])
        for lst in payload.get("lists") or []:
            out.append(((lst.get("text") or "Section").strip(), True))
            out += html_to_lines(lst.get("content") or "")
        if payload.get("additionalPlain"):
            out.append(("Additional", True))
            out += plain_to_lines(payload["additionalPlain"])
        return out
    return []


def full_text(source: str, payload: dict) -> str:
    return "\n".join(t for t, _ in description_lines(source, payload))
