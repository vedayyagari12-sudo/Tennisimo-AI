"""Full-chain scoring of a court session against scene truth (design 11.2).

Test-only. Shared by the T0 (projected points) and T2 (rendered pixels) runs so
both tiers are measured the same way. Uses the ``truth.py`` measures.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from app.analysis.court_model import half_region
from app.analysis.court_session import CourtSessionResult
from app.analysis.line_calls import BALL_CONTACT_PATCH_RADIUS_M
from app.models.court import CourtEnd, CourtFormat, LineCallTarget, LineVerdict
from tests.unit.court_synth.truth import (
    BOUNCE_MATCH_FRAMES,
    band_coverage,
    confident_wrong_per_1000,
    in_out_accuracy_outside_band,
    match_times,
    rally_length_scores,
)
from tests.unit.court_synth.scene import SceneTruth


def _other(end: CourtEnd) -> CourtEnd:
    return CourtEnd.FAR if end is CourtEnd.NEAR else CourtEnd.NEAR


def _inside(edge: str, region: object, xy: tuple[float, float]) -> float:
    x, y = xy
    return {
        "x_min": x - region.x_min,  # type: ignore[attr-defined]
        "x_max": region.x_max - x,  # type: ignore[attr-defined]
        "y_min": y - region.y_min,  # type: ignore[attr-defined]
        "y_max": region.y_max - y,  # type: ignore[attr-defined]
    }[edge]


@dataclass
class Tally:
    """Raw per-bounce and per-rally rows, summed over scenes."""

    bounces_truth: int = 0
    #: First bounce of each shot: the one that decides in/out and the point.
    deciding_truth: int = 0
    deciding_matched: int = 0
    bounces_matched: int = 0
    bounces_real: int = 0
    bounces_detected: int = 0
    near_errors_m: list[float] = field(default_factory=list)
    far_along_errors_m: list[float] = field(default_factory=list)
    verdicts: list[str] = field(default_factory=list)
    truth_in: list[bool] = field(default_factory=list)
    true_margins_m: list[float] = field(default_factory=list)
    estimated_margins_m: list[float] = field(default_factory=list)
    radii_m: list[float] = field(default_factory=list)
    too_close_by_half: dict[str, list[int]] = field(
        default_factory=lambda: {"near": [0, 0], "far": [0, 0]}
    )
    rally_truth: list[int] = field(default_factory=list)
    rally_est: list[int] = field(default_factory=list)
    rally_min: list[int] = field(default_factory=list)
    rally_max: list[int] = field(default_factory=list)
    rally_count_mismatch: int = 0

    def add(
        self,
        scene: SceneTruth,
        result: CourtSessionResult,
        fps: float,
        truth_uv: np.ndarray | None = None,
    ) -> None:
        """``truth_uv`` (frames x 2, NaN off-frame) restricts truth to bounces
        the camera could see: in frame for +/-3 frames, not only unoccluded."""

        def in_frame(time_s: float) -> bool:
            if truth_uv is None:
                return True
            frame = int(round(time_s * fps))
            window = truth_uv[max(frame - 3, 0) : frame + 4]
            return frame < len(truth_uv) and bool(np.all(np.isfinite(window)))

        truth = [b for b in scene.bounces if b.visible and in_frame(b.time_s)]
        detected = list(result.landings)
        self.bounces_truth += len(truth)
        self.bounces_detected += len(detected)
        # Precision against EVERY true bounce: a detection of a bounce the
        # truth calls hidden or off-frame is still a real bounce.
        every = match_times(
            [b.time_s for b in scene.bounces], [l.bounce.time_s for l in detected],
            BOUNCE_MATCH_FRAMES / fps,
        )
        self.bounces_real += len(every.pairs)
        match = match_times(
            [b.time_s for b in truth],
            [l.bounce.time_s for l in detected],
            BOUNCE_MATCH_FRAMES / fps,
        )
        self.bounces_matched += len(match.pairs)
        matched_truth = {ti for ti, _ in match.pairs}
        for index, b in enumerate(truth):
            if b.bounce_index == 1:
                self.deciding_truth += 1
                self.deciding_matched += int(index in matched_truth)
        for ti, di in match.pairs:
            b, l = truth[ti], detected[di]
            dx = l.bounce.court_xy_m[0] - b.position_m[0]
            dy = l.bounce.court_xy_m[1] - b.position_m[1]
            if b.half is CourtEnd.NEAR:
                self.near_errors_m.append(float(np.hypot(dx, dy)))
            else:
                self.far_along_errors_m.append(abs(float(dy)))
            call = l.call
            # Calls are judged where engine and truth agree on what was aimed
            # at: the full half opposite the true hitter. Serves are re-called
            # against the box later (point_inference), so they are left out.
            if (
                call is None
                or b.target is not LineCallTarget.SINGLES_HALF
                or l.hitter_end is not b.hitter_end
                or call.target is not LineCallTarget.SINGLES_HALF
            ):
                continue
            region = half_region(_other(b.hitter_end), CourtFormat.SINGLES)
            edge = next(e for e, line in region.edge_lines.items() if line is call.deciding_line)
            true_margin = _inside(edge, region, b.court_xy_m) + BALL_CONTACT_PATCH_RADIUS_M
            self.verdicts.append(call.verdict.value)
            self.truth_in.append(b.is_in)
            self.true_margins_m.append(b.signed_margin_m)
            self.estimated_margins_m.append(call.signed_margin_m)
            self.radii_m.append(call.uncertainty_radius_m)
            counts = self.too_close_by_half[b.half.value]
            counts[1] += 1
            counts[0] += int(call.verdict is LineVerdict.TOO_CLOSE_TO_CALL)
            self._band_true.append(true_margin)
        if len(result.rallies) != len(scene.rallies):
            self.rally_count_mismatch += 1
            return
        for rally, truth_rally in zip(result.rallies, scene.rallies, strict=True):
            self.rally_truth.append(truth_rally.length_hits)
            self.rally_est.append(rally.length_hits)
            self.rally_min.append(rally.length_min)
            self.rally_max.append(rally.length_max)

    _band_true: list[float] = field(default_factory=list)

    def summary(self) -> dict[str, float | int | None]:
        rally = rally_length_scores(self.rally_truth, self.rally_est, self.rally_min, self.rally_max)
        return {
            "bounces_truth": self.bounces_truth,
            "bounce_recall": self.bounces_matched / max(self.bounces_truth, 1),
            "bounce_precision": self.bounces_real / max(self.bounces_detected, 1),
            "deciding_bounce_recall": self.deciding_matched / max(self.deciding_truth, 1),
            "median_bounce_error_near_m": (
                float(np.median(self.near_errors_m)) if self.near_errors_m else None
            ),
            "median_along_error_far_m": (
                float(np.median(self.far_along_errors_m)) if self.far_along_errors_m else None
            ),
            "calls_judged": len(self.verdicts),
            "in_out_accuracy_outside_band": in_out_accuracy_outside_band(
                self.verdicts, self.truth_in, self.true_margins_m, self.radii_m
            ),
            "confident_wrong_per_1000": confident_wrong_per_1000(self.verdicts, self.truth_in),
            "band_coverage": band_coverage(self.estimated_margins_m, self._band_true, self.radii_m),
            "too_close_rate_near": _rate(self.too_close_by_half["near"]),
            "too_close_rate_far": _rate(self.too_close_by_half["far"]),
            "rallies": len(self.rally_truth),
            "rally_exact": rally.exact,
            "rally_within_one": rally.within_one,
            "rally_range_contains_truth": rally.range_contains_truth,
            "rally_count_mismatch_scenes": self.rally_count_mismatch,
        }


def _rate(counts: Sequence[int]) -> float | None:
    return counts[0] / counts[1] if counts[1] else None
