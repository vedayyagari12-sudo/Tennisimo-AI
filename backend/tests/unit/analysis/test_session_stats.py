"""Session statistics (SESSION_MODE.md 10.4)."""

from __future__ import annotations

import dataclasses
import random

import numpy as np
import pytest

from app.analysis.session_stats import (
    MAD_SCALE,
    ConsistencyLabel,
    SessionShotPoint,
    SpreadTreatment,
    SwingRecord,
    TrendDirection,
    between_session_trend,
    metric_consistency,
    robust_spread,
    session_stats,
    shot_consistency,
    shot_mix,
    spread_label,
    theil_sen_slope,
    treatment_for,
    within_session_trend,
)
from app.models.enums import ShotType
from app.models.session import SwingStatus

FH = ShotType.FOREHAND_TOPSPIN
BH = ShotType.BACKHAND_TWO_HANDED
UNK = ShotType.UNKNOWN


def swing(
    index: int,
    shot: ShotType = FH,
    *,
    t: float | None = None,
    score: float | None = 70.0,
    speed: float | None = 8.0,
    **metrics: float | None,
) -> SwingRecord:
    values: dict[str, float | None] = {"peak_hand_speed_tu_s": speed}
    values.update(metrics)
    return SwingRecord(
        swing_index=index,
        contact_time_s=float(index * 10) if t is None else t,
        shot_type=shot,
        overall_score=score,
        metrics=values,
    )


# ------------------------------------------------------------ robust_spread


def test_robust_spread_known_vector() -> None:
    spread = robust_spread([1.0, 2.0, 3.0, 4.0, 100.0])
    assert spread is not None
    assert spread.median == 3.0
    assert spread.mad == 1.0
    assert spread.robust_sd == pytest.approx(MAD_SCALE)


def test_robust_spread_constant_is_zero() -> None:
    spread = robust_spread([5.0] * 6)
    assert spread is not None and spread.robust_sd == 0.0


def test_robust_spread_needs_two_values() -> None:
    assert robust_spread([]) is None
    assert robust_spread([1.0]) is None
    assert robust_spread([1.0, None]) is None


def test_outlier_barely_moves_robust_spread() -> None:
    base = [10.0, 11.0, 9.0, 10.5, 9.5, 10.2, 9.8]
    a = robust_spread(base)
    b = robust_spread(base + [500.0])
    assert a is not None and b is not None
    assert abs(b.robust_sd - a.robust_sd) < 0.5
    assert np.std(base + [500.0]) > 100


# ----------------------------------------------------------------- shot_mix


def test_shot_mix_keeps_unknown_as_its_own_slice() -> None:
    mix = shot_mix([swing(0), swing(1), swing(2, UNK), swing(3, BH)])
    by_type = {e.shot_type: e for e in mix.entries}
    assert mix.total == 4
    assert by_type[UNK].count == 1
    assert by_type[FH].percent == pytest.approx(50.0)
    assert sum(e.percent for e in mix.entries) == pytest.approx(100.0)


def test_shot_mix_drops_excluded_and_unaccepted() -> None:
    swings = [
        swing(0),
        dataclasses.replace(swing(1), user_excluded=True),
        dataclasses.replace(swing(2), status=SwingStatus.LOW_CONFIDENCE),
        dataclasses.replace(swing(3), status=SwingStatus.REJECTED),
    ]
    assert shot_mix(swings).total == 1


def test_shot_mix_empty() -> None:
    mix = shot_mix([])
    assert mix.total == 0 and mix.entries == ()


# --------------------------------------------------------- per-shot stats


def test_nothing_crosses_shot_types() -> None:
    forehands = [swing(i, FH, speed=10.0 + 0.1 * i) for i in range(10)]
    backhands = [swing(20 + i, BH, speed=4.0 + 0.1 * i) for i in range(10)]
    alone = shot_consistency(forehands, FH, {})
    mixed = shot_consistency(forehands + backhands, FH, {})
    assert alone == mixed
    speed = next(m for m in mixed.metrics if m.metric == "peak_hand_speed_tu_s")
    assert speed.median == pytest.approx(10.45)


def test_unknown_gets_no_per_shot_stats() -> None:
    with pytest.raises(ValueError):
        shot_consistency([swing(0, UNK)], UNK, {})
    stats = session_stats([swing(i, UNK) for i in range(10)], 600, {}, False, rubric_version="r")
    assert stats.consistency == ()
    assert stats.counted_swings == 10


def test_none_is_dropped_and_real_zero_kept() -> None:
    values = [0.0, 0.0, 0.0, 0.0, 0.0, None, None, 1.0]
    result = metric_consistency(values, "swing_path_angle_deg", None)
    assert result.n == 6
    assert result.median == 0.0
    assert result.minimum == 0.0


