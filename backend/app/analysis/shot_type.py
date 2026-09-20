"""Stage 14: shot-type inference from TECHNIQUE (PURE, PIPELINE.md 4.4 row 14).

Arrays and value objects in, a ``ShotTypeInference`` out. No I/O, no MediaPipe,
no network. Never raises: an ambiguous swing is ``ShotType.UNKNOWN``
(PIPELINE.md:978), which is a first-class outcome and not a failure.

NO BALL INPUT, STRUCTURALLY
---------------------------
CLAUDE.md: "Shot type is INFERRED FROM TECHNIQUE, not from the ball." There is
no ``BallTrack`` parameter and no ball argument of any kind, and this module
imports nothing from ``app.ball``. The ABSENCE OF THE PARAMETER IS THE
ENFORCEMENT (PIPELINE.md:982); the orchestration additionally runs this stage
before ball detection exists, so a ball track cannot be consulted even by
accident.

WHAT THIS CAN AND CANNOT SEPARATE
---------------------------------
* serve vs ground stroke -- reliably (contact height is large and view-stable)
* two-handed vs one-handed -- reliably, and it is the ONLY rule immune to Stage
  8 picking the wrong hand, because it uses both wrists symmetrically
* volley vs full swing -- probably; needs ``phases`` for the duration term
* topspin vs slice -- weakly; the path-angle window is contaminated by the
  accepted Stage 9 ``+1`` frame residual (PIPELINE.md:581), which biases
  ``swing_path_angle_deg`` POSITIVE, i.e. away from slice. Expect slice to be
  UNDER-reported and validate in that direction first.
* forehand vs backhand -- NOT reliably. The discriminating axis is the lateral
  one the 2D projection collapsed (``internal.py:61``), and every camera-view
  gate in this pipeline is inert (``normalize.py:325``). It is scored as capped
  soft evidence and is zeroed outright on a weak or unknown handedness read.

The consequence is deliberate and is not a bug: on a clip with no handedness
hint and a weak Stage 8 read, this stage can still call serve / two-handed /
volley, but CANNOT tell a forehand from a one-handed backhand, and returns
``unknown`` rather than guessing.

EVIDENCE, NOT A CASCADE
-----------------------
``ShotTypeInference.class_scores`` must be calibrated (``responses.py:240``), and
a first-match-wins cascade cannot produce calibrated scores. Evidence is
accumulated for all six real classes and then gated. "Evaluation order" survives
as evidence PRIORITY -- serve is weighted highest and suppresses volley -- not as
early exit.

A rule whose input is ``None`` contributes NOTHING. It does not contribute
zero-as-a-value and it never silently drops one conjunct of a conjunction: that
is exactly how PIPELINE.md:989's two-term slice rule would quietly become the
looser single-term rule ``swing_path_angle_deg < -10``.
"""

from __future__ import annotations

from typing import Final

import numpy as np

from app.analysis.handedness import HINT_CONFIDENCE_FLOOR
from app.analysis.metrics import frame_is_visible, swing_span
from app.analysis.normalize import (
    LEFT_SHOULDER,
    LEFT_WRIST,
    NOSE,
    RIGHT_SHOULDER,
    RIGHT_WRIST,
    landmark_visible,
    path_length,
)
from app.models.enums import Handedness, HandednessSource, ShotType, SwingPhaseName
from app.models.internal import NormalizedSequence
from app.models.responses import (
    ContactDetection,
    HandednessResult,
    ShotTypeInference,
    SwingMetrics,
    SwingPhases,
)

# --- thresholds --------------------------------------------------------------
# Inherited from PIPELINE.md, none validated against this pipeline's own
# normalization.
SERVE_HEIGHT_RATIO: Final[float] = 1.30  # PIPELINE.md:986
TWO_HANDED_SEPARATION_TU: Final[float] = 0.35  # PIPELINE.md:987
SUSTAIN_HALF_WIDTH: Final[int] = 3  # PIPELINE.md:987, "sustained across contact +/- 3"
MIN_SUSTAIN_FRAMES: Final[int] = 4  # of the 7 window frames, how many must be visible
SLICE_PATH_ANGLE_DEG: Final[float] = -10.0  # PIPELINE.md:989
VOLLEY_PATH_LENGTH_TU: Final[float] = 1.0  # PIPELINE.md:990
VOLLEY_DURATION_S: Final[float] = 0.35  # PIPELINE.md:990
TOP_SCORE_MIN: Final[float] = 0.45  # PIPELINE.md:994
MARGIN_MIN: Final[float] = 0.15  # PIPELINE.md:994

