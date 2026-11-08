/// Turns every scored measurement into something a player can act on:
/// what it is, their number in a unit they can read, the target, how far off
/// they are, what to change, and a drill to practise it.
///
/// PURE and DETERMINISTIC. Every number shown here is a [MetricScore] value or
/// band the pipeline produced; this file only labels, formats and subtracts
/// them. The sentences and drills are written once, here, so the "how to fix
/// it" guidance is always present — even when the AI coaching text is
/// missing or fell back to its template — and it can never carry a number the
/// pipeline did not produce.
///
/// ## Units
///
/// Body-relative distances are shown as **torso lengths** (the pipeline's
/// "TU": 1 = the distance from mid-hip to mid-shoulder). They are NEVER
/// converted to centimetres — that would invent a real-world scale the
/// pipeline does not measure. Naming the unit plainly is what makes "0.34"
/// readable; [kTorsoLengthExplainer] says what it means.
library;

import 'analysis_response.dart';
import 'enums.dart';
import 'key_numbers.dart';
import 'metric_score.dart';
import 'swing_advice.dart';

/// One line for the bottom of the measurement list.
const String kTorsoLengthExplainer =
    '1 torso length = the distance from your hips to your shoulders. '
    'Measuring against your own body means the numbers hold at any camera '
    'distance.';

/// What a measurement is, in a player's words.
class MetricInfo {
  const MetricInfo({required this.label, required this.what});

  final String label;
  final String what;
}

/// Plain names and one-line explanations, keyed by wire metric name. The
/// degree and tempo labels match [kPlainMetricLabels] so the dashboard and
/// the results screen call a measurement the same thing.
const Map<String, MetricInfo> kMetricInfo = <String, MetricInfo>{
  'shoulder_hip_separation_deg': MetricInfo(
    label: 'Hip-shoulder separation',
    what: 'How far your shoulders stay turned past your hips as the swing '
        'starts — the coil that stores power.',
  ),
  'elbow_angle_at_contact_deg': MetricInfo(
    label: 'Elbow angle at contact',
    what: 'The bend in your hitting arm when you meet the ball. 180° is a '
        'straight arm.',
  ),
  'knee_flexion_min_deg': MetricInfo(
    label: 'Front-knee angle',
    what: 'Your front knee at its most bent. 180° is a straight leg, so a '
        'smaller number means more bend.',
  ),
  'swing_path_angle_deg': MetricInfo(
    label: 'Swing path',
    what: 'The angle your hand travels through contact. Positive is low to '
        'high (topspin); negative is high to low (slice).',
  ),
  'tempo_ratio': MetricInfo(
    label: 'Tempo',
    what: 'How long your take-back lasts compared with your forward swing.',
  ),
  'contact_height_ratio': MetricInfo(
    label: 'Contact height',
    what: 'Where you meet the ball: 0% is hip height, 100% is shoulder '
        'height.',
  ),
  'contact_point_forward_tu': MetricInfo(
    label: 'Contact in front',
    what: 'How far in front of your hips you meet the ball.',
  ),
  'peak_hand_speed_tu_s': MetricInfo(
    label: 'Hand speed',
    what: 'The fastest your hitting hand moves during the swing.',
  ),
  'swing_plane_deviation_tu': MetricInfo(
    label: 'Swing smoothness',
    what: 'How far your hand strays from one clean path through contact. '
        'Lower is smoother.',
  ),
  'head_stillness_tu': MetricInfo(
    label: 'Head stillness',
    what: 'How much your head moves during the forward swing. Lower is '
        'steadier.',
  ),
  'follow_through_height_tu': MetricInfo(
    label: 'Finish height',
    what: 'How high your hand finishes above your shoulder after contact.',
  ),
};

