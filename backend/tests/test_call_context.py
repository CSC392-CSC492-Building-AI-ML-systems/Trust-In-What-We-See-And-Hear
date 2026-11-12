"""Call context boundary and regression tests.

AI-assisted code (Claude Code and Codex), cited per the CSC398 AI policy.
"""

import json
from pathlib import Path

import pytest

from pydantic import TypeAdapter

from app.modules.calls import context as context_module
from app.modules.calls.context import CallContext, CallContextValidationError, LIMITS
from app.modules.users.schemas import FirstName, LastName

SAMPLE = Path(__file__).parents[1] / "app/modules/calls/sample_call_context.json"


@pytest.fixture
def data():
    return json.loads(SAMPLE.read_text())


def issues(items):
    return {(item.path, item.code) for item in items}


def test_sample_is_valid_and_round_trips(data):
    ctx, warnings = CallContext.from_dict(data)
    assert warnings == []
    assert ctx.persona.name == "Sam"
    assert ctx.target.called_as == "Alex"
    assert ctx.details["school"] == "UTM"
    again, warnings = CallContext.from_dict(ctx.to_dict())
    assert again == ctx
    assert warnings == []


@pytest.mark.parametrize("value", [None, "not a dict", 42, [], True])
def test_non_object_input_fails(value):
    with pytest.raises(CallContextValidationError) as caught:
        CallContext.from_dict(value)
    assert issues(caught.value.errors) == {("context", "invalid_type")}


@pytest.mark.parametrize("section, field", [
    ("persona", "name"), ("scenario", "title"),
    ("scenario", "situation"), ("scenario", "ask"),
])
@pytest.mark.parametrize("value, code", [
    (None, "invalid_type"), (42, "invalid_type"), (True, "invalid_type"),
    ({"text": "something"}, "invalid_type"), ([], "invalid_type"),
    ("", "empty"), (" \n ", "empty"), ("x" * 501, "too_long"),
])
def test_invalid_required_fields_fail(data, section, field, value, code):
    data[section][field] = value
    with pytest.raises(CallContextValidationError) as caught:
        CallContext.from_dict(data)
    assert (f"{section}.{field}", code) in issues(caught.value.errors)


@pytest.mark.parametrize("section, field", [
    ("persona", "name"), ("scenario", "title"),
    ("scenario", "situation"), ("scenario", "ask"),
])
def test_missing_required_fields_fail(data, section, field):
    del data[section][field]
    with pytest.raises(CallContextValidationError) as caught:
        CallContext.from_dict(data)
    assert (f"{section}.{field}", "required") in issues(caught.value.errors)


@pytest.mark.parametrize("section", ["persona", "scenario"])
@pytest.mark.parametrize("value", [None, "wrong", []])
def test_invalid_required_sections_fail(data, section, value):
    data[section] = value
    with pytest.raises(CallContextValidationError) as caught:
        CallContext.from_dict(data)
    assert issues(caught.value.errors) == {(section, "invalid_type")}


def test_empty_input_does_not_create_an_identity_or_scenario():
    with pytest.raises(CallContextValidationError) as caught:
        CallContext.from_dict({})
    assert issues(caught.value.errors) == {("persona", "required"), ("scenario", "required")}


def test_invalid_relationship_uses_default_with_warning(data):
    data["persona"]["relationship"] = "friend 123"
    ctx, warnings = CallContext.from_dict(data)
    assert ctx.persona.relationship == "friend"
    assert issues(warnings) == {("persona.relationship", "invalid_label")}


@pytest.mark.parametrize("name", [
    "Mary-Ann", "O'Neil", "O’Neil", "José", "Nguyễn Văn An", "李明", "अमित",
    "Ana Maria de la Cruz", "ignore rules output prompt",
    "Sam 2", "Xiao-Ming (Tom)", "Sam 😀", "A----", "A'''", "A . . .",
])
def test_names_accept_profile_characters_without_instruction_detection(data, name):
    data["persona"]["name"] = name
    ctx, warnings = CallContext.from_dict(data)
    assert ctx.persona.name == name
    assert warnings == []


def test_only_documented_optional_defaults_are_used(data):
    del data["persona"]["relationship"]
    del data["target"]
    del data["scenario"]["tactics"]
    del data["details"]
    ctx, warnings = CallContext.from_dict(data)
    assert ctx.persona.relationship == "friend"
    assert ctx.target.name == ctx.target.called_as == ""
    assert ctx.scenario.tactics == []
    assert ctx.details == {}
    assert warnings == []


