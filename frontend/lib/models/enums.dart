/// Enums mirrored from the backend contract.
///
/// Every enum parses defensively: an unknown or missing wire string degrades to
/// a documented safe default instead of throwing, because a single unrecognised
/// string from a newer backend build must never blank a screen.
library;

/// Implemented by every enum that has a backend wire representation.
abstract interface class WireEnum {
  String get wire;
}

/// Returns the member whose [WireEnum.wire] matches [raw], else [fallback].
T parseWireEnum<T extends WireEnum>(List<T> values, Object? raw, T fallback) {
  if (raw is! String) return fallback;
  for (final T value in values) {
    if (value.wire == raw) return value;
  }
  return fallback;
}

/// Nullable variant: absent, null, or unrecognised all yield null.
///
/// Unrecognised deliberately does NOT map onto some arbitrary member — an
/// invented reason is worse than no reason.
T? parseWireEnumOrNull<T extends WireEnum>(List<T> values, Object? raw) {
  if (raw is! String) return null;
  for (final T value in values) {
    if (value.wire == raw) return value;
  }
  return null;
}

enum MetricVerdict implements WireEnum {
  low('low'),
  ideal('ideal'),
  high('high'),
  unavailable('unavailable');

  const MetricVerdict(this.wire);

  @override
  final String wire;

  static MetricVerdict fromJson(Object? raw) =>
      parseWireEnum(values, raw, MetricVerdict.unavailable);

  String get label => switch (this) {
        MetricVerdict.low => 'Below range',
        MetricVerdict.ideal => 'In range',
        MetricVerdict.high => 'Above range',
        MetricVerdict.unavailable => 'Not measurable',
      };
}

enum MetricUnit implements WireEnum {
  degrees('deg'),
  torsoUnits('TU'),
  torsoUnitsPerSec('TU/s'),
  seconds('s'),
  ratio('ratio');

  const MetricUnit(this.wire);

  @override
  final String wire;

  static MetricUnit fromJson(Object? raw) =>
      parseWireEnum(values, raw, MetricUnit.ratio);

  /// Suffix appended to a raw value. Torso units are NEVER converted to a
  /// real-world length: ball speed is the only real-world unit in this app.
  String get suffix => switch (this) {
        MetricUnit.degrees => '°',
        MetricUnit.torsoUnits => ' TU',
        MetricUnit.torsoUnitsPerSec => ' TU/s',
        MetricUnit.seconds => ' s',
        MetricUnit.ratio => '',
      };
}

enum FeedbackSource implements WireEnum {
  gemini('gemini'),
  geminiPartial('gemini_partial'),
  template('template');

  const FeedbackSource(this.wire);

  @override
  final String wire;

  static FeedbackSource fromJson(Object? raw) =>
      parseWireEnum(values, raw, FeedbackSource.template);
}

enum BallSpeedConfidence implements WireEnum {
  high('high'),
  medium('medium'),
  low('low'),
  unavailable('unavailable');

  const BallSpeedConfidence(this.wire);

  @override
  final String wire;

  static BallSpeedConfidence fromJson(Object? raw) =>
      parseWireEnum(values, raw, BallSpeedConfidence.unavailable);

  /// Plain, non-cautionary wording. `medium` is the normal outcome on 30 fps
  /// footage (PIPELINE.md 11.5) and is worded as a sound result.
  String get label => switch (this) {
        BallSpeedConfidence.high => 'High confidence',
        BallSpeedConfidence.medium => 'Medium confidence',
        BallSpeedConfidence.low => 'Low confidence',
        BallSpeedConfidence.unavailable => 'Not available',
      };
}

enum BallSpeedUnavailableReason implements WireEnum {
  notCalibrated('not_calibrated'),
  contactUnreliable('contact_unreliable'),
  cameraViewUnsuitable('camera_view_unsuitable'),
  calibrationFrameMismatch('calibration_frame_mismatch'),
  calibrationImplausible('calibration_implausible'),
  noTrackSeeded('no_track_seeded'),
  tooFewDetections('too_few_detections'),
  depthDriftExceeded('depth_drift_exceeded'),
  displacementBelowNoiseFloor('displacement_below_noise_floor'),
  implausibleSpeed('implausible_speed'),
  detectionTimeout('detection_timeout'),
  detectionDisabled('detection_disabled');

