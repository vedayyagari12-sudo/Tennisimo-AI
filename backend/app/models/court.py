"""Session Mode Phase 2 court contract (SESSION_MODE_PHASE2.md section 7).

Frozen before any court math exists (build step 1). Nothing here is routed yet:
the endpoints in section 7.4 are blocked on Phase 1.

Rules carried from the design and the implementation brief:

* Every court enum is defined ONCE, here (brief G18). ``app/analysis/`` modules
  import and re-export them. They are deliberately not in ``enums.py``, so the
  persisted-values rule there and its tests are untouched.
* Every enum value is a lower-case wire string. Dart maps unknown strings to
  ``unrecognized``.
* Every body is ``extra="forbid"``. Null means "not measured", never 0.
* ``sigma_m`` on a landing is 1 sigma (brief G16); Flutter draws it at 1.96x.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# --------------------------------------------------------------------------- enums


class CourtEnd(StrEnum):
    NEAR = "near"
    FAR = "far"


class CourtFormat(StrEnum):
    SINGLES = "singles"
    DOUBLES = "doubles"


class CourtKeypoint(StrEnum):
    """The 14 tappable ground keypoints (brief G19), at outer-edge intersections."""

    NEAR_LEFT_DOUBLES_CORNER = "near_left_doubles_corner"
    NEAR_RIGHT_DOUBLES_CORNER = "near_right_doubles_corner"
    FAR_LEFT_DOUBLES_CORNER = "far_left_doubles_corner"
    FAR_RIGHT_DOUBLES_CORNER = "far_right_doubles_corner"
    NEAR_LEFT_SINGLES_CORNER = "near_left_singles_corner"
    NEAR_RIGHT_SINGLES_CORNER = "near_right_singles_corner"
    FAR_LEFT_SINGLES_CORNER = "far_left_singles_corner"
    FAR_RIGHT_SINGLES_CORNER = "far_right_singles_corner"
    NEAR_LEFT_SERVICE_CORNER = "near_left_service_corner"
    NEAR_RIGHT_SERVICE_CORNER = "near_right_service_corner"
    FAR_LEFT_SERVICE_CORNER = "far_left_service_corner"
    FAR_RIGHT_SERVICE_CORNER = "far_right_service_corner"
    NEAR_SERVICE_T = "near_service_t"
    FAR_SERVICE_T = "far_service_t"


class CourtLine(StrEnum):
    """The nine court lines (brief G19)."""

    NEAR_BASELINE = "near_baseline"
    FAR_BASELINE = "far_baseline"
    NEAR_SERVICE_LINE = "near_service_line"
    FAR_SERVICE_LINE = "far_service_line"
    LEFT_SINGLES_SIDELINE = "left_singles_sideline"
    RIGHT_SINGLES_SIDELINE = "right_singles_sideline"
    LEFT_DOUBLES_SIDELINE = "left_doubles_sideline"
    RIGHT_DOUBLES_SIDELINE = "right_doubles_sideline"
    CENTRE_SERVICE_LINE = "centre_service_line"


class CalibrationRejection(StrEnum):
    """Section 3.4 gates, plus ``too_few_taps`` (brief G18)."""

    TOO_FEW_TAPS = "too_few_taps"
    DUPLICATE_TAPS = "duplicate_taps"
    COLLINEAR_TAPS = "collinear_taps"
    TAPS_TOO_SMALL = "taps_too_small"
    TAPS_MIRRORED = "taps_mirrored"
    ILL_CONDITIONED = "ill_conditioned"
    REPROJECTION_ERROR_HIGH = "reprojection_error_high"
    FRAME_SHAPE_MISMATCH = "frame_shape_mismatch"
    CAMERA_POSE_IMPLAUSIBLE = "camera_pose_implausible"


class StabilityVerdict(StrEnum):
    STABLE = "stable"
    SHAKY = "shaky"
    MOVING = "moving"
    UNKNOWN = "unknown"


class BallEventKind(StrEnum):
    BOUNCE = "bounce"
    HIT = "hit"
    NET_CONTACT = "net_contact"
    UNKNOWN_KINK = "unknown_kink"


class LineVerdict(StrEnum):
    """Closed set of three. There is no fourth value."""

    IN = "in"
    OUT = "out"
    TOO_CLOSE_TO_CALL = "too_close_to_call"


class LineCallTarget(StrEnum):
    """The region a bounce was called against (brief G8)."""

    SINGLES_HALF = "singles_half"
    DOUBLES_HALF = "doubles_half"
    SERVICE_BOX_DEUCE = "service_box_deuce"
    SERVICE_BOX_AD = "service_box_ad"
    HALF_ONLY = "half_only"


class DepthZone(StrEnum):
    SHORT = "short"
    MID = "mid"
    DEEP = "deep"
    BEYOND = "beyond"


class WidthZone(StrEnum):
    LEFT = "left"
    CENTRE = "centre"
    RIGHT = "right"


class ServeZone(StrEnum):
    WIDE = "wide"
    BODY = "body"
    T = "t"


class RallyEndReason(StrEnum):
    BALL_OUT = "ball_out"
    DOUBLE_BOUNCE = "double_bounce"
    NET = "net"
    LOST_TRACK = "lost_track"
    TIMEOUT = "timeout"


class LengthConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class PlayerId(StrEnum):
    A = "player_a"
    B = "player_b"


class FinalSetRule(StrEnum):
    TIEBREAK = "tiebreak"
    MATCH_TIEBREAK_10 = "match_tiebreak_10"
    ADVANTAGE = "advantage"


class ScoreEventKind(StrEnum):
    POINT_WON = "point_won"
    POINT_VOID = "point_void"
    POINT_UNRESOLVED = "point_unresolved"


class ScoreSource(StrEnum):
    INFERRED = "inferred"
    USER = "user"


class InferenceBasis(StrEnum):
    """Section 6.8 table, one member per row."""

    BALL_OUT = "ball_out"
    DOUBLE_BOUNCE = "double_bounce"
    SERVE_FAULT = "serve_fault"
    DOUBLE_FAULT = "double_fault"
    NET = "net"
    TOO_CLOSE = "too_close"
    LOST_TRACK = "lost_track"
    UNSEEN_BOUNCE = "unseen_bounce"


class CourtSessionStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    REFUSED = "refused"


class CourtRefusalReason(StrEnum):
    """Brief G7."""

    CAMERA_MOVING = "camera_moving"
    CAMERA_UNKNOWN = "camera_unknown"
    NOT_CALIBRATED = "not_calibrated"
    FRAME_RATE_TOO_LOW = "frame_rate_too_low"


ServeSide = Literal["deuce", "ad"]
ScoreStatus = Literal["estimate", "confirmed"]
DataQuality = Literal["good", "limited"]

#: Bounds on a single tap set: 14 keypoints exist, each tappable once.
MAX_COURT_TAPS: Final[int] = 14
#: Brief G17: client event ids are free strings, not UUIDs.
CLIENT_EVENT_ID_MAX_LENGTH: Final[int] = 64

_FORBID: Final[ConfigDict] = ConfigDict(extra="forbid")

# --------------------------------------------------------------------- calibration


class CourtTapRequest(BaseModel):
    """One tap, as fractions of the VIDEO frame, y down, 0 to 1."""

    model_config = _FORBID

    keypoint: CourtKeypoint
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)


class CourtCalibrationRequest(BaseModel):
    """Section 7.2. Fewer than 4 taps is a ``too_few_taps`` REJECTION computed by
    ``court_calibration``, not a 422, so there is no ``min_length`` here."""

    model_config = _FORBID

    frame_time_s: float = Field(ge=0.0)
    frame_width_px: int = Field(gt=0)
    frame_height_px: int = Field(gt=0)
    court_format: CourtFormat
    taps: list[CourtTapRequest] = Field(max_length=MAX_COURT_TAPS)


class OverlayLine(BaseModel):
    """One fitted court line drawn back on the frame, endpoints normalised."""

    model_config = _FORBID

    line: CourtLine
    a: tuple[float, float]
    b: tuple[float, float]


class CalibrationOverlay(BaseModel):
    model_config = _FORBID

    lines: list[OverlayLine]


class CourtCalibrationResponse(BaseModel):
    """Section 7.2. A rejected calibration carries null numbers and no overlay."""

    model_config = _FORBID

    accepted: bool
    rejection_reason: CalibrationRejection | None
    calls_allowed: bool
    reprojection_rms_px: float | None = Field(ge=0.0)
    reprojection_max_px: float | None = Field(ge=0.0)
    camera_height_m: float | None
    overlay: CalibrationOverlay | None
    flags: list[str]
    calibration_version: str

    @model_validator(mode="after")
    def _accepted_matches_rejection(self) -> CourtCalibrationResponse:
        if self.accepted == (self.rejection_reason is not None):
            raise ValueError("accepted must be true exactly when rejection_reason is null")
        if self.calls_allowed and not self.accepted:
            raise ValueError("a rejected calibration cannot allow calls")
        return self


# ------------------------------------------------------------------------- results


class CameraStabilityReport(BaseModel):
    model_config = _FORBID

    verdict: StabilityVerdict
    p95_shift_fraction: float | None = Field(ge=0.0)
    rms_shift_px: float | None = Field(ge=0.0)
    bumped_at_s: float | None = Field(ge=0.0)


class CaptureInfo(BaseModel):
    """Brief G18. ``fps`` is the median PTS delta (brief G7)."""

    model_config = _FORBID

    fps: float = Field(gt=0.0)
    frame_width_px: int = Field(gt=0)
    frame_height_px: int = Field(gt=0)


class BounceSigma(BaseModel):
    """1-sigma ellipse in court metres (brief G16). ``angle_deg`` is measured from
    court +x, counter-clockwise, in [0, 180)."""

    model_config = _FORBID

    major: float = Field(ge=0.0)
    minor: float = Field(ge=0.0)
    angle_deg: float = Field(ge=0.0, lt=180.0)

    @model_validator(mode="after")
    def _major_not_below_minor(self) -> BounceSigma:
        if self.major < self.minor:
            raise ValueError("major must be >= minor")
        return self


class LineCallResponse(BaseModel):
    model_config = _FORBID

    verdict: LineVerdict
    deciding_line: CourtLine
    signed_margin_m: float
    normal_sigma_m: float = Field(ge=0.0)
    uncertainty_radius_m: float = Field(ge=0.0)
    flags: list[str]


class ShotLandingResponse(BaseModel):
    """Section 7.3 landing.

    ``line_call`` is null when calibration does not allow calls (4-5 taps: shot
    map only). ``hitter`` is filled only when a score config maps ends to
    players (brief, Decision 1 assumption); it is null otherwise.
    """

    model_config = _FORBID

    bounce_id: str = Field(min_length=1)
    time_s: float = Field(ge=0.0)
    time_sigma_s: float = Field(ge=0.0)
    court_xy_m: tuple[float, float]
    sigma_m: BounceSigma
    half: CourtEnd
    hitter_end: CourtEnd | None
    hitter: PlayerId | None = None
    shot_type: str | None
    is_serve: bool
    target: LineCallTarget
    line_call: LineCallResponse | None
    depth_zone: DepthZone
    depth_zone_confident: bool
    depth_probabilities: dict[DepthZone, float]
    width_zone: WidthZone
    confidence: float = Field(ge=0.0, le=1.0)
    rally_id: str | None
    shot_index_in_rally: int | None = Field(ge=1)
    flags: list[str]


class ShotMapSummaryResponse(BaseModel):
    """Aggregates only; no ``landings`` (brief G18). A refused session carries
    count 0 and a null ``median_sigma_along_m`` (brief G7)."""

    model_config = _FORBID

    count: int = Field(ge=0)
    verdict_counts: dict[LineVerdict, int]
    depth_expected_counts: dict[DepthZone, float]
    width_counts: dict[WidthZone, int]
    median_sigma_along_m: float | None = Field(ge=0.0)


class RallyResponse(BaseModel):
    """Brief G15: ``length_min = hits_observed + hits_inferred`` and
    ``length_min <= length_hits <= length_max``."""

    model_config = _FORBID

    rally_id: str = Field(min_length=1)
    start_s: float = Field(ge=0.0)
    end_s: float = Field(ge=0.0)
    length_hits: int = Field(ge=1)
    length_min: int = Field(ge=1)
    length_max: int = Field(ge=1)
    length_confidence: LengthConfidence
    hits_observed: int = Field(ge=0)
    hits_inferred: int = Field(ge=0)
    end_reason: RallyEndReason
    bounce_ids: list[str]
    flags: list[str]

    @model_validator(mode="after")
    def _length_invariants(self) -> RallyResponse:
        if self.end_s < self.start_s:
            raise ValueError("end_s must not precede start_s")
        if not self.length_min <= self.length_hits <= self.length_max:
            raise ValueError("require length_min <= length_hits <= length_max")
        if self.hits_observed + self.hits_inferred != self.length_min:
            raise ValueError("length_min must equal hits_observed + hits_inferred")
        return self


class CourtAnalysisResponse(BaseModel):
    model_config = _FORBID

    status: CourtSessionStatus
    refusal_reason: CourtRefusalReason | None
    court_engine_version: str
    is_estimate: bool
    camera: CameraStabilityReport
    capture: CaptureInfo
    summary: ShotMapSummaryResponse
    landings: list[ShotLandingResponse]
    rallies: list[RallyResponse]

    @model_validator(mode="after")
    def _refusal_consistent(self) -> CourtAnalysisResponse:
        refused = self.status is CourtSessionStatus.REFUSED
        if refused != (self.refusal_reason is not None):
            raise ValueError("refusal_reason is set exactly when status is refused")
        if refused and (self.landings or self.rallies or self.summary.count != 0):
            raise ValueError("a refused session carries no court output")
        return self


# --------------------------------------------------------------------------- score


class ScoreConfigRequest(BaseModel):
    """``opponent_acknowledged`` is wire-only (brief G18); the engine never reads it."""

    model_config = _FORBID

    court_format: CourtFormat
    best_of_sets: Literal[1, 3, 5]
    ad_scoring: bool
    games_per_set: int = Field(default=6, ge=1)
    tiebreak_at_games: int = Field(default=6, ge=1)
    final_set: FinalSetRule = FinalSetRule.TIEBREAK
    first_server: PlayerId = PlayerId.A
    player_a_end_at_start: CourtEnd = CourtEnd.NEAR
    opponent_acknowledged: bool


class ScoreStateResponse(BaseModel):
    """``points_display`` is player_a first ("30-15", "Deuce", "Ad-40").

    ``point_labels`` (brief G14) is ``[player_a, player_b]``. It defaults to null
    so the section 7.3 sample, which predates G14, still parses verbatim.
    """

    model_config = _FORBID

    sets: list[tuple[int, int]]
    games: tuple[int, int]
    points_display: str
    point_labels: tuple[str, str] | None = None
    in_tiebreak: bool
    server: PlayerId
    serve_side: ServeSide
    player_a_end: CourtEnd
    match_winner: PlayerId | None
    unresolved_before: int = Field(ge=0)


class ScoreTimelineEntryResponse(BaseModel):
    model_config = _FORBID

    event_id: str = Field(min_length=1)
    rally_id: str | None
    kind: ScoreEventKind
    winner: PlayerId | None
    source: ScoreSource
    basis: InferenceBasis | None
    needs_review: bool
    points_display: str


class ScoreResponse(BaseModel):
    """``status`` is "confirmed" only when every effective event is user-sourced
    (brief G13)."""

    model_config = _FORBID

    status: ScoreStatus
    needs_review_count: int = Field(ge=0)
    score_rules_version: str
    current: ScoreStateResponse
    timeline: list[ScoreTimelineEntryResponse]


class ScoreEventRequest(BaseModel):
    """A user correction. Only ``point_won`` and ``point_void`` may be posted."""

    model_config = _FORBID

    client_event_id: str = Field(min_length=1, max_length=CLIENT_EVENT_ID_MAX_LENGTH)
    kind: Literal[ScoreEventKind.POINT_WON, ScoreEventKind.POINT_VOID]
    winner: PlayerId | None
    rally_id: str | None
    supersedes: str | None

    @model_validator(mode="after")
    def _winner_matches_kind(self) -> ScoreEventRequest:
        if (self.kind is ScoreEventKind.POINT_WON) != (self.winner is not None):
            raise ValueError("point_won needs a winner; point_void must not have one")
        return self


# ---------------------------------------------------------------- Gemini payload


class CourtFeedbackLineCalls(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    in_: int = Field(alias="in", ge=0)
    out: int = Field(ge=0)
    too_close: int = Field(ge=0)
    in_percent: int = Field(ge=0, le=100)
    out_percent: int = Field(ge=0, le=100)
    too_close_percent: int = Field(ge=0, le=100)


class CourtFeedbackZone(BaseModel):
    """A depth row carries ``expected_percent`` (soft counts); a width row carries
    ``percent``. Exactly one is set."""

    model_config = _FORBID

    zone: DepthZone | WidthZone
    expected_percent: int | None = Field(default=None, ge=0, le=100)
    percent: int | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def _exactly_one_percent(self) -> CourtFeedbackZone:
        if (self.expected_percent is None) == (self.percent is None):
            raise ValueError("exactly one of expected_percent and percent is set")
        return self


class CourtFeedbackServes(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    in_: int = Field(alias="in", ge=0)
    out: int = Field(ge=0)
    too_close: int = Field(ge=0)


class CourtFeedbackRally(BaseModel):
    model_config = _FORBID

    rallies: int = Field(ge=0)
    median_hits: int | None = Field(ge=0)
    longest_hits: int | None = Field(ge=0)
    low_confidence_rallies: int = Field(ge=0)


class CourtFeedbackScore(BaseModel):
    """``included`` only when every point is user-confirmed (section 9.1); then
    game and set counts only."""

    model_config = _FORBID

    included: bool
    reason: str | None = None
    sets: list[tuple[int, int]] | None = None
    games: tuple[int, int] | None = None

    @model_validator(mode="after")
    def _counts_only_when_included(self) -> CourtFeedbackScore:
        if not self.included and (self.sets is not None or self.games is not None):
            raise ValueError("score counts are present only when included")
        return self


class CourtFeedbackBallSpeed(BaseModel):
    """Court mode never measures ball speed, so ``mph`` is always null."""

    model_config = _FORBID

    mph: None
    confidence: Literal["unavailable"]
    reason: Literal["not_measured_in_court_mode"]


class CourtFeedbackInput(BaseModel):
    """Section 9.1. No coordinates, no metre sigmas, no names, no video time."""

    model_config = _FORBID

    court_engine_version: str
    is_estimate: bool
    data_quality: DataQuality
    landings_counted: int = Field(ge=0)
    line_calls: CourtFeedbackLineCalls
    depth: list[CourtFeedbackZone]
    width: list[CourtFeedbackZone]
    serves: CourtFeedbackServes
    rally: CourtFeedbackRally
    score: CourtFeedbackScore
    ball_speed: CourtFeedbackBallSpeed
