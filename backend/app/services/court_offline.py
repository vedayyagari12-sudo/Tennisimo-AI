"""Court mode offline glue (SESSION_MODE_PHASE2.md build step 16). IMPURE.

Decodes a video into the pure layer's inputs, runs ``analyze_court_session``,
and projects the result onto the wire models in ``app/models/court.py``. No
routes, no DB, no Jobs: those wait on Session Mode Phase 1. The projection
functions at the bottom are pure and unit-tested without any video.

With no taps, or taps that are rejected, the court is found automatically
(``court_lines.fit_court_from_frame``) on the first frame, retried at about
0.5 s and 1.5 s in case a player hides a line. An automatic fit is used for
positions, rallies and line calls when its support is at least
``AUTO_FIT_CALLS_MIN_SUPPORT``. Every automatic fit accepted on the 9 real
TrackNet matches was right by eye (support 0.74-1.0); a fit shifted by one
painted line scored 0.713, under the fit's own 0.72 floor. The frame the
court was found on is kept (``calibration_frame``) so the app can show the
lines that were used, and the user can see a wrong court for themselves.

Taps arrive as 0-1 fractions of the VIDEO frame (``CourtCalibrationRequest``)
and are converted to TRACK_SPACE pixels here (brief Constants), so the
calibration, the ROI and the candidates all share one pixel space.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from concurrent.futures.process import BrokenProcessPool
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import dataclasses
from typing import Final, Literal

import cv2
import numpy as np
import numpy.typing as npt

from app.analysis.ball_events import PoseHit
from app.analysis.calibration_check import CalibrationCheck, check_calibration, corners_in_frame
from app.analysis.dead_time import chunk_spans, keep_spans
from app.analysis.camera_stability import CameraStability, assess_camera_stability
from app.analysis.court_calibration import (
    CALIBRATION_VERSION,
    CalibrationVerdict,
    CourtCalibration,
    CourtTap,
    calibrate_court,
)
from app.analysis.court_model import court_line_endpoints
from app.analysis.court_session import COURT_ENGINE_VERSION, CourtSessionResult, analyze_court_session
from app.analysis.homography import apply_homography
from app.analysis.line_calls import LineCall, uncertainty_ellipse
from app.analysis.placement import ShotLanding, ShotMapSummary
from app.analysis.rally import Rally
from app.ball.camera_motion import CameraMotionSamples, CameraMotionSampler
from app.services.court_parallel import (
    MIN_RANGE_S,
    ParallelPass,
    StallWatch,
    camera_motion_pass,
    number_candidates,
    scan_change,
    split_ranges,
    worker_count,
)
from app.services.player_tracking import PlayerTrackingReport
from app.ball.court_lines import fit_court_from_frame
from app.ball.flight_detector import (
    FLIGHT_CANDIDATE_DTYPE,
    court_roi_mask,
    expected_diameter_fn,
    max_diameter_fn,
    find_flight_candidates,
)
from app.ball.flight_frames import (
    TRACK_SPACE_LONG_EDGE_PX,
    ClipProbe,
    iter_track_frames,
    probe_court_clip,
)
from app.models.court import (
    BounceSigma,
    CalibrationOverlay,
    CameraStabilityReport,
    CaptureInfo,
    CourtAnalysisResponse,
    CourtCalibrationRequest,
    CourtCalibrationResponse,
    CourtFormat,
    CourtLine,
    CourtSessionStatus,
    LineCallResponse,
    OverlayLine,
    RallyResponse,
    ShotLandingResponse,
    ShotMapSummaryResponse,
)


#: Frames at which an automatic court fit is tried, until one is accepted.
AUTO_FIT_FRAMES: Final[tuple[int, ...]] = (0, 15, 45)
AUTO_FIT_CALLS_MIN_SUPPORT: Final[float] = 0.72
logger = logging.getLogger("tennisform.court")

#: A failing automatic court fit costs 20 to 35 s a try (an exhaustive search).
#: Once this much time has gone on failed tries, the later fit frames are not
#: tried: a second and third look at a court that did not fit rarely find one,
#: and a refusal should not take a minute and a half.
AUTO_FIT_BUDGET_S: Final[float] = 30.0
#: The ball pass is this share of a match's progress; player tracking is the rest.
BALL_PASS_SHARE: Final[float] = 0.8
#: How often the decode loop reports progress, in frames.
PROGRESS_EVERY_FRAMES: Final[int] = 30
CalibrationSource = Literal["taps", "auto"]


@dataclass(frozen=True, eq=False)
class AutoFit:
    """An accepted automatic court fit: the calibration, and how well the
    detected lines supported it (what the calibration check rates)."""

    verdict: CalibrationVerdict
    support: float
    lines_visible: int


def auto_calibration_fit(bgr: npt.NDArray[np.uint8]) -> AutoFit | None:
    """The best automatic court fit on one frame, calibrated like taps, with
    its support; None when no court is found or its taps do not calibrate."""
    track_size = (int(bgr.shape[1]), int(bgr.shape[0]))
    fits = fit_court_from_frame(bgr, max_candidates=1)
    if not fits:
        return None
    verdict = calibrate_court(list(fits[0].taps), track_size)
    if verdict.calibration is None:
        return None
    if fits[0].support < AUTO_FIT_CALLS_MIN_SUPPORT and verdict.calibration.calls_allowed:
        verdict = CalibrationVerdict(
            dataclasses.replace(
                verdict.calibration,
                calls_allowed=False,
                flags=(*verdict.calibration.flags, "auto_fit_low_support"),
            ),
            None,
        )
    return AutoFit(verdict, float(fits[0].support), int(fits[0].lines_visible))


def auto_calibration(bgr: npt.NDArray[np.uint8]) -> CalibrationVerdict | None:
    """``auto_calibration_fit``'s calibration alone."""
    fit = auto_calibration_fit(bgr)
    return None if fit is None else fit.verdict