# NEW NUMBERS -- not in PIPELINE.md, not measured. See the design doc's Part F.
#
# SLICE_FOLLOW_THROUGH_TU replaces PIPELINE.md:989's second conjunct
# `takeback_displacement_tu < 0.6`, which is PERMANENTLY None (metrics.py:682)
# and therefore unevaluable. Dropping the conjunct instead would silently
# convert a two-term rule into a looser one-term rule. 0.20 sits below the slice
# band's midpoint ([0.0, 0.45]) and below the topspin band's floor (0.30) in the
# reference table -- but both of those bands are themselves placeholders, so
# this is the weakest number in this module.
SLICE_FOLLOW_THROUGH_TU: Final[float] = 0.20
# The mirror of SLICE_PATH_ANGLE_DEG. PIPELINE.md defines only the slice side;
# without a topspin side, "not a slice" would read as topspin by default.
TOPSPIN_PATH_ANGLE_DEG: Final[float] = 10.0
# Below this the racket wrist is too close to the torso's own midline for the
# forehand/backhand projection to mean anything.
FOREHAND_BACKHAND_MIN_PROJECTION_TU: Final[float] = 0.10

# --- evidence weights (all new; the design fixes only the 0.25 cap) ----------
SERVE_EVIDENCE_WEIGHT: Final[float] = 1.0
SERVE_CORROBORATION_WEIGHT: Final[float] = 0.5
HAND_COUNT_EVIDENCE_WEIGHT: Final[float] = 1.0
SPIN_EVIDENCE_WEIGHT: Final[float] = 0.5
FOREHAND_BACKHAND_MAX_EVIDENCE: Final[float] = 0.25
VOLLEY_PATH_WEIGHT: Final[float] = 0.5
VOLLEY_DURATION_WEIGHT: Final[float] = 0.5
# Near-tautological at contact -- a negative value is a Stage 9 sanity FAILURE
# (contact.py:563-564), not a shot type -- so it must never carry volley
# evidence on its own.
VOLLEY_CONTACT_FORWARD_WEIGHT: Final[float] = 0.1

RULE_VERSION: Final[str] = "shot_rules_v1"
INTERNAL_FAILURE_EVIDENCE: Final[str] = "shot-type inference failed internally"

REAL_CLASSES: Final[tuple[ShotType, ...]] = (
    ShotType.FOREHAND_TOPSPIN,
    ShotType.FOREHAND_SLICE,
    ShotType.BACKHAND_ONE_HANDED,
    ShotType.BACKHAND_TWO_HANDED,
    ShotType.SERVE,
    ShotType.VOLLEY,
)
# Everything a player holds the racket in one hand for. A serve and a volley are
# one-handed too, so the hand-count rule must not implicitly treat them as
# evidence AGAINST themselves.
ONE_HANDED_CLASSES: Final[tuple[ShotType, ...]] = (
    ShotType.FOREHAND_TOPSPIN,
    ShotType.FOREHAND_SLICE,
    ShotType.BACKHAND_ONE_HANDED,
    ShotType.SERVE,
    ShotType.VOLLEY,
)
FOREHAND_CLASSES: Final[tuple[ShotType, ...]] = (
    ShotType.FOREHAND_TOPSPIN,
    ShotType.FOREHAND_SLICE,
)
BACKHAND_CLASSES: Final[tuple[ShotType, ...]] = (
    ShotType.BACKHAND_ONE_HANDED,
    ShotType.BACKHAND_TWO_HANDED,
)


# --- measurements this stage makes for itself --------------------------------


def racket_wrist_for(handedness: Handedness) -> int | None:
    """The racket wrist index, or ``None`` when the hand was never established.

    Deliberately NOT ``handedness.racket_wrist_index``: that one raises, which is
    right for a metric but wrong here, where "we do not know which hand" is a
    state this stage reports rather than an error.
    """
    if handedness == Handedness.RIGHT:
        return RIGHT_WRIST
    if handedness == Handedness.LEFT:
        return LEFT_WRIST
    return None


