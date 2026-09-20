"""The keyset cursor codec (DATABASE_SETUP.md Part 3.4). PURE."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.services.repository import decode_cursor, encode_cursor

EXAMPLE_TIME = datetime(2026, 9, 14, 9, 12, 44, 118000, tzinfo=UTC)
EXAMPLE_ID = UUID("9f2c1a44-3b7e-4f0d-9a11-6c5c2e8b71aa")
EXAMPLE_CURSOR = (
    "MjAyNi0wOS0xNFQwOToxMjo0NC4xMTgwMDArMDA6MDB8OWYyYzFhNDQtM2I3ZS00ZjBkLTlhMTEtNmM1YzJlOGI3MWFh"
)


def test_encodes_to_the_documented_example() -> None:
    assert encode_cursor(EXAMPLE_TIME, EXAMPLE_ID) == EXAMPLE_CURSOR


def test_round_trips() -> None:
    assert decode_cursor(encode_cursor(EXAMPLE_TIME, EXAMPLE_ID)) == (EXAMPLE_TIME, EXAMPLE_ID)


def test_encoding_is_unpadded() -> None:
    assert not encode_cursor(EXAMPLE_TIME, EXAMPLE_ID).endswith("=")


def test_encoding_is_url_safe() -> None:
    cursor = encode_cursor(EXAMPLE_TIME, EXAMPLE_ID)
    assert "+" not in cursor and "/" not in cursor


@pytest.mark.parametrize(
    "bad",
    [
        "!!!not-base64!!!",
        "",
        "bm8tc2VwYXJhdG9y",  # "no-separator"
        "MjAyNi0wOS0xNHxub3QtYS11dWlk",  # valid separator, bad uuid
    ],
)
def test_undecodable_cursors_raise_value_error(bad: str) -> None:
    with pytest.raises(ValueError):
        decode_cursor(bad)
