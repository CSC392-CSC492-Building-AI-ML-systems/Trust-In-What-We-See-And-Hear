# Call context contract

`CallContext.from_dict(data)` is the validation boundary between the code that
assembles a call and the call agent. It accepts an object, validates and
normalises it, and returns `(context, warnings)`. The agent receives only the
context. See `sample_call_context.json` for a complete example.

The endpoint and agent integration are currently stubs. This module does not
perform consent, friendship, voice or authentication checks. The future caller
must perform those checks first and must not start the agent if validation fails.

## Required data

The `persona` and `scenario` objects are required. These fields must be strings
that remain nonempty after normalisation:

| Field | Maximum characters |
|---|---:|
| `persona.name` | 40 |
| `scenario.title` | 80 |
| `scenario.situation` | 500 |
| `scenario.ask` | 160 |

Invalid types, empty content, invalid name characters and excessive lengths
produce errors. Required content is never replaced by an invented identity or
scenario, and never shortened to fit. Validation collects all errors before
raising `CallContextValidationError`; it does not return a partial context.

Name/label fields allow Unicode letters and combining marks, spaces, apostrophes
(straight or curly), hyphens and periods, and must start with a letter. These
are formatting restrictions, not identity verification or instruction detection.

## Optional data

| Field | Missing/invalid fallback | Limit |
|---|---|---|
| `persona.relationship` | `"friend"` | 40 characters; label rules |
| `target.name` | empty string | 40 characters; label rules |
| `target.called_as` | cleaned `target.name`, or empty | 40 characters; label rules |
| `scenario.tactics` | empty list | 6 unique strings, 40 characters each |
| `details` | empty object | 8 unique entries; keys 30, values 100 characters |

A missing optional field uses its documented fallback without a warning. An
explicitly supplied invalid value (including `null`) produces a warning. An
empty tactics list is valid and stays empty. An invalid tactics container or a
list with no valid items produces warnings and no invented tactics.

Tactics and detail keys/values must be strings. Non-string or cleaned-empty
items are dropped with warnings. Optional tactic/detail text is truncated with
a warning. Tactics are deduplicated after cleaning. If cleaning or truncation
makes detail keys collide, the first valid value is kept and later entries are
reported and dropped. Invalid items and duplicates do not consume item slots:
filtering and deduplication happen before the limits are applied. Extra valid
items also produce a warning.

Unknown top-level and section fields are ignored and reported. Arbitrary keys
inside `details` are facts, subject to the key/value validation above.

## Errors and warnings

Every `ValidationIssue` has `path`, `code` and `message` fields. Code should
inspect `path` and `code`, rather than parsing human-readable messages.

| Code | Meaning |
|---|---|
| `required` | Required object or field is absent |
| `invalid_type` | Wrong object, list or string type |
| `empty` | String has no content after cleaning |
| `too_long` | Required text or a label exceeds its limit |
| `invalid_label` | Label contains disallowed characters |
| `unknown_field` | Unrecognised field ignored |
| `truncated` | Optional text shortened |
| `duplicate` | Duplicate cleaned tactic or detail key dropped |
| `no_valid_items` | A nonempty tactics list contained no usable strings |
| `extra_items` | Valid items exceeded the collection limit |

The exception exposes separate `errors` and `warnings` lists. The successful
return contains only warnings. Issues omit rejected values; detail entry paths
use input indexes so they do not embed personal fact keys or values.

```python
from app.modules.calls.context import CallContext, CallContextValidationError

try:
    context, warnings = CallContext.from_dict(assembled_call_data)
except CallContextValidationError as exc:
    # Report exc.errors and exc.warnings to the call setup layer.
    # Stop here: do not invoke the agent or substitute a demo context.
    raise

# Report any optional-field warnings before handing context to the agent.
# context.to_dict() returns a separate, serialisable copy.
```

Dataclass constructors are for internal, already validated data. Use
`from_dict` for external input. The parser neither mutates its input nor shares
mutable tactics/detail collections between contexts.

## Trust boundary and integration decisions

Normalisation uses Unicode NFKC, replaces nonprintable characters with spaces,
removes selected formatting characters, and collapses whitespace. This does not
strip HTML elements as a parser would, make HTML safe to render, verify that
facts are true, or prevent prompt injection. Cleaned values remain untrusted.
The call agent must keep trusted instructions separate from this data and apply
its own safeguards before generating speech.

Two database mappings remain unresolved: there is no source column for
`scenario.ask`, and the team must choose whose onboarding answers populate
`details`. The assembling code must supply an explicit ask; there is no fallback
money request. The local prototype has its own older contract and is not changed
by this module; its eventual integration must handle this validation API.

AI-assisted documentation (Codex), cited per the CSC398 AI policy.
