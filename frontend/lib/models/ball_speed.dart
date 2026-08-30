import 'enums.dart';
import 'json_utils.dart';

/// The canonical measurement definition, used only as a fallback.
///
/// The implemented `backend/app/models/feedback.py::BallSpeedResult` has no
/// `measurement_definition` field, so on those payloads the client must still
/// be able to show the definition next to the number. This is the constant
/// string from PIPELINE.md 2.4 verbatim in substance — it is a definition, not
/// a derived or fabricated value.
const String kBallSpeedMeasurementDefinition =
    'Average ball speed over the first 0.25 seconds after contact, not the '
    'speed off the racket.';

/// Ball-speed measurement. The speed inside it is nullable and is NEVER
/// substituted, derived, or rounded client-side.
class BallSpeedResult {
  const BallSpeedResult({
    required this.ballSpeedMph,
    required this.confidence,
    required this.detectionsUsed,
    required this.unavailableReason,
    required this.confidenceCapsApplied,
    required this.measurementDefinition,
    required this.hasCalibrationEcho,
  });

  /// Whole miles per hour, or null. The only real-world unit in this app.
  final int? ballSpeedMph;
  final BallSpeedConfidence confidence;
  final int detectionsUsed;
  final BallSpeedUnavailableReason? unavailableReason;

  /// PIPELINE.md 2.4 only; absent from the implemented backend model.
  final List<String> confidenceCapsApplied;

  /// PIPELINE.md 2.4 only; falls back to
  /// [kBallSpeedMeasurementDefinition] when the server omits it.
  final String measurementDefinition;

  /// True when the server echoed a `calibration` block. PIPELINE.md 2.4 only.
  final bool hasCalibrationEcho;

  factory BallSpeedResult.fromJson(Map<String, dynamic> json) {
    return BallSpeedResult(
      ballSpeedMph: asIntOrNull(json, 'ball_speed_mph'),
      confidence: BallSpeedConfidence.fromJson(json['confidence']),
      detectionsUsed: asInt(json, 'detections_used'),
      unavailableReason:
          BallSpeedUnavailableReason.fromJson(json['unavailable_reason']),
      confidenceCapsApplied: asStringList(json, 'confidence_caps_applied'),
      measurementDefinition: asStringOrNull(json, 'measurement_definition') ??
          kBallSpeedMeasurementDefinition,
      hasCalibrationEcho: asMap(json, 'calibration') != null,
    );
  }

  /// A speed to display. Null means: show nothing at all.
  bool get hasSpeed => ballSpeedMph != null;

  /// Whether the user actually calibrated this clip.
  ///
  /// `not_calibrated` is the one reason that means the user skipped. An absent
  /// or unrecognised reason is treated as "did not calibrate", so the block is
  /// omitted rather than explained with a guess.
  bool get userCalibrated {
    if (hasSpeed) return true;
    if (hasCalibrationEcho) return true;
    final BallSpeedUnavailableReason? reason = unavailableReason;
    return reason != null && reason != BallSpeedUnavailableReason.notCalibrated;
  }

  /// Whole number plus " mph". Only called when [hasSpeed] is true.
  String get displaySpeed => '$ballSpeedMph mph';
}