  const BallSpeedUnavailableReason(this.wire);

  @override
  final String wire;

  static BallSpeedUnavailableReason? fromJson(Object? raw) =>
      parseWireEnumOrNull(values, raw);

  /// Plain-language explanation, shown only when the user actually calibrated.
  String get explanation => switch (this) {
        BallSpeedUnavailableReason.notCalibrated =>
          'No court calibration was added for this clip.',
        BallSpeedUnavailableReason.contactUnreliable =>
          'The contact moment could not be pinned down precisely enough to time '
              'the ball from.',
        BallSpeedUnavailableReason.cameraViewUnsuitable =>
          'The camera was in front of or behind you. Ball speed needs a side-on '
              'view, roughly perpendicular to the ball.',
        BallSpeedUnavailableReason.calibrationFrameMismatch =>
          'The calibration taps did not line up with the recorded frame, so the '
              'scale could not be trusted.',
        BallSpeedUnavailableReason.calibrationImplausible =>
          'The two tapped points were too close together to give a reliable '
              'scale. Tap the full baseline-to-net line next time.',
        BallSpeedUnavailableReason.noTrackSeeded =>
          'The ball was never picked up in the frames just after contact.',
        BallSpeedUnavailableReason.tooFewDetections =>
          'The ball was visible in too few frames after contact to measure a '
              'speed.',
        BallSpeedUnavailableReason.depthDriftExceeded =>
          'The ball travelled toward or away from the camera too much for the '
              'court scale to hold.',
        BallSpeedUnavailableReason.displacementBelowNoiseFloor =>
          'The ball barely moved between frames, which is indistinguishable '
              'from tracking noise.',
        BallSpeedUnavailableReason.implausibleSpeed =>
          'The measured speed fell outside the plausible range, so it was '
              'discarded rather than reported.',
        BallSpeedUnavailableReason.detectionTimeout =>
          'Ball detection ran out of time on this clip.',
        BallSpeedUnavailableReason.detectionDisabled =>
          'Ball detection was switched off for this analysis.',
      };
}

enum ShotType implements WireEnum {
  forehandTopspin('forehand_topspin'),
  forehandSlice('forehand_slice'),
  backhandOneHanded('backhand_one_handed'),
  backhandTwoHanded('backhand_two_handed'),
  serve('serve'),
  volley('volley'),
  unknown('unknown');

  const ShotType(this.wire);

  @override
  final String wire;

  static ShotType fromJson(Object? raw) =>
      parseWireEnum(values, raw, ShotType.unknown);

  String get label => switch (this) {
        ShotType.forehandTopspin => 'Forehand topspin',
        ShotType.forehandSlice => 'Forehand slice',
        ShotType.backhandOneHanded => 'Backhand (one-handed)',
        ShotType.backhandTwoHanded => 'Backhand (two-handed)',
        ShotType.serve => 'Serve',
        ShotType.volley => 'Volley',
        ShotType.unknown => 'Unknown shot',
      };

  /// The six selectable hints. `unknown` is a server outcome, never a choice.
  static const List<ShotType> selectable = <ShotType>[
    ShotType.forehandTopspin,
    ShotType.forehandSlice,
    ShotType.backhandOneHanded,
    ShotType.backhandTwoHanded,
    ShotType.serve,
    ShotType.volley,
  ];
}

enum Handedness implements WireEnum {
  right('right'),
  left('left'),
  unknown('unknown');

  const Handedness(this.wire);

  @override
  final String wire;

  static Handedness fromJson(Object? raw) =>
      parseWireEnum(values, raw, Handedness.unknown);

  String get label => switch (this) {
        Handedness.right => 'Right-handed',
        Handedness.left => 'Left-handed',
        Handedness.unknown => 'Unknown',
      };
}

enum CameraView implements WireEnum {
  sideOn('side_on'),
  behind('behind'),
  front('front'),
  oblique('oblique'),
  unknown('unknown');

  const CameraView(this.wire);