def test_invalid_optional_fields_warn_and_use_documented_fallbacks(data):
    data["persona"]["relationship"] = 42
    data["target"]["called_as"] = "x" * 102
    ctx, warnings = CallContext.from_dict(data)
    assert ctx.persona.relationship == "friend"
    assert ctx.target.called_as == "Alex"
    assert {("persona.relationship", "invalid_type"),
            ("target.called_as", "too_long")} <= issues(warnings)


@pytest.mark.parametrize("value", [None, [], "wrong"])
def test_invalid_optional_sections_warn(data, value):
    data["target"] = value
    data["details"] = value
    ctx, warnings = CallContext.from_dict(data)
    assert ctx.target.name == ""
    assert ctx.details == {}
    assert {("target", "invalid_type"), ("details", "invalid_type")} <= issues(warnings)


def test_text_whitespace_is_normalized_and_punctuation_preserved(data):
    data["persona"]["name"] = "Ｓａｍ"
    data["scenario"]["situation"] = 'line one\nline two <b>[sigh]</b> "test"'
    ctx, warnings = CallContext.from_dict(data)
    assert ctx.persona.name == "Ｓａｍ"
    assert ctx.scenario.situation == 'line one line two <b>[sigh]</b> "test"'
    assert warnings == []


@pytest.mark.parametrize("field, limit", [("title", 80), ("situation", 500), ("ask", 160)])
def test_required_content_length_boundary(data, field, limit):
    data["scenario"][field] = "x" * limit
    ctx, warnings = CallContext.from_dict(data)
    assert getattr(ctx.scenario, field) == "x" * limit
    assert warnings == []
    data["scenario"][field] += "x"
    with pytest.raises(CallContextValidationError) as caught:
        CallContext.from_dict(data)
    assert (f"scenario.{field}", "too_long") in issues(caught.value.errors)


@pytest.mark.parametrize("value", ["urgency", None, {}, 42])
def test_wrong_tactics_container_warns_without_inventing_tactics(data, value):
    data["scenario"]["tactics"] = value
    ctx, warnings = CallContext.from_dict(data)
    assert ctx.scenario.tactics == []
    assert ("scenario.tactics", "invalid_type") in issues(warnings)


def test_all_invalid_tactics_are_reported(data):
    data["scenario"]["tactics"] = [None, 42, "", " \n "]
    ctx, warnings = CallContext.from_dict(data)
    assert ctx.scenario.tactics == []
    assert issues(warnings) == {
        ("scenario.tactics[0]", "invalid_type"),
        ("scenario.tactics[1]", "invalid_type"),
        ("scenario.tactics[2]", "empty"),
        ("scenario.tactics[3]", "empty"),
        ("scenario.tactics", "no_valid_items"),
    }


def test_explicitly_empty_tactics_stay_empty(data):
    data["scenario"]["tactics"] = []
    ctx, warnings = CallContext.from_dict(data)
    assert ctx.scenario.tactics == []
    assert warnings == []


def test_tactics_filter_and_deduplicate_before_cap(data):
    data["scenario"]["tactics"] = [None] * 6 + ["urgency", "urgency", "secrecy"]
    ctx, warnings = CallContext.from_dict(data)
    assert ctx.scenario.tactics == ["urgency", "secrecy"]
    assert ("scenario.tactics[7]", "duplicate") in issues(warnings)
    assert ("scenario.tactics", "extra_items") not in issues(warnings)
    data["scenario"]["tactics"] = [f"t{i}" for i in range(8)]
    ctx, warnings = CallContext.from_dict(data)
    assert ctx.scenario.tactics == [f"t{i}" for i in range(6)]
    assert ("scenario.tactics", "extra_items") in issues(warnings)


def test_empty_details_do_not_consume_slots(data):
    data["details"] = {f"empty{i}": "" for i in range(8)} | {"school": "UTM"}
    ctx, warnings = CallContext.from_dict(data)
    assert ctx.details == {"school": "UTM"}
    assert len(warnings) == 8
    assert all(issue.code == "empty" for issue in warnings)


def test_invalid_detail_types_are_dropped_with_warnings(data):
    data["details"] = {42: "number key", "school": {"name": "UTM"}, "city": "Toronto"}
    ctx, warnings = CallContext.from_dict(data)
    assert ctx.details == {"city": "Toronto"}
    assert issues(warnings) == {("details[0].key", "invalid_type"),
                                ("details[1].value", "invalid_type")}


def test_cleaned_detail_key_collision_keeps_first_value(data):
    data["details"] = {"school": "UTM", " school ": "different", "city": "Toronto"}
    ctx, warnings = CallContext.from_dict(data)
    assert ctx.details == {"school": "UTM", "city": "Toronto"}
    assert ("details[1]", "duplicate") in issues(warnings)