/// Fix sentences for the torso-length and contact-height metrics. The degree
/// and tempo metrics keep their rules in `swing_advice.dart`.
///
/// Directions, from `backend/app/analysis/metrics.py`:
///  * contact_height_ratio: wrist height at contact, 0 = hip, 1 = shoulder.
///  * contact_point_forward_tu: wrist ahead of mid-hip in the hitting
///    direction. Below = late (beside or behind the body).
///  * peak_hand_speed_tu_s: below = slow. Above the band is not a fault.
///  * swing_plane_deviation_tu, head_stillness_tu: lower is better; only
///    "above" is a fault.
///  * follow_through_height_tu: wrist above shoulder after contact.
/// View-sensitive ones say "Looks like", as in `swing_advice.dart`.
const Map<String, AdviceRule> kBodyLengthAdviceRules = <String, AdviceRule>{
  'contact_height_ratio': AdviceRule(
    belowNear: 'You’re meeting the ball a bit low — move your feet so it’s '
        'around waist height when you hit.',
    belowFar: 'You’re meeting the ball very low — set your feet earlier and '
        'take it between waist and chest.',
    aboveNear: 'You’re meeting the ball a bit high — step back to let it '
        'drop, or move in to take it earlier.',
    aboveFar: 'You’re meeting the ball very high — step back so it drops into '
        'your waist-to-chest zone, or take it earlier on the rise.',
    inRange: 'Good contact height — right in your strike zone.',
  ),
  'contact_point_forward_tu': AdviceRule(
    belowNear: 'Looks like you’re hitting a bit late — meet the ball a little '
        'further in front of you.',
    belowFar: 'Looks like you’re hitting late, beside your body — start the '
        'swing earlier and meet the ball out in front.',
    aboveNear: 'Looks like you’re reaching a bit far in front — let the ball '
        'come to you a little more.',
    aboveFar: 'Looks like you’re reaching well out in front — let the ball '
        'come closer before you hit.',
    inRange: 'Nice contact point out in front — keep it up.',
  ),
  'peak_hand_speed_tu_s': AdviceRule(
    belowNear: 'Looks like your swing is a bit slow — stay loose and let the '
        'racket speed up through contact.',
    belowFar: 'Looks like your swing speed is low — relax your grip and swing '
        'through the ball, not at it.',
    inRange: 'Good racket-hand speed — keep it up.',
  ),
  'swing_plane_deviation_tu': AdviceRule(
    aboveNear: 'Looks like your racket path wobbles a bit through contact — '
        'aim for one smooth line.',
    aboveFar: 'Looks like your racket path wanders through contact — slow '
        'down and groove one smooth swing.',
    inRange: 'Smooth, clean swing path — keep it up.',
  ),
  'head_stillness_tu': AdviceRule(
    aboveNear: 'Your head moves a little during the swing — keep it still '
        'and watch the contact point.',
    aboveFar: 'Your head moves a lot during the swing — keep it still until '
        'after you hit.',
    inRange: 'Nice steady head — keep it up.',
  ),
  'follow_through_height_tu': AdviceRule(
    belowNear: 'Finish a little higher — let the racket come up over your '
        'shoulder.',
    belowFar: 'Your swing stops low — follow through all the way up over your '
        'opposite shoulder.',
    aboveNear: 'Your finish is a bit high — extend forward toward your target '
        'before the racket comes up.',
    aboveFar: 'Your finish shoots straight up — drive forward through the '
        'ball before you finish.',
    inRange: 'Full, complete finish — keep it up.',
  ),
};

/// A drill for each direction a metric can be off. Null = no drill.
class Drill {
  const Drill({this.below, this.above});

  final String? below;
  final String? above;
}

/// Key used for the slice variant of the swing path.
const String kSlicePathDrillKey = 'swing_path_angle_deg/slice';

