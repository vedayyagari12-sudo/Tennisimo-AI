"""Stage 3's three calibration gates, tested as pure functions on synthetic taps."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.models.enums import CourtReference
from app.models.requests import (
    BallSpeedCalibration,
    CreateAnalysisRequest,
    NormalizedPoint,
    UploadTicketRequest,
)


def make_calibration(**overrides: Any) -> BallSpeedCalibration:
    fields: dict[str, Any] = {
        "point_a": NormalizedPoint(x=0.182, y=0.744),
        "point_b": NormalizedPoint(x=0.617, y=0.488),
        "reference": CourtReference.SIDELINE_BASELINE_TO_NET,
        "distance_m": 11.885,
        "capture_width_px": 1080,
        "capture_height_px": 1920,
    }
    fields.update(overrides)
    return BallSpeedCalibration(**fields)


def test_canonical_calibration_is_valid() -> None:
    assert make_calibration().validation_error() is None


def test_points_closer_than_the_threshold_are_rejected() -> None:
    cal = make_calibration(
        point_a=NormalizedPoint(x=0.500, y=0.500),
        point_b=NormalizedPoint(x=0.530, y=0.530),  # separation ~0.042 < 0.05
    )
    assert cal.validation_error() is not None


def test_points_exactly_at_the_threshold_are_accepted() -> None:
    cal = make_calibration(
        point_a=NormalizedPoint(x=0.5, y=0.5),
        point_b=NormalizedPoint(x=0.55, y=0.5),  # separation exactly 0.05
    )
    assert cal.validation_error() is None


def test_distance_mismatch_beyond_tolerance_is_rejected() -> None:
    assert make_calibration(distance_m=11.9).validation_error() is not None


def test_distance_within_one_centimetre_is_accepted() -> None:
    assert make_calibration(distance_m=11.880).validation_error() is None


@pytest.mark.parametrize(
    ("reference", "distance"),
    [
        (CourtReference.SIDELINE_BASELINE_TO_SERVICE_LINE, 5.485),
        (CourtReference.BASELINE_SINGLES_WIDTH, 8.230),
        (CourtReference.BASELINE_DOUBLES_WIDTH, 10.970),
    ],
)
def test_every_canonical_reference_round_trips(
    reference: CourtReference, distance: float
) -> None:
    assert make_calibration(reference=reference, distance_m=distance).validation_error() is None


def test_custom_reference_skips_the_distance_cross_check() -> None:
    cal = make_calibration(reference=CourtReference.CUSTOM, distance_m=3.75)
    assert cal.validation_error() is None


@pytest.mark.parametrize(("width", "height"), [(400, 1200), (2600, 1000)])
def test_implausible_capture_aspect_is_rejected(width: int, height: int) -> None:
    cal = make_calibration(capture_width_px=width, capture_height_px=height)
    assert cal.validation_error() is not None


def test_validation_error_is_pure_and_repeatable() -> None:
    cal = make_calibration(distance_m=11.0)
    assert cal.validation_error() == cal.validation_error()


def test_out_of_range_distance_is_a_type_error_not_a_gate() -> None:
    """Bounds with no dedicated ErrorCode stay on the model -> invalid_request."""
    with pytest.raises(ValidationError):
        make_calibration(distance_m=0.4)


def test_upload_ticket_forbids_extra_keys() -> None:
    with pytest.raises(ValidationError):
        UploadTicketRequest(content_type="video/mp4", size_bytes=1, duration_s=1.0, extra=1)


def test_create_analysis_forbids_extra_keys() -> None:
    with pytest.raises(ValidationError):
        CreateAnalysisRequest(storage_path="swing-videos/u/x.mp4", extra=1)


def test_upload_ticket_size_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        UploadTicketRequest(content_type="video/mp4", size_bytes=0, duration_s=1.0)
