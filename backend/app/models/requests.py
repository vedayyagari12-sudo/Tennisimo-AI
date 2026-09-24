"""Request bodies and their paired acknowledgement responses (PIPELINE.md §2.2).

``UploadTicketResponse`` and ``CreateAnalysisResponse`` live here rather than in
``responses.py`` because §2.2 and DATABASE_SETUP.md Part 5.1 both place them
here: they are acknowledgements of a request, not analysis results.

TWO DELIBERATE DEVIATIONS FROM §2.2, both made so the route can raise the
*specific* ``ErrorCode`` the Stage 1 / Stage 3 contract promises instead of the
generic ``invalid_request`` a Pydantic failure produces:

1. ``UploadTicketRequest.content_type`` carries no ``pattern=`` and
   ``size_bytes`` carries no ``le=``. §2.2 puts both constraints on the field,
   but a field-level failure surfaces as ``400 invalid_request`` via the
   ``RequestValidationError`` handler, whereas Stage 1 promises
   ``400 unsupported_content_type`` and ``413 file_too_large`` respectively.
   The checks move into ``routes_uploads.py``, which reads
   ``settings.max_upload_bytes`` — keeping ``config.MAX_UPLOAD_BYTES`` the
   single source for the cap, as DATABASE_SETUP.md Part 3.1 requires.
   ``ALLOWED_CONTENT_TYPES`` below is the one definition of the accepted set.

2. ``BallSpeedCalibration`` has no ``@model_validator`` running the three
   Stage 3 checks. Those live in ``validation_error()``, a pure method
   returning a reason string, because §2.2's own docstring says they must
   produce ``400 CALIBRATION_INVALID`` and a raising validator cannot. Bounds
   that have no dedicated error code (``gt``/``le``/``ge`` on individual
   fields, ``extra="forbid"``) are left on the model and do yield
   ``400 invalid_request``, which is correct for them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.config import MAX_UPLOAD_BYTES
from app.models.enums import (
    COURT_REFERENCE_METRES,
    CameraView,
    CourtReference,
    Handedness,
    JobStatus,
    ShotType,
)

#: Accepted upload MIME types and the extension each maps to. The server
#: chooses the path, so this map is also the extension authority (Stage 1).
#:
#: The three entries are the three things a real capture surface emits:
#: ``video/mp4`` from the Flutter camera plugin and from iOS Safari's
#: MediaRecorder, ``video/quicktime`` from an iOS photo-library pick, and
#: ``video/webm`` from Android Chrome's MediaRecorder, which has no MP4 muxer
#: and produces Matroska/WebM with a VP8 or VP9 video track.
#:
#: ``video/webm`` is listed only because the decode path was checked rather than
#: assumed: Stage 5 (``app/pose/video_io.py``) and Stage 10
#: (``app/ball/frames.py``) both demux with PyAV, whose bundled FFmpeg is built
#: ``--enable-libvpx`` and carries the matroska/webm demuxer on both the local
#: Windows wheel and the manylinux wheel the container installs. Accepting a
#: MIME type the pipeline cannot decode would move the failure from a clear
#: 400 at the ticket endpoint to a DECODE_FAILED after the user has waited out
#: an upload. See ``tests/unit/pose/test_webm_decode.py``.
ALLOWED_CONTENT_TYPES: Final[dict[str, str]] = {
    "video/mp4": "mp4",
    "video/quicktime": "mov",
    "video/webm": "webm",
}

#: Stage 3 calibration gates. Named so no other module holds the literals.
MIN_TAP_SEPARATION_NORMALIZED: Final[float] = 0.05
REFERENCE_DISTANCE_TOLERANCE_M: Final[float] = 0.01
MIN_CAPTURE_ASPECT_RATIO: Final[float] = 0.4
MAX_CAPTURE_ASPECT_RATIO: Final[float] = 2.5


class UploadTicketRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_type: str = Field(
        description=(
            "MIME type of the video the client is about to upload. Must be one of "
            "ALLOWED_CONTENT_TYPES; checked in the route so the failure is "
            "400 unsupported_content_type rather than 400 invalid_request."
        ),
    )
    size_bytes: int = Field(
        gt=0,
        description=(
            f"Declared size. Hard cap {MAX_UPLOAD_BYTES} bytes (50 MiB, Supabase "
            "free-tier per-file limit), enforced in the route so the failure is "
            "413 file_too_large."
        ),
    )
    duration_s: float = Field(gt=0.0, le=60.0, description="Client-measured clip duration, seconds.")


class UploadTicketResponse(BaseModel):
    storage_path: str = Field(
        description=(
            "Canonical path: swing-videos/{user_id}/{uuid4}.{ext}, where {ext} is "
            "the extension ALLOWED_CONTENT_TYPES maps the requested content_type "
            "to (mp4, mov or webm). The server chooses the whole path; the client "
            "echoes it back verbatim on POST /v1/analyses."
        )
    )
    upload_url: str = Field(description="Supabase Storage signed upload URL.")
    upload_token: str = Field(description="Token to pass to the Supabase Storage client.")
    expires_at: datetime = Field(description="UTC expiry of the signed URL.")


class NormalizedPoint(BaseModel):
    """A tap location, normalized to the PREVIEW SURFACE the user touched.

    Not normalized to the recorded frame. The two differ whenever the client
    letterboxes, crops, or rotates the preview -- which is the common case on
    Android. capture_width_px / capture_height_px / capture_rotation_deg on
    BallSpeedCalibration describe the space these coordinates live in, and the
    server inverts that transform (Stage 11.1).
    """

    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0.0, le=1.0, description="Fraction of preview surface width.")
    y: float = Field(ge=0.0, le=1.0, description="Fraction of preview surface height, y DOWN.")


class BallSpeedCalibration(BaseModel):
    """One-time pre-recording calibration: two tapped court points + a real distance.

    Absent from CreateAnalysisRequest => ball detection and speed calculation are
    skipped entirely. That is a normal, non-error outcome.
    """

    model_config = ConfigDict(extra="forbid")

    point_a: NormalizedPoint
    point_b: NormalizedPoint
    reference: CourtReference = Field(
        description="Which court feature was tapped. Determines the canonical distance "
        "and which camera views the calibration is valid for.",
    )
    distance_m: float = Field(
        gt=0.5,
        le=25.0,
        description="Real-world distance between the two points, metres. Must match the "
        "canonical value for `reference` unless reference == custom.",
    )
    capture_width_px: int = Field(gt=0, description="Preview surface width the taps were made on.")
    capture_height_px: int = Field(gt=0, description="Preview surface height the taps were made on.")
    capture_rotation_deg: Literal[0, 90, 180, 270] = Field(
        default=0,
        description="Rotation the client applied to recorded frames to produce that preview. "
        "The server inverts this before applying the container display matrix.",
    )
    tapped_at: datetime | None = Field(
        default=None,
        description="When calibration was performed. Advisory: a calibration reused across a "
        "camera move is invalid, and a stale timestamp is the only hint we get.",
    )

    def validation_error(self) -> str | None:
        """PURE. Return a human-readable reason, or ``None`` when valid.

        The three Stage 3 checks, in the order the spec lists them. The caller
        turns a non-``None`` result into ``400 calibration_invalid``.

        No I/O, no network, no clock. Unit-tested with synthetic points.
        """
        dx = self.point_a.x - self.point_b.x
        dy = self.point_a.y - self.point_b.y
        separation = (dx * dx + dy * dy) ** 0.5
        if separation < MIN_TAP_SEPARATION_NORMALIZED:
            return (
                "The two calibration points are too close together. "
                "Tap two clearly separated points on the court."
            )

        if self.reference is not CourtReference.CUSTOM:
            canonical = COURT_REFERENCE_METRES[self.reference]
            if abs(self.distance_m - canonical) > REFERENCE_DISTANCE_TOLERANCE_M:
                # Never silently overridden server-side: a mismatch means the
                # client build is desynced, and correcting it hides that.
                return (
                    "The calibration distance does not match the selected court reference. "
                    "Update the app and calibrate again."
                )

        aspect = self.capture_width_px / self.capture_height_px
        if not (MIN_CAPTURE_ASPECT_RATIO <= aspect <= MAX_CAPTURE_ASPECT_RATIO):
            return "The calibration was captured at an implausible screen shape."

        return None


class CreateAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    storage_path: str = Field(
        min_length=1,
        max_length=512,
        description="Path returned by /v1/uploads/ticket. Must be prefixed swing-videos/{user_id}/.",
    )
    handedness_hint: Handedness | None = Field(
        default=None,
        description="From the user profile. Used when auto-detection confidence < 0.5.",
    )
    camera_view_hint: CameraView = Field(
        default=CameraView.UNKNOWN,
        description="Which framing guide the user filmed with. Advisory only; the server's own "
        "estimate_camera_view() is what gates ball speed.",
    )
    client_capture_fps: float | None = Field(
        default=None,
        gt=0.0,
        le=240.0,
        description="Nominal capture fps reported by the phone. Advisory; real timing comes from PTS.",
    )
    label_hint: ShotType | None = Field(
        default=None,
        description="Optional user-declared shot type. Recorded for eval ONLY; never overrides "
        "technique inference and never enters the Gemini payload.",
    )
    ball_speed_calibration: BallSpeedCalibration | None = Field(
        default=None,
        description="Optional. Absent => ball speed is not measured, ball_speed_mph is null with "
        "reason 'not_calibrated', and the analysis proceeds normally. Never an error.",
    )


class CreateAnalysisResponse(BaseModel):
    analysis_id: UUID
    status: JobStatus
    poll_url: str = Field(description="Relative URL: /v1/analyses/{analysis_id}")
    estimated_seconds: int = Field(description="Rough ETA including queue depth and calibration.")
    ball_speed_requested: bool = Field(
        description="Echo: whether a calibration block was supplied and accepted. Lets the client "
        "show the right placeholder while polling.",
    )