def wrist_separations_near(
    seq: NormalizedSequence, frame_index: int, half_width: int = SUSTAIN_HALF_WIDTH
) -> list[float]:
    """Both-wrist separations over ``frame_index +/- half_width``, VISIBLE frames only.

    Frames where either wrist was not visible are EXCLUDED from the sample, not
    counted as failures: an unobserved wrist is not evidence that the hands were
    apart. The caller decides whether the surviving sample is large enough.
    """
    points = np.asarray(seq.points, dtype=np.float64)
    separations: list[float] = []
    for frame in range(int(frame_index) - int(half_width), int(frame_index) + int(half_width) + 1):
        if not frame_is_visible(seq, frame, (LEFT_WRIST, RIGHT_WRIST)):
            continue
        distance = float(
            np.linalg.norm(points[frame, LEFT_WRIST] - points[frame, RIGHT_WRIST])
        )
        if np.isfinite(distance):
            separations.append(distance)
    return separations


def shoulder_axis_projection_tu(
    seq: NormalizedSequence, handedness: Handedness, frame_index: int
) -> float | None:
    """Racket wrist at contact, projected onto the torso's own lateral axis.

    Origin is the shoulder-line midpoint and the axis points from the off-hand
    shoulder toward the racket shoulder, so POSITIVE means the hand is on the
    racket side of the torso (a forehand) and NEGATIVE means it crossed the body
    (a backhand).

    This uses a BODY-INTRINSIC axis rather than the target axis on purpose.
    ``contact_point_forward_tu`` is signed toward the target (``metrics.py:470``)
    and is positive for a forehand and a backhand alike, so its sign cannot make
    this distinction at all.
    """
    wrist = racket_wrist_for(handedness)
    if wrist is None:
        return None
    if not frame_is_visible(seq, frame_index, (wrist, LEFT_SHOULDER, RIGHT_SHOULDER)):
        return None
    points = np.asarray(seq.points, dtype=np.float64)
    left = points[int(frame_index), LEFT_SHOULDER]
    right = points[int(frame_index), RIGHT_SHOULDER]
    racket_shoulder, off_shoulder = (
        (right, left) if handedness == Handedness.RIGHT else (left, right)
    )
    axis = racket_shoulder - off_shoulder
    norm = float(np.linalg.norm(axis))
    if not np.isfinite(norm) or norm <= 0.0:
        return None
    midpoint = (left + right) / 2.0
    projection = float(np.dot(points[int(frame_index), wrist] - midpoint, axis / norm))
    return projection if np.isfinite(projection) else None


def wrist_above_nose(
    seq: NormalizedSequence, handedness: Handedness, frame_index: int
) -> bool | None:
    """Whether the racket wrist is above the nose at contact. y is positive UP."""
    wrist = racket_wrist_for(handedness)
    if wrist is None or not frame_is_visible(seq, frame_index, (wrist, NOSE)):
        return None
    points = np.asarray(seq.points, dtype=np.float64)
    return bool(points[int(frame_index), wrist, 1] > points[int(frame_index), NOSE, 1])


def swing_path_length_tu(
    seq: NormalizedSequence, handedness: Handedness, phases: SwingPhases | None
) -> float | None:
    """Racket-hand path length over the SWING span, in TU.

    ``HandednessResult.racket_hand_path_length_tu`` is computed over the whole
    sequence (``handedness.py:102,165``), so on a long clip containing idle
    motion it over-reads and volleys go undetected. When ``phases`` is available
    the path is recomputed over the swing only.
    """
    wrist = racket_wrist_for(handedness)
    span = swing_span(phases)
    if wrist is None or span is None:
        return None
    points = np.asarray(seq.points, dtype=np.float64)
    visibility = np.asarray(seq.visibility, dtype=np.float64)
    first, last = span
    first = max(int(first), 0)
    last = min(int(last), int(points.shape[0]) - 1)
    if last <= first:
        return None
    column = (
        visibility[first : last + 1, wrist]
        if visibility.ndim == 2 and visibility.shape[0] == points.shape[0]
        else None
    )
    if column is not None and int(np.count_nonzero(landmark_visible(column))) < 2:
        # path_length excludes steps whose endpoints were not both visible, so
        # an unseen wrist would come back as a perfect 0.0 TU -- the shortest
        # path there is, and a guaranteed false volley. Nothing was measured.
        return None
    return float(path_length(points[first : last + 1, wrist, :], column))


