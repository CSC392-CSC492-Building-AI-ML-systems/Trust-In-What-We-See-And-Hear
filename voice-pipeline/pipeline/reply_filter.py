"""Clean the LLM's reply before it is spoken or stored.

The prompt asks for plain spoken words, but the model does not always obey.
Seen in testing: replies in script format,
    Grandma: "Thank you."
    Jordan: "I- I don't know how to explain this..."
where the voice then read the labels and the other person's line aloud.

- clean_reply removes lines spoken by the other person, speaker labels,
  quotes and stage directions like *sobs* or (whispering). Tags like [sigh]
  are kept; chunker.strip_tags handles them per engine.
- safe_destinations makes sure any email, website or phone number the caller
  gives is an obviously fake one, whatever the model wrote.
- split_end finds the marker the model uses to end the call.

Apply these to each chunk from chunker.SpeechChunker (it splits at sentence
ends, so a label and its line stay together) and to the full reply before
storing it in the conversation.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

from __future__ import annotations

import re

LABEL = r"[A-Z][\w.'\- ]{0,30}"
END_CALL = "[end call]"
END_RE = re.compile(r"\[\s*end\s+call\s*\]", re.IGNORECASE)

EMAIL = re.compile(r"\b([\w.+-]+)@([\w-]+(?:\.[\w-]+)+)")
URL = re.compile(r"(?i)\b(?:https?://|www\.)[\w\-./%?=&#]*[\w/]")
DOMAIN = re.compile(
    r"(?i)(?<![\w@.-])(?!example\.com\b)[a-z0-9-]+(?:\.[a-z0-9-]+)*"
    r"\.(?:com|net|org|ca|io|co|info|biz|me|app|xyz)\b(?!@)")
# North American numbers, with or without an area code. Not amounts
# ("$500-1000", "500-1000 dollars").
PHONE = re.compile(
    r"(?<![\d$])(?:(?:\+?1[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)?\d{3}[\s.-]\d{4}|\d{10})"
    r"(?!\d)(?!\s*(?:dollars|bucks))")
FAKE_PHONE = re.compile(r"555[\s.-]?01\d\d$")


def names_and_first_names(*names: str) -> list[str]:
    """Each name plus its first word: the model writes "Jordan:", not the full name."""
    out: list[str] = []
    for name in names:
        for label in (name, name.split(" ")[0] if name else ""):
            if label and label not in out:
                out.append(label)
    return out


def clean_reply(text: str, persona_labels: list[str], other_labels: list[str]) -> str:
    others = [re.escape(o) for o in other_labels if o]
    # Stage directions and markup (keep [tags]). First, so a label that
    # follows one ("*sobs* Grandma: ...") is still found below.
    out = re.sub(r"\*[^*\n]{1,60}\*", "", text)
    out = re.sub(r"\((?:[^()\n]{1,40})\)", "", out)
    if others:
        alt = "|".join(others)
        # Whole lines or quoted segments attributed to the other person.
        out = re.sub(rf'(?mi)^\s*(?:{alt})\s*:\s*"[^"\n]*"\s*', "", out)
        out = re.sub(rf'(?mi)^\s*(?:{alt})\s*:[^\n]*(\n|$)', "", out)
        out = re.sub(rf'(?i)(?:^|(?<=\s))(?:{alt})\s*:\s*"[^"]*"\s*', "", out)
        # Unquoted turn by the other person starting a new sentence mid-line:
        # drop it up to its sentence end. (Not after a comma: "a lot, Grandma:".)
        out = re.sub(rf'(?i)(^|[.?!]["”]?\s+)(?:{alt})\s*:[^.?!\n]*[.?!]+\s*', r"\1", out)
    # The persona's own label, and any remaining "Name:" at a line start.
    own = "|".join(re.escape(p) for p in persona_labels if p)
    if own:
        out = re.sub(rf'(?i)(?:^|(?<=\s))(?:{own})\s*:\s*', "", out)
    out = re.sub(rf"(?m)^\s*{LABEL}\s*:\s*(?=\")", "", out)
    # Double quotes are never spoken, so drop them all (curly ones too).
    out = re.sub(r'["“”]', "", out)
    return re.sub(r"\s+", " ", out).strip()


def safe_destinations(text: str) -> str:
    """Rewrite emails, websites and phone numbers to obviously fake ones.

    The call is a simulation, but a real-looking address said aloud could
    still belong to someone. Emails go to @example.com, websites to
    example.com, and phone numbers to 555-01xx (reserved for fiction).
    """
    def email(m: re.Match) -> str:
        return m.group(0) if m.group(2).lower() == "example.com" else f"{m.group(1)}@example.com"

    def phone(m: re.Match) -> str:
        number = m.group(0)
        return number if FAKE_PHONE.search(number) else f"555-01{number[-2:]}"

    out = EMAIL.sub(email, text)
    out = URL.sub("example.com", out)
    out = DOMAIN.sub("example.com", out)
    return PHONE.sub(phone, out)


def split_end(text: str) -> tuple[str, bool]:
    """Remove the end-of-call marker. Returns (text, whether it was there)."""
    out, n = END_RE.subn("", text)
    return re.sub(r"\s+", " ", out).strip(), n > 0
