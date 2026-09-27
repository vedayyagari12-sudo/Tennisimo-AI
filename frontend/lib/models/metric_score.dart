import 'enums.dart';
import 'json_utils.dart';

/// One scored swing metric.
///
/// Mirrors `backend/app/models/feedback.py::MetricScore`, widened for the
/// PIPELINE.md 2.3 variant of the same model. Only `name`, `unit` and `verdict`
/// are treated as guaranteed; everything else is optional.
///
/// `value == null` means NOT MEASURABLE. It is never rendered as 0.
class MetricScore {
  const MetricScore({
    required this.name,
    required this.value,
    required this.unit,
    required this.verdict,
    required this.score,
    required this.idealMin,
    required this.idealMax,
    required this.viewSensitive,
  });

  final String name;

  /// Null means the metric could not be measured in this clip.
  final double? value;
  final MetricUnit unit;
  final MetricVerdict verdict;

  /// 0-100. The implemented backend model calls this `score`; PIPELINE.md 2.3
  /// calls it `score_0_100`. Both keys are accepted.
  final double? score;
  final double? idealMin;
  final double? idealMax;

  /// True when the value shifts materially with camera yaw; de-emphasised.
  final bool viewSensitive;

  bool get isMeasurable => value != null;

  /// True only when a full reference band is available to draw.
  bool get hasIdealBand => idealMin != null && idealMax != null;

  factory MetricScore.fromJson(Map<String, dynamic> json) {
    return MetricScore(
      name: asString(json, 'name', fallback: 'metric'),
      value: asDoubleOrNull(json, 'value'),
      unit: MetricUnit.fromJson(json['unit']),
      verdict: MetricVerdict.fromJson(json['verdict']),
      score: asDoubleOrNull(json, 'score_0_100') ?? asDoubleOrNull(json, 'score'),
      idealMin: asDoubleOrNull(json, 'ideal_min'),
      idealMax: asDoubleOrNull(json, 'ideal_max'),
      viewSensitive: asBool(json, 'view_sensitive'),
    );
  }

  /// Human label: `shoulder_hip_separation_deg` -> `Shoulder hip separation`.
  String get displayName {
    String cleaned = name.replaceAll('_', ' ').trim();
    for (final String suffix in const <String>[' deg', ' tu s', ' tu', ' ratio']) {
      if (cleaned.toLowerCase().endsWith(suffix)) {
        cleaned = cleaned.substring(0, cleaned.length - suffix.length);
        break;
      }
    }
    if (cleaned.isEmpty) return name;
    return cleaned[0].toUpperCase() + cleaned.substring(1);
  }

  /// Formatted value, or the not-measured wording. Never "0" for null.
  ///
  /// Neutral on purpose: a null can mean this clip hid the metric OR that
  /// the pipeline never measures it, and the payload cannot tell the two
  /// apart. "in this clip" would blame the clip for the second case.
  String get displayValue {
    final double? v = value;
    if (v == null) return 'not measured';
    final String number = (v.abs() >= 100 ? v.toStringAsFixed(0) : v.toStringAsFixed(1));
    return '$number${unit.suffix}';
  }

  /// Formatted reference band, or null when there is nothing to show.
  String? get displayIdealBand {
    final double? lo = idealMin;
    final double? hi = idealMax;
    if (lo == null || hi == null) return null;
    final String loText = lo.abs() >= 100 ? lo.toStringAsFixed(0) : lo.toStringAsFixed(1);
    final String hiText = hi.abs() >= 100 ? hi.toStringAsFixed(0) : hi.toStringAsFixed(1);
    return '$loText–$hiText${unit.suffix}';
  }
}