def rate_calibration(
    calibration: CourtCalibration,
    source: CalibrationSource,
    frame_size: tuple[int, int],
    *,
    support: float | None = None,
    lines_visible: int | None = None,
) -> CalibrationCheck:
    """How far this calibration can be trusted, and why (``analysis/calibration_check``)."""
    return check_calibration(
        source=source,
        corners_inside=corners_in_frame(calibration.h_court_to_image, frame_size),
        calls_allowed=calibration.calls_allowed,
        support=support,
        lines_visible=lines_visible,
        tap_rms_px=calibration.reprojection_rms_px if source == "taps" else None,
    )


@dataclass(frozen=True, eq=False)
class CourtVideoInputs:
    """Everything the pure layer needs, decoded from one clip."""

    probe: ClipProbe
    track_size: tuple[int, int] | None
    frame_times_s: npt.NDArray[np.float64]
    candidates: npt.NDArray[np.void]
    per_frame_counts: npt.NDArray[np.int64]
    stability: CameraStability
    median_feature_count: float | None
    calibration_verdict: CalibrationVerdict | None
    #: Where the accepted calibration came from; None when there is none.
    calibration_source: CalibrationSource | None = None
    #: The TRACK_SPACE frame the calibration was made on (BGR), for the
    #: court-lines picture; None without a calibration.
    calibration_frame: npt.NDArray[np.uint8] | None = None
    #: What player tracking did; None when it was not run.
    player_tracking: PlayerTrackingReport | None = None
    #: How far the court calibration can be trusted; None without one.
    calibration_check: CalibrationCheck | None = None


def taps_in_track_space(
    request: CourtCalibrationRequest, track_size: tuple[int, int]
) -> list[CourtTap]:
    """Normalised taps to TRACK_SPACE pixels (brief Constants)."""
    width, height = track_size
    return [CourtTap(t.keypoint, t.x * width, t.y * height) for t in request.taps]


#: While waiting on workers, progress is reported this often.
REPORT_EVERY_S: Final[float] = 1.0
#: The one-core loop's index of the last frame the calibration can be decided
#: on (``AUTO_FIT_FRAMES`` ends at 45) plus the frame after it.
PHASE1_LAST_INDEX: Final[int] = max(AUTO_FIT_FRAMES) + 1