@pytest.mark.parametrize(
    ("n", "treatment"),
    [
        (4, SpreadTreatment.COUNT_ONLY),
        (5, SpreadTreatment.RANGE_ONLY),
        (7, SpreadTreatment.RANGE_ONLY),
        (8, SpreadTreatment.LABEL_WITH_N),
        (14, SpreadTreatment.LABEL_WITH_N),
        (15, SpreadTreatment.LABEL_WITH_N),
        (19, SpreadTreatment.LABEL_WITH_N),
        (20, SpreadTreatment.LABEL),
    ],
)
def test_sample_size_boundaries(n: int, treatment: SpreadTreatment) -> None:
    assert treatment_for(n) is treatment
    result = metric_consistency([1.0 + 0.01 * i for i in range(n)], "tempo_ratio", (1.5, 3.0))
    assert result.treatment is treatment
    if treatment is SpreadTreatment.COUNT_ONLY:
        assert result.median is None and result.minimum is None and result.label is None
    elif treatment is SpreadTreatment.RANGE_ONLY:
        assert result.minimum is not None and result.label is None and result.robust_sd is None
    else:
        assert result.label is ConsistencyLabel.TIGHT
        assert result.provisional


def test_cv_is_not_produced_for_signed_metrics() -> None:
    values = [10.0 + i for i in range(10)]
    signed = metric_consistency(values, "swing_path_angle_deg", None)
    ratio = metric_consistency(values, "tempo_ratio", None)
    assert signed.robust_cv is None
    assert ratio.robust_cv is not None


@pytest.mark.parametrize(
    ("sd", "label"),
    [(0.5, ConsistencyLabel.TIGHT), (1.0, ConsistencyLabel.MODERATE), (1.01, ConsistencyLabel.WIDE)],
)
def test_spread_label_against_band_width(sd: float, label: ConsistencyLabel) -> None:
    assert spread_label(sd, (0.0, 1.0)) is label


def test_unbanded_metric_gets_no_label() -> None:
    assert spread_label(0.1, None) is None
    assert spread_label(0.1, (1.0, 1.0)) is None
    result = metric_consistency([1.0 + i for i in range(20)], "tempo_ratio", None)
    assert result.label is None and result.robust_sd is not None
    assert not result.provisional


# -------------------------------------------------------------------- trends


def trend_swings(values: list[float], span_s: float = 900.0) -> list[SwingRecord]:
    step = span_s / (len(values) - 1)
    return [
        swing(i, t=i * step, speed=None, tempo_ratio=v) for i, v in enumerate(values)
    ]


def test_trend_flat_is_steady() -> None:
    trend = within_session_trend(trend_swings([2.0] * 21), FH, "tempo_ratio")
    assert trend is not None and trend.direction is TrendDirection.STEADY


def test_trend_rising_and_falling() -> None:
    rising = within_session_trend(
        trend_swings([1.5 + 0.05 * i for i in range(21)]), FH, "tempo_ratio"
    )
    falling = within_session_trend(
        trend_swings([3.0 - 0.05 * i for i in range(21)]), FH, "tempo_ratio"
    )
    assert rising is not None and rising.direction is TrendDirection.RISING
    assert falling is not None and falling.direction is TrendDirection.FALLING
    assert rising.delta > 0 and rising.slope_per_10_min > 0


def test_trend_noisy_without_signal_is_steady() -> None:
    rng = random.Random(7)
    values = [2.0 + rng.uniform(-0.5, 0.5) for _ in range(30)]
    trend = within_session_trend(trend_swings(values), FH, "tempo_ratio")
    assert trend is not None and trend.direction is TrendDirection.STEADY


def test_trend_resists_outliers() -> None:
    values = [2.0] * 21
    values[-1] = 50.0
    values[-2] = 40.0
    trend = within_session_trend(trend_swings(values), FH, "tempo_ratio")
    assert trend is not None and trend.direction is TrendDirection.STEADY


def test_trend_needs_span_and_n() -> None:
    assert within_session_trend(trend_swings([2.0] * 21, span_s=200.0), FH, "tempo_ratio") is None
    assert within_session_trend(trend_swings([2.0] * 14), FH, "tempo_ratio") is None
    assert within_session_trend(trend_swings([2.0] * 15), FH, "tempo_ratio") is not None
    assert within_session_trend(trend_swings([2.0] * 21), UNK, "tempo_ratio") is None