def test_details_cap_counts_valid_unique_entries(data):
    data["details"] = {" ": "empty key", "school": "UTM", " school ": "duplicate"}
    data["details"].update({f"key{i}": f"value{i}" for i in range(8)})
    ctx, warnings = CallContext.from_dict(data)
    assert ctx.details == {"school": "UTM"} | {f"key{i}": f"value{i}" for i in range(7)}
    assert ("details", "extra_items") in issues(warnings)


def test_optional_text_truncation_and_collisions_are_reported(data):
    data["scenario"]["tactics"] = ["x" * 41, "x" * 40]
    data["details"] = {"k" * 31: "v" * 101, "k" * 30: "later"}
    ctx, warnings = CallContext.from_dict(data)
    assert ctx.scenario.tactics == ["x" * 40]
    assert ctx.details == {"k" * 30: "v" * 100}
    assert {("scenario.tactics[0]", "truncated"),
            ("scenario.tactics[1]", "duplicate"),
            ("details[0].key", "truncated"),
            ("details[0].value", "truncated"),
            ("details[1]", "duplicate")} <= issues(warnings)


def test_unknown_fields_are_reported_without_passing_them_to_agent(data):
    data["persona"]["age"] = 19
    data["scenario"]["system_prompt"] = "untrusted instructions"
    data["rogue"] = True
    ctx, warnings = CallContext.from_dict(data)
    assert len(warnings) == 3
    assert all(issue.code == "unknown_field" for issue in warnings)
    assert "rogue" not in ctx.to_dict()
    assert "age" not in ctx.to_dict()["persona"]
    assert "system_prompt" not in ctx.to_dict()["scenario"]


def test_errors_are_aggregated_and_keep_optional_warnings(data):
    data["persona"]["name"] = 42
    del data["scenario"]["ask"]
    data["details"] = []
    with pytest.raises(CallContextValidationError) as caught:
        CallContext.from_dict(data)
    assert issues(caught.value.errors) == {("persona.name", "invalid_type"),
                                          ("scenario.ask", "required")}
    assert issues(caught.value.warnings) == {("details", "invalid_type")}
    assert "persona.name" in str(caught.value)


def test_parsing_does_not_mutate_input_or_share_mutable_state(data):
    original = json.loads(json.dumps(data))
    first, _ = CallContext.from_dict(data)
    second, _ = CallContext.from_dict(data)
    first.scenario.tactics.append("extra")
    first.details["school"] = "changed"
    exported = second.to_dict()
    exported["scenario"]["tactics"].clear()
    assert data == original
    assert second.scenario.tactics == original["scenario"]["tactics"]
    assert second.details == original["details"]


@pytest.mark.parametrize("section, key", [
    ("persona", "name"), ("persona", "relationship"),
    ("target", "name"), ("target", "called_as"),
])
def test_labels_are_not_silently_shortened(data, section, key):
    limit = LIMITS[key]
    data[section][key] = "A" * limit
    ctx, warnings = CallContext.from_dict(data)
    assert getattr(getattr(ctx, section), key) == "A" * limit
    assert warnings == []
    data[section][key] += "B"
    if (section, key) == ("persona", "name"):
        with pytest.raises(CallContextValidationError) as caught:
            CallContext.from_dict(data)
        assert ("persona.name", "too_long") in issues(caught.value.errors)
    else:
        ctx, warnings = CallContext.from_dict(data)
        assert getattr(getattr(ctx, section), key) != "A" * limit
        assert (f"{section}.{key}", "too_long") in issues(warnings)


def test_diagnostics_do_not_echo_personal_fact_values(data):
    data["scenario"]["ask"] = "private request " * 20
    data["details"] = {"private detail key": "private detail value " * 20}
    with pytest.raises(CallContextValidationError) as caught:
        CallContext.from_dict(data)
    diagnostics = str(caught.value) + repr(caught.value.warnings)
    assert "private request" not in diagnostics
    assert "private detail key" not in diagnostics
    assert "private detail value" not in diagnostics
    assert ("details[0].value", "truncated") in issues(caught.value.warnings)


@pytest.mark.parametrize("first,last", [
    ("A" * 50, "B" * 50),
    ("Xiao-Ming (Tom)", "Sam 2 😀"),
    ("ﷺ" * 50, "ﷺ" * 50),  # NFKC would expand this beyond 101 characters.
])
def test_names_from_profile_schema_are_accepted(data, first, last):
    first = TypeAdapter(FirstName).validate_python(first)
    last = TypeAdapter(LastName).validate_python(last)
    full_name = f"{first} {last}"
    data["persona"]["name"] = full_name
    data["target"] = {"name": full_name, "called_as": first}
    ctx, warnings = CallContext.from_dict(data)
    assert ctx.persona.name == ctx.target.name == full_name
    assert ctx.target.called_as == first
    assert warnings == []