class _BallPass:
    """What the ball pass keeps while it walks frames in order: the
    calibration (taps, else the automatic fit), the court ROI and size
    functions made from it, and the three-frame window. ``step`` is the loop
    body, unchanged; a one-core run and the first frames of a parallel run
    both use it, so they cannot drift apart."""

    def __init__(
        self,
        request: CourtCalibrationRequest | None,
        skip_without_court: bool = False,
        on_court: Callable[[CalibrationCheck, bytes], None] | None = None,
    ) -> None:
        self.request = request
        #: Called ONCE, the moment a calibration is accepted, with how far it
        #: can be trusted and the picture of the lines: the player sees them
        #: within seconds and can stop a rally that would be wasted.
        self.on_court = on_court
        self.check: CalibrationCheck | None = None
        #: Stop searching for ball candidates once no court has been found on
        #: any fit frame. The production job sets this (the session is refused
        #: and no candidate is read); the evaluation tools do not, because
        #: they measure candidates without a court.
        self.skip_without_court = skip_without_court
        self.window: deque[tuple[float, npt.NDArray[np.uint8], npt.NDArray[np.uint8]]] = deque(maxlen=3)
        self.chunks: list[npt.NDArray[np.void]] = []
        self.per_frame: list[int] = []
        self.verdict: CalibrationVerdict | None = None
        self.roi: npt.NDArray[np.uint8] | None = None
        self.diameter: Callable[[float, float], float] | None = None
        self.largest: Callable[[float, float], float] | None = None
        self.track_size: tuple[int, int] | None = None
        self.source: CalibrationSource | None = None
        self.calibration_frame: npt.NDArray[np.uint8] | None = None
        #: Automatic court fits tried so far, and the seconds they took.
        self.fit_tries = 0
        self.fit_spent_s = 0.0

    def _announce(self, bgr: npt.NDArray[np.uint8], *, support: float | None, lines_visible: int | None) -> None:
        """Rate the calibration just accepted and tell the caller, once."""
        verdict, source = self.verdict, self.source
        if verdict is None or verdict.calibration is None or source is None or self.track_size is None:
            return
        self.check = rate_calibration(
            verdict.calibration, source, self.track_size, support=support, lines_visible=lines_visible
        )
        if self.on_court is not None:
            self.on_court(self.check, court_picture_jpeg(bgr, verdict.calibration))

    def _may_try_fit(self) -> bool:
        """The first try always runs; a later one only while the tries so far
        have not used up ``AUTO_FIT_BUDGET_S``."""
        return self.fit_tries == 0 or self.fit_spent_s < AUTO_FIT_BUDGET_S

    def step(self, index: int, pts_s: float, bgr: npt.NDArray[np.uint8]) -> None:
        request = self.request
        if self.track_size is None:
            self.track_size = (int(bgr.shape[1]), int(bgr.shape[0]))
            if request is not None and request.taps:
                self.verdict = calibrate_court(
                    taps_in_track_space(request, self.track_size),
                    self.track_size,
                    video_frame_size=(request.frame_width_px, request.frame_height_px),
                )
                if self.verdict.calibration is not None:
                    self.source, self.calibration_frame = "taps", bgr.copy()
                    self._announce(bgr, support=None, lines_visible=None)
        if self.source is None and index in AUTO_FIT_FRAMES and self._may_try_fit():
            self.fit_tries += 1
            started = time.perf_counter()
            found_auto = auto_calibration_fit(bgr)
            self.fit_spent_s += time.perf_counter() - started
            if found_auto is not None:
                self.verdict, self.source, self.calibration_frame = found_auto.verdict, "auto", bgr.copy()
                self._announce(bgr, support=found_auto.support, lines_visible=found_auto.lines_visible)
        if (
            self.source is not None
            and self.roi is None
            and self.verdict is not None
            and self.verdict.calibration is not None
        ):
            self.roi = court_roi_mask(self.verdict.calibration, self.track_size)
            self.diameter = expected_diameter_fn(self.verdict.calibration)
            self.largest = max_diameter_fn(self.verdict.calibration)
        if self.skip_without_court and self.source is None and (index > max(AUTO_FIT_FRAMES) or not self._may_try_fit()):
            # No court was found on any frame it is tried on, so the session
            # is refused as ``not_calibrated`` and no candidate is ever read.
            # Do not spend the rest of the video finding them (a 6 s refusal
            # took 66 s, most of it this).
            return
        self.window.append((pts_s, cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), bgr))
        if len(self.window) == 3:
            (_, prev_gray, _), (cur_s, cur_gray, cur_bgr), (_, next_gray, _) = self.window
            found = find_flight_candidates(
                prev_gray, cur_gray, next_gray, cur_bgr, index - 1, cur_s, self.roi, self.diameter, self.largest
            )
            self.chunks.append(found)
            self.per_frame.append(len(found))

    def inputs(
        self,
        probe: ClipProbe,
        times: Sequence[float],
        candidates: npt.NDArray[np.void],
        per_frame: Sequence[int],
        samples: CameraMotionSamples,
    ) -> CourtVideoInputs:
        stability = assess_camera_stability(
            samples.times_s, samples.shifts_px, samples.feature_counts, max(samples.long_edge_px, 1)
        )
        return CourtVideoInputs(
            probe=probe,
            track_size=self.track_size,
            frame_times_s=np.asarray(times, dtype=np.float64),
            candidates=candidates,
            per_frame_counts=np.asarray(per_frame, dtype=np.int64),
            stability=stability,
            median_feature_count=(
                float(np.median(samples.feature_counts)) if samples.feature_counts.size else None
            ),
            calibration_verdict=self.verdict,
            calibration_source=self.source,
            calibration_frame=self.calibration_frame,
            calibration_check=self.check,
        )


