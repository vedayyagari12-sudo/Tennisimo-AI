"""Match mode wire models (2026-11-09): a rally/match video in, the court
engine's shot map, line calls and rallies out.

A match is a background job like a swing analysis, but its record lives as
one JSON object in the user's own storage folder
(``{bucket}/{user_id}/matches/{match_id}.json``), not in a table: no migration
is needed, and account deletion already removes the whole folder. The video
is deleted when processing ends (SESSION_MODE.md Decision 3).
"""

from __future__ import annotations

from datetime import datetime
from typing import Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.court import CourtAnalysisResponse, CourtCalibrationRequest, CourtFormat
from app.models.enums import ErrorCode
from app.models.responses import CoachingFeedback

MatchStatus = Literal["queued", "running", "complete", "failed"]
CalibrationSource = Literal["taps", "auto"]
#: "match": shot map, calls and rallies. "rally": the same numbers plus an
#: overall coaching summary that Gemini writes from them (text only).
SessionKind = Literal["match", "rally"]
#: What a running job is doing, for the progress bar: getting the video ready,
#: finding the court lines, following the ball, following the players, writing
#: the result.
MatchStage = Literal["preparing", "court", "ball", "players", "finishing"]

#: A match video may run this long (owner, 2026-11-09).
MAX_MATCH_SECONDS: Final[float] = 300.0
#: At most this many uploaded pieces (``requests.MATCH_MAX_PARTS``).
MAX_MATCH_PARTS: Final[int] = 12


class CourtCheck(BaseModel):
    """How far the court calibration can be trusted, and why. Written as soon
    as the court is found, long before the analysis ends, so the player can
    look at the lines and stop a rally that would be wasted."""

    confidence: Literal["good", "check", "poor"]
    source: CalibrationSource
    #: Share of the court lines that matched the picture (automatic only).
    support: float | None = Field(default=None, ge=0.0, le=1.0)
    lines_visible: int | None = Field(default=None, ge=0)
    #: How many of the four doubles-court corners are inside the picture.
    corners_in_frame: int = Field(ge=0, le=4)
    #: Why it is not "good": ``court_cut_off``, ``few_lines``, ``weak_match``,
    #: ``calls_off``, ``tap_error``.
    reasons: list[str] = Field(default_factory=list)
    calls_allowed: bool


class CreateMatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    storage_path: str = Field(min_length=1, max_length=512)
    court_format: CourtFormat = CourtFormat.SINGLES
    #: Optional court taps. Without them (or when they are rejected) the court
    #: is found automatically from its lines.
    calibration: CourtCalibrationRequest | None = None
    title: str | None = Field(default=None, max_length=80)
    kind: SessionKind = "match"
    #: How many pieces the video was uploaded in (``{storage_path}.part000``
    #: ...). 0: one object at ``storage_path``.
    part_count: int = Field(default=0, ge=0, le=MAX_MATCH_PARTS)


class MatchStats(BaseModel):
    """Headline numbers, computed from the result so a list can show them
    without the whole shot map. Null means not measured."""

    shots_landed: int = Field(ge=0)
    rallies: int = Field(ge=0)
    longest_rally_hits: int | None = Field(default=None, ge=1)
    average_rally_hits: float | None = Field(default=None, ge=0.0)
    in_count: int = Field(ge=0)
    out_count: int = Field(ge=0)
    #: Share of called landings that were IN; null when nothing was called.
    in_percent: float | None = Field(default=None, ge=0.0, le=100.0)
    deep_percent: float | None = Field(default=None, ge=0.0, le=100.0)


class MatchSummary(BaseModel):
    """One match without its shot map: the list view."""

    match_id: UUID
    status: MatchStatus
    created_at: datetime
    updated_at: datetime
    title: str | None = None
    #: Records written before rallies existed have no kind: they are matches.
    kind: SessionKind = "match"
    court_format: CourtFormat
    progress: float | None = Field(default=None, ge=0.0, le=1.0)
    #: While running: the step it is on, and seconds left at the rate so far
    #: (null until there is enough progress to estimate from).
    stage: MatchStage | None = None
    eta_s: float | None = Field(default=None, ge=0.0)
    error_code: ErrorCode | None = None
    error_message: str | None = None
    court_status: str | None = None
    stats: MatchStats | None = None


class MatchRecord(MatchSummary):
    """The stored object and the poll body."""

    model_config = ConfigDict(extra="ignore")

    calibration_source: CalibrationSource | None = None
    #: A picture of the court lines that were used (``GET .../court.jpg``).
    #: Available while the job runs, as soon as the court is found.
    has_court_picture: bool = False
    #: Whether those lines can be trusted; same timing as the picture.
    court_check: CourtCheck | None = None
    #: Seconds of video the engine analysed.
    duration_s: float | None = Field(default=None, ge=0.0)
    result: CourtAnalysisResponse | None = None
    #: Rallies only: Gemini's wording of the computed numbers (or the
    #: deterministic template when there is too little to say). Null for a
    #: match, or while running.
    feedback: CoachingFeedback | None = None

    def summary(self) -> MatchSummary:
        return MatchSummary.model_validate(self.model_dump(include=set(MatchSummary.model_fields)))


class MatchListResponse(BaseModel):
    items: list[MatchSummary]


__all__ = [
    "CalibrationSource",
    "CourtCheck",
    "CreateMatchRequest",
    "MAX_MATCH_SECONDS",
    "MatchListResponse",
    "MatchRecord",
    "MatchStage",
    "MatchStats",
    "MatchStatus",
    "MatchSummary",
    "SessionKind",
]