def test_theil_sen_slope() -> None:
    assert theil_sen_slope([0, 1, 2, 3, 4], [0, 2, 4, 6, 100]) == pytest.approx(2.0)
    assert theil_sen_slope([1, 1], [0, 5]) == 0.0


# --------------------------------------------------------- between sessions


def point(
    sid: str, started: float, score: float, *, n: int = 10, pipe: str = "p1", seg: str = "seg_v1"
) -> SessionShotPoint:
    return SessionShotPoint(
        session_id=sid,
        started_at_s=started,
        pipeline_version=pipe,
        segmentation_version=seg,
        shot_type=FH,
        n=n,
        overall_score_median=score,
        metric_medians={"tempo_ratio": score / 30.0, "peak_hand_speed_tu_s": 9.0},
    )


def test_between_session_rising_and_steady() -> None:
    rising = between_session_trend([point("a", 1, 60), point("b", 2, 66)])
    assert rising is not None and rising.direction is TrendDirection.RISING
    steady = between_session_trend([point("a", 1, 60), point("b", 2, 61.5)])
    assert steady is not None and steady.direction is TrendDirection.STEADY


def test_between_session_excludes_version_mismatch() -> None:
    trend = between_session_trend(
        [point("a", 1, 40, pipe="old"), point("b", 2, 60), point("c", 3, 62)]
    )
    assert trend is not None and trend.sessions == 2 and trend.first_score == 60
    assert between_session_trend([point("a", 1, 40, seg="seg_v0"), point("b", 2, 60)]) is None


def test_between_session_needs_n_per_session() -> None:
    assert between_session_trend([point("a", 1, 60, n=7), point("b", 2, 66)]) is None


def test_between_session_never_trends_view_sensitive_metrics() -> None:
    trend = between_session_trend([point("a", 1, 60), point("b", 2, 66)])
    assert trend is not None
    assert "peak_hand_speed_tu_s" not in trend.metric_deltas
    assert trend.metric_deltas["tempo_ratio"] == pytest.approx(0.2)


def test_between_session_rejects_mixed_shot_types() -> None:
    with pytest.raises(ValueError):
        between_session_trend([point("a", 1, 60), dataclasses.replace(point("b", 2, 6), shot_type=BH)])


# ------------------------------------------------------------- whole session


def sample_session() -> list[SwingRecord]:
    rng = random.Random(3)
    swings = []
    for i in range(40):
        shot = (FH, BH, UNK)[i % 3]
        swings.append(
            swing(
                i,
                shot,
                t=i * 20.0,
                score=60 + rng.uniform(-5, 5),
                speed=8 + rng.uniform(-1, 1),
                tempo_ratio=2.0 + rng.uniform(-0.2, 0.2),
                contact_height_ratio=0.9 + rng.uniform(-0.05, 0.05),
            )
        )
    return swings


def test_session_stats_whole() -> None:
    swings = sample_session()
    swings[0] = dataclasses.replace(swings[0], ball_speed_mph=55)
    stats = session_stats(
        swings, 800.0, {FH: {"tempo_ratio": (1.5, 3.0)}}, True, rubric_version="rubric_vX"
    )
    assert stats.rubric_version == "rubric_vX"
    assert stats.counted_swings == 40
    assert stats.swings_per_minute == pytest.approx(3.0)
    assert [c.shot_type for c in stats.consistency] == [FH, BH]
    assert stats.ball_speed is not None
    fh_speed = next(b for b in stats.ball_speed if b.shot_type is FH)
    assert fh_speed.n_measured == 1 and fh_speed.max_mph == 55
    assert fh_speed.n_missing == 13
    assert session_stats(swings, 800.0, {}, False, rubric_version="r").ball_speed is None


def test_session_stats_is_deterministic_and_order_free() -> None:
    swings = sample_session()
    shuffled = list(swings)
    random.Random(11).shuffle(shuffled)
    a = session_stats(swings, 800.0, {}, True, rubric_version="r")
    b = session_stats(shuffled, 800.0, {}, True, rubric_version="r")
    c = session_stats(swings, 800.0, {}, True, rubric_version="r")
    assert a == b == c


def test_excluding_a_swing_equals_computing_without_it() -> None:
    swings = sample_session()
    excluded = list(swings)
    excluded[5] = dataclasses.replace(excluded[5], user_excluded=True)
    without = swings[:5] + swings[6:]
    assert session_stats(excluded, 800.0, {}, True, rubric_version="r") == session_stats(
        without, 800.0, {}, True, rubric_version="r"
    )


def test_zero_duration_has_no_rate() -> None:
    assert session_stats([], 0.0, {}, False, rubric_version="r").swings_per_minute is None