/// Practice drills. Each is something a player can do on court with a
/// partner or a basket of balls, with a rep count so it is a plan, not a vibe.
const Map<String, Drill> kDrills = <String, Drill>{
  'elbow_angle_at_contact_deg': Drill(
    below: 'Spacing drill: 20 fed balls, setting up so contact is a full '
        'arm’s reach plus racket from your body — no crowding.',
    above: 'Soft-arm drill: 20 fed balls at 70% pace, keeping a relaxed bend '
        'in the elbow at contact.',
  ),
  'knee_flexion_min_deg': Drill(
    below: 'Stay-tall drill: 20 fed balls, pushing up out of your legs as '
        'you swing instead of sinking.',
    above: 'Wide-base drill: 20 fed balls from a stance a little wider than '
        'your shoulders, sitting into your legs before each swing.',
  ),
  'swing_path_angle_deg': Drill(
    below: 'Low-to-high drill: start the racket below the ball, finish over '
        'your opposite shoulder. 20 fed balls aiming 1 m over the net.',
    above: 'Through-the-ball drill: 20 fed balls driven deep at a target '
        '2 m inside the baseline, swinging forward toward it.',
  ),
  kSlicePathDrillKey: Drill(
    below: 'Tray drill: carry the ball forward on the strings as if on a '
        'tray. 20 fed slices that stay low and land deep.',
    above: 'High-to-low drill: start the racket above the ball and finish '
        'forward and low. 20 fed slices.',
  ),
  'tempo_ratio': Drill(
    below: '“One-two” drill: say “one” on the take-back and “two” on the '
        'swing. 20 fed balls at a steady rhythm.',
    above: 'Early-prep drill: turn and take the racket back the moment the '
        'ball leaves the feeder’s hand. 20 fed balls.',
  ),
  'contact_height_ratio': Drill(
    below: 'Footwork drill: 20 fed balls, moving so every contact is between '
        'waist and chest — never down by your knees.',
    above: 'Back-up drill: 20 high fed balls, taking two quick steps back so '
        'each one drops to chest height before you hit.',
  ),
  'contact_point_forward_tu': Drill(
    below: 'Cone drill: place a cone a step in front of your front foot and '
        'make contact level with it. 20 fed balls.',
    above: 'Let-it-come drill: 20 fed balls, making contact just ahead of '
        'your front hip instead of reaching for it.',
  ),
  'peak_hand_speed_tu_s': Drill(
    below: 'Loose-and-fast drill: 10 quick, relaxed shadow swings, then 20 '
        'fed balls at 80% effort with a grip pressure of 3 out of 10.',
  ),
  'swing_plane_deviation_tu': Drill(
    above: 'Slow-motion drill: 10 slow shadow swings along a fence line, '
        'then 20 fed balls at half pace keeping that same path.',
  ),
  'head_stillness_tu': Drill(
    above: 'Watch-the-contact drill: 20 fed balls, keeping your eyes on the '
        'contact spot for a beat after the ball has gone.',
  ),
  'follow_through_height_tu': Drill(
    below: 'Hold-the-finish drill: 20 fed balls, freezing for two seconds '
        'with the racket over your opposite shoulder.',
    above: 'Extend-to-target drill: 20 fed balls, reaching the racket toward '
        'your target before letting it finish.',
  ),
};

/// One measurement, ready to read.
class CoachingItem {
  const CoachingItem({
    required this.metric,
    required this.label,
    required this.what,
    required this.valueText,
    required this.targetText,
    required this.gapText,
    required this.advice,
    required this.drill,
  });

  final MetricScore metric;
  final String label;

  /// What the measurement is. Null for a metric this build has no words for.
  final String? what;

  /// "128°", "0.34 torso lengths", "90%", "2.1 : 1", or "not measured".
  final String valueText;

  /// "140–165°", or null when the pipeline gave no band.
  final String? targetText;

  /// "12° below target", "Within target", or null when it cannot be said.
  final String? gapText;

  /// What to change (off target) or what is good (in range). May be null.
  final String? advice;

  /// A practice drill, only for an off-target measurement.
  final String? drill;

  bool get isMeasured => metric.value != null;
  bool get isOffTarget =>
      isMeasured &&
      (metric.verdict == MetricVerdict.low ||
          metric.verdict == MetricVerdict.high);
  bool get isInRange => isMeasured && metric.verdict == MetricVerdict.ideal;
}

