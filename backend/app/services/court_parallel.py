"""The ball pass on several CPU cores (2026-11-22). IMPURE: decodes video and
starts processes.

``extract_court_inputs`` decodes a video once and, per frame, finds ball
candidates from three consecutive frames. Candidates for one frame depend on
nothing but that frame and its two neighbours, so the video can be cut by time
and the pieces done on different cores, with EXACTLY the same result as one
core doing it in order:

* A worker seeks to a key frame about a second before its range, decodes
  forward, and works out candidates for the centre frames whose time falls in
  ``[start, end)``. It uses the frame just before and just after its range as
  neighbours, so no frame is lost or counted twice at a seam. Decoding from a
  key frame gives the same pictures as decoding from the start.
* The camera-motion check is a sequential state machine over frame times
  (``CameraMotionSampler``), so it is NOT split: the main process runs it,
  over every frame's time but converting only the frames it measures, while
  the workers run.
* Frame numbers are put back by the main process from the full list of frame
  times, so the candidates carry the same ``frame_index`` as a one-core run.

Progress is real: workers add to a shared counter as they finish frames.
"""

from __future__ import annotations

import math
import multiprocessing
import os
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ProcessPoolExecutor
from pathlib import Path
from typing import Any, Final

import av
import cv2
import numpy as np
import numpy.typing as npt

from app.analysis.court_calibration import CourtCalibration
from app.analysis.dead_time import change_share
from app.analysis.flight_track import FLIGHT_CANDIDATE_DTYPE
from app.ball.camera_motion import CameraMotionSampler
from app.ball.flight_detector import (
    court_roi_mask,
    expected_diameter_fn,
    find_flight_candidates,
    max_diameter_fn,
)
from app.models.enums import ErrorCode
from app.pose.video_io import VideoDecodeError, apply_rotation, resize_to_long_edge

#: Environment override for the number of worker processes.
WORKERS_ENV: Final[str] = "TENNISFORM_TRACK_WORKERS"
#: Default cap: more workers than this give little on a small server and cost
#: memory (each holds a copy of the detector's libraries and a few frames).
DEFAULT_MAX_WORKERS: Final[int] = 4
#: A range shorter than this is not worth a process.
MIN_RANGE_S: Final[float] = 6.0
#: A worker seeks this far before its range, so the key frame it lands on and
#: any frames that depend on earlier ones are behind it.
SEEK_MARGIN_S: Final[float] = 1.0
#: Frames this close before the range are converted (the first centre needs
#: the frame before it).
CONVERT_MARGIN_S: Final[float] = 0.5
#: Thread counts to pin to one for the workers (see ``ParallelPass``).
SINGLE_THREAD_ENV: Final[tuple[str, ...]] = (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)
#: How long to wait for the worker processes to come up.
START_TIMEOUT_S: Final[float] = 30.0
#: A worker adds to the shared counter this often, in frames.
COUNT_EVERY_FRAMES: Final[int] = 10

#: The workers are given up on (and the pass redone on one core) when none of
#: them has finished a frame for this long. A healthy worker reports every
#: ``COUNT_EVERY_FRAMES`` frames, a second or two apart at worst.
STALL_TIMEOUT_S: Final[float] = 90.0

_counter: Any = None


class StallWatch:
    """Notices a worker pool that has stopped making progress. PURE: the caller
    passes the frame count and the clock."""

    def __init__(self, timeout_s: float = STALL_TIMEOUT_S) -> None:
        self.timeout_s = timeout_s
        self._frames = -1
        self._since: float | None = None

    def stalled(self, frames_done: int, now_s: float) -> bool:
        if self._since is None or frames_done != self._frames:
            self._frames, self._since = frames_done, now_s
            return False
        return now_s - self._since >= self.timeout_s


def worker_count() -> int:
    """Worker processes for a ball pass: the environment override, else the
    CPUs this process may use, capped at ``DEFAULT_MAX_WORKERS``."""
    raw = os.environ.get(WORKERS_ENV)
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    cpus = getattr(os, "process_cpu_count", os.cpu_count)() or 1
    return max(1, min(DEFAULT_MAX_WORKERS, int(cpus)))


