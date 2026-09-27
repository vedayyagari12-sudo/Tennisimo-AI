/// Plain, one-sentence coaching bullets for a swing, from a fixed rule table.
///
/// PURE and DETERMINISTIC: a metric's verdict picks a sentence written once,
/// here. No AI text is involved — the sentences are always available, even
/// when coaching feedback is missing or fell back to the template, and they
/// can never carry a number the pipeline did not produce (they carry none).
///
/// ## The direction of the advice is the whole point
///
/// "Turn more" versus "turn less" comes from the pipeline's [MetricVerdict]
/// — `low` is BELOW the ideal band, `high` is ABOVE it — and what that means
/// physically differs per metric. Each entry below states what its metric
/// measures (from `backend/app/analysis/metrics.py`) and so which way
/// "below" points. Where it points against intuition — the knee — it says so.
///
/// Metrics whose direction cannot be stated with confidence get no fix
/// advice at all:
///
///  * `shoulder_hip_separation_deg` is a SIGNED shoulder-minus-hip angle that
///    the backend never normalises for which way the player faces, so "below
///    range" may just be a mirror-image player. Praise only.
///  * `wrist_lag_deg` is, in the backend's own words, "a proxy only: the
///    racket head is not an observable landmark". No advice either way.
///  * `swing_path_angle_deg` is only trusted within ±90°. The backend fits the
///    hand's direction of travel against +x without applying
///    `swing_direction_sign`, so a low-to-high swing travelling LEFT reads as
///    ~135°–160° and would come back "above range" for a topspin band.
library;

import 'enums.dart';
import 'key_numbers.dart';
import 'metric_score.dart';

/// How far outside its range a value must be, in multiples of the range's
/// own width, before a sentence may say "way too", "very" or "hardly".
///
/// One full range-width: hip turn with a 35–60° band (25° wide) has to be
/// under 10° or over 85° to be called far off. Anything closer gets the
/// "a bit" wording — the advice must not exaggerate.
const double kFarOutsideRangeWidths = 1.0;

/// How many fix bullets and "nice" bullets the dashboard summary shows.
const int kDashboardFixBullets = 3;
const int kDashboardPraiseBullets = 1;

/// How many "nice" bullets the per-swing results show.
const int kResultsPraiseBullets = 2;

/// The sentences for one metric. A null sentence means "say nothing".
class AdviceRule {
  const AdviceRule({
    this.belowNear,
    this.belowFar,
    this.aboveNear,
    this.aboveFar,
    this.inRange,
  });

  /// [MetricVerdict.low], within [kFarOutsideRangeWidths] of the band.
  final String? belowNear;

  /// [MetricVerdict.low], further out than that.
  final String? belowFar;

  /// [MetricVerdict.high], within [kFarOutsideRangeWidths] of the band.
  final String? aboveNear;

  /// [MetricVerdict.high], further out than that.
  final String? aboveFar;

  /// [MetricVerdict.ideal].
  final String? inRange;
}

