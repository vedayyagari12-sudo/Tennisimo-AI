import 'dart:math' as math;

import 'enums.dart';

/// Minimum separation between the two taps, in normalized units.
///
/// The server rejects anything closer with `CALIBRATION_INVALID`; checking it
/// here turns a 400 into an inline hint.
const double kMinCalibrationSeparation = 0.05;

/// A tap location normalized to the PREVIEW SURFACE the user touched — not to
/// the recorded frame. The server inverts the client transform (PIPELINE.md
/// 11.1) using the capture dimensions and rotation sent alongside.
class NormalizedPoint {
  const NormalizedPoint({required this.x, required this.y});

  /// Fraction of preview surface width, 0.0-1.0.
  final double x;

  /// Fraction of preview surface height, y DOWN, 0.0-1.0.
  final double y;

  Map<String, dynamic> toJson() => <String, dynamic>{'x': x, 'y': y};
}

/// Optional pre-recording calibration: two tapped court points plus the real
/// distance between them.
///
/// Omitting this from the analysis request is a normal outcome, not an error:
/// ball speed is simply not measured.
class BallSpeedCalibration {
  const BallSpeedCalibration({
    required this.pointA,
    required this.pointB,
    required this.reference,
    required this.captureWidthPx,
    required this.captureHeightPx,
    this.captureRotationDeg = 0,
    this.tappedAt,
  });

  final NormalizedPoint pointA;
  final NormalizedPoint pointB;
  final CourtReference reference;

  /// Preview surface width in device pixels, as tapped.
  final int captureWidthPx;

  /// Preview surface height in device pixels, as tapped.
  final int captureHeightPx;

  /// Rotation this client applied to produce the preview it showed. This client
  /// draws the camera preview unrotated, so it is 0. Kept explicit because the
  /// server inverts exactly this value.
  final int captureRotationDeg;

  final DateTime? tappedAt;

  /// Canonical distance for [reference]. Sent so the server can detect a
  /// desynced client build rather than silently overriding it.
  double get distanceM => reference.metres;

  /// Normalized separation of the two taps.
  double get separation {
    final double dx = pointA.x - pointB.x;
    final double dy = pointA.y - pointB.y;
    return math.sqrt(dx * dx + dy * dy);
  }

  bool get isFarEnoughApart => separation >= kMinCalibrationSeparation;

  Map<String, dynamic> toJson() => <String, dynamic>{
        'point_a': pointA.toJson(),
        'point_b': pointB.toJson(),
        'reference': reference.wire,
        'distance_m': distanceM,
        'capture_width_px': captureWidthPx,
        'capture_height_px': captureHeightPx,
        'capture_rotation_deg': captureRotationDeg,
        if (tappedAt != null) 'tapped_at': tappedAt!.toUtc().toIso8601String(),
      };
}