def swing_duration_s(phases: SwingPhases | None) -> float | None:
    """Takeback start to follow-through end, from PTS. Requires ``phases``."""
    if phases is None:
        return None
    by_name = {phase.name: phase for phase in phases.phases}
    takeback = by_name.get(SwingPhaseName.TAKEBACK)
    follow_through = by_name.get(SwingPhaseName.FOLLOW_THROUGH)
    if takeback is None or follow_through is None:
        return None
    duration = float(follow_through.end_time_s) - float(takeback.start_time_s)
    return duration if np.isfinite(duration) and duration >= 0.0 else None


# --- evidence bookkeeping ----------------------------------------------------


def _add(scores: dict[ShotType, float], classes: tuple[ShotType, ...], amount: float) -> None:
    """Split ``amount`` equally across ``classes``.

    An equal split is what "this rule does not discriminate between these
    classes" means in an additive scheme: the rule raises them together and
    leaves the separation to a rule that can actually see it.
    """
    if not classes or amount <= 0.0:
        return
    share = float(amount) / float(len(classes))
    for member in classes:
        scores[member] += share


def normalize_scores(scores: dict[ShotType, float]) -> dict[str, float]:
    """Raw evidence -> ``class_scores``. All-zero stays all-zero.

    When NO rule fired, normalizing zeros would produce a uniform 1/6 per class,
    which reads as a weak measurement when in fact nothing was measured. The
    all-zero map is returned instead. This deliberately violates
    ``responses.py:240``'s "sum to 1.0" note in the one case where summing to
    1.0 would be a fabrication.
    """
    total = float(sum(scores.values()))
    if total <= 0.0:
        return {member.value: 0.0 for member in REAL_CLASSES}
    return {member.value: float(scores[member]) / total for member in REAL_CLASSES}


def apply_acceptance_gates(class_scores: dict[str, float]) -> tuple[ShotType, float]:
    """PIPELINE.md:994's two gates. Returns ``(shot_type, confidence)``.

    ``confidence`` is the top score, the honest number, EVEN WHEN the answer is
    ``UNKNOWN``: a rejected top score of 0.41 is reported as 0.41, not as 0.0 and
    not as ``1 - 0.41``.
    """
    ranked = sorted(class_scores.items(), key=lambda item: item[1], reverse=True)
    if not ranked or ranked[0][1] <= 0.0:
        return ShotType.UNKNOWN, 0.0
    top_name, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    if top_score < TOP_SCORE_MIN or (top_score - second_score) < MARGIN_MIN:
        return ShotType.UNKNOWN, float(top_score)
    return ShotType(top_name), float(top_score)


# --- the rules ---------------------------------------------------------------


def infer_shot_type(
    metrics: SwingMetrics,
    seq: NormalizedSequence,
    handedness: HandednessResult,
    contact: ContactDetection,
    phases: SwingPhases | None,
) -> ShotTypeInference:
    """Stage 14. NEVER raises; ambiguous is ``ShotType.UNKNOWN``."""
    scores: dict[ShotType, float] = {member: 0.0 for member in REAL_CLASSES}
    evidence: list[str] = []
    try:
        hand = handedness.handedness
        contact_frame = int(contact.frame_index)

        serve_fired = _rule_serve(metrics, seq, hand, contact_frame, scores, evidence)
        _rule_hand_count(seq, contact_frame, scores, evidence)
        side = _rule_forehand_backhand(seq, handedness, contact_frame, scores, evidence)
        _rule_spin(metrics, side, scores, evidence)
        _rule_volley(metrics, seq, handedness, phases, serve_fired, scores, evidence)

        class_scores = normalize_scores(scores)
        if all(value == 0.0 for value in class_scores.values()):
            evidence.append(
                "no discriminating measurement was available; nothing was "
                "classified and no score is reported"
            )
            return ShotTypeInference(
                shot_type=ShotType.UNKNOWN,
                confidence=0.0,
                class_scores=class_scores,
                evidence=evidence,
                rule_version=RULE_VERSION,
            )
        shot_type, confidence = apply_acceptance_gates(class_scores)
        if shot_type == ShotType.UNKNOWN:
            evidence.append(
                f"top class score {confidence:.2f} did not clear both gates "
                f"(>= {TOP_SCORE_MIN:.2f} and a margin >= {MARGIN_MIN:.2f}) -> unknown"
            )
        return ShotTypeInference(
            shot_type=shot_type,
            confidence=float(np.clip(confidence, 0.0, 1.0)),
            class_scores=class_scores,
            evidence=evidence,
            rule_version=RULE_VERSION,
        )
    except Exception as error:  # noqa: BLE001 -- Stage 14 contract: cannot raise.
        evidence.append(
            f"{INTERNAL_FAILURE_EVIDENCE} ({type(error).__name__}: {error}); "
            "the shot type is unknown because of that failure, NOT because the "
            "swing was ambiguous"
        )
        return ShotTypeInference(
            shot_type=ShotType.UNKNOWN,
            confidence=0.0,
            class_scores={member.value: 0.0 for member in REAL_CLASSES},
            evidence=evidence,
            rule_version=RULE_VERSION,
        )


