"""Stage 5: decode, orientation correction, analysis-window location, sampling.

IMPURE: PyAV demuxes and decodes, cv2 resizes. Everything downstream of the
generator this module yields is pure.

THE ONE THING TO GET RIGHT
--------------------------
**The generator yields ABSOLUTE PTS in the source file, not window-relative
time.** ``extract_keypoints`` subtracts ``window_start_s`` itself
(``extractor.py:215-221`` -> ``sequence.py:47``) and then stores the
*unmodified* value as the retained full-precision PTS
(``extractor.py:227-228``). Yielding window-relative timestamps subtracts the
window start twice.

That bug is invisible on every short test clip, because a clip under
``motion_scan_threshold_s`` uses the whole file and ``window_start_s`` is 0.0.
On a long clip with a motion-scan-located window the MediaPipe timestamps go
negative, the strict-monotonic clamp at ``sequence.py:48`` masks it into a
plausible increasing sequence, and ``NormalizedSequence.timestamps_s`` ends up
window-relative -- which silently breaks
``ContactDetection.contact_absolute_time_s`` and therefore the entire
ball-speed seek. The yielded value is named ``absolute_pts_s`` throughout this
module for that reason.

COLOUR SPACE
------------
This module yields **RGB**, because MediaPipe is fed ``mp.ImageFormat.SRGB``
(``extractor.py:146-147``). The ball detector consumes **BGR**
(``detector.py`` uses ``COLOR_BGR2GRAY`` / ``COLOR_BGR2HSV``) and gets its own
decode pass in ``app/ball/frames.py``. Handing one array to both does not
raise: it silently feeds the HSV colour mask -- the thing that finds a yellow
ball -- a hue channel computed from the wrong axis. The two streams are
separate anyway (different resolution, different rate, different extent), so
the colour split costs nothing.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

import av
import cv2
import numpy as np
from av.sidedata.sidedata import Type as SideDataType

from app.models.enums import ErrorCode
from app.models.responses import VideoMeta

# --------------------------------------------------------------------------- #
# Constants -- PIPELINE.md Stage 5. No magic numbers below this block.
# --------------------------------------------------------------------------- #

#: Fixed pose-analysis rate. PIPELINE.md Stage 5: 30, not 24, not 60.
ANALYSIS_FPS: Final[float] = 30.0

#: Pose-stream long edge. BlazePose resizes to 256x256 internally anyway.
POSE_LONG_EDGE_PX: Final[int] = 640

#: CAL_SPACE long edge (PIPELINE.md 10.1). Exactly 2x the pose stream.
CAL_SPACE_LONG_EDGE_PX: Final[int] = 1280

#: Hard cap on frames handed to MediaPipe (8.0 s at 30 fps).
MAX_ANALYSIS_FRAMES: Final[int] = 240

ANALYSIS_WINDOW_S: Final[float] = 8.0

MIN_CLIP_S: Final[float] = 2.0
MAX_CLIP_S: Final[float] = 60.0

#: Clips longer than this get the keyframe motion scan; shorter ones use the
#: whole clip.
MOTION_SCAN_THRESHOLD_S: Final[float] = 10.0
MOTION_SCAN_LONG_EDGE_PX: Final[int] = 160

#: Hard bound on frames SAMPLED by the dense refinement pass (PIPELINE.md
#: 5.1.1). 48 samples an ~8 s refinement span at about 6 Hz, which localises
#: a 0.3-0.5 s strike to ~0.2 s. The bound is a frame count, not a wall time,
#: so the cost cannot grow with clip length.
MOTION_REFINE_FRAME_BUDGET: Final[int] = 48

#: A whole-clip dense scan striding wider than this is coarse enough that the
#: response must say so, via ``PoseQuality.flags``.
MOTION_SCAN_COARSE_STRIDE_S: Final[float] = 0.5
MOTION_SCAN_COARSE_FLAG: Final[str] = "motion_scan_coarse"

_LEGAL_ROTATIONS: Final[tuple[int, ...]] = (0, 90, 180, 270)


class VideoDecodeError(Exception):
    """A Stage 5 failure carrying the ``ErrorCode`` the orchestrator must raise."""

    def __init__(self, error_code: ErrorCode, message: str) -> None:
        self.error_code: ErrorCode = error_code
        super().__init__(f"{error_code.value}: {message}")


# --------------------------------------------------------------------------- #
# PURE helpers
# --------------------------------------------------------------------------- #


def rotation_from_display_matrix(matrix: Sequence[int]) -> int:
    """Clockwise degrees to APPLY to a decoded frame, from a 9-int32 matrix.

    Mirrors ffmpeg's ``av_display_rotation_get``: the matrix entries are 16.16
    fixed point, the angle is ``atan2(m[1], m[0])`` after per-axis scale
    normalization, and ffmpeg NEGATES it. ffmpeg's value is the rotation the
    frame must be turned **counter-clockwise** to be displayed upright, so the
    clockwise rotation we apply is its negation again.

    Snapped to {0, 90, 180, 270}: a phone writes exactly those, and an
    arbitrary angle would need a resampling rotate this pipeline does not do.
    """
    values = [int(v) for v in matrix]
    if len(values) != 9:
        raise ValueError("a display matrix has exactly 9 entries")
    conv = [v / 65536.0 for v in values]
    scale_x = math.hypot(conv[0], conv[3])
    scale_y = math.hypot(conv[1], conv[4])
    if scale_x == 0.0 or scale_y == 0.0:
        return 0
    ffmpeg_deg = -math.degrees(math.atan2(conv[1] / scale_y, conv[0] / scale_x))
    clockwise = int(round(-ffmpeg_deg / 90.0)) * 90
    return clockwise % 360


def apply_rotation(frame: np.ndarray, rotation_deg: int) -> np.ndarray:
    """Rotate an (H, W, C) frame CLOCKWISE by ``rotation_deg``.

    ``np.rot90`` turns counter-clockwise, so the k is the complement. The result
    is made contiguous: ``rot90`` returns a strided view, and both cv2 and
    ``mp.Image`` want contiguous buffers.
    """
    if rotation_deg not in _LEGAL_ROTATIONS:
        raise ValueError(f"rotation_deg must be one of {_LEGAL_ROTATIONS}")
    if rotation_deg == 0:
        return np.ascontiguousarray(frame)
    return np.ascontiguousarray(np.rot90(frame, k=(4 - rotation_deg // 90) % 4))


def scaled_size(width: int, height: int, long_edge_px: int) -> tuple[int, int]:
    """``(width, height)`` scaled so the long edge is ``long_edge_px``.

    Never upscales: a 480 px clip stays 480 px rather than being interpolated
    into fake detail.
    """
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    longest = max(width, height)
    if longest <= long_edge_px:
        return int(width), int(height)
    scale = float(long_edge_px) / float(longest)
    return max(1, round(width * scale)), max(1, round(height * scale))


def resize_to_long_edge(frame: np.ndarray, long_edge_px: int) -> np.ndarray:
    """Downscale with INTER_AREA, the correct filter for shrinking."""
    height, width = frame.shape[:2]
    target_w, target_h = scaled_size(width, height, long_edge_px)
    if (target_w, target_h) == (width, height):
        return np.ascontiguousarray(frame)
    return np.ascontiguousarray(
        cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_AREA)
    )


def analysis_window_bounds(
    duration_s: float,
    centre_s: float | None,
    *,
    window_s: float = ANALYSIS_WINDOW_S,
) -> tuple[float, float]:
    """The ``[start, end]`` analysis window, clamped inside ``[0, duration_s]``.

    ``centre_s is None`` means "no motion scan ran" -- the window starts at 0.
    """
    if duration_s <= window_s:
        return 0.0, float(duration_s)
    if centre_s is None:
        return 0.0, float(window_s)
    start = float(centre_s) - window_s / 2.0
    start = min(max(start, 0.0), float(duration_s) - window_s)
    return start, start + window_s


def target_times(start_s: float, end_s: float, *, fps: float, cap: int) -> list[float]:
    """``t_k = start + k/fps`` inside ``[start, end]``, at most ``cap`` entries."""
    if fps <= 0.0:
        raise ValueError("fps must be positive")
    span = max(0.0, float(end_s) - float(start_s))
    count = min(int(cap), int(math.floor(span * fps)) + 1)
    return [float(start_s) + index / float(fps) for index in range(max(count, 0))]


# --------------------------------------------------------------------------- #
# IMPURE -- probe
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ClipProbe:
    """What one cheap pass over the container can establish."""

    duration_s: float
    source_fps: float
    rotation_deg: int
    #: Post-rotation, pre-downscale.
    width_px: int
    height_px: int
    codec_name: str


def _display_rotation(stream: "av.video.stream.VideoStream", container: "av.container.InputContainer") -> int:
    """Read the container display matrix.

    PyAV 18.1.0 exposes NO stream-level side-data accessor -- ``VideoStream``
    has ``set_display_matrix`` / ``set_display_rotation`` and no getter -- so the
    matrix is read from the first decoded FRAME's side data, where libavcodec
    propagates it. ``stream.metadata['rotate']`` is checked first because older
    muxes expose it there and it costs nothing.
    """
    rotate = stream.metadata.get("rotate")
    if rotate is not None:
        try:
            return int(round(float(rotate) / 90.0)) * 90 % 360
        except (TypeError, ValueError):
            pass
    for frame in container.decode(stream):
        side = frame.side_data.get(SideDataType.DISPLAYMATRIX)
        if side is None:
            return 0
        raw = np.frombuffer(bytes(side), dtype=np.int32)
        if raw.size < 9:
            return 0
        return rotation_from_display_matrix(raw[:9].tolist())
    return 0


def probe_clip(path: Path) -> ClipProbe:
    """Open the container once and read duration, fps, rotation and size.

    ``source_fps`` is measured from ``guessed_rate`` (which PyAV derives from
    PTS deltas when the nominal rate is absent) rather than from metadata alone.

    Raises:
        VideoDecodeError: ``NO_VIDEO_STREAM``, ``UNSUPPORTED_CODEC``,
            ``DECODE_FAILED``, ``VIDEO_TOO_SHORT`` or ``VIDEO_TOO_LONG``.
    """
    try:
        container = av.open(str(path))
    except av.FFmpegError as exc:
        raise VideoDecodeError(ErrorCode.DECODE_FAILED, f"could not open: {exc}") from exc
    except OSError as exc:
        raise VideoDecodeError(ErrorCode.DECODE_FAILED, f"could not open: {exc}") from exc

    try:
        if not container.streams.video:
            raise VideoDecodeError(ErrorCode.NO_VIDEO_STREAM, "no video stream")
        stream = container.streams.video[0]
        codec = stream.codec_context
        if codec is None or codec.name is None:
            raise VideoDecodeError(ErrorCode.UNSUPPORTED_CODEC, "no decodable codec")

        duration_s = _stream_duration_s(stream, container)
        if duration_s <= 0.0:
            raise VideoDecodeError(ErrorCode.DECODE_FAILED, "clip has no measurable duration")
        if duration_s < MIN_CLIP_S:
            raise VideoDecodeError(
                ErrorCode.VIDEO_TOO_SHORT, f"{duration_s:.2f}s is under {MIN_CLIP_S}s"
            )
        if duration_s > MAX_CLIP_S:
            raise VideoDecodeError(
                ErrorCode.VIDEO_TOO_LONG, f"{duration_s:.2f}s is over {MAX_CLIP_S}s"
            )

        rate = stream.guessed_rate or stream.average_rate or stream.base_rate
        source_fps = float(rate) if rate else 0.0
        if source_fps <= 0.0:
            raise VideoDecodeError(ErrorCode.DECODE_FAILED, "clip has no measurable frame rate")

        rotation = _display_rotation(stream, container)
        width, height = int(codec.width), int(codec.height)
        if width <= 0 or height <= 0:
            raise VideoDecodeError(ErrorCode.DECODE_FAILED, "clip has no frame dimensions")
        if rotation in (90, 270):
            width, height = height, width
        return ClipProbe(
            duration_s=duration_s,
            source_fps=source_fps,
            rotation_deg=rotation,
            width_px=width,
            height_px=height,
            codec_name=str(codec.name),
        )
    except av.FFmpegError as exc:
        raise VideoDecodeError(ErrorCode.DECODE_FAILED, str(exc)) from exc
    finally:
        container.close()


def _stream_duration_s(
    stream: "av.video.stream.VideoStream", container: "av.container.InputContainer"
) -> float:
    """Seconds, from the stream's own duration, else the container's."""
    if stream.duration is not None and stream.time_base is not None:
        return float(stream.duration) * float(stream.time_base)
    if container.duration is not None:
        return float(container.duration) / float(av.time_base)
    return 0.0


# --------------------------------------------------------------------------- #
# IMPURE -- motion scan (keyframe profile + bounded dense refinement)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class KeyframeBucket:
    """The interval between two consecutive keyframes, and its motion energy."""

    start_s: float
    end_s: float
    energy: float

    @property
    def midpoint_s(self) -> float:
        """The only point in the bucket this module is allowed to report."""
        return (self.start_s + self.end_s) / 2.0


@dataclass(frozen=True)
class MotionScanResult:
    """Where the swing is, and how much the caller should trust that.

    ``source`` distinguishes a located swing from a bucket boundary -- the
    distinction whose absence let a 2.6 s miscentre ship unnoticed
    (PIPELINE.md 5.1):

    - ``"refined"``  -- a keyframe bucket won and a bounded dense pass located
      the event inside it. The normal path.
    - ``"dense"``    -- fewer than two keyframes, so there was no bucket; the
      dense pass ran over the whole clip at ``duration_s / budget`` stride.
      Coarse, and deliberately legible as such.
    - ``"keyframe"`` -- a bucket won but the dense pass produced nothing; the
      answer is the winning bucket's MIDPOINT, never an endpoint.

    ``frames_decoded`` counts frames SAMPLED BY THE DENSE PASS only. That is the
    quantity ``MOTION_REFINE_FRAME_BUDGET`` bounds; the keyframe pass is
    reported separately as ``keyframe_count``.
    """

    centre_s: float
    source: Literal["refined", "dense", "keyframe"]
    keyframe_count: int
    observed_gop_s: float
    frames_decoded: int


def _motion_frame(frame: "av.VideoFrame", rotation_deg: int) -> np.ndarray:
    """One decoded frame -> rotated, 160 px, float32 luma. The energy unit.

    Both passes go through here, so pass 2 measures the same physical quantity
    as pass 1 -- only at finer spacing.
    """
    gray = frame.to_ndarray(format="gray")
    rotated = apply_rotation(gray[:, :, None], rotation_deg)[:, :, 0]
    return resize_to_long_edge(rotated, MOTION_SCAN_LONG_EDGE_PX).astype(np.float32)


def _energy(current: np.ndarray, previous: np.ndarray) -> float:
    """Mean absolute pixel difference between two prepared frames."""
    return float(np.mean(np.abs(current - previous)))


def _best_pair_midpoint(samples: Sequence[tuple[float, np.ndarray]]) -> float | None:
    """Midpoint of the highest-energy ADJACENT pair. Never an endpoint.

    The structural fix of PIPELINE.md 5.1, expressed once and applied at every
    level: reporting either end of the winning interval throws away where inside
    it the motion actually happened.
    """
    if len(samples) < 2:
        return None
    best_energy = -1.0
    best_midpoint: float | None = None
    for index in range(1, len(samples)):
        previous_time, previous_frame = samples[index - 1]
        current_time, current_frame = samples[index]
        if previous_frame.shape != current_frame.shape:
            continue
        energy = _energy(current_frame, previous_frame)
        if energy > best_energy:
            best_energy = energy
            best_midpoint = (previous_time + current_time) / 2.0
    return best_midpoint


def _keyframe_scan(path: Path, probe: ClipProbe) -> tuple[int, list[KeyframeBucket]]:
    """``(keyframe_count, buckets)`` from one I-frame-only pass.

    The count is carried separately because a one-keyframe clip and a
    zero-keyframe clip both yield an empty bucket list and are not the same
    thing.
    """
    try:
        container = av.open(str(path))
    except (av.FFmpegError, OSError):
        return 0, []
    try:
        if not container.streams.video:
            return 0, []
        stream = container.streams.video[0]
        stream.codec_context.skip_frame = "NONKEY"
        time_base = float(stream.time_base) if stream.time_base else 0.0
        if time_base <= 0.0:
            return 0, []

        keyframe_count = 0
        buckets: list[KeyframeBucket] = []
        previous: tuple[float, np.ndarray] | None = None
        for frame in container.decode(stream):
            if frame.pts is None:
                continue
            absolute_pts_s = float(frame.pts) * time_base
            small = _motion_frame(frame, probe.rotation_deg)
            keyframe_count += 1
            if previous is not None and previous[1].shape == small.shape:
                buckets.append(
                    KeyframeBucket(
                        start_s=previous[0],
                        end_s=absolute_pts_s,
                        energy=_energy(small, previous[1]),
                    )
                )
            previous = (absolute_pts_s, small)
        return keyframe_count, buckets
    except av.FFmpegError:
        return 0, []
    finally:
        container.close()


def keyframe_motion_profile(path: Path, probe: ClipProbe) -> list[KeyframeBucket]:
    """Pass 1: every keyframe-to-keyframe bucket with its motion energy.

    ``skip_frame = "NONKEY"`` decodes I-frames only, at 160 px long edge. The
    WHOLE profile is retained rather than only the argmax, because the winning
    bucket's endpoints are what bound pass 2's decode.
    """
    return _keyframe_scan(path, probe)[1]


def dense_motion_centre_s(
    path: Path,
    probe: ClipProbe,
    *,
    span_start_s: float,
    span_end_s: float,
    max_frames_decoded: int = MOTION_REFINE_FRAME_BUDGET,
) -> tuple[float, int] | None:
    """``(centre_s, frames_sampled)`` over ``[span_start_s, span_end_s]``.

    Re-opens the container WITHOUT ``skip_frame``, seeks to the span start and
    decodes forward to its end, sampling at ``span / max_frames_decoded`` so the
    sampled count stays inside the budget. The returned centre is the midpoint
    of the highest-energy adjacent SAMPLED pair.

    One primitive, invoked over different spans: a winning bucket plus its guard
    bands, or -- when there is no bucket -- the whole clip. There is no density
    test in here, deliberately: two paths behind two thresholds is the shape
    that produced this defect (PIPELINE.md 5.1.1).

    Returns ``None`` when fewer than two frames could be sampled.
    """
    if max_frames_decoded < 2:
        raise ValueError("max_frames_decoded must admit at least one pair")
    span_start = max(0.0, float(span_start_s))
    span_end = min(float(probe.duration_s), float(span_end_s))
    if span_end <= span_start:
        return None
    stride_s = (span_end - span_start) / float(max_frames_decoded)
    if stride_s <= 0.0:
        return None

    samples: list[tuple[float, np.ndarray]] = []
    try:
        container = av.open(str(path))
    except (av.FFmpegError, OSError):
        return None
    try:
        if not container.streams.video:
            return None
        stream = container.streams.video[0]
        # The budget bounds SAMPLED frames; the decoder still runs every frame
        # of the span (PIPELINE.md 5.1.2), and that sequential decode is the
        # whole cost of this pass. Frame-threading it is what keeps a 1080p
        # refinement span inside the wall-time acceptance bound.
        stream.thread_type = "AUTO"
        time_base = float(stream.time_base) if stream.time_base else 0.0
        if time_base <= 0.0:
            return None
        if span_start > 0.0:
            try:
                container.seek(
                    int(span_start / time_base), stream=stream, backward=True, any_frame=False
                )
            except av.FFmpegError:
                container.seek(0)

        next_target = span_start
        for frame in container.decode(stream):
            if frame.pts is None:
                continue
            absolute_pts_s = float(frame.pts) * time_base
            if absolute_pts_s > span_end:
                break
            if absolute_pts_s < next_target:
                continue
            samples.append((absolute_pts_s, _motion_frame(frame, probe.rotation_deg)))
            if len(samples) >= max_frames_decoded:
                break
            # Advance past the frame just taken, so a stride shorter than the
            # source frame interval cannot sample the same frame twice.
            next_target = max(next_target + stride_s, absolute_pts_s + stride_s / 2.0)
    except av.FFmpegError:
        return None
    finally:
        container.close()

    centre_s = _best_pair_midpoint(samples)
    if centre_s is None:
        return None
    return centre_s, len(samples)


def motion_scan_centre_s(path: Path, probe: ClipProbe) -> MotionScanResult | None:
    """Locate the swing: keyframe profile, then bounded refinement inside it.

    Pass 1 profiles every keyframe bucket. The highest-energy bucket wins, and
    pass 2 re-decodes that bucket WIDENED BY HALF A GOP ON EACH SIDE -- the
    guard band that stops a swing straddling a keyframe from being localised
    into the wrong half -- sampling at most ``MOTION_REFINE_FRAME_BUDGET``
    frames.

    With fewer than two keyframes no bucket exists, and the scan degrades to a
    whole-clip dense pass at ``duration_s / budget`` stride rather than to the
    head-of-clip guess today's ``None`` produces. ``None`` comes back only when
    even that decodes nothing usable.
    """
    keyframe_count, buckets = _keyframe_scan(path, probe)

    if not buckets:
        dense = dense_motion_centre_s(path, probe, span_start_s=0.0, span_end_s=probe.duration_s)
        if dense is None:
            return None
        centre_s, frames_decoded = dense
        return MotionScanResult(
            centre_s=centre_s,
            source="dense",
            keyframe_count=keyframe_count,
            observed_gop_s=float(probe.duration_s),
            frames_decoded=frames_decoded,
        )

    observed_gop_s = (buckets[-1].end_s - buckets[0].start_s) / float(len(buckets))
    winner = max(buckets, key=lambda bucket: bucket.energy)
    guard_s = observed_gop_s / 2.0
    refined = dense_motion_centre_s(
        path,
        probe,
        span_start_s=winner.start_s - guard_s,
        span_end_s=winner.end_s + guard_s,
    )
    if refined is None:
        # Pass 1's degenerate fallback: the MIDPOINT, because the endpoint rule
        # applies at every level, including here.
        return MotionScanResult(
            centre_s=winner.midpoint_s,
            source="keyframe",
            keyframe_count=keyframe_count,
            observed_gop_s=observed_gop_s,
            frames_decoded=0,
        )
    centre_s, frames_decoded = refined
    return MotionScanResult(
        centre_s=centre_s,
        source="refined",
        keyframe_count=keyframe_count,
        observed_gop_s=observed_gop_s,
        frames_decoded=frames_decoded,
    )


def motion_scan_is_coarse(result: MotionScanResult, duration_s: float) -> bool:
    """Whether the scan ran degraded enough to owe the response a flag.

    True for a whole-clip dense pass whose stride exceeded
    ``MOTION_SCAN_COARSE_STRIDE_S``. Same principle as
    ``rotation_retry_applied``: a degraded path must cost something visible in
    the response, not only in the logs (PIPELINE.md 5.1.1).
    """
    if result.source != "dense" or result.frames_decoded < 1:
        return False
    stride_s = float(duration_s) / float(MOTION_REFINE_FRAME_BUDGET)
    return stride_s > MOTION_SCAN_COARSE_STRIDE_S


# --------------------------------------------------------------------------- #
# IMPURE -- the pose stream
# --------------------------------------------------------------------------- #


@dataclass
class FrameCounts:
    """Mutable running totals, filled in as the generator is consumed."""

    decoded: int = 0
    sampled: int = 0


@dataclass
class PoseStream:
    """Stage 5's output: the frame source plus everything ``VideoMeta`` needs.

    ``VideoMeta.frames_decoded`` / ``frames_sampled`` are not knowable until the
    generator has been consumed -- the stream is streaming precisely so 240
    frames are never resident at once -- so ``video_meta()`` is a method that
    must be called AFTER iteration, not a field filled in before it.
    """

    frames: Iterator[tuple[float, np.ndarray]]
    duration_s: float
    source_fps: float
    rotation_deg: int
    width_px: int
    height_px: int
    cal_space_width_px: int
    cal_space_height_px: int
    analysis_window_start_s: float
    analysis_window_end_s: float
    motion_scan_used: bool
    #: The scan's own account of itself, or ``None`` when it did not run. The
    #: caller needs ``source`` to tell a located swing from a bucket boundary.
    motion_scan: MotionScanResult | None = None
    #: True when the scan ran degraded enough to owe ``PoseQuality.flags`` a
    #: ``motion_scan_coarse`` entry. Stage 7 appends it; Stage 5 decides it,
    #: because only Stage 5 knows the stride.
    motion_scan_coarse: bool = False
    counts: FrameCounts = field(default_factory=FrameCounts)

    def video_meta(self) -> VideoMeta:
        """Build ``VideoMeta``. Call after the generator is exhausted."""
        return VideoMeta(
            duration_s=self.duration_s,
            source_fps=self.source_fps,
            analysis_fps=ANALYSIS_FPS,
            rotation_deg=self.rotation_deg,
            width_px=self.width_px,
            height_px=self.height_px,
            cal_space_width_px=self.cal_space_width_px,
            cal_space_height_px=self.cal_space_height_px,
            frames_decoded=self.counts.decoded,
            frames_sampled=self.counts.sampled,
            analysis_window_start_s=self.analysis_window_start_s,
            analysis_window_end_s=self.analysis_window_end_s,
            motion_scan_used=self.motion_scan_used,
        )


def _sample_rgb_frames(
    path: Path,
    probe: ClipProbe,
    targets: list[float],
    long_edge_px: int,
    counts: FrameCounts,
) -> Iterator[tuple[float, np.ndarray]]:
    """Yield ``(absolute_pts_s, frame_rgb)`` for the nearest frame to each target.

    Streaming and one frame deep: exactly one decoded frame is retained so the
    nearest of ``(previous, current)`` can be chosen when the target is crossed.
    240 frames at 640x360x3 is 166 MB held at once, which is why this is a
    generator and not a list (PIPELINE.md Stage 5: "a hard requirement, not an
    optimization").

    ``absolute_pts_s`` is the frame's real PTS in the SOURCE file. It is not
    window-relative and it is not ``k / 30`` -- see the module docstring.
    """
    if not targets:
        return
    container = av.open(str(path))
    try:
        stream = container.streams.video[0]
        # Frame-threaded decode, as the dense motion scan already does. This is
        # the decode every analysis pays, and single-threaded it was the largest
        # single cost of Stage 5 (7.8 s -> 1.2 s for a 350-frame 1080p clip on a
        # 12-core host). Output is bit-identical: threading changes when frames
        # are decoded, never what they decode to.
        stream.thread_type = "AUTO"
        time_base = float(stream.time_base) if stream.time_base else 0.0
        if time_base <= 0.0:
            raise VideoDecodeError(ErrorCode.DECODE_FAILED, "stream has no time base")

        # Seek to slightly before the window so a long clip does not decode
        # everything ahead of it. Seeking lands on a keyframe at or before the
        # request, so frames before the window are decoded and dropped.
        first_target = targets[0]
        if first_target > 0.0:
            try:
                container.seek(
                    int(first_target / time_base), stream=stream, backward=True, any_frame=False
                )
            except av.FFmpegError:
                container.seek(0)

        index = 0
        previous: tuple[float, np.ndarray] | None = None
        # The last frame converted, keyed by its PTS. A source slower than the
        # 30 Hz target grid (25 fps is common) is nearest-neighbour sampled, so
        # one decoded frame can be the nearest to two consecutive targets. It
        # was converted (full-res RGB + resize, the dominant per-frame cost)
        # once per target; now it is converted once and copied for each reuse.
        # The first yield shares its buffer with this cache, which is safe only
        # because the consumer (extract_keypoints) treats frames as read-only --
        # it wraps them in mp.Image and writes nothing back. A consumer that
        # mutates frames in place must not be added without revisiting this.
        converted: tuple[float, np.ndarray] | None = None
        for frame in container.decode(stream):
            if index >= len(targets):
                break
            if frame.pts is None:
                continue
            counts.decoded += 1
            absolute_pts_s = float(frame.pts) * time_base
            current = (absolute_pts_s, frame)

            while index < len(targets) and absolute_pts_s >= targets[index]:
                chosen = current
                if previous is not None and abs(previous[0] - targets[index]) <= abs(
                    absolute_pts_s - targets[index]
                ):
                    chosen = previous
                counts.sampled += 1
                if converted is not None and converted[0] == chosen[0]:
                    rgb = converted[1].copy()
                else:
                    rgb = _to_pose_rgb(chosen[1], probe.rotation_deg, long_edge_px)
                    converted = (chosen[0], rgb)
                yield chosen[0], rgb
                index += 1
            previous = current

        # The clip ended before the last target: emit the final decoded frame
        # once for the first unmet target rather than silently truncating a
        # window the caller believes was covered.
        if index < len(targets) and previous is not None and index == 0:
            counts.sampled += 1
            yield previous[0], _to_pose_rgb(previous[1], probe.rotation_deg, long_edge_px)
    except av.FFmpegError as exc:
        raise VideoDecodeError(ErrorCode.DECODE_FAILED, str(exc)) from exc
    finally:
        container.close()


def _to_pose_rgb(frame: "av.VideoFrame", rotation_deg: int, long_edge_px: int) -> np.ndarray:
    """One decoded frame -> rotated, downscaled, contiguous uint8 **RGB**.

    ``format="rgb24"`` is the colour-space decision, made here at the seam and
    nowhere else. The ball stream asks PyAV for ``bgr24`` in its own pass
    (``app/ball/frames.py``); neither converts the other's buffer.
    """
    rgb = frame.to_ndarray(format="rgb24")
    rotated = apply_rotation(rgb, rotation_deg)
    return resize_to_long_edge(rotated, long_edge_px)


def open_pose_stream(
    path: Path,
    *,
    long_edge_px: int = POSE_LONG_EDGE_PX,
    cal_long_edge_px: int = CAL_SPACE_LONG_EDGE_PX,
    analysis_window_s: float = ANALYSIS_WINDOW_S,
    analysis_fps: float = ANALYSIS_FPS,
    max_frames: int = MAX_ANALYSIS_FRAMES,
    motion_scan_threshold_s: float = MOTION_SCAN_THRESHOLD_S,
) -> PoseStream:
    """Stage 5 entry point. Probe, locate the window, return a frame generator.

    The generator yields ``(absolute_pts_s, frame_rgb)``. ``absolute_pts_s`` is
    the frame's PTS **in the source file**, NOT relative to
    ``analysis_window_start_s``: ``extract_keypoints`` subtracts the window
    start itself. Pre-subtracting it here subtracts it twice, and every short
    test clip hides the bug. See the module docstring.

    Raises:
        VideoDecodeError: any Stage 5 failure, carrying its ``ErrorCode``.
    """
    probe = probe_clip(path)

    motion_scan_used = probe.duration_s > motion_scan_threshold_s
    scan = motion_scan_centre_s(path, probe) if motion_scan_used else None
    if motion_scan_used and scan is None:
        # The scan was attempted and produced nothing usable. Say so rather than
        # reporting motion_scan_used=True for a window that is just the head of
        # the clip.
        motion_scan_used = False
    centre_s = scan.centre_s if scan is not None else None
    motion_scan_coarse = scan is not None and motion_scan_is_coarse(scan, probe.duration_s)
    start_s, end_s = analysis_window_bounds(
        probe.duration_s, centre_s, window_s=analysis_window_s
    )

    targets = target_times(start_s, end_s, fps=analysis_fps, cap=max_frames)
    counts = FrameCounts()
    pose_w, pose_h = scaled_size(probe.width_px, probe.height_px, long_edge_px)
    cal_w, cal_h = scaled_size(probe.width_px, probe.height_px, cal_long_edge_px)

    return PoseStream(
        frames=_sample_rgb_frames(path, probe, targets, long_edge_px, counts),
        duration_s=probe.duration_s,
        source_fps=probe.source_fps,
        rotation_deg=probe.rotation_deg,
        width_px=pose_w,
        height_px=pose_h,
        cal_space_width_px=cal_w,
        cal_space_height_px=cal_h,
        analysis_window_start_s=start_s,
        analysis_window_end_s=end_s,
        motion_scan_used=motion_scan_used,
        motion_scan=scan,
        motion_scan_coarse=motion_scan_coarse,
        counts=counts,
    )


__all__: Sequence[str] = (
    "ANALYSIS_FPS",
    "ANALYSIS_WINDOW_S",
    "CAL_SPACE_LONG_EDGE_PX",
    "MAX_ANALYSIS_FRAMES",
    "MAX_CLIP_S",
    "MIN_CLIP_S",
    "MOTION_REFINE_FRAME_BUDGET",
    "MOTION_SCAN_COARSE_FLAG",
    "MOTION_SCAN_COARSE_STRIDE_S",
    "MOTION_SCAN_LONG_EDGE_PX",
    "MOTION_SCAN_THRESHOLD_S",
    "POSE_LONG_EDGE_PX",
    "ClipProbe",
    "FrameCounts",
    "KeyframeBucket",
    "MotionScanResult",
    "PoseStream",
    "VideoDecodeError",
    "analysis_window_bounds",
    "apply_rotation",
    "dense_motion_centre_s",
    "keyframe_motion_profile",
    "motion_scan_centre_s",
    "motion_scan_is_coarse",
    "open_pose_stream",
    "probe_clip",
    "resize_to_long_edge",
    "rotation_from_display_matrix",
    "scaled_size",
    "target_times",
)