def extract_court_inputs(
    video: Path,
    request: CourtCalibrationRequest | None,
    progress: Callable[[float], None] | None = None,
    *,
    skip_without_court: bool = False,
    on_court: Callable[[CalibrationCheck, bytes], None] | None = None,
    trim_dead_time: bool = False,
) -> CourtVideoInputs:
    """Decode ``video`` once: candidates per frame, camera motion, calibration.

    The court ROI and expected ball size are used once a calibration is
    accepted (taps, else the automatic fit); before that, and when neither
    gives one, candidates are searched over the whole frame, and with no
    calibration at all the session is later refused as ``not_calibrated``.

    A long video is split by time over several processes
    (``services/court_parallel``) with exactly the same result; the first
    frames, where the court is found, always run here in order. If processes
    cannot be started the whole pass runs on one core.
    """
    probe = probe_court_clip(video)
    workers = worker_count()
    frames = probe.duration_s * probe.source_fps
    if workers > 1 and frames >= PHASE1_LAST_INDEX + 1 + 2 * MIN_RANGE_S * probe.source_fps:
        try:
            return _extract_parallel(
                video, request, progress, probe, workers, skip_without_court, on_court, trim_dead_time
            )
        except (BrokenProcessPool, OSError) as exc:
            logger.warning("parallel ball pass unavailable (%s); running on one core", exc)
    return _extract_sequential(video, request, progress, probe, skip_without_court, on_court)


def _extract_sequential(
    video: Path,
    request: CourtCalibrationRequest | None,
    progress: Callable[[float], None] | None,
    probe: ClipProbe,
    skip_without_court: bool = False,
    on_court: Callable[[CalibrationCheck, bytes], None] | None = None,
) -> CourtVideoInputs:
    state = _BallPass(request, skip_without_court, on_court)
    sampler = CameraMotionSampler()
    times: list[float] = []
    for index, (pts_s, bgr) in enumerate(iter_track_frames(video, probe)):
        times.append(pts_s)
        if progress is not None and index % PROGRESS_EVERY_FRAMES == 0 and probe.duration_s > 0.0:
            progress(min(max(pts_s / probe.duration_s, 0.0), 1.0))
        sampler.add(pts_s, bgr)
        state.step(index, pts_s, bgr)
    candidates = np.concatenate(state.chunks) if state.chunks else np.zeros(0, dtype=FLIGHT_CANDIDATE_DTYPE)
    return state.inputs(probe, times, candidates, state.per_frame, sampler.result())