def _rule_serve(
    metrics: SwingMetrics,
    seq: NormalizedSequence,
    hand: Handedness,
    contact_frame: int,
    scores: dict[ShotType, float],
    evidence: list[str],
) -> bool:
    """Rule 1 (PIPELINE.md:986). Weighted highest; suppresses volley when it fires.

    PIPELINE.md also names "trunk extended". No ``SwingMetrics`` field measures
    trunk extension and inventing one here would be a Stage 13 metric smuggled
    into Stage 14, so that term is DROPPED rather than approximated.
    """
    ratio = metrics.contact_height_ratio
    if ratio is None:
        evidence.append(
            "contact_height_ratio unavailable; the serve rule contributed nothing"
        )
        return False
    if ratio <= SERVE_HEIGHT_RATIO:
        evidence.append(
            f"contact height ratio {ratio:.2f} <= {SERVE_HEIGHT_RATIO:.2f} "
            "threshold -> not a serve"
        )
        return False
    _add(scores, (ShotType.SERVE,), SERVE_EVIDENCE_WEIGHT)
    evidence.append(
        f"contact height ratio {ratio:.2f} > {SERVE_HEIGHT_RATIO:.2f} threshold -> serve"
    )
    above = wrist_above_nose(seq, hand, contact_frame)
    if above is None:
        evidence.append(
            "racket wrist or nose not visible at contact; the serve "
            "corroboration contributed nothing"
        )
    elif above:
        _add(scores, (ShotType.SERVE,), SERVE_CORROBORATION_WEIGHT)
        evidence.append("racket wrist above the nose at contact -> corroborates serve")
    return True


def _rule_hand_count(
    seq: NormalizedSequence,
    contact_frame: int,
    scores: dict[ShotType, float],
    evidence: list[str],
) -> None:
    """Rule 2 (PIPELINE.md:987). The most trustworthy rule in this module.

    Both wrists are used symmetrically, so it is immune to Stage 8 picking the
    wrong hand, and it is view-insensitive (``metrics.py:106``). The sustain
    requirement across ``contact +/- 3`` is what rejects incidental proximity.
    """
    separations = wrist_separations_near(seq, contact_frame)
    window = 2 * SUSTAIN_HALF_WIDTH + 1
    if len(separations) < MIN_SUSTAIN_FRAMES:
        evidence.append(
            f"both wrists visible on only {len(separations)} of {window} frames "
            f"around contact (< {MIN_SUSTAIN_FRAMES}); the two-handed rule "
            "contributed nothing"
        )
        return
    widest = max(separations)
    if widest < TWO_HANDED_SEPARATION_TU:
        _add(scores, (ShotType.BACKHAND_TWO_HANDED,), HAND_COUNT_EVIDENCE_WEIGHT)
        evidence.append(
            f"wrist separation stayed below {TWO_HANDED_SEPARATION_TU:.2f} TU "
            f"(widest {widest:.2f} TU) across {len(separations)} frames around "
            "contact -> two-handed"
        )
        return
    _add(scores, ONE_HANDED_CLASSES, HAND_COUNT_EVIDENCE_WEIGHT)
    evidence.append(
        f"wrist separation reached {widest:.2f} TU >= "
        f"{TWO_HANDED_SEPARATION_TU:.2f} threshold around contact -> one-handed"
    )