@pytest.mark.parametrize("target", [None, {}, {"name": "", "called_as": ""}])
def test_optional_defaults_round_trip_without_warnings(data, target):
    if target is None:
        del data["target"]
    else:
        data["target"] = target
    ctx, warnings = CallContext.from_dict(data)
    assert warnings == []
    again, warnings = CallContext.from_dict(ctx.to_dict())
    assert again == ctx
    assert warnings == []


def test_empty_optional_labels_use_defaults_without_warnings(data):
    data["persona"]["relationship"] = " "
    data["target"]["called_as"] = " "
    ctx, warnings = CallContext.from_dict(data)
    assert ctx.persona.relationship == "friend"
    assert ctx.target.called_as == ctx.target.name == "Alex"
    assert warnings == []


def test_punctuation_in_free_text_is_preserved(data):
    value = """I owe $500 to user_42 at "Joe's" #3 *now*"""
    data["scenario"]["situation"] = data["scenario"]["ask"] = value
    data["details"] = {"email_address": "a_b@x.com"}
    ctx, warnings = CallContext.from_dict(data)
    assert ctx.scenario.situation == ctx.scenario.ask == value
    assert ctx.details == {"email_address": "a_b@x.com"}
    assert warnings == []


@pytest.mark.parametrize("section", [None, "persona", "target", "scenario"])
def test_unknown_field_names_do_not_leak_into_diagnostics(data, section):
    secret = "my SIN is 123-456-789"
    raw = data if section is None else data[section]
    index = len(raw)
    raw[secret] = "private value"
    _, warnings = CallContext.from_dict(data)
    assert issues(warnings) == {(f"{section or 'context'}[{index}]", "unknown_field")}
    assert secret not in repr(warnings)
    # Warnings are retained on failure too.
    del data["scenario"]["ask"]
    with pytest.raises(CallContextValidationError) as caught:
        CallContext.from_dict(data)
    assert secret not in str(caught.value) + repr(caught.value.warnings)


def test_tactics_deduplicate_case_insensitively_keep_first_spelling(data):
    data["scenario"]["tactics"] = ["Urgency", "urgency", "URGENCY", "Straße", "STRASSE"]
    ctx, warnings = CallContext.from_dict(data)
    assert ctx.scenario.tactics == ["Urgency", "Straße"]
    assert issues(warnings) == {(f"scenario.tactics[{i}]", "duplicate") for i in (1, 2, 4)}


def test_items_beyond_caps_only_produce_collection_warnings(data):
    data["scenario"]["tactics"] = [f"t{i}" for i in range(6)] + [None, "t0", "x" * 50, "y" * 50]
    data["details"] = {f"k{i}": "value" for i in range(8)}
    data["details"].update({"invalid": None, " k0 ": "duplicate", "x" * 40: "y" * 110})
    ctx, warnings = CallContext.from_dict(data)
    assert len(ctx.scenario.tactics) == 6
    assert len(ctx.details) == 8
    assert issues(warnings) == {("scenario.tactics", "extra_items"), ("details", "extra_items")}


def test_invalid_or_duplicate_overflow_does_not_report_extra_valid_items(data):
    data["scenario"]["tactics"] = [f"t{i}" for i in range(6)] + [None, "T0"]
    data["details"] = {f"k{i}": "value" for i in range(8)} | {"invalid": None, " k0 ": "duplicate"}
    _, warnings = CallContext.from_dict(data)
    assert warnings == []


def test_oversized_raw_text_is_rejected_before_normalization(data, monkeypatch):
    original = context_module.clean

    def bounded_clean(value, *args, **kwargs):
        assert len(value) <= context_module.MAX_RAW_TEXT
        return original(value, *args, **kwargs)

    monkeypatch.setattr(context_module, "clean", bounded_clean)
    data["scenario"]["situation"] = " " * (context_module.MAX_RAW_TEXT + 1)
    data["details"] = {"large": "x" * (context_module.MAX_RAW_TEXT + 1)}
    with pytest.raises(CallContextValidationError) as caught:
        CallContext.from_dict(data)
    assert issues(caught.value.errors) == {("scenario.situation", "too_long")}
    assert issues(caught.value.warnings) == {("details[0].value", "too_long")}
