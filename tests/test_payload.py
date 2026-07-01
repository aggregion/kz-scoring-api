import json

import pytest

from kz_scoring_api.payload import parse_pipeline_result


def test_parse_empty_returns_empty_list():
    assert parse_pipeline_result("") == []


def test_parse_whitespace_returns_empty_list():
    assert parse_pipeline_result("   \n\t ") == []


def test_parse_json_null_returns_empty_list():
    assert parse_pipeline_result("null") == []


def test_parse_empty_array_returns_empty_list():
    assert parse_pipeline_result("[]") == []


def test_parse_single_row_returns_single_element_list():
    payload = json.dumps([{"col_a": "1", "col_b": "2", "col_c": "3"}])
    assert parse_pipeline_result(payload) == [
        {"col_a": "1", "col_b": "2", "col_c": "3"}
    ]


def test_parse_multiple_rows_preserves_order():
    payload = json.dumps(
        [
            {"a": "1", "b": "2"},
            {"a": "3", "b": "4"},
            {"a": "5", "b": "6"},
        ]
    )
    assert parse_pipeline_result(payload) == [
        {"a": "1", "b": "2"},
        {"a": "3", "b": "4"},
        {"a": "5", "b": "6"},
    ]


def test_parse_bare_object_is_wrapped_in_list():
    """Forward-compat: if a future pipeline variant emits a single JSON
    object instead of a one-element array, the parser still returns a
    list so LookupService._shape can apply has_phone consistently.
    """
    assert parse_pipeline_result('{"x": "42"}') == [{"x": "42"}]


def test_parse_double_encoded_string_is_unwrapped():
    """DDM's `publish_to_session type: json` + `extract single_value: true`
    on a Utf8 cell serializes the cell content as a JSON string. Depending
    on how vaultee-pipelines relays DDM's session.result_json field, the
    payload may arrive wrapped in one extra level of JSON-string encoding.
    Being defensive on both is cheaper than pinning the interop precisely.
    """
    inner = json.dumps([{"x": "42"}])
    outer = json.dumps(inner)  # produces `"\"[{...}]\""`
    assert parse_pipeline_result(outer) == [{"x": "42"}]


def test_parse_strips_utf8_bom():
    payload = "﻿" + json.dumps([{"a": "1"}])
    assert parse_pipeline_result(payload) == [{"a": "1"}]


def test_parse_invalid_json_raises():
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_pipeline_result("not-json{")


def test_parse_unexpected_top_level_raises():
    with pytest.raises(ValueError, match="unexpected top-level type"):
        parse_pipeline_result("42")


def test_parse_non_object_row_raises():
    with pytest.raises(ValueError, match="row is not an object"):
        parse_pipeline_result("[42]")