def _rule_forehand_backhand(
    seq: NormalizedSequence,
    handedness: HandednessResult,
    contact_frame: int,
    scores: dict[ShotType, float],
    evidence: list[str],
) -> str | None:
    """Rule 3. SOFT evidence only, capped, and zeroed on a weak handedness read.

    Returns ``"forehand"``, ``"backhand"`` or ``None``. The cap means this axis
    can tip a decision between otherwise-tied classes but can never carry one
    past the acceptance gate by itself.
    """
    if handedness.handedness == Handedness.UNKNOWN:
        evidence.append(
            "handedness is unknown, so the racket hand is unknown; the "
            "forehand/backhand axis contributed nothing"
        )
        return None
    if (
        float(handedness.confidence) < HINT_CONFIDENCE_FLOOR
        and handedness.source == HandednessSource.DETECTED
    ):
        # A present and correct handedness hint is a PRECONDITION for trusting
        # anything handedness-sensitive (PIPELINE.md:583), not a convenience --
        # so a weak DETECTED read with no hint rescuing it is not evidence.
        evidence.append(
            f"handedness confidence {float(handedness.confidence):.2f} < "
            f"{HINT_CONFIDENCE_FLOOR} with no user hint; the forehand/backhand "
            "axis contributed nothing"
        )
        return None
    projection = shoulder_axis_projection_tu(seq, handedness.handedness, contact_frame)
    if projection is None:
        evidence.append(
            "racket wrist or shoulder line not visible at contact; the "
            "forehand/backhand axis contributed nothing"
        )
        return None
    if abs(projection) < FOREHAND_BACKHAND_MIN_PROJECTION_TU:
        evidence.append(
            f"racket wrist {projection:+.2f} TU along the shoulder line at "
            f"contact, inside the +/-{FOREHAND_BACKHAND_MIN_PROJECTION_TU:.2f} TU "
            "dead band; the forehand/backhand axis contributed nothing"
        )
        return None
    if projection > 0.0:
        _add(scores, FOREHAND_CLASSES, FOREHAND_BACKHAND_MAX_EVIDENCE)
        evidence.append(
            f"racket wrist {projection:+.2f} TU toward the racket side of the "
            "shoulder line at contact -> weak evidence for a forehand"
        )
        return "forehand"
    _add(scores, BACKHAND_CLASSES, FOREHAND_BACKHAND_MAX_EVIDENCE)
    evidence.append(
        f"racket wrist {projection:+.2f} TU across the body from the shoulder "
        "line at contact -> weak evidence for a backhand"
    )
    return "backhand"


def _rule_spin(
    metrics: SwingMetrics,
    side: str | None,
    scores: dict[ShotType, float],
    evidence: list[str],
) -> None:
    """Rule 4. Topspin vs slice, with a REPLACEMENT for the dead conjunct.

    PIPELINE.md:989 is ``swing_path_angle_deg < -10`` AND
    ``takeback_displacement_tu < 0.6``. The second term is permanently ``None``
    (``metrics.py:682``, and Stage 13 deliberately declines to invent a
    definition at ``metrics.py:636-638``), so the rule as written cannot be
    evaluated. ``follow_through_height_tu`` replaces it: it is measurable, it is
    view-insensitive, a slice finishes low and a topspin finishes high, and --
    being a maximum over all frames after contact -- it is insensitive to the
    contact-frame error that shifts the path-angle fit window wholesale. Pairing
    a window-sensitive term with a window-insensitive one is the point.

    Only the two spin-qualified enum members exist (``forehand_topspin`` /
    ``forehand_slice``), so this axis is attributable only once the shot has been
    identified as a forehand. Attributing it otherwise would let "the path rose"
    stand in for a forehand/backhand call the pipeline explicitly cannot make.
    """
    if side != "forehand":
        evidence.append(
            "the shot was not identified as a forehand; the topspin/slice axis "
            "contributed nothing (only the forehand classes carry a spin qualifier)"
        )
        return
    angle = metrics.swing_path_angle_deg
    height = metrics.follow_through_height_tu
    missing = [
        name
        for name, value in (
            ("swing_path_angle_deg", angle),
            ("follow_through_height_tu", height),
        )
        if value is None
    ]
    if missing:
        # BOTH terms must be present. Skipping a None conjunct would convert
        # this into a different, looser classifier with no warning.
        evidence.append(
            f"{' and '.join(missing)} unavailable; the topspin/slice axis "
            "contributed nothing to either class"
        )
        return
    if angle < SLICE_PATH_ANGLE_DEG and height < SLICE_FOLLOW_THROUGH_TU:
        _add(scores, (ShotType.FOREHAND_SLICE,), SPIN_EVIDENCE_WEIGHT)
        evidence.append(
            f"swing path {angle:.1f} deg < {SLICE_PATH_ANGLE_DEG:.1f} and "
            f"follow-through height {height:.2f} TU < "
            f"{SLICE_FOLLOW_THROUGH_TU:.2f} -> slice"
        )
        return
    if angle > TOPSPIN_PATH_ANGLE_DEG and height >= SLICE_FOLLOW_THROUGH_TU:
        _add(scores, (ShotType.FOREHAND_TOPSPIN,), SPIN_EVIDENCE_WEIGHT)
        evidence.append(
            f"swing path {angle:.1f} deg > {TOPSPIN_PATH_ANGLE_DEG:.1f} and "
            f"follow-through height {height:.2f} TU >= "
            f"{SLICE_FOLLOW_THROUGH_TU:.2f} -> topspin"
        )
        return
    evidence.append(
        f"swing path {angle:.1f} deg with follow-through height {height:.2f} TU "
        "matches neither the slice nor the topspin conjunction; the "
        "topspin/slice axis contributed nothing"
    )