def _extract_parallel(
    video: Path,
    request: CourtCalibrationRequest | None,
    progress: Callable[[float], None] | None,
    probe: ClipProbe,
    workers: int,
    skip_without_court: bool = False,
    on_court: Callable[[CalibrationCheck, bytes], None] | None = None,
    trim_dead_time: bool = False,
) -> CourtVideoInputs:
    """The first frames here (the court is found on them, in order), the rest
    of the video's candidates on ``workers`` processes while this process runs
    the camera-motion check. Same arrays as ``_extract_sequential``."""
    state = _BallPass(request, skip_without_court, on_court)
    last_pts: float | None = None
    reached = False
    frames_iter = iter_track_frames(video, probe)
    try:
        for index, (pts_s, bgr) in enumerate(frames_iter):
            state.step(index, pts_s, bgr)
            last_pts = pts_s
            if index >= PHASE1_LAST_INDEX:
                reached = True
                break
    finally:
        frames_iter.close()

    if reached and state.roi is None and not skip_without_court:
        # No court, and the caller wants candidates anyway (an evaluation):
        # workers need a court ROI, so do the whole pass in order.
        return _extract_sequential(video, request, progress, probe, skip_without_court, on_court)

    ranges: list[tuple[float, float]] = []
    if reached and state.roi is not None and last_pts is not None and state.verdict is not None:
        assert state.verdict.calibration is not None and state.track_size is not None
        ranges = split_ranges(last_pts, probe.duration_s, workers)
        if trim_dead_time:
            ranges = _trimmed_ranges(video, probe, state, last_pts, workers) or ranges

    # Frames the ball pass will really visit: the first few, and the ranges.
    covered_s = sum(min(end, probe.duration_s) - start for start, end in ranges)
    total = max(PHASE1_LAST_INDEX + 1 + covered_s * probe.source_fps, 1.0) if ranges else max(
        probe.duration_s * probe.source_fps, 1.0
    )

    def report(frames_seen: int, workers_done: int) -> None:
        if progress is not None:
            progress(min(0.999, (min(frames_seen, PHASE1_LAST_INDEX + 1) + workers_done) / total))

    pool: ParallelPass | None = None
    try:
        if ranges:
            assert state.verdict is not None and state.verdict.calibration is not None and state.track_size is not None
            pool = ParallelPass(
                video,
                probe.rotation_deg,
                TRACK_SPACE_LONG_EDGE_PX,
                state.verdict.calibration,
                state.track_size,
                ranges,
                max_workers=workers,
            )
        times, sampler = camera_motion_pass(
            video,
            probe.rotation_deg,
            TRACK_SPACE_LONG_EDGE_PX,
            tick=lambda seen: report(seen if pool is None else PHASE1_LAST_INDEX + 1, 0 if pool is None else pool.frames_done),
        )
        if pool is not None:
            # The camera check is usually done first; keep reporting the
            # workers' real frame count until they are too.
            watch = StallWatch()
            while not pool.done():
                report(PHASE1_LAST_INDEX + 1, pool.frames_done)
                if watch.stalled(pool.frames_done, time.monotonic()):
                    # Caught by ``extract_court_inputs``, which redoes the
                    # pass on one core instead of waiting forever.
                    raise BrokenProcessPool("the ball-pass workers stopped making progress")
                time.sleep(REPORT_EVERY_S)
        worker_records = pool.results() if pool is not None else []
    except BaseException:
        if pool is not None:
            pool.abort()  # a cancel or an error: do not wait out the workers
        raise
    finally:
        if pool is not None:
            pool.__exit__(None, None, None)

    times_array = np.asarray(times, dtype=np.float64)
    records = [*state.chunks, *(number_candidates(r, times_array) for r in worker_records)]
    candidates = np.concatenate(records) if records else np.zeros(0, dtype=FLIGHT_CANDIDATE_DTYPE)
    # One count per centre frame, frames 1 .. N-2, as the one-core loop keeps.
    per_frame = np.bincount(candidates["frame_index"], minlength=len(times))[1 : max(len(times) - 1, 1)]
    if not ranges:
        per_frame = np.asarray(state.per_frame, dtype=np.int64)
    return state.inputs(probe, times, candidates, per_frame, sampler.result())


