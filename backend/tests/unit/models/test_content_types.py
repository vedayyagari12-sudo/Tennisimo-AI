"""`ALLOWED_CONTENT_TYPES` -- the accepted upload MIME types and their extensions.

This map is the single authority for two separate things (Stage 1): what the
ticket endpoint accepts, and what extension the server-chosen storage path ends
in. A test here rather than only in the route test, because the route reads the
map and cannot notice an entry that was never added.
"""

from __future__ import annotations

import pytest

from app.models.requests import ALLOWED_CONTENT_TYPES


def test_the_map_is_exactly_the_three_container_formats_clients_produce() -> None:
    assert ALLOWED_CONTENT_TYPES == {
        "video/mp4": "mp4",
        "video/quicktime": "mov",
        "video/webm": "webm",
    }


@pytest.mark.parametrize(
    ("content_type", "extension"),
    [
        # Flutter camera plugin, and iOS Safari MediaRecorder.
        ("video/mp4", "mp4"),
        # iOS photo-library pick.
        ("video/quicktime", "mov"),
        # Android Chrome MediaRecorder: no MP4 muxer, emits Matroska/WebM.
        ("video/webm", "webm"),
    ],
)
def test_each_accepted_type_maps_to_its_extension(content_type: str, extension: str) -> None:
    assert ALLOWED_CONTENT_TYPES[content_type] == extension


def test_webm_is_accepted_so_android_chrome_can_upload() -> None:
    assert "video/webm" in ALLOWED_CONTENT_TYPES


@pytest.mark.parametrize(
    "rejected",
    [
        "video/x-matroska",  # a .mkv is not necessarily VP8/VP9/H.264
        "video/avi",
        "application/octet-stream",
        "image/jpeg",
        "",
        "VIDEO/MP4",  # lookup is exact; the route does not case-fold
    ],
)
def test_unlisted_types_are_absent(rejected: str) -> None:
    assert rejected not in ALLOWED_CONTENT_TYPES


def test_extensions_are_bare_and_unique() -> None:
    extensions = list(ALLOWED_CONTENT_TYPES.values())
    assert len(set(extensions)) == len(extensions), "two types sharing an extension is ambiguous"
    for extension in extensions:
        assert not extension.startswith("."), "the path template already supplies the dot"
        assert extension == extension.lower()