def _rule_volley(
    metrics: SwingMetrics,
    seq: NormalizedSequence,
    handedness: HandednessResult,
    phases: SwingPhases | None,
    serve_fired: bool,
    scores: dict[ShotType, float],
    evidence: list[str],
) -> None:
    """Rule 5 (PIPELINE.md:990): short path, short duration, contact forward."""
    if serve_fired:
        evidence.append(
            "serve evidence fired; volley evidence suppressed (a serve satisfies "
            "'contact forward of the body' trivially)"
        )
        return

    substantive_term_fired = False
    path = swing_path_length_tu(seq, handedness.handedness, phases)
    if path is None:
        path = handedness.racket_hand_path_length_tu
        if path is not None:
            evidence.append(
                f"racket-hand path {path:.2f} TU measured over the WHOLE clip "
                "(no swing span available), so idle motion inflates it"
            )
    if path is None:
        evidence.append(
            "racket-hand path length unavailable; that volley term contributed nothing"
        )
    elif path < VOLLEY_PATH_LENGTH_TU:
        _add(scores, (ShotType.VOLLEY,), VOLLEY_PATH_WEIGHT)
        substantive_term_fired = True
        evidence.append(
            f"racket-hand path {path:.2f} TU < {VOLLEY_PATH_LENGTH_TU:.2f} "
            "threshold -> volley"
        )
    else:
        evidence.append(
            f"racket-hand path {path:.2f} TU >= {VOLLEY_PATH_LENGTH_TU:.2f} "
            "threshold -> not a volley"
        )

    duration = swing_duration_s(phases)
    if duration is None:
        # Worth stating: without phases the volley rule loses a whole term and
        # the remaining two cannot clear the acceptance gate alone, so
        # `phases is None` effectively disables volley detection.
        evidence.append(
            "swing duration unavailable (no phase segmentation); that volley "
            "term contributed nothing"
        )
    elif duration < VOLLEY_DURATION_S:
        _add(scores, (ShotType.VOLLEY,), VOLLEY_DURATION_WEIGHT)
        substantive_term_fired = True
        evidence.append(
            f"swing duration {duration:.2f} s < {VOLLEY_DURATION_S:.2f} "
            "threshold -> volley"
        )
    else:
        evidence.append(
            f"swing duration {duration:.2f} s >= {VOLLEY_DURATION_S:.2f} "
            "threshold -> not a volley"
        )

    # The third term is near-tautological: contact is forward of the body on
    # essentially every stroke, and a NEGATIVE value is a Stage 9 sanity failure
    # rather than a shot type. It is therefore only allowed to CORROBORATE a
    # volley that one of the two substantive terms already evidenced -- never to
    # start one, and never to inflate the evidence total on a clip that is
    # plainly not a volley.
    forward = metrics.contact_point_forward_tu
    if not substantive_term_fired:
        return
    if forward is None:
        evidence.append(
            "contact_point_forward_tu unavailable; that volley term contributed nothing"
        )
    elif forward > 0.0:
        _add(scores, (ShotType.VOLLEY,), VOLLEY_CONTACT_FORWARD_WEIGHT)
        evidence.append(
            f"contact {forward:.2f} TU forward of the body -> weakly consistent "
            "with a volley"
        )