/// Display style for a metric's number.
enum _Style { degrees, tempo, percent, torso, torsoPerSec, seconds, plain }

_Style _styleFor(MetricScore m) {
  if (m.name == kTempoMetric) return _Style.tempo;
  if (m.name == 'contact_height_ratio') return _Style.percent;
  return switch (m.unit) {
    MetricUnit.degrees => _Style.degrees,
    MetricUnit.torsoUnits => _Style.torso,
    MetricUnit.torsoUnitsPerSec => _Style.torsoPerSec,
    MetricUnit.seconds => _Style.seconds,
    MetricUnit.ratio || MetricUnit.unrecognized => _Style.plain,
  };
}

/// Displayed number (percent is ×100) and its base precision.
double _scaled(_Style s, double v) => s == _Style.percent ? v * 100 : v;

int _baseDecimals(_Style s) => switch (s) {
      _Style.degrees || _Style.percent => 0,
      _Style.tempo || _Style.torsoPerSec || _Style.seconds => 1,
      _Style.torso || _Style.plain => 2,
    };

String _suffix(_Style s, MetricUnit unit) => switch (s) {
      _Style.degrees => '°',
      _Style.tempo => ' : 1',
      _Style.percent => '%',
      _Style.torso => ' torso lengths',
      _Style.torsoPerSec => ' torso lengths/s',
      _Style.seconds => ' s',
      _Style.plain => unit.suffix,
    };

/// The value at the coarsest precision that does not contradict the verdict
/// (34.6 under a 35 band must not print as "35").
String _number(MetricScore m, _Style s, double raw) {
  final double v = _scaled(s, raw);
  final double? lo = m.idealMin == null ? null : _scaled(s, m.idealMin!);
  final double? hi = m.idealMax == null ? null : _scaled(s, m.idealMax!);
  int side(double x) => lo != null && x < lo ? -1 : (hi != null && x > hi ? 1 : 0);
  for (int d = _baseDecimals(s); d < 4; d++) {
    if (side(double.parse(v.toStringAsFixed(d))) == side(v)) {
      return v.toStringAsFixed(d);
    }
  }
  return v.toStringAsFixed(3);
}

String _bandNumber(_Style s, double raw) {
  final double v = _scaled(s, raw);
  final int d = _baseDecimals(s);
  if (v == v.roundToDouble() && d <= 1 && s != _Style.tempo) {
    return v.round().toString();
  }
  return v.toStringAsFixed(d);
}

/// PURE. The readable value, e.g. "0.34 torso lengths". Never "0" for null.
String coachingValueText(MetricScore m) {
  final double? v = m.value;
  if (v == null) return 'not measured';
  final _Style s = _styleFor(m);
  return '${_number(m, s, v)}${_suffix(s, m.unit)}';
}

/// PURE. The readable target band, or null when there is none. A band whose
/// lower end is 0 for a lower-is-better metric reads as a limit: "under 0.08
/// torso lengths".
String? coachingTargetText(MetricScore m) {
  final double? lo = m.idealMin;
  final double? hi = m.idealMax;
  if (lo == null || hi == null) return null;
  final _Style s = _styleFor(m);
  final String suffix = _suffix(s, m.unit);
  if (lo == 0 && _lowerIsBetter.contains(m.name)) {
    return 'under ${_bandNumber(s, hi)}$suffix';
  }
  return '${_bandNumber(s, lo)}–${_bandNumber(s, hi)}$suffix';
}

const Set<String> _lowerIsBetter = <String>{
  'swing_plane_deviation_tu',
  'head_stillness_tu',
};

