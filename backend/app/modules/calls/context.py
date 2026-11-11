"""Call context: what the call agent needs to know to run one simulated call.

`POST /calls` will build this after checking the consent, the friendship and
the persona's voice (see "How a call is authorized" in
supabase/database-schema.md), and pass it to the call agent. The agent does
not read the database itself. Example: `sample_call_context.json`.

| Field | Meaning | Suggested source |
|---|---|---|
| persona.name | Friend being impersonated | `users.first_name` + `users.last_name` of `calls.persona_user_id` |
| persona.relationship | Persona's relationship to the target | "friend" (default) |
| target.name | User receiving the call | `users.first_name` + `users.last_name` of `calls.user_id` |
| target.called_as | How the persona addresses them | optional, defaults to target.name |
| scenario.title | Scenario name | `scam_scenarios.title` |
| scenario.situation | The caller's story | `scam_scenarios.description` |
| scenario.ask | What the caller wants | **TBD**: no column yet |
| scenario.tactics | Pressure tactics | `scam_scenarios.red_flags` |
| details | Facts the caller can use | `users.onboarding_answers` (**TBD**: whose) |

Everything is treated as untrusted input:
- every field is optional and falls back to a default,
- strings are normalised, stripped of newlines and markup, and length-capped,
- name-like fields must look like names (an instruction pasted into a name
  is rejected and replaced by the default, not passed on),
- unknown keys are ignored and reported.

`CallContext.from_dict` returns the context and a list of warnings rather
than raising, so a bad field cannot fail a call; log the warnings, they
point to mapping problems upstream.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field

LIMITS = {
    "name": 40, "relationship": 40, "called_as": 40, "title": 80,
    "situation": 500, "ask": 160, "tactic": 40, "detail_key": 30,
    "detail_value": 100,
}
MAX_TACTICS = 6
MAX_DETAILS = 8
# Short label fields must look like names or labels, not sentences.
LABEL_FIELDS = {"name", "relationship", "called_as"}
LABEL_RE = re.compile(r"^[^\W\d_]+(?:[ '\-.][^\W\d_]+){0,3}\.?$")


def clean(value, limit: int) -> str:
    """Make an untrusted value safe to quote inside a prompt."""
    if value is None:
        return ""
    s = unicodedata.normalize("NFKC", str(value))
    s = "".join(ch if ch.isprintable() else " " for ch in s)  # drops newlines etc.
    s = re.sub(r"[\[\]{}<>`#*_|\\\"]", "", s)  # no tags, markup or quote breaking
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit].strip()


@dataclass
class Persona:
    """The friend being impersonated (calls.persona_user_id)."""
    name: str = "Sam"
    relationship: str = "friend"  # the persona's relationship TO the target


@dataclass
class Target:
    """The user receiving the call and practising (calls.user_id)."""
    name: str = ""
    called_as: str = ""  # what the persona would call them; defaults to name


@dataclass
class Scenario:
    """From scam_scenarios: title, description -> situation, red_flags -> tactics."""
    title: str = "Stranded and needs money"
    situation: str = ("Your phone was stolen while travelling, you are borrowing "
                      "someone else's phone, and you are stuck without money.")
    ask: str = "money sent right away, and keep it between the two of you"
    tactics: list[str] = field(default_factory=lambda: ["urgency", "secrecy", "guilt"])


@dataclass
class CallContext:
    persona: Persona = field(default_factory=Persona)
    target: Target = field(default_factory=Target)
    scenario: Scenario = field(default_factory=Scenario)
    # Optional facts the caller can use, e.g. from users.onboarding_answers.
    details: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict | None) -> tuple["CallContext", list[str]]:
        warnings: list[str] = []
        data = data if isinstance(data, dict) else {}
        ctx = cls()

        def section(key: str, obj, fields: list[str]):
            raw = data.get(key)
            if raw is None:
                warnings.append(f"{key}: missing, using defaults")
                return
            if not isinstance(raw, dict):
                warnings.append(f"{key}: expected an object, using defaults")
                return
            for k in raw:
                if k not in fields:
                    warnings.append(f"{key}.{k}: unknown field ignored")
            for f in fields:
                if f not in raw or f == "tactics":
                    continue
                value = clean(raw[f], LIMITS.get(f, 80))
                if f in LABEL_FIELDS and value and not LABEL_RE.match(value):
                    warnings.append(f"{key}.{f}: not a plain name/label, using default")
                    continue
                if len(str(raw[f])) > LIMITS.get(f, 80):
                    warnings.append(f"{key}.{f}: truncated")
                if value:
                    setattr(obj, f, value)
                else:
                    warnings.append(f"{key}.{f}: empty after cleaning, using default")

        section("persona", ctx.persona, ["name", "relationship"])
        section("target", ctx.target, ["name", "called_as"])
        section("scenario", ctx.scenario, ["title", "situation", "ask", "tactics"])

        scenario = data.get("scenario")
        tactics = scenario.get("tactics") if isinstance(scenario, dict) else None
        if isinstance(tactics, list):
            cleaned = list(dict.fromkeys(  # strings only, deduplicated, in order
                t for t in (clean(x, LIMITS["tactic"]) for x in tactics
                            if isinstance(x, str)) if t))
            if cleaned:
                ctx.scenario.tactics = cleaned[:MAX_TACTICS]
            if len(tactics) > MAX_TACTICS:
                warnings.append("scenario.tactics: extra items dropped")

        details = data.get("details")
        if isinstance(details, dict):
            for k, v in list(details.items())[:MAX_DETAILS]:
                ck, cv = clean(k, LIMITS["detail_key"]), clean(v, LIMITS["detail_value"])
                if ck and cv:
                    ctx.details[ck] = cv
            if len(details) > MAX_DETAILS:
                warnings.append("details: extra items dropped")
        elif details is not None:
            warnings.append("details: expected an object, ignored")

        # How the persona addresses the target defaults to the target's name.
        if not ctx.target.called_as and ctx.target.name:
            ctx.target.called_as = ctx.target.name

        for k in data:
            if k not in ("persona", "target", "scenario", "details"):
                warnings.append(f"{k}: unknown top-level field ignored")
        return ctx, warnings

    def to_dict(self) -> dict:
        return asdict(self)
