"""The Part 4.4a discriminator invariant, plus ErrorCode completeness.

The disjointness assertion is cheap and catches the mistake at the moment
someone makes it. Without it, adding `complete` to JobStatus or `succeeded` to
AnalysisStatus silently breaks the Flutter client's finished/in-progress
discriminator, and the symptom is a 120 s spinner on a successful job.
"""

from __future__ import annotations

from app.api.errors import ERROR_MESSAGES, ERROR_RETRYABLE
from app.models.enums import (
    COURT_REFERENCE_METRES,
    AnalysisStatus,
    CourtReference,
    ErrorCode,
    JobStatus,
)


def test_job_status_and_analysis_status_value_sets_are_disjoint() -> None:
    assert {e.value for e in JobStatus} & {e.value for e in AnalysisStatus} == set()


def test_job_status_members_are_exactly_the_four_database_values() -> None:
    assert {e.value for e in JobStatus} == {"queued", "running", "succeeded", "failed"}


def test_analysis_status_members_are_exactly_complete_and_partial() -> None:
    assert {e.value for e in AnalysisStatus} == {"complete", "partial"}


def test_error_code_has_the_full_twenty_members() -> None:
    """PIPELINE.md §2.1's 18 plus DATABASE_SETUP.md Part 3.0.1's two."""
    assert len(ErrorCode) == 20
    assert ErrorCode.INVALID_REQUEST.value == "invalid_request"
    assert ErrorCode.ANALYSIS_NOT_FOUND.value == "analysis_not_found"


def test_error_code_matches_the_ddl_check_constraint_list() -> None:
    """DATABASE_SETUP.md Part 1's analysis_jobs_error_code_check, verbatim."""
    ddl_codes = {
        "auth_invalid_token", "storage_path_forbidden", "object_not_found",
        "storage_unavailable", "file_too_large", "unsupported_content_type",
        "unsupported_codec", "decode_failed", "no_video_stream",
        "video_too_short", "video_too_long", "no_pose_detected",
        "pose_quality_too_low", "contact_not_found", "calibration_invalid",
        "queue_full", "worker_lost", "internal_error",
        "invalid_request", "analysis_not_found",
    }
    assert {e.value for e in ErrorCode} == ddl_codes


def test_every_error_code_has_a_retryability_and_a_message() -> None:
    for code in ErrorCode:
        assert code in ERROR_RETRYABLE
        assert code in ERROR_MESSAGES


def test_retryable_codes_are_exactly_the_four_in_the_table() -> None:
    retryable = {code for code, flag in ERROR_RETRYABLE.items() if flag}
    assert retryable == {
        ErrorCode.QUEUE_FULL,
        ErrorCode.STORAGE_UNAVAILABLE,
        ErrorCode.WORKER_LOST,
        ErrorCode.INTERNAL_ERROR,
    }


def test_court_reference_metres_covers_every_member_except_custom() -> None:
    """CUSTOM has no canonical length; a fabricated one would be worse than absent."""
    assert set(COURT_REFERENCE_METRES) == set(CourtReference) - {CourtReference.CUSTOM}
    assert COURT_REFERENCE_METRES[CourtReference.SIDELINE_BASELINE_TO_NET] == 11.885
