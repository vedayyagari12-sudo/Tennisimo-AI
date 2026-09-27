/// The handful of swing numbers a player can read at a glance.
///
/// PURE: an [AnalysisResponse] in, plain values out. No widgets, no I/O.
/// Every number here is a [MetricScore] the deterministic pipeline produced —
/// value, ideal band and verdict alike. This file only chooses, orders and
/// formats them; `swing_advice.dart` turns them into words.
///
/// Only metrics a teenager can read without a glossary are included: joint
/// and rotation angles in degrees, and tempo. The torso-unit (`TU`) metrics
/// are left out rather than translated — "0.34 TU" means nothing to a player,
/// and converting it to centimetres would invent a real-world scale the
/// pipeline does not measure. So are the always-null ones
/// (`weight_transfer_tu`, `balance_sway_tu`, `takeback_displacement_tu`).
library;

import 'analysis_response.dart';
import 'enums.dart';
import 'metric_score.dart';

/// Plain labels, in display order for ties. The ONE place a wire metric name
/// becomes words a player reads.
///
/// `knee_flexion_min_deg` is the front knee's joint angle at its most bent —
/// 180 is a straight leg — so it is called an angle, not "knee bend": "knee
/// bend 158°" would read as MORE bend for a bigger number, which is backwards.
const Map<String, String> kPlainMetricLabels = <String, String>{
  'hip_rotation_deg': 'Hip turn',
  'shoulder_turn_deg': 'Shoulder turn',
  'shoulder_hip_separation_deg': 'Hip-shoulder separation',
  'elbow_angle_at_contact_deg': 'Elbow angle at contact',
  'knee_flexion_min_deg': 'Front-knee angle',
  'wrist_lag_deg': 'Wrist lag',
  'swing_path_angle_deg': 'Swing path',
  'tempo_ratio': 'Tempo',
};

/// The metric whose unit is a ratio, shown as "take-back : swing".
const String kTempoMetric = 'tempo_ratio';

/// One metric in the simple view.
class KeyNumber {
  const KeyNumber({required this.metric, required this.label});

  final MetricScore metric;

  /// From [kPlainMetricLabels]; never the raw wire name.
  final String label;

  bool get isMeasured => metric.value != null;

  bool get isOffTarget =>
      isMeasured &&
      (metric.verdict == MetricVerdict.low ||
          metric.verdict == MetricVerdict.high);

  bool get isInRange => isMeasured && metric.verdict == MetricVerdict.ideal;

  /// "25°", "3.4 : 1", or an em dash when not measured — never "0".
  String get valueText {
    final double? v = metric.value;
    if (v == null) return '—';
    return _withUnit(_valueNumber(v));
  }

  /// "35–60°", or null when the pipeline gave no band. A range is never
  /// invented for a metric that has none.
  String? get idealText {
    final double? lo = metric.idealMin;
    final double? hi = metric.idealMax;
    if (lo == null || hi == null) return null;
    if (metric.name == kTempoMetric) {
      // Tempo reads to one decimal on both ends: "1.5–3.0 : 1", not "1.5–3".
      return _withUnit('${lo.toStringAsFixed(1)}–${hi.toStringAsFixed(1)}');
    }
    return _withUnit('${_bandNumber(lo)}–${_bandNumber(hi)}');
  }

  String _withUnit(String number) =>
      metric.name == kTempoMetric ? '$number : 1' : '$number°';

  /// The value at the coarsest precision that does not contradict the
  /// verdict. 34.6° below a 35° band rounds to "35°", which would read as in
  /// range next to advice to turn more; it is shown as "34.6°" instead.
  String _valueNumber(double v) {
    final int base = metric.name == kTempoMetric ? 1 : 0;
    for (int decimals = base; decimals < 3; decimals++) {
      final double shown = double.parse(v.toStringAsFixed(decimals));
      if (_side(shown) == _side(v)) return v.toStringAsFixed(decimals);
    }
    return v.toStringAsFixed(2);
  }

  int _side(double x) {
    final double? lo = metric.idealMin;
    final double? hi = metric.idealMax;
    if (lo != null && x < lo) return -1;
    if (hi != null && x > hi) return 1;
    return 0;
  }

  static String _bandNumber(double v) =>
      v == v.roundToDouble() ? v.round().toString() : v.toStringAsFixed(1);
}

/// The plain-labelled metrics of [analysis]: off-target first — worst server
/// score first — then in range, best first, then measured without a range,
/// then not measured.
List<KeyNumber> buildKeyNumbers(AnalysisResponse analysis) {
  final List<String> order = kPlainMetricLabels.keys.toList();

  final List<MetricScore> chosen = <MetricScore>[
    for (final MetricScore m in analysis.metrics)
      if (kPlainMetricLabels.containsKey(m.name) && _unitFits(m)) m,
  ];

  int rank(MetricScore m) {
    if (m.value == null) return 3;
    return switch (m.verdict) {
      MetricVerdict.low || MetricVerdict.high => 0,
      MetricVerdict.ideal => 1,
      MetricVerdict.unavailable => 2,
    };
  }

  chosen.sort((MetricScore a, MetricScore b) {
    final int byRank = rank(a).compareTo(rank(b));
    if (byRank != 0) return byRank;
    // The server's own 0-100 score orders each group: off-target lowest
    // first (furthest from ideal), in-range highest first (best first). An
    // absent score sorts last either way.
    final bool bestFirst = rank(a) == 1;
    final double missing = bestFirst
        ? double.negativeInfinity
        : double.infinity;
    final double sa = a.score ?? missing;
    final double sb = b.score ?? missing;
    final int byScore = bestFirst ? sb.compareTo(sa) : sa.compareTo(sb);
    if (byScore != 0) return byScore;
    return order.indexOf(a.name).compareTo(order.indexOf(b.name));
  });

  return <KeyNumber>[
    for (final MetricScore m in chosen)
      KeyNumber(metric: m, label: kPlainMetricLabels[m.name]!),
  ];
}

/// A metric is only shown in plain form if its unit is the one the plain
/// formatting assumes. An angle arriving in some other unit is left out
/// rather than printed with a degree sign it does not have.
bool _unitFits(MetricScore m) => m.name == kTempoMetric
    ? m.unit == MetricUnit.ratio
    : m.unit == MetricUnit.degrees;