def _trimmed_ranges(
    video: Path, probe: ClipProbe, state: _BallPass, after_s: float, workers: int
) -> list[tuple[float, float]] | None:
    """Ranges for the workers with the still stretches of the video left out
    (``analysis/dead_time``), or None to keep the whole video. A failed scan
    never costs the analysis: it just keeps everything."""
    assert state.verdict is not None and state.verdict.calibration is not None and state.roi is not None
    try:
        times, shares = scan_change(video, probe.rotation_deg, state.roi)
        spans = keep_spans(times, shares, probe.duration_s)
    except Exception:  # noqa: BLE001 - trimming is an optimisation only
        logger.warning("dead-time scan failed; analysing the whole video", exc_info=True)
        return None
    pieces = [(max(start, after_s), end) for start, end in spans if end > after_s]
    if len(spans) == 1 and spans[0] == (0.0, probe.duration_s):
        return None
    skipped = probe.duration_s - sum(end - start for start, end in pieces) - after_s
    logger.info("trimmed %.0f s of still footage from %.0f s", max(skipped, 0.0), probe.duration_s)
    return chunk_spans(pieces, workers, MIN_RANGE_S)


def analyze_court_video(
    video: Path,
    request: CourtCalibrationRequest | None,
    pose_hits: Sequence[PoseHit] = (),
    *,
    court_format: CourtFormat | None = None,
    progress: Callable[[float], None] | None = None,
    track_players: bool = False,
    skip_without_court: bool = False,
    on_court: Callable[[CalibrationCheck, bytes], None] | None = None,
    trim_dead_time: bool = False,
) -> tuple[CourtVideoInputs, CourtSessionResult]:
    """Decode, then run the pure court engine. Pose hits are optional.
    ``court_format`` overrides the request's (needed when there are no taps).

    ``track_players``: after the ball pass, find the player at each ball hit
    (``services/player_tracking``) and run the engine again with that
    evidence, so landings carry the hitter's end, where they stood and the
    stroke. The ball pass is the first 80 % of ``progress``. A failure in
    tracking leaves the ball-only result."""
    ball_progress = (lambda f: progress(BALL_PASS_SHARE * f)) if (progress and track_players) else progress
    inputs = extract_court_inputs(
        video,
        request,
        ball_progress,
        skip_without_court=skip_without_court,
        on_court=on_court,
        trim_dead_time=trim_dead_time,
    )
    calibration = (
        inputs.calibration_verdict.calibration if inputs.calibration_verdict is not None else None
    )
    if court_format is None:
        court_format = request.court_format if request is not None else CourtFormat.SINGLES
    def run(hits: Sequence[PoseHit]) -> CourtSessionResult:
        return analyze_court_session(
            inputs.candidates,
            inputs.frame_times_s,
            calibration,
            inputs.stability,
            hits,
            court_format=court_format,
            frame_size=inputs.track_size or (1, 1),
        )

    result = run(pose_hits)
    if track_players and calibration is not None and result.events:
        from app.services.player_tracking import track_players as find_players

        found, report = find_players(
            video,
            calibration,
            result.events,
            progress=(lambda f: progress(BALL_PASS_SHARE + (1.0 - BALL_PASS_SHARE) * f)) if progress else None,
        )
        inputs = dataclasses.replace(inputs, player_tracking=report)
        if found:
            result = run((*pose_hits, *found))
    return inputs, result


# ------------------------------------------------------- wire projection (pure)


COURT_PICTURE_LONG_EDGE_PX: Final[int] = 960
COURT_PICTURE_JPEG_QUALITY: Final[int] = 75


