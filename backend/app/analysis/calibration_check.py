"""Can this court calibration be trusted? (2026-11-22). PURE: numbers in, a
rating and reasons out.

A rally is filmed once and cannot be re-played, so the player must learn
within seconds, not after the whole analysis, whether the court lines the
engine found sit on the real ones. If they do not, every bounce position and
in/out call is off. This module rates one calibration and says WHY, in codes
the app turns into plain advice:

* ``good``  the lines matched well, enough of them were seen, and the whole
  court is in the picture.
* ``check`` usable, but something is weaker than it should be; the player
  should look at the picture of the lines and decide.
* ``poor``  the fit is barely above the acceptance gate or much of the court
  is outside the picture; the numbers are likely to be off.

The rating never claims the lines are RIGHT, only how well the evidence
supports them: the player's own eyes on the overlay are the final check.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

import numpy as np
import numpy.typing as npt

Confidence = Literal["good", "check", "poor"]
Source = Literal["taps", "auto"]

CHECK_VERSION: Final[str] = "calibration_check_v1"

#: Share of the model court lines (sampled) that sit on detected lines.
GOOD_SUPPORT: Final[float] = 0.85
POOR_SUPPORT: Final[float] = 0.78
#: Court lines (of 14) that were mostly inside the picture and matched.
GOOD_LINES_VISIBLE: Final[int] = 9
POOR_LINES_VISIBLE: Final[int] = 7
#: Tapped points: reprojection error, in 1280-equivalent pixels.
GOOD_TAP_RMS_PX: Final[float] = 2.0

#: The doubles court corners, metres: (x across, y along).
DOUBLES_CORNERS_M: Final[tuple[tuple[float, float], ...]] = (
    (-5.485, -11.885),
    (5.485, -11.885),
    (5.485, 11.885),
    (-5.485, 11.885),
)

# Reason codes. The app words each one for the player.
BASELINE_CUT_OFF: Final[str] = "court_cut_off"
FEW_LINES: Final[str] = "few_lines"
WEAK_MATCH: Final[str] = "weak_match"
CALLS_OFF: Final[str] = "calls_off"
TAP_ERROR: Final[str] = "tap_error"


@dataclass(frozen=True)
class CalibrationCheck:
    confidence: Confidence
    source: Source
    #: Fit support 0-1 (automatic fits only; None for taps).
    support: float | None
    #: Court lines matched (automatic fits only).
    lines_visible: int | None
    #: How many of the four doubles corners fall inside the picture.
    corners_in_frame: int
    reasons: tuple[str, ...]
    #: In/out calls are allowed for this calibration.
    calls_allowed: bool
    version: str = CHECK_VERSION


def corners_in_frame(
    h_court_to_image: npt.NDArray[np.float64], frame_size: tuple[int, int]
) -> int:
    """How many of the four doubles-court corners project inside the frame.
    A corner behind the camera or at infinity counts as outside."""
    width, height = frame_size
    inside = 0
    for x, y in DOUBLES_CORNERS_M:
        u, v, w = (np.asarray(h_court_to_image, dtype=np.float64) @ np.array([x, y, 1.0])).tolist()
        if abs(w) < 1e-9:
            continue
        pu, pv = u / w, v / w
        if np.isfinite(pu) and np.isfinite(pv) and 0.0 <= pu < width and 0.0 <= pv < height:
            inside += 1
    return inside


def check_calibration(
    *,
    source: Source,
    corners_inside: int,
    calls_allowed: bool,
    support: float | None = None,
    lines_visible: int | None = None,
    tap_rms_px: float | None = None,
) -> CalibrationCheck:
    """Rate a calibration. PURE.

    Automatic fits are rated on ``support`` and ``lines_visible``; tapped ones
    on ``tap_rms_px``. Both on how much of the court is in the picture and
    whether calls are allowed.
    """
    reasons: list[str] = []
    poor = False
    weak = False

    if corners_inside < 4:
        reasons.append(BASELINE_CUT_OFF)
        # One corner out is a cut-off edge; two or more is a court mostly
        # out of the picture.
        if corners_inside <= 2:
            poor = True
        else:
            weak = True

    if source == "auto":
        if support is not None:
            if support < POOR_SUPPORT:
                reasons.append(WEAK_MATCH)
                poor = True
            elif support < GOOD_SUPPORT:
                reasons.append(WEAK_MATCH)
                weak = True
        if lines_visible is not None:
            if lines_visible < POOR_LINES_VISIBLE:
                reasons.append(FEW_LINES)
                poor = True
            elif lines_visible < GOOD_LINES_VISIBLE:
                reasons.append(FEW_LINES)
                weak = True
    elif tap_rms_px is not None and tap_rms_px > GOOD_TAP_RMS_PX:
        reasons.append(TAP_ERROR)
        weak = True

    if not calls_allowed:
        reasons.append(CALLS_OFF)
        weak = True

    confidence: Confidence = "poor" if poor else ("check" if weak else "good")
    return CalibrationCheck(
        confidence=confidence,
        source=source,
        support=None if support is None else round(float(support), 3),
        lines_visible=lines_visible,
        corners_in_frame=corners_inside,
        reasons=tuple(dict.fromkeys(reasons)),
        calls_allowed=calls_allowed,
    )


def describe(reasons: Sequence[str]) -> list[str]:
    """Each reason as a sentence for a log line (the app has its own wording)."""
    words = {
        BASELINE_CUT_OFF: "part of the court is outside the picture",
        FEW_LINES: "only a few court lines were clear",
        WEAK_MATCH: "the lines matched the picture only partly",
        CALLS_OFF: "in/out calls are switched off",
        TAP_ERROR: "the tapped points do not agree with each other",
    }
    return [words.get(r, r) for r in reasons]


__all__ = [
    "BASELINE_CUT_OFF",
    "CALLS_OFF",
    "CHECK_VERSION",
    "CalibrationCheck",
    "Confidence",
    "FEW_LINES",
    "GOOD_SUPPORT",
    "POOR_SUPPORT",
    "TAP_ERROR",
    "WEAK_MATCH",
    "check_calibration",
    "corners_in_frame",
    "describe",
]
