"""Ball flight linking: per-frame candidates in, ball flights out (design 4.4).

PURE: numpy and stdlib only, no cv2, no I/O, no clock. Candidates arrive as
``FLIGHT_CANDIDATE_DTYPE`` records produced by ``app/ball/flight_detector.py``
(pure-CV), which imports the dtype from here and re-exports it (brief G4), so
the record layout is owned by the pure side of the seam.

Pixel units are TRACK_SPACE: rotated native frames capped at a 1920 px long
edge (design 2.3), y DOWN.
"""

from __future__ import annotations

from typing import Final

import numpy as np

__all__ = [
    "FLIGHT_CANDIDATE_DTYPE",
]

#: Brief G4: 10 fields, packed, 56 bytes. ``orientation_deg`` is the major-axis
#: angle folded to [0, 180), y DOWN. ``colour_score`` is the fraction of the
#: component's pixels that pass the ball colour gate. ``contrast`` is the peak
#: double-difference strength over the component, in 8-bit grey levels.
FLIGHT_CANDIDATE_DTYPE: Final[np.dtype[np.void]] = np.dtype(
    [
        ("frame_index", "<i8"),
        ("time_s", "<f8"),
        ("x", "<f8"),
        ("y", "<f8"),
        ("area", "<f4"),
        ("minor", "<f4"),
        ("major", "<f4"),
        ("orientation_deg", "<f4"),
        ("colour_score", "<f4"),
        ("contrast", "<f4"),
    ]
)