/// PURE. How far outside the target the value is, in the same unit, e.g.
/// "12° below target". "Within target" when in range. Null when unmeasured
/// or there is no band.
String? coachingGapText(MetricScore m) {
  final double? v = m.value;
  final double? lo = m.idealMin;
  final double? hi = m.idealMax;
  if (v == null || lo == null || hi == null) return null;
  final _Style s = _styleFor(m);
  final double sv = _scaled(s, v);
  final double slo = _scaled(s, lo);
  final double shi = _scaled(s, hi);
  if (sv >= slo && sv <= shi) return 'Within target';
  final bool below = sv < slo;
  final double gap = below ? slo - sv : sv - shi;
  // Rounded at the display precision, but never down to "0": a value that
  // is off target by less than one display step still says how much.
  int d = _baseDecimals(s);
  while (d < 4 && double.parse(gap.toStringAsFixed(d)) == 0) {
    d++;
  }
  final String amount = '${gap.toStringAsFixed(d)}${_suffix(s, m.unit)}';
  if (_lowerIsBetter.contains(m.name)) return '$amount over the limit';
  return '$amount ${below ? 'below' : 'above'} target';
}

/// The sentence for [m]: the existing degree/tempo rules, else the
/// torso-length rules. Null for anything unmeasured or unscored.
String? coachingAdviceFor(MetricScore m) {
  if (kUnscoredMetrics.contains(m.name)) return null;
  if (kAdviceRules.containsKey(m.name)) return adviceFor(m);
  final double? v = m.value;
  final AdviceRule? rule = kBodyLengthAdviceRules[m.name];
  if (v == null || rule == null) return null;
  final bool far = isFarOutsideRange(m);
  return switch (m.verdict) {
    MetricVerdict.low => far ? rule.belowFar : rule.belowNear,
    MetricVerdict.high => far ? rule.aboveFar : rule.aboveNear,
    MetricVerdict.ideal => rule.inRange,
    MetricVerdict.unavailable => null,
  };
}

/// The drill for an off-target [m], or null. Only offered alongside a fix
/// sentence: a drill without a stated direction could train the wrong way.
String? coachingDrillFor(MetricScore m) {
  if (coachingAdviceFor(m) == null) return null;
  String key = m.name;
  if (key == 'swing_path_angle_deg' && adviceRuleFor(m) == kSlicePathRule) {
    key = kSlicePathDrillKey;
  }
  final Drill? drill = kDrills[key];
  return switch (m.verdict) {
    MetricVerdict.low => drill?.below,
    MetricVerdict.high => drill?.above,
    _ => null,
  };
}

/// PURE. Every scored measurement as a [CoachingItem], ordered: off target
/// first (lowest server score first), then in range (best first), then
/// measured without a band, then not measured.
List<CoachingItem> buildCoaching(AnalysisResponse analysis) {
  final List<MetricScore> metrics = <MetricScore>[
    for (final MetricScore m in analysis.metrics)
      if (!kUnscoredMetrics.contains(m.name)) m,
  ];

  int rank(MetricScore m) {
    if (m.value == null) return 3;
    return switch (m.verdict) {
      MetricVerdict.low || MetricVerdict.high => 0,
      MetricVerdict.ideal => 1,
      MetricVerdict.unavailable => 2,
    };
  }

  final List<MetricScore> sorted = <MetricScore>[...metrics]
    ..sort((MetricScore a, MetricScore b) {
      final int r = rank(a).compareTo(rank(b));
      if (r != 0) return r;
      final bool bestFirst = rank(a) == 1;
      final double missing =
          bestFirst ? double.negativeInfinity : double.infinity;
      final double sa = a.score ?? missing;
      final double sb = b.score ?? missing;
      final int byScore = bestFirst ? sb.compareTo(sa) : sa.compareTo(sb);
      if (byScore != 0) return byScore;
      return metrics.indexOf(a).compareTo(metrics.indexOf(b));
    });

  return <CoachingItem>[
    for (final MetricScore m in sorted)
      CoachingItem(
        metric: m,
        label: kMetricInfo[m.name]?.label ?? m.displayName,
        what: kMetricInfo[m.name]?.what,
        valueText: coachingValueText(m),
        targetText: coachingTargetText(m),
        gapText: coachingGapText(m),
        advice: coachingAdviceFor(m),
        drill: coachingDrillFor(m),
      ),
  ];
}
