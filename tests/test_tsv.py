from kz_scoring_api.tsv import parse_tsv


def test_parse_empty_returns_empty_list():
    assert parse_tsv("") == []


def test_parse_header_only_returns_empty_list():
    assert parse_tsv("col_a\tcol_b\tcol_c\n") == []


def test_parse_single_row():
    payload = "col_a\tcol_b\tcol_c\n1\t2\t3\n"
    assert parse_tsv(payload) == [{"col_a": "1", "col_b": "2", "col_c": "3"}]


def test_parse_multiple_rows_preserves_order():
    payload = "a\tb\n1\t2\n3\t4\n5\t6\n"
    rows = parse_tsv(payload)
    assert rows == [
        {"a": "1", "b": "2"},
        {"a": "3", "b": "4"},
        {"a": "5", "b": "6"},
    ]


def test_parse_strips_utf8_bom():
    payload = "﻿a\tb\n1\t2\n"
    assert parse_tsv(payload) == [{"a": "1", "b": "2"}]