  @override
  final String wire;

  static CameraView fromJson(Object? raw) =>
      parseWireEnum(values, raw, CameraView.unknown);
}

enum JobStatus implements WireEnum {
  queued('queued'),
  running('running'),
  succeeded('succeeded'),
  failed('failed');

  const JobStatus(this.wire);

  @override
  final String wire;

  static JobStatus fromJson(Object? raw) =>
      parseWireEnum(values, raw, JobStatus.queued);
}

enum AnalysisStatus implements WireEnum {
  complete('complete'),
  partial('partial');

  const AnalysisStatus(this.wire);

  @override
  final String wire;

  /// Unknown degrades to [complete]: defaulting to `partial` would put a
  /// degraded-result banner on a perfectly good analysis.
  static AnalysisStatus fromJson(Object? raw) =>
      parseWireEnum(values, raw, AnalysisStatus.complete);
}

/// Court features the user may tap during calibration.
///
/// Only the two constant-depth, side-on-valid references are offered by this
/// client (PIPELINE.md 10.6). The court-width references exist server-side for
/// views in which speed is unmeasurable anyway.
enum CourtReference implements WireEnum {
  sidelineBaselineToNet('sideline_baseline_to_net', 11.885),
  sidelineBaselineToServiceLine('sideline_baseline_to_service_line', 5.485);

  const CourtReference(this.wire, this.metres);

  @override
  final String wire;

  /// Canonical distance. The server rejects a mismatch beyond 0.01 m, so this
  /// must stay in step with COURT_REFERENCE_METRES.
  final double metres;

  static CourtReference fromJson(Object? raw) =>
      parseWireEnum(values, raw, CourtReference.sidelineBaselineToNet);

  String get label => switch (this) {
        CourtReference.sidelineBaselineToNet => 'Sideline: baseline to net',
        CourtReference.sidelineBaselineToServiceLine =>
          'Sideline: baseline to service line',
      };

  String get description => switch (this) {
        CourtReference.sidelineBaselineToNet =>
          'Tap where the near sideline meets the baseline, then where it meets '
              'the net. 11.885 m.',
        CourtReference.sidelineBaselineToServiceLine =>
          'Tap where the near sideline meets the baseline, then where it meets '
              'the service line. 5.485 m.',
      };
}

/// Plain-language rendering of a server `error_code`.
///
/// Unknown codes fall through to [fallback] rather than being shown raw.
String errorCodeToPlainLanguage(String? code, {required String fallback}) {
  return switch (code) {
    'auth_invalid_token' => 'Your session expired. Sign in again and retry.',
    'storage_path_forbidden' =>
      'That video does not belong to this account. Record a new clip.',
    'object_not_found' =>
      'The uploaded video could not be found on the server. Record and upload '
          'again.',
    'storage_unavailable' =>
      'Video storage is temporarily unavailable. Try again in a moment.',
    'file_too_large' =>
      'That clip is too large to upload. Record a shorter one.',
    'unsupported_content_type' =>
      'That video format is not supported. Record with the in-app camera.',
    'unsupported_codec' =>
      'That video codec is not supported. Record with the in-app camera.',
    'decode_failed' => 'The video could not be read. Record the swing again.',
    'no_video_stream' => 'That file has no video in it. Record the swing again.',
    'video_too_short' => 'The clip is too short to analyse. Record again.',
    'video_too_long' => 'The clip is too long to analyse. Record again.',
    'no_pose_detected' =>
      'No player was detected in the clip. Make sure your whole body is in '
          'frame, side-on to the camera.',
    'pose_quality_too_low' =>
      'The body tracking was too unclear to analyse. Try better light and keep '
          'your whole body in frame.',
    'contact_not_found' =>
      'No clear ball-strike was found in the clip. Record a single full swing.',
    'calibration_invalid' =>
      'The court calibration taps were not usable. Redo them, or skip '
          'calibration.',
    'queue_full' => 'The analyser is busy right now. Try again shortly.',
    'worker_lost' => 'The analysis was interrupted. Try again.',
    'internal_error' => 'Something went wrong on the server. Try again.',
    _ => fallback,
  };
}