def court_picture_jpeg(frame_bgr: npt.NDArray[np.uint8], calibration: CourtCalibration) -> bytes:
    """The frame the court was found on, with the court lines that were used
    drawn over it, as a JPEG no wider than ``COURT_PICTURE_LONG_EDGE_PX``."""
    picture = frame_bgr.copy()
    height, width = picture.shape[:2]
    for line in calibration_overlay(calibration).lines:
        a = (int(round(line.a[0] * width)), int(round(line.a[1] * height)))
        b = (int(round(line.b[0] * width)), int(round(line.b[1] * height)))
        cv2.line(picture, a, b, (0, 0, 0), 5, cv2.LINE_AA)
        cv2.line(picture, a, b, (60, 230, 255), 2, cv2.LINE_AA)
    height, width = picture.shape[:2]
    scale = COURT_PICTURE_LONG_EDGE_PX / float(max(width, height))
    if scale < 1.0:
        picture = cv2.resize(picture, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".jpg", picture, [cv2.IMWRITE_JPEG_QUALITY, COURT_PICTURE_JPEG_QUALITY])
    if not ok:  # pragma: no cover - imencode of a valid uint8 image does not fail
        raise ValueError("could not encode the court picture")
    return encoded.tobytes()


def calibration_overlay(calibration: CourtCalibration) -> CalibrationOverlay:
    """Every court line drawn back into the frame, endpoints as 0-1 fractions."""
    width, height = calibration.frame_size
    lines: list[OverlayLine] = []
    for line in CourtLine:
        a, b = court_line_endpoints(line)
        uv = apply_homography(calibration.h_court_to_image, np.array([a, b], dtype=np.float64))
        lines.append(
            OverlayLine(
                line=line,
                a=(float(uv[0, 0]) / width, float(uv[0, 1]) / height),
                b=(float(uv[1, 0]) / width, float(uv[1, 1]) / height),
            )
        )
    return CalibrationOverlay(lines=lines)


def calibration_response(verdict: CalibrationVerdict) -> CourtCalibrationResponse:
    calibration = verdict.calibration
    if calibration is None:
        return CourtCalibrationResponse(
            accepted=False,
            rejection_reason=verdict.rejection,
            calls_allowed=False,
            reprojection_rms_px=None,
            reprojection_max_px=None,
            camera_height_m=None,
            overlay=None,
            flags=[],
            calibration_version=CALIBRATION_VERSION,
        )
    return CourtCalibrationResponse(
        accepted=True,
        rejection_reason=None,
        calls_allowed=calibration.calls_allowed,
        reprojection_rms_px=float(calibration.reprojection_rms_px),
        reprojection_max_px=float(calibration.reprojection_max_px),
        camera_height_m=float(calibration.pose.height_m),
        overlay=calibration_overlay(calibration),
        flags=list(calibration.flags),
        calibration_version=calibration.version,
    )


def line_call_response(call: LineCall | None) -> LineCallResponse | None:
    if call is None:
        return None
    return LineCallResponse(
        verdict=call.verdict,
        deciding_line=call.deciding_line,
        signed_margin_m=float(call.signed_margin_m),
        normal_sigma_m=float(call.normal_sigma_m),
        uncertainty_radius_m=float(call.uncertainty_radius_m),
        flags=list(call.flags),
    )


def rally_positions(rallies: Sequence[Rally]) -> dict[str, tuple[str, int]]:
    """Bounce id -> (rally id, 1-based landing order within the rally).

    Derived here, not in the pure layer (brief G8). A landing in no rally gets
    nulls on the wire.
    """
    out: dict[str, tuple[str, int]] = {}
    for rally in rallies:
        ordered = sorted(rally.landings, key=lambda l: (l.bounce.time_s, l.bounce.event_id))
        for position, landing in enumerate(ordered, start=1):
            out[landing.bounce.event_id] = (rally.rally_id, position)
    return out