def split_ranges(first_s: float, last_s: float, workers: int) -> list[tuple[float, float]]:
    """Contiguous ``[start, end)`` time ranges covering ``[first_s, last_s]``,
    one per worker, the last open-ended. Fewer than ``workers`` when the span is
    too short to give each ``MIN_RANGE_S``. PURE."""
    span = last_s - first_s
    count = max(1, min(workers, int(span // MIN_RANGE_S)))
    edges = [first_s + span * i / count for i in range(count)]
    return [(edges[i], edges[i + 1] if i + 1 < count else math.inf) for i in range(count)]


def _init_worker(counter: Any) -> None:
    """Runs once in each worker. One thread per worker: OpenCV and the decoder
    otherwise start a thread per core in EVERY worker, and four workers then
    fight over the cores (measured: each ran about 3x slower than alone)."""
    global _counter
    _counter = counter
    cv2.setNumThreads(1)


def process_range(
    video: str,
    rotation_deg: int,
    long_edge_px: int,
    calibration: CourtCalibration,
    track_size: tuple[int, int],
    start_s: float,
    end_s: float,
) -> npt.NDArray[np.void]:
    """Candidates for every centre frame with time in ``[start_s, end_s)``,
    with ``frame_index`` 0 (the caller numbers them). Runs in a worker."""
    roi = court_roi_mask(calibration, track_size)
    diameter = expected_diameter_fn(calibration)
    largest = max_diameter_fn(calibration)
    try:
        container = av.open(video)
    except (av.FFmpegError, OSError) as exc:
        raise VideoDecodeError(ErrorCode.DECODE_FAILED, f"could not open: {exc}") from exc
    chunks: list[npt.NDArray[np.void]] = []
    done = 0
    try:
        stream = container.streams.video[0]
        stream.codec_context.thread_count = 1
        time_base = float(stream.time_base) if stream.time_base else 0.0
        if time_base <= 0.0:
            raise VideoDecodeError(ErrorCode.DECODE_FAILED, "stream has no time base")
        seek_to = start_s - SEEK_MARGIN_S
        if seek_to > 0.0:
            try:
                container.seek(int(seek_to / time_base), stream=stream, backward=True, any_frame=False)
            except av.FFmpegError:
                container.seek(0)
        window: deque[tuple[float, npt.NDArray[np.uint8], npt.NDArray[np.uint8]]] = deque(maxlen=3)
        for frame in container.decode(stream):
            if frame.pts is None:
                continue
            pts_s = float(frame.pts) * time_base
            if pts_s < start_s - CONVERT_MARGIN_S:
                continue
            bgr = resize_to_long_edge(apply_rotation(frame.to_ndarray(format="bgr24"), rotation_deg), long_edge_px)
            window.append((pts_s, cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), bgr))
            if len(window) < 3:
                continue
            (_, prev_gray, _), (cur_s, cur_gray, cur_bgr), (_, next_gray, _) = window
            if cur_s >= end_s:
                break
            if cur_s < start_s:
                continue
            chunks.append(
                find_flight_candidates(prev_gray, cur_gray, next_gray, cur_bgr, 0, cur_s, roi, diameter, largest)
            )
            done += 1
            if _counter is not None and done % COUNT_EVERY_FRAMES == 0:
                with _counter.get_lock():
                    _counter.value += COUNT_EVERY_FRAMES
    except av.FFmpegError as exc:
        raise VideoDecodeError(ErrorCode.DECODE_FAILED, str(exc)) from exc
    finally:
        container.close()
    if _counter is not None and done % COUNT_EVERY_FRAMES:
        with _counter.get_lock():
            _counter.value += done % COUNT_EVERY_FRAMES
    return np.concatenate(chunks) if chunks else np.zeros(0, dtype=FLIGHT_CANDIDATE_DTYPE)


class ParallelPass:
    """Workers running over the later part of a video while the caller does
    other work. Use as a context manager."""

    def __init__(
        self,
        video: Path,
        rotation_deg: int,
        long_edge_px: int,
        calibration: CourtCalibration,
        track_size: tuple[int, int],
        ranges: list[tuple[float, float]],
        max_workers: int | None = None,
    ) -> None:
        self._counter = multiprocessing.Value("l", 0)
        # NumPy's math library starts a thread per core in every process that
        # imports it, and those threads spin-wait; four workers doing that
        # starve each other. A child reads these when it imports NumPy, so they
        # are set while the children start and put back after.
        saved = {name: os.environ.get(name) for name in SINGLE_THREAD_ENV}
        os.environ.update({name: "1" for name in SINGLE_THREAD_ENV})
        try:
            self._pool = ProcessPoolExecutor(
                max_workers=max(1, min(len(ranges), max_workers or len(ranges))), initializer=_init_worker, initargs=(self._counter,)
            )
            self._futures: list[Future[npt.NDArray[np.void]]] = [
                self._pool.submit(
                    process_range, str(video), rotation_deg, long_edge_px, calibration, track_size, start, end
                )
                for start, end in ranges
            ]
            # Workers start on demand: wait for each to be up before the
            # environment is restored.
            self._wait_until_started(max(1, min(len(ranges), max_workers or len(ranges))))
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def _wait_until_started(self, count: int) -> None:
        deadline = time.monotonic() + START_TIMEOUT_S
        while time.monotonic() < deadline:
            if len(getattr(self._pool, "_processes", {})) >= count:
                return
            time.sleep(0.05)

    @property
    def frames_done(self) -> int:
        return int(self._counter.value)

    def abort(self) -> None:
        """Stop the workers now (the user cancelled). Not a polite shutdown:
        a worker mid-range would otherwise run on for a minute."""
        for process in list(getattr(self._pool, "_processes", {}).values()):
            try:
                process.terminate()
            except Exception:  # noqa: BLE001 - already gone
                pass
        self._pool.shutdown(wait=False, cancel_futures=True)

    def done(self) -> bool:
        return all(future.done() for future in self._futures)

    def results(self) -> list[npt.NDArray[np.void]]:
        """Each range's records, in range order. Re-raises a worker's error."""
        return [future.result() for future in self._futures]

    def __enter__(self) -> ParallelPass:
        return self

    def __exit__(self, *_: object) -> None:
        self._pool.shutdown(wait=True, cancel_futures=True)


def camera_motion_pass(
    video: Path,
    rotation_deg: int,
    long_edge_px: int,
    tick: Callable[[int], None] | None = None,
) -> tuple[list[float], CameraMotionSampler]:
    """Every frame's time, and the camera-motion sampler fed exactly as a
    one-core run feeds it (``wants`` is its own test, so only the frames it
    measures are converted). ``tick(frames_seen)`` is called every so often."""
    sampler = CameraMotionSampler()
    times: list[float] = []
    try:
        container = av.open(str(video))
    except (av.FFmpegError, OSError) as exc:
        raise VideoDecodeError(ErrorCode.DECODE_FAILED, f"could not open: {exc}") from exc
    try:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        time_base = float(stream.time_base) if stream.time_base else 0.0
        if time_base <= 0.0:
            raise VideoDecodeError(ErrorCode.DECODE_FAILED, "stream has no time base")
        for frame in container.decode(stream):
            if frame.pts is None:
                continue
            pts_s = float(frame.pts) * time_base
            times.append(pts_s)
            if sampler.wants(pts_s):
                bgr = resize_to_long_edge(
                    apply_rotation(frame.to_ndarray(format="bgr24"), rotation_deg), long_edge_px
                )
                sampler.add(pts_s, bgr)
            if tick is not None and len(times) % 30 == 0:
                tick(len(times))
    except av.FFmpegError as exc:
        raise VideoDecodeError(ErrorCode.DECODE_FAILED, str(exc)) from exc
    finally:
        container.close()
    return times, sampler


#: The scan compares keyframes at this width, in pixels.
SCAN_WIDTH_PX: Final[int] = 192


def scan_change(
    video: Path, rotation_deg: int, roi: npt.NDArray[np.uint8]
) -> tuple[list[float], list[float]]:
    """Keyframe times and the share of the court region that changed from each
    keyframe to the next. Only keyframes are decoded (a small fraction of the
    video), so this costs little next to the ball pass. ``roi`` is the full
    track-size court mask."""
    times: list[float] = []
    shares: list[float] = []
    try:
        container = av.open(str(video))
    except (av.FFmpegError, OSError) as exc:
        raise VideoDecodeError(ErrorCode.DECODE_FAILED, f"could not open: {exc}") from exc
    try:
        stream = container.streams.video[0]
        stream.codec_context.skip_frame = "NONKEY"
        time_base = float(stream.time_base) if stream.time_base else 0.0
        if time_base <= 0.0:
            return [], []
        small_roi: npt.NDArray[np.uint8] | None = None
        previous: npt.NDArray[np.uint8] | None = None
        for frame in container.decode(stream):
            if frame.pts is None:
                continue
            gray = apply_rotation(frame.to_ndarray(format="gray"), rotation_deg)
            gray = cv2.GaussianBlur(resize_to_long_edge(gray, SCAN_WIDTH_PX), (5, 5), 0)
            if small_roi is None:
                small_roi = cv2.resize(roi, (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_NEAREST)
            if previous is not None:
                shares.append(change_share(previous, gray, small_roi))
            times.append(float(frame.pts) * time_base)
            previous = gray
    except av.FFmpegError as exc:
        raise VideoDecodeError(ErrorCode.DECODE_FAILED, str(exc)) from exc
    finally:
        container.close()
    return times, shares


def number_candidates(
    records: npt.NDArray[np.void], times_s: npt.NDArray[np.float64]
) -> npt.NDArray[np.void]:
    """Put the true ``frame_index`` on records made with index 0: the position
    of each record's time in the video's full list of frame times."""
    numbered = records.copy()
    numbered["frame_index"] = np.searchsorted(times_s, numbered["time_s"])
    return numbered


__all__ = [
    "ParallelPass",
    "STALL_TIMEOUT_S",
    "StallWatch",
    "WORKERS_ENV",
    "camera_motion_pass",
    "number_candidates",
    "scan_change",
    "process_range",
    "split_ranges",
    "worker_count",
]
