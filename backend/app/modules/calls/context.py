"""Validate the data needed to run one simulated call.

`POST /calls` will build this after checking consent, friendship and the
persona's voice (see supabase/database-schema.md). The agent does not read
the database itself. Example input: `sample_call_context.json`.

| Field | Suggested source | Required / default |
|---|---|---|
| persona.name | `users.first_name` + `users.last_name` of `calls.persona_user_id` | required |
| persona.relationship | Persona's relationship to target | "friend" |
| target.name | `users.first_name` + `users.last_name` of `calls.user_id` | empty |
| target.called_as | How the persona addresses the target | target.name |
| scenario.title | `scam_scenarios.title` | required |
| scenario.situation | `scam_scenarios.description` | required |
| scenario.ask | **TBD**: no column yet | required |
| scenario.tactics | `scam_scenarios.red_flags` | empty list |
| details | `users.onboarding_answers` (**TBD**: whose) | empty dict |

`CallContext.from_dict` returns (context, warnings) on success. Invalid core
fields raise `CallContextValidationError`, with structured `errors` and
`warnings`; no context is returned. Callers must handle that exception and
must not start the agent. Optional fields may be dropped or use the documented
fallback, with warnings. Issues contain a path, code and message.

Only strings are accepted as text. Text is normalised, selected formatting
characters are removed, and length limits are enforced. Required fields and
names are rejected if too long rather than silently changing their meaning.
Name/label checks restrict character shape only. Optional free text is capped;
tactics and details are filtered before their item limits are applied.

All resulting values remain untrusted. These checks do not detect instructions
or prevent prompt injection. The agent must keep trusted instructions separate
from call data and apply its own safeguards before speaking.

AI-assisted code (Claude Code and Codex), cited per the CSC398 AI policy.
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
LABEL_FIELDS = {"name", "relationship", "called_as"}


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


class CallContextValidationError(ValueError):
    """Core data is invalid; do not start a call or invent replacement data."""

    def __init__(self, errors: list[ValidationIssue], warnings: list[ValidationIssue]):
        self.errors = errors
        self.warnings = warnings
        super().__init__("Invalid call context: " + "; ".join(map(str, errors)))


def clean(value: str, limit: int | None = None) -> str:
    """Normalise text, remove selected formatting characters, and optionally cap it.

    The result remains untrusted prompt content. This is not HTML sanitisation
    or instruction detection; removing brackets does not remove tag contents.
    """
    s = unicodedata.normalize("NFKC", value)
    s = "".join(ch if ch.isprintable() else " " for ch in s)
    s = re.sub(r"[\[\]{}<>`#*_|\\\"]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s if limit is None else s[:limit].strip()


def _is_label(value: str) -> bool:
    # Unicode letters/marks allow international names, including combining marks.
    # This deliberately checks characters, not whether the words are instructions.
    return (
        unicodedata.category(value[0]).startswith("L")
        and all(unicodedata.category(ch)[0] in "LM" or ch in " '-.’" for ch in value)
    )


@dataclass
class Persona:
    name: str
    relationship: str = "friend"


@dataclass
class Target:
    name: str = ""
    called_as: str = ""


@dataclass
class Scenario:
    title: str
    situation: str
    ask: str
    tactics: list[str] = field(default_factory=list)


@dataclass
class CallContext:
    """Use from_dict at the untrusted-input boundary; direct construction is internal."""

    persona: Persona
    scenario: Scenario
    target: Target = field(default_factory=Target)
    details: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: object) -> tuple[CallContext, list[ValidationIssue]]:
        errors: list[ValidationIssue] = []
        warnings: list[ValidationIssue] = []

        def issue(path: str, code: str, message: str, *, fatal: bool = False) -> None:
            (errors if fatal else warnings).append(ValidationIssue(path, code, message))

        if not isinstance(data, dict):
            issue("context", "invalid_type", "expected an object", fatal=True)
            raise CallContextValidationError(errors, warnings)

        def unknown(raw: dict, allowed: set[str], prefix: str = "") -> None:
            for key in raw:
                if key not in allowed:
                    issue(f"{prefix}[{key!r}]", "unknown_field", "unknown field ignored")

        def section(key: str, fields: set[str], *, required: bool = False) -> dict | None:
            if key not in data:
                if required:
                    issue(key, "required", "required object is missing", fatal=True)
                return None
            raw = data[key]
            if not isinstance(raw, dict):
                issue(key, "invalid_type", "expected an object", fatal=required)
                return None
            unknown(raw, fields, key)
            return raw

        def text(value: object, path: str, kind: str, *, required: bool = False) -> str:
            if not isinstance(value, str):
                issue(path, "invalid_type", "expected a string", fatal=required)
                return ""
            value = clean(value)
            if not value:
                issue(path, "empty", "empty after cleaning", fatal=required)
                return ""
            limit = LIMITS[kind]
            if len(value) > limit:
                if required or kind in LABEL_FIELDS:
                    issue(path, "too_long", f"must be at most {limit} characters", fatal=required)
                    return ""
                issue(path, "truncated", f"truncated to {limit} characters")
                value = value[:limit].strip()
            if kind in LABEL_FIELDS and not _is_label(value):
                issue(path, "invalid_label", "expected letters and name/label punctuation", fatal=required)
                return ""
            return value

        def member(raw: dict | None, key: str, prefix: str, *, required: bool = False) -> str:
            if raw is None:
                return ""  # The missing/invalid parent section was already reported.
            if key not in raw:
                if required:
                    issue(f"{prefix}.{key}", "required", "required field is missing", fatal=True)
                return ""
            return text(raw[key], f"{prefix}.{key}", key, required=required)

        unknown(data, {"persona", "target", "scenario", "details"})
        persona = section("persona", {"name", "relationship"}, required=True)
        target = section("target", {"name", "called_as"})
        scenario = section("scenario", {"title", "situation", "ask", "tactics"}, required=True)
        name = member(persona, "name", "persona", required=True)
        relationship = member(persona, "relationship", "persona") or "friend"
        target_name = member(target, "name", "target")
        called_as = member(target, "called_as", "target") or target_name
        title = member(scenario, "title", "scenario", required=True)
        situation = member(scenario, "situation", "scenario", required=True)
        ask = member(scenario, "ask", "scenario", required=True)

        tactics: list[str] = []
        if scenario is not None and "tactics" in scenario:
            raw_tactics = scenario["tactics"]
            if not isinstance(raw_tactics, list):
                issue("scenario.tactics", "invalid_type", "expected a list; omitted")
            else:
                seen: set[str] = set()
                for index, value in enumerate(raw_tactics):
                    path = f"scenario.tactics[{index}]"
                    tactic = text(value, path, "tactic")
                    if not tactic:
                        continue
                    if tactic in seen:
                        issue(path, "duplicate", "duplicate tactic dropped")
                        continue
                    seen.add(tactic)
                    tactics.append(tactic)
                if raw_tactics and not tactics:
                    issue("scenario.tactics", "no_valid_items", "no valid tactics; omitted")
                if len(tactics) > MAX_TACTICS:
                    issue("scenario.tactics", "extra_items", f"only the first {MAX_TACTICS} valid tactics kept")
                    tactics = tactics[:MAX_TACTICS]

        details: dict[str, str] = {}
        if "details" in data:
            raw_details = data["details"]
            if not isinstance(raw_details, dict):
                issue("details", "invalid_type", "expected an object; omitted")
            else:
                seen_keys: set[str] = set()
                extra = False
                for index, (key, value) in enumerate(raw_details.items()):
                    path = f"details[{index}]"
                    cleaned_key = text(key, f"{path}.key", "detail_key")
                    cleaned_value = text(value, f"{path}.value", "detail_value")
                    if not cleaned_key or not cleaned_value:
                        continue
                    if cleaned_key in seen_keys:
                        issue(path, "duplicate", "duplicate cleaned key dropped; first value kept")
                        continue
                    seen_keys.add(cleaned_key)
                    if len(details) < MAX_DETAILS:
                        details[cleaned_key] = cleaned_value
                    else:
                        extra = True
                if extra:
                    issue("details", "extra_items", f"only the first {MAX_DETAILS} valid details kept")

        if errors:
            raise CallContextValidationError(errors, warnings)
        return cls(
            persona=Persona(name=name, relationship=relationship),
            target=Target(name=target_name, called_as=called_as),
            scenario=Scenario(title=title, situation=situation, ask=ask, tactics=tactics),
            details=details,
        ), warnings

    def to_dict(self) -> dict:
        return asdict(self)