def landing_response(
    landing: ShotLanding, rally: tuple[str, int] | None
) -> ShotLandingResponse:
    major, minor, angle = uncertainty_ellipse(landing.bounce.cov_m2, z=1.0)  # brief G16
    bounce = landing.bounce
    return ShotLandingResponse(
        bounce_id=bounce.event_id,
        time_s=max(float(bounce.time_s), 0.0),
        time_sigma_s=float(bounce.time_sigma_s),
        court_xy_m=(float(bounce.court_xy_m[0]), float(bounce.court_xy_m[1])),
        sigma_m=BounceSigma(major=float(major), minor=float(minor), angle_deg=float(angle) % 180.0),
        half=bounce.half,
        hitter_end=landing.hitter_end,
        shot_type=landing.shot_type,
        is_serve=landing.is_serve,
        target=landing.target,
        line_call=line_call_response(landing.call),
        depth_zone=landing.depth_zone,
        depth_zone_confident=landing.depth_confident,
        depth_probabilities={k: float(v) for k, v in landing.depth_probabilities.items()},
        width_zone=landing.width_zone,
        confidence=float(np.clip(bounce.confidence, 0.0, 1.0)),
        rally_id=None if rally is None else rally[0],
        shot_index_in_rally=None if rally is None else rally[1],
        flags=list(bounce.flags),
        hit_xy_m=None
        if landing.hit_xy_m is None
        else (float(landing.hit_xy_m[0]), float(landing.hit_xy_m[1])),
    )


def summary_response(summary: ShotMapSummary) -> ShotMapSummaryResponse:
    return ShotMapSummaryResponse(
        count=summary.count,
        verdict_counts=dict(summary.verdict_counts),
        depth_expected_counts={k: float(v) for k, v in summary.depth_expected_counts.items()},
        width_counts=dict(summary.width_counts),
        median_sigma_along_m=summary.median_sigma_along_m,
    )


def rally_response(rally: Rally) -> RallyResponse:
    return RallyResponse(
        rally_id=rally.rally_id,
        start_s=max(float(rally.start_s), 0.0),
        end_s=max(float(rally.end_s), 0.0),
        length_hits=rally.length_hits,
        length_min=rally.length_min,
        length_max=rally.length_max,
        length_confidence=rally.confidence,
        hits_observed=rally.hits_observed,
        hits_inferred=rally.hits_inferred,
        end_reason=rally.end_reason,
        bounce_ids=sorted(l.bounce.event_id for l in rally.landings),
        flags=list(rally.flags),
    )


def project_court_result(
    result: CourtSessionResult, *, frame_size: tuple[int, int]
) -> CourtAnalysisResponse:
    """The wire body for one analysed court session. Every court number is an
    estimate from one fixed camera, so ``is_estimate`` is always true."""
    positions = rally_positions(result.rallies)
    width, height = frame_size
    return CourtAnalysisResponse(
        status=result.status,
        refusal_reason=result.refusal_reason,
        court_engine_version=COURT_ENGINE_VERSION,
        is_estimate=True,
        camera=CameraStabilityReport(
            verdict=result.stability.verdict,
            p95_shift_fraction=result.stability.p95_shift_fraction,
            rms_shift_px=result.stability.rms_shift_px,
            bumped_at_s=result.stability.bumped_at_s,
        ),
        capture=CaptureInfo(
            # A refused-for-frame-rate clip may have no measurable fps; the wire
            # field is positive, so report the floor of what was decoded.
            fps=result.fps if result.fps > 0.0 else 1.0,
            frame_width_px=max(int(width), 1),
            frame_height_px=max(int(height), 1),
        ),
        summary=summary_response(result.summary),
        landings=[
            landing_response(l, positions.get(l.bounce.event_id)) for l in result.landings
        ],
        rallies=[rally_response(r) for r in result.rallies],
    )


__all__ = [
    "CourtSessionStatus",
    "CourtVideoInputs",
    "analyze_court_video",
    "calibration_overlay",
    "calibration_response",
    "court_picture_jpeg",
    "extract_court_inputs",
    "landing_response",
    "project_court_result",
    "rally_positions",
    "taps_in_track_space",
]
