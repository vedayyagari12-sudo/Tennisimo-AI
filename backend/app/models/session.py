"""Session Mode vocabulary (SESSION_MODE.md 4.2 step 5, 4.2 step 6).

Closed enums: their wire values are persisted per swing and counted per session
in ``rejection_counts``, so members are never renamed or reordered.
"""

from __future__ import annotations

from enum import StrEnum


class SwingStatus(StrEnum):
    """What a detected swing is allowed to do (SESSION_MODE.md 4.2 step 6)."""

    ACCEPTED = "accepted"            # confidence >= 0.45, counts in statistics
    LOW_CONFIDENCE = "low_confidence"  # 0.35-0.45: stored and shown, not counted
    REJECTED = "rejected"            # below 0.35, or any rejection reason


class SwingRejection(StrEnum):
    """Why a speed peak did not become a swing (SESSION_MODE.md 4.2 step 5)."""

    BELOW_RELATIVE_FLOOR = "below_relative_floor"
    NOT_PROMINENT = "not_prominent"
    WRIST_UNOBSERVED = "wrist_unobserved"
    WINDOW_TOO_SHORT = "window_too_short"
    POSE_UNUSABLE = "pose_unusable"
    IDENTITY_BREAK_IN_WINDOW = "identity_break_in_window"
    CONTACT_NOT_FOUND = "contact_not_found"
    SEQUENCE_UNUSABLE = "sequence_unusable"
    CONTACT_OFF_PEAK = "contact_off_peak"
    NO_MOTION_EVIDENCE = "no_motion_evidence"
    # Not in the 4.2 step 5 list, which names no reason for step 6's "< 0.35
    # means rejected". Without it those swings would be rejected silently.
    CONFIDENCE_BELOW_FLOOR = "confidence_below_floor"
