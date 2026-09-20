"""The vendored pose bundle matches the pinned size + digest. No network."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.pose.extractor import (
    POSE_MODEL_FILENAME,
    POSE_MODEL_SHA256,
    POSE_MODEL_SIZE_BYTES,
    NUM_LANDMARKS,
    resolve_model_path,
    verify_model_asset,
)


def test_constants() -> None:
    assert POSE_MODEL_FILENAME == "pose_landmarker_full.task"
    assert POSE_MODEL_SIZE_BYTES == 9_398_198
    assert NUM_LANDMARKS == 33
    assert len(POSE_MODEL_SHA256) == 64
    assert POSE_MODEL_SHA256 == POSE_MODEL_SHA256.lower()


def test_resolve_model_path_defaults_to_vendored_bundle() -> None:
    path = resolve_model_path()
    assert path.name == POSE_MODEL_FILENAME
    assert path.parent.name == "models"
    assert path.is_file()


def test_resolve_model_path_honours_override(tmp_path: Path) -> None:
    override = tmp_path / "other.task"
    assert resolve_model_path(override) == override


def test_vendored_asset_passes_verification() -> None:
    verify_model_asset(resolve_model_path())


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="missing"):
        verify_model_asset(tmp_path / "nope.task")


def test_zero_byte_copy_raises(tmp_path: Path) -> None:
    empty = tmp_path / "empty.task"
    empty.write_bytes(b"")
    with pytest.raises(RuntimeError, match="size mismatch"):
        verify_model_asset(empty)


def test_truncated_copy_raises(tmp_path: Path) -> None:
    truncated = tmp_path / "truncated.task"
    truncated.write_bytes(resolve_model_path().read_bytes()[: POSE_MODEL_SIZE_BYTES - 1])
    with pytest.raises(RuntimeError, match="size mismatch"):
        verify_model_asset(truncated)


def test_lfs_pointer_substitution_raises(tmp_path: Path) -> None:
    pointer = tmp_path / "pointer.task"
    pointer.write_bytes(
        b"version https://git-lfs.github.com/spec/v1\noid sha256:deadbeef\nsize 9398198\n"
    )
    with pytest.raises(RuntimeError, match="size mismatch"):
        verify_model_asset(pointer)


def test_right_size_wrong_digest_raises(tmp_path: Path) -> None:
    corrupted = tmp_path / "corrupted.task"
    data = bytearray(resolve_model_path().read_bytes())
    data[-1] ^= 0xFF  # same length, one flipped byte
    corrupted.write_bytes(bytes(data))
    assert corrupted.stat().st_size == POSE_MODEL_SIZE_BYTES
    with pytest.raises(RuntimeError, match="digest mismatch"):
        verify_model_asset(corrupted)
