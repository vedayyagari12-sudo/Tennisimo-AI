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
/// Metrics in [kUnscoredMetrics] (`wrist_lag_deg`, `shoulder_turn_deg`,
/// `hip_rotation_deg`, `shoulder_hip_separation_deg`) get no advice of any
/// kind, praise included: they do not measure what their names say, so there
/// is nothing true to advise.
///
/// Metrics whose direction cannot be stated with confidence get no fix
/// advice at all:
///
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
/// One full range-width: a swing path with a 15–35° band (20° wide) has to
/// be under -5° or over 55° to be called far off. Anything closer gets the
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
  // No hip_rotation_deg, shoulder_turn_deg or shoulder_hip_separation_deg
  // entries: the first two are in-plane line angles that read tilt, not turn
  // (a true 80° turn seen by a level camera reads 0.0), and separation is
  // their difference. All three are in [kUnscoredMetrics] and get no advice.
  //
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
/// that was not seen. Null for anything in [kUnscoredMetrics], whatever
/// verdict an older backend still sent with it.
String? adviceFor(MetricScore metric) {
  if (kUnscoredMetrics.contains(metric.name)) return null;
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
