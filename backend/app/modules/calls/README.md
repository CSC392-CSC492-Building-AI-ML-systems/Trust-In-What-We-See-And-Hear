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
| `persona.name` | 101 |
| `scenario.title` | 80 |
| `scenario.situation` | 500 |
| `scenario.ask` | 160 |

Invalid types, empty content and excessive lengths
produce errors. Required content is never replaced by an invented identity or
scenario, and never shortened to fit. Validation collects all errors before
raising `CallContextValidationError`; it does not return a partial context.

Names use `users.first_name` + a space + `users.last_name`, from
`calls.persona_user_id` for the persona and `calls.user_id` for the target.
The 101-character limit accommodates both profile fields at their 50-character
maximum. Names and `called_as` accept the profile schema's characters, including
digits, parentheses and emoji. They retain Unicode characters without NFKC
expansion; only whitespace and nonprintable characters are normalised.
Required names that become empty still fail validation.

Only `persona.relationship` restricts character shape: Unicode letters and
combining marks, spaces, apostrophes (straight or curly), hyphens and periods,
starting with a letter. Repeated punctuation is intentionally allowed. These
are formatting restrictions, not identity verification or instruction detection.

## Optional data

| Field | Missing/invalid fallback | Limit |
|---|---|---|
| `persona.relationship` | `"friend"` | 40 characters; label rules |
| `target.name` | empty string | 101 characters; profile name characters |
| `target.called_as` | cleaned `target.name`, or empty | 101 characters; profile name characters |
| `scenario.tactics` | empty list | 6 unique strings, 40 characters each |
| `details` | empty object | 8 unique entries; keys 30, values 100 characters |

A missing optional field uses its documented fallback without a warning. An
empty or whitespace-only optional name, relationship or `called_as` also uses
its fallback without a warning, so exported defaults round-trip cleanly. An
explicitly supplied invalid value (including `null`) produces a warning. An
empty tactics list is valid and stays empty. An invalid tactics container or a
list with no valid items produces warnings and no invented tactics.

Tactics and detail keys/values must be strings. Non-string or cleaned-empty
items are dropped with warnings. Optional tactic/detail text is truncated with
a warning. Tactics are deduplicated after cleaning using Unicode case folding;
the first spelling is kept. If cleaning or truncation
makes detail keys collide, the first valid value is kept and later entries are
reported and dropped. Invalid items and duplicates do not consume item slots:
filtering and deduplication happen before the limits are applied. Extra valid
items also produce a warning. Once a collection is full, additional items are
checked only to detect a further valid, unique item. That produces one
`extra_items` warning; individual warnings for discarded overflow are suppressed.

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
| `too_long` | Required text or a label exceeds its limit, or raw text exceeds 10,000 characters |
| `invalid_label` | Relationship contains disallowed characters |
| `unknown_field` | Unrecognised field ignored |
| `truncated` | Optional text shortened |
| `duplicate` | Duplicate cleaned tactic or detail key dropped |
| `no_valid_items` | A nonempty tactics list contained no usable strings |
| `extra_items` | Valid items exceeded the collection limit |

The exception exposes separate `errors` and `warnings` lists. The successful
return contains only warnings. Issues omit rejected values. Detail entries and
unknown fields use input indexes so paths do not embed arbitrary personal keys
or values (for example, `persona[2]` or `details[0].value`).

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

Normalisation uses Unicode NFKC except for names, replaces nonprintable characters
with spaces, and collapses whitespace. Punctuation is preserved, including quotes,
underscores, hashes and markup characters in scenario text and personal facts.
Each input string is limited to 10,000 raw characters before normalisation to
bound processing work. Longer required strings fail; longer optional strings are
omitted with a `too_long` warning. The future endpoint must also bound the overall
request size and collection sizes. This does not
strip HTML elements as a parser would, make HTML safe to render, verify that
facts are true, or prevent prompt injection. Cleaned values remain untrusted.
The call agent must keep trusted instructions separate from this data and apply
its own safeguards before generating speech.

Two database mappings remain unresolved: there is no source column for
`scenario.ask`, and the team must choose whose onboarding answers populate
`details`. The assembling code must supply an explicit ask; there is no fallback
money request. Endpoint and agent integration must handle this validation API.
Local issue drafts for both decisions are in [FOLLOW_UPS.md](FOLLOW_UPS.md).

AI-assisted documentation (Codex), cited per the CSC398 AI policy.
