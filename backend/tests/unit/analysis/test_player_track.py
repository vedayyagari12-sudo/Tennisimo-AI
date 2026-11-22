"""``app/analysis/player_track.py`` with synthetic skeletons (no model, no video).

A skeleton is built in a 1920x1080 frame from a hip centre, a torso length
and which way the player faces. Facing AWAY from the camera (the near player)
the player's right is on the image right; facing the camera (the far player)
it is on the image left. The anatomical left/right of the landmarks is what
the module reads, so both must give the same answer.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.analysis.player_track import (
    L_ANKLE,
    L_HIP,
    L_SHOULDER,
    L_WRIST,
    NOSE,
    R_ANKLE,
    R_HIP,
    R_SHOULDER,
    R_WRIST,
    Contact,
    PlayerFrame,
    classify_stroke,
    contact_candidate,
    estimate_handedness,
    feet_point,
    lateral_offset,
    on_players_side,
    pose_hit_from_contact,
    torso_px,
)
from app.models.court import CourtEnd

SIZE = (1920, 1080)
TORSO = 100.0
SCALE = 150.0  # pixels per metre, so the wrist gate is 180 px


def skeleton(
    hip: tuple[float, float] = (900.0, 700.0),
    *,
    facing_away: bool = True,
    right_wrist: tuple[float, float] | None = None,
    left_wrist: tuple[float, float] | None = None,
    shoulder_half_width: float = 40.0,
) -> np.ndarray:
    """(33, 4) landmarks as fractions of the frame. Wrists are offsets from
    the hip centre in IMAGE pixels; the default is a hand hanging at the hip."""
    lm = np.zeros((33, 4), dtype=np.float32)
    sign = 1.0 if facing_away else -1.0  # image x of the player's right

    def put(index: int, x: float, y: float) -> None:
        lm[index] = (x / SIZE[0], y / SIZE[1], 0.0, 0.95)

    hx, hy = hip
    put(L_HIP, hx - sign * 25, hy)
    put(R_HIP, hx + sign * 25, hy)
    put(L_SHOULDER, hx - sign * shoulder_half_width, hy - TORSO)
    put(R_SHOULDER, hx + sign * shoulder_half_width, hy - TORSO)
    put(NOSE, hx, hy - TORSO - 40)
    put(L_ANKLE, hx - sign * 20, hy + 160)
    put(R_ANKLE, hx + sign * 20, hy + 160)
    rw = right_wrist if right_wrist is not None else (sign * 30, 10)
    lw = left_wrist if left_wrist is not None else (-sign * 30, 10)
    put(R_WRIST, hx + rw[0], hy + rw[1])
    put(L_WRIST, hx + lw[0], hy + lw[1])
    return lm


def swing_frames(
    *, speed_px_s: float, hit_time: float = 1.0, start_x: float = 1020.0, y: float = 705.0
) -> list[PlayerFrame]:
    """20 Hz frames either side of ``hit_time``: the right wrist moves
    horizontally at ``speed_px_s`` and passes (``start_x``, ``y``) at the hit;
    the left hand hangs still."""
    frames: list[PlayerFrame] = []
    for k in range(-6, 7):
        t = hit_time + k * 0.05
        wx = start_x + speed_px_s * (t - hit_time)
        frames.append(
            PlayerFrame(
                time_s=t,
                landmarks=skeleton(right_wrist=(wx - 900.0, y - 700.0)),
            )
        )
    return frames


def contact_with(wrist_dx: float, *, facing_away: bool = True, wrist: int = R_WRIST, **skeleton_kwargs: object) -> Contact:
    lm = skeleton(facing_away=facing_away, right_wrist=(wrist_dx, 10), left_wrist=(wrist_dx, 10), **skeleton_kwargs)  # type: ignore[arg-type]
    point = (900.0 + wrist_dx, 710.0)
    return Contact(
        wrist=wrist,
        wrist_xy_px=point,
        peak_speed=5.0,
        distance_px=3.0,
        landmarks=lm,
        frame_time_s=1.0,
        frame_size=SIZE,
    )


# ------------------------------------------------------------- the swing


def test_the_wrist_at_the_ball_that_swung_is_the_hitter() -> None:
    contact = contact_candidate(swing_frames(speed_px_s=600.0), 1.0, (1020.0, 705.0), SIZE, SCALE)
    assert contact is not None
    assert contact.wrist == R_WRIST
    assert contact.distance_px == pytest.approx(0.0, abs=1.0)
    assert contact.peak_speed > 2.0


def test_a_wrist_that_only_happens_to_be_near_the_ball_is_not_a_hit() -> None:
    # 100 px/s is one torso per second: standing, not swinging.
    assert contact_candidate(swing_frames(speed_px_s=100.0), 1.0, (1020.0, 705.0), SIZE, SCALE) is None


def test_a_swing_far_from_the_ball_is_another_players() -> None:
    assert contact_candidate(swing_frames(speed_px_s=600.0), 1.0, (1020.0 + 600.0, 705.0), SIZE, SCALE) is None


def test_too_few_frames_give_no_contact() -> None:
    assert contact_candidate(swing_frames(speed_px_s=600.0)[:1], 1.0, (1020.0, 705.0), SIZE, SCALE) is None
    assert contact_candidate([], 1.0, (1020.0, 705.0), SIZE, SCALE) is None


def test_a_pose_taken_long_before_the_hit_does_not_count() -> None:
    frames = swing_frames(speed_px_s=600.0, hit_time=1.0)
    assert contact_candidate(frames, 5.0, (1020.0, 705.0), SIZE, SCALE) is None


# ------------------------------------------------------------ body frame


def test_torso_and_feet_are_read_in_pixels() -> None:
    lm = skeleton()
    assert torso_px(lm, SIZE) == pytest.approx(TORSO)
    feet = feet_point(lm, SIZE)
    assert feet == pytest.approx((900.0, 860.0))
    hidden = lm.copy()
    hidden[[L_ANKLE, R_ANKLE], 3] = 0.0
    assert feet_point(hidden, SIZE) is None
    hidden[[L_HIP], 3] = 0.0
    assert torso_px(hidden, SIZE) is None


@pytest.mark.parametrize("facing_away", [True, False])
def test_the_players_right_is_positive_whichever_way_they_face(facing_away: bool) -> None:
    sign = 1.0 if facing_away else -1.0
    right_side = contact_with(sign * 120.0, facing_away=facing_away)
    left_side = contact_with(-sign * 120.0, facing_away=facing_away)
    assert lateral_offset(right_side) == pytest.approx(1.2, abs=0.05)
    assert lateral_offset(left_side) == pytest.approx(-1.2, abs=0.05)


def test_a_side_on_body_cannot_be_read() -> None:
    contact = contact_with(120.0, shoulder_half_width=5.0)
    assert lateral_offset(contact) is None


# ------------------------------------------------------- handedness, stroke


def test_handedness_needs_enough_swings_and_a_majority() -> None:
    assert estimate_handedness([R_WRIST, R_WRIST]) is None
    assert estimate_handedness([R_WRIST, R_WRIST, L_WRIST]) == "right"
    assert estimate_handedness([L_WRIST, L_WRIST, L_WRIST, R_WRIST]) == "left"
    assert estimate_handedness([L_WRIST, R_WRIST, L_WRIST, R_WRIST]) is None


@pytest.mark.parametrize("facing_away", [True, False])
def test_a_right_hander_hits_forehands_on_the_right_and_backhands_on_the_left(facing_away: bool) -> None:
    sign = 1.0 if facing_away else -1.0
    forehand = contact_with(sign * 120.0, facing_away=facing_away)
    backhand = contact_with(-sign * 120.0, facing_away=facing_away)
    assert classify_stroke(forehand, "right", launch=False, ground_along_m=12.0) == "forehand"
    assert classify_stroke(backhand, "right", launch=False, ground_along_m=12.0) == "backhand"


def test_a_left_hander_is_the_mirror() -> None:
    right_side = contact_with(120.0)
    assert classify_stroke(right_side, "left", launch=False, ground_along_m=12.0) == "backhand"


def test_an_unclear_stroke_is_none_not_a_guess() -> None:
    assert classify_stroke(contact_with(120.0), None, launch=False, ground_along_m=12.0) is None
    assert classify_stroke(contact_with(3.0), "right", launch=False, ground_along_m=12.0) is None
    side_on = contact_with(120.0, shoulder_half_width=5.0)
    assert classify_stroke(side_on, "right", launch=False, ground_along_m=12.0) is None


def test_a_wrist_above_the_head_is_a_serve_at_a_launch_else_an_overhead() -> None:
    lm = skeleton()
    high = Contact(
        wrist=R_WRIST, wrist_xy_px=(930.0, 700.0 - TORSO - 40 - 80), peak_speed=5.0,
        distance_px=2.0, landmarks=lm, frame_time_s=1.0, frame_size=SIZE,
    )
    assert classify_stroke(high, "right", launch=True, ground_along_m=11.0) == "serve"
    assert classify_stroke(high, "right", launch=False, ground_along_m=11.0) == "overhead"


def test_a_hitter_at_the_net_is_volleying() -> None:
    assert classify_stroke(contact_with(120.0), "right", launch=False, ground_along_m=3.0) == "volley"
    assert classify_stroke(contact_with(120.0), None, launch=False, ground_along_m=3.0) == "volley"


# ------------------------------------------------------- the pose hit


def test_the_pose_hit_carries_the_ball_hits_time_and_the_feet() -> None:
    contact = contact_with(120.0)
    hit = pose_hit_from_contact(12.5, contact, CourtEnd.NEAR, "forehand", gate_px=180.0)
    assert hit.time_s == 12.5
    assert hit.end is CourtEnd.NEAR and hit.shot_type == "forehand"
    assert hit.wrist_xy_px == contact.wrist_xy_px
    assert hit.feet_xy_px == pytest.approx((900.0, 860.0))
    assert 0.3 <= hit.confidence <= 1.0
    nearer = pose_hit_from_contact(12.5, contact, CourtEnd.NEAR, None, gate_px=3000.0)
    assert nearer.confidence > hit.confidence


def test_a_detection_belongs_to_the_end_whose_side_it_stands_on() -> None:
    assert on_players_side(CourtEnd.NEAR, (0.0, -10.0))
    assert not on_players_side(CourtEnd.NEAR, (0.0, 6.0))
    assert on_players_side(CourtEnd.NEAR, (0.0, 0.5))  # a step over the net line
    assert on_players_side(CourtEnd.FAR, (0.0, 10.0))
    assert not on_players_side(CourtEnd.FAR, (0.0, -6.0))
    assert on_players_side(CourtEnd.FAR, None)  # no feet: no evidence against