/// The rule table, keyed by wire metric name.
///
/// View-sensitive metrics (`METRIC_VIEW_SENSITIVE` in metrics.py) are
/// written as "Looks like …": the reading may partly be the filming angle,
/// so the advice is offered, not asserted.
const Map<String, AdviceRule> kAdviceRules = <String, AdviceRule>{
  // hip_rotation_deg: max - min of the hip-line angle, take-back start to
  // follow-through end. Below = the hips turned less than ideal.
  'hip_rotation_deg': AdviceRule(
    belowNear:
        'Looks like your hips could turn a bit more — that’s where '
        'your power comes from.',
    belowFar:
        'Looks like your hips are hardly turning — let them turn '
        'through the shot for more power.',
    aboveNear:
        'Looks like your hips turn a little too much — try keeping '
        'them a bit steadier.',
    aboveFar:
        'Looks like your hips turn way too much — keep them steadier '
        'through the swing.',
    inRange: 'Nice hip turn — keep it up.',
  ),
  // shoulder_turn_deg: the same range for the shoulder line. Below = the
  // shoulders turned less than ideal.
  'shoulder_turn_deg': AdviceRule(
    belowNear:
        'Looks like your shoulders could turn a bit more — turn them '
        'further as you take the racket back.',
    belowFar:
        'Looks like your shoulders are hardly turning — turn them '
        'sideways as you take the racket back.',
    aboveNear:
        'Looks like your shoulders turn a little too much — try a '
        'slightly shorter turn.',
    aboveFar:
        'Looks like your shoulders turn way too much — keep the turn '
        'more compact.',
    inRange: 'Nice shoulder turn — keep it up.',
  ),
  // Signed, and not normalised for facing direction: praise only.
  'shoulder_hip_separation_deg': AdviceRule(
    inRange: 'Nice stretch between your hips and shoulders — keep it up.',
  ),
  // elbow_angle_at_contact_deg: shoulder-elbow-wrist angle at contact; 180
  // is a straight arm. Below = MORE bent than ideal; above = straighter.
  'elbow_angle_at_contact_deg': AdviceRule(
    belowNear:
        'Looks like your arm is a bit bent when you hit — let it '
        'reach out a little more.',
    belowFar:
        'Looks like your arm is very bent when you hit — give the ball '
        'more room and reach for it.',
    aboveNear:
        'Looks like your arm is a bit too straight when you hit — '
        'keep a little bend in your elbow.',
    aboveFar:
        'Looks like your arm is locked straight when you hit — keep a '
        'relaxed bend in your elbow.',
    inRange: 'Nice arm shape when you hit — keep it up.',
  ),
  // knee_flexion_min_deg: the SMALLEST front-knee (hip-knee-ankle) angle in
  // the forward swing; 180 is a straight leg. So it runs AGAINST intuition:
  // below range = the knee bent MORE than ideal (too low); above range = the
  // leg stayed too straight.
  'knee_flexion_min_deg': AdviceRule(
    belowNear:
        'Looks like you bend your front knee a bit more than you need '
        'to — stay a touch taller.',
    belowFar:
        'Looks like you’re crouching very low — you don’t need to bend '
        'your front knee that much.',
    aboveNear:
        'Looks like your front leg is a bit straight — bend your knees '
        'a little more to get lower.',
    aboveFar:
        'Looks like your front leg is almost straight — bend your knees '
        'more to get down to the ball.',
    inRange: 'Nice knee bend — keep it up.',
  ),
  // A proxy the backend itself will not interpret: nothing to say.
  'wrist_lag_deg': AdviceRule(),
  // swing_path_angle_deg, for an UPWARD band (topspin, 15..35): the angle of
  // the hand's path through contact, positive = low to high. Below = too
  // flat or downward; above = rising too steeply.
  'swing_path_angle_deg': AdviceRule(
    belowNear:
        'Swing a bit more from low to high — brush up the back of the '
        'ball.',
    belowFar:
        'Your swing is coming through flat or downward — swing from low '
        'to high to brush up the ball.',
    aboveNear:
        'Your swing rises a bit steeply — swing a little more forward '
        'through the ball.',
    aboveFar:
        'Your swing goes up very steeply — swing more forward through '
        'the ball.',
    inRange: 'Nice low-to-high swing — keep it up.',
  ),
  // tempo_ratio: take-back duration / forward-swing duration (phases.py).
  // Below = the take-back is short next to the swing (rushed); above = the
  // take-back is long next to the swing.
  'tempo_ratio': AdviceRule(
    belowNear: 'Take the racket back a little slower — no need to rush it.',
    belowFar:
        'Your take-back is very rushed — take the racket back more '
        'slowly and smoothly.',
    aboveNear:
        'Your take-back is a bit slow compared with your swing — get '
        'the racket back a little quicker.',
    aboveFar:
        'Your take-back is very slow compared with your swing — get '
        'the racket back quicker.',
    inRange: 'Nice smooth rhythm — keep it up.',
  ),
};

