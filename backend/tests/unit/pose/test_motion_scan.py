"""Stage 5's motion scan: where the analysis window gets centred.

PIPELINE.md 5.1. This file exists because of a specific finding: no test in
``test_video_io.py`` would have caught a 2.6 s miscentre. That suite covers the
window arithmetic, absolute-versus-relative PTS and the frame cap -- nothing
asserts *where* the scan points, and nothing controls keyframe spacing.

Every fixture below therefore FORCES ``gop_size``, so keyframe spacing is an
input to the test rather than an accident of whichever file was to hand, and
every clip's correct answer is known by construction.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest

from app.pose.video_io import (
    MOTION_REFINE_FRAME_BUDGET,
    dense_motion_centre_s,
    keyframe_motion_profile,
    motion_scan_centre_s,
    open_pose_stream,
    probe_clip,
)
from tests.unit.seam_clips import step_centre_at, write_clip

#: The instant the synthetic ball starts moving.
EVENT_S: Final[float] = 3.45
EVENT_DURATION_S: Final[float] = 0.3


def _clip(
    path: Path,
    *,
    gop_size: int,
    frame_count: int = 300,
    fps: int = 25,
    move_start_s: float = EVENT_S,
    move_duration_s: float = EVENT_DURATION_S,
) -> Path:
    return write_clip(
        path,
        frame_count=frame_count,
        fps=fps,
        gop_size=gop_size,
        centre_fn=step_centre_at(
            move_start_s=move_start_s,
            move_duration_s=move_duration_s,
            fps=fps,
            width=160,
            height=96,
        ),
    )


@pytest.fixture(scope="module")
def gop3_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """12 s, 25 fps, keyframes every 3 s: the MEASURED corpus case.

    The displaced step starts at t = 3.45 s -- 0.45 s into the [3, 6) bucket --
    so a function that reports either END of its winning bucket is wrong by
    0.45 s or 2.55 s, and only intra-bucket refinement can be right.
    """
    return _clip(tmp_path_factory.mktemp("motion_scan") / "gop3.mp4", gop_size=75)


@pytest.fixture(scope="module")
def gop1_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """12 s, 25 fps, keyframes every 1 s: the case Stage 5 originally ASSUMED."""
    return _clip(tmp_path_factory.mktemp("motion_scan") / "gop1.mp4", gop_size=25)


@pytest.fixture(scope="module")
def single_keyframe_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """12 s with exactly one keyframe: no bucket exists at all."""
    return _clip(tmp_path_factory.mktemp("motion_scan") / "single.mp4", gop_size=100_000)


@pytest.fixture(scope="module")
def long_single_keyframe_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """58 s, one keyframe: worst case for both the frame budget and the stride."""
    return _clip(
        tmp_path_factory.mktemp("motion_scan") / "long.mp4",
        gop_size=100_000,
        frame_count=580,
        fps=10,
        move_start_s=30.0,
        move_duration_s=1.0,
    )


def test_the_fixture_gop_is_actually_forced(gop3_clip: Path, gop1_clip: Path) -> None:
    """If x264 ignored the forced spacing, every assertion below is meaningless."""
    buckets_3 = keyframe_motion_profile(gop3_clip, probe_clip(gop3_clip))
    buckets_1 = keyframe_motion_profile(gop1_clip, probe_clip(gop1_clip))
    assert [b.end_s - b.start_s for b in buckets_3] == pytest.approx([3.0] * 3, abs=0.05)
    assert len(buckets_1) >= 10


def test_refinement_locates_a_sub_gop_event(gop3_clip: Path) -> None:
    """THE regression test: it fails before the fix and passes after.

    The old function returned the winning bucket's END, 6.0 s -- an error of
    2.55 s on a clip whose event is 0.45 s into that bucket.
    """
    result = motion_scan_centre_s(gop3_clip, probe_clip(gop3_clip))
    assert result is not None
    assert abs(result.centre_s - EVENT_S) <= 0.35


def test_a_bucket_endpoint_is_never_returned(gop3_clip: Path) -> None:
    """The structural rule, asserted directly rather than via a tolerance.

    A regression to endpoint-reporting is caught here even if the tolerance test
    above happens to survive it. The assertion is "not a bucket boundary" rather
    than "strictly interior to the winning bucket": the refinement span is the
    bucket widened by half a GOP on each side, so a centre landing just outside
    the bucket is legitimate, and only a boundary VALUE is evidence of the old
    behaviour.
    """
    probe = probe_clip(gop3_clip)
    buckets = keyframe_motion_profile(gop3_clip, probe)
    result = motion_scan_centre_s(gop3_clip, probe)
    assert result is not None
    assert result.source == "refined"

    boundaries = {b.start_s for b in buckets} | {b.end_s for b in buckets}
    for boundary in boundaries:
        assert abs(result.centre_s - boundary) > 1e-6, "a keyframe PTS came back verbatim"

    winner = max(buckets, key=lambda bucket: bucket.energy)
    guard = result.observed_gop_s / 2.0
    assert winner.start_s - guard <= result.centre_s <= winner.end_s + guard


def test_sparse_keyframes_take_the_refined_path(gop3_clip: Path) -> None:
    """Sparse is not a separate algorithm; it is the same primitive, wider span."""
    result = motion_scan_centre_s(gop3_clip, probe_clip(gop3_clip))
    assert result is not None
    assert result.source == "refined"
    assert result.observed_gop_s == pytest.approx(3.0, abs=0.2)
    assert result.keyframe_count == 4
    assert result.frames_decoded <= MOTION_REFINE_FRAME_BUDGET


def test_dense_keyframes_reach_the_same_answer(gop1_clip: Path) -> None:
    """One primitive over different spans: a 1 s GOP must not answer differently."""
    result = motion_scan_centre_s(gop1_clip, probe_clip(gop1_clip))
    assert result is not None
    assert result.source == "refined"
    assert abs(result.centre_s - EVENT_S) <= 0.35


def test_single_keyframe_clip_degrades_to_dense_not_none(
    single_keyframe_clip: Path,
) -> None:
    """One keyframe used to mean ``None`` -- a head-of-clip guess dressed up."""
    probe = probe_clip(single_keyframe_clip)
    assert keyframe_motion_profile(single_keyframe_clip, probe) == []

    result = motion_scan_centre_s(single_keyframe_clip, probe)
    assert result is not None
    assert result.source == "dense"
    assert result.keyframe_count == 1
    assert abs(result.centre_s - EVENT_S) <= 0.5


def test_decode_budget_is_bounded(long_single_keyframe_clip: Path) -> None:
    """Asserted on the counter, never on wall time: CI timing is a flake source."""
    result = motion_scan_centre_s(
        long_single_keyframe_clip, probe_clip(long_single_keyframe_clip)
    )
    assert result is not None
    assert result.frames_decoded <= MOTION_REFINE_FRAME_BUDGET


def test_motion_scan_coarse_flag_is_raised_when_the_stride_is_wide(
    long_single_keyframe_clip: Path, gop3_clip: Path
) -> None:
    """The degradation is a contract, not a courtesy."""
    coarse = open_pose_stream(long_single_keyframe_clip)
    assert coarse.motion_scan is not None
    assert coarse.motion_scan.source == "dense"
    assert coarse.motion_scan_coarse, "58 s / 48 is a 1.2 s stride; that owes a flag"

    refined = open_pose_stream(gop3_clip)
    assert refined.motion_scan is not None
    assert refined.motion_scan.source == "refined"
    assert not refined.motion_scan_coarse, "a refined scan is not coarse"


def test_dense_motion_centre_respects_an_explicit_span(gop3_clip: Path) -> None:
    """The primitive is span-bounded: it cannot answer outside what it was given."""
    probe = probe_clip(gop3_clip)
    found = dense_motion_centre_s(gop3_clip, probe, span_start_s=7.0, span_end_s=11.0)
    assert found is not None
    centre_s, frames = found
    assert 7.0 <= centre_s <= 11.0
    assert frames <= MOTION_REFINE_FRAME_BUDGET


def test_the_window_is_centred_on_the_event_not_on_a_bucket_boundary(
    gop3_clip: Path,
) -> None:
    """End to end through Stage 5: the window the rest of the pipeline gets."""
    stream = open_pose_stream(gop3_clip)
    assert stream.motion_scan_used
    midpoint = (stream.analysis_window_start_s + stream.analysis_window_end_s) / 2.0
    assert abs(midpoint - EVENT_S) <= 1.0