/// `swing_path_angle_deg` for a DOWNWARD band (slice, -30..-8). Below = more
/// downward than ideal (chopping); above = too flat or rising.
const AdviceRule kSlicePathRule = AdviceRule(
  belowNear:
      'You’re chopping down a bit steeply — swing more forward '
      'through the ball.',
  belowFar:
      'You’re chopping down very steeply — swing more forward through '
      'the ball.',
  aboveNear: 'Swing a little more from high to low for your slice.',
  aboveFar:
      'Your slice is coming through flat or upward — swing from high '
      'to low.',
  inRange: 'Nice high-to-low slice swing — keep it up.',
);

/// One bullet: the sentence, and the number it is about.
class AdviceBullet {
  const AdviceBullet({
    required this.number,
    required this.text,
    required this.isPraise,
  });

  final KeyNumber number;
  final String text;

  /// True for an in-range "nice" bullet, false for something to change.
  final bool isPraise;
}

/// True when [metric] is outside its band by at least
/// [kFarOutsideRangeWidths] band-widths.
bool isFarOutsideRange(MetricScore metric) {
  final double? v = metric.value;
  final double? lo = metric.idealMin;
  final double? hi = metric.idealMax;
  if (v == null || lo == null || hi == null) return false;
  final double width = hi - lo;
  if (width <= 0) return false;
  final double outside = v < lo ? lo - v : (v > hi ? v - hi : 0);
  return outside >= width * kFarOutsideRangeWidths;
}

/// The rule that applies to [metric], or null when there is none.
AdviceRule? adviceRuleFor(MetricScore metric) {
  if (metric.name != 'swing_path_angle_deg') return kAdviceRules[metric.name];
  final double? lo = metric.idealMin;
  final double? hi = metric.idealMax;
  if (lo == null || hi == null) return null;
  if (lo >= 0) return kAdviceRules['swing_path_angle_deg'];
  if (hi <= 0) return kSlicePathRule;
  // A band straddling flat has no single "up" or "down" to advise towards.
  return null;
}

/// The sentence for [metric], or null when nothing should be said.
///
/// Null for anything not measured: there is never advice about something
/// that was not seen.
String? adviceFor(MetricScore metric) {
  final double? v = metric.value;
  if (v == null) return null;
  final AdviceRule? rule = adviceRuleFor(metric);
  if (rule == null) return null;
  final bool far = isFarOutsideRange(metric);
  switch (metric.verdict) {
    case MetricVerdict.low:
    case MetricVerdict.high:
      // See the library note: outside ±90° the path's direction of travel is
      // ambiguous, and fix advice could point the wrong way.
      if (metric.name == 'swing_path_angle_deg' && v.abs() > 90) return null;
      if (metric.verdict == MetricVerdict.low) {
        return far ? rule.belowFar : rule.belowNear;
      }
      return far ? rule.aboveFar : rule.aboveNear;
    case MetricVerdict.ideal:
      return rule.inRange;
    case MetricVerdict.unavailable:
      return null;
  }
}

/// The bullets for one swing: things to change first (most off-target
/// first), then at most [maxPraise] "nice" bullets for what is in range.
List<AdviceBullet> buildSwingAdvice(
  List<KeyNumber> numbers, {
  int? maxFixes,
  required int maxPraise,
}) {
  final List<AdviceBullet> fixes = <AdviceBullet>[];
  final List<AdviceBullet> praise = <AdviceBullet>[];
  // [numbers] is already ordered: off-target worst first, in range best first.
  for (final KeyNumber n in numbers) {
    final String? text = adviceFor(n.metric);
    if (text == null) continue;
    if (n.isOffTarget) {
      fixes.add(AdviceBullet(number: n, text: text, isPraise: false));
    } else if (n.isInRange) {
      praise.add(AdviceBullet(number: n, text: text, isPraise: true));
    }
  }
  return <AdviceBullet>[
    ...(maxFixes == null ? fixes : fixes.take(maxFixes)),
    ...praise.take(maxPraise),
  ];
}
