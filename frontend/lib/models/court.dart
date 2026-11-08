/// Court mode results, mirrored from `backend/app/models/court.py`
/// (SESSION_MODE_PHASE2.md section 7.3).
///
/// Parsing is defensive like `enums.dart`: an unknown wire string becomes the
/// enum's `unrecognized` member and never throws. Nullable court enums follow
/// brief G20: null stays null (not measured), and only an unknown STRING
/// becomes `unrecognized`. Every court number is an estimate from one camera.
library;

import 'enums.dart';
import 'json_utils.dart';

const String _unrecognized = '__unrecognized__';

/// Brief G20: null stays null; an unknown string is [fallback].
T? _nullableEnum<T extends WireEnum>(List<T> values, Object? raw, T fallback) {
  if (raw == null) return null;
  return parseWireEnum(values, raw, fallback);
}

enum CourtEnd implements WireEnum {
  near('near'),
  far('far'),
  unrecognized(_unrecognized);

  const CourtEnd(this.wire);
  @override
  final String wire;
  static CourtEnd fromJson(Object? raw) => parseWireEnum(values, raw, unrecognized);
  static CourtEnd? fromJsonOrNull(Object? raw) => _nullableEnum(values, raw, unrecognized);
}

enum LineVerdict implements WireEnum {
  inside('in', 'In'),
  out('out', 'Out'),
  tooCloseToCall('too_close_to_call', 'Too close to call'),
  unrecognized(_unrecognized, 'Not shown');

  const LineVerdict(this.wire, this.label);
  @override
  final String wire;
  final String label;
  static LineVerdict fromJson(Object? raw) => parseWireEnum(values, raw, unrecognized);
}

enum CourtLine implements WireEnum {
  nearBaseline('near_baseline', 'Near baseline'),
  farBaseline('far_baseline', 'Far baseline'),
  nearServiceLine('near_service_line', 'Near service line'),
  farServiceLine('far_service_line', 'Far service line'),
  leftSinglesSideline('left_singles_sideline', 'Left singles sideline'),
  rightSinglesSideline('right_singles_sideline', 'Right singles sideline'),
  leftDoublesSideline('left_doubles_sideline', 'Left doubles sideline'),
  rightDoublesSideline('right_doubles_sideline', 'Right doubles sideline'),
  centreServiceLine('centre_service_line', 'Centre service line'),
  unrecognized(_unrecognized, 'A court line');

  const CourtLine(this.wire, this.label);
  @override
  final String wire;
  final String label;
  static CourtLine fromJson(Object? raw) => parseWireEnum(values, raw, unrecognized);
  static CourtLine? fromJsonOrNull(Object? raw) => _nullableEnum(values, raw, unrecognized);
}

enum LineCallTarget implements WireEnum {
  singlesHalf('singles_half'),
  doublesHalf('doubles_half'),
  serviceBoxDeuce('service_box_deuce'),
  serviceBoxAd('service_box_ad'),
  halfOnly('half_only'),
  unrecognized(_unrecognized);

  const LineCallTarget(this.wire);
  @override
  final String wire;
  static LineCallTarget fromJson(Object? raw) => parseWireEnum(values, raw, unrecognized);
}

enum DepthZone implements WireEnum {
  short('short', 'Short'),
  mid('mid', 'Mid'),
  deep('deep', 'Deep'),
  beyond('beyond', 'Beyond'),
  unrecognized(_unrecognized, 'Other');

  const DepthZone(this.wire, this.label);
  @override
  final String wire;
  final String label;
  static DepthZone fromJson(Object? raw) => parseWireEnum(values, raw, unrecognized);

  /// The four real zones, nearest the net first.
  static const List<DepthZone> ordered = <DepthZone>[short, mid, deep, beyond];
}

enum WidthZone implements WireEnum {
  left('left'),
  centre('centre'),
  right('right'),
  unrecognized(_unrecognized);

  const WidthZone(this.wire);
  @override
  final String wire;
  static WidthZone fromJson(Object? raw) => parseWireEnum(values, raw, unrecognized);
}

enum RallyEndReason implements WireEnum {
  ballOut('ball_out', 'out'),
  doubleBounce('double_bounce', 'double bounce'),
  net('net', 'net'),
  lostTrack('lost_track', 'lost track'),
  timeout('timeout', 'ball not seen again'),
  unrecognized(_unrecognized, 'not known');

  const RallyEndReason(this.wire, this.label);
  @override
  final String wire;
  final String label;
  static RallyEndReason fromJson(Object? raw) => parseWireEnum(values, raw, unrecognized);
}

enum LengthConfidence implements WireEnum {
  high('high'),
  medium('medium'),
  low('low'),
  unrecognized(_unrecognized);

  const LengthConfidence(this.wire);
  @override
  final String wire;
  static LengthConfidence fromJson(Object? raw) => parseWireEnum(values, raw, unrecognized);
}

enum CourtSessionStatus implements WireEnum {
  complete('complete'),
  partial('partial'),
  refused('refused'),
  unrecognized(_unrecognized);

  const CourtSessionStatus(this.wire);
  @override
  final String wire;
  static CourtSessionStatus fromJson(Object? raw) => parseWireEnum(values, raw, unrecognized);
}

enum CourtRefusalReason implements WireEnum {
  cameraMoving('camera_moving', 'The camera moved. Put the phone on a tripod or fence mount.'),
  cameraUnknown('camera_unknown', 'We could not tell whether the camera stayed still.'),
  notCalibrated('not_calibrated', 'Tap the court corners first so we can map the court.'),
  frameRateTooLow('frame_rate_too_low', 'Record at 30 fps or more to track the ball.'),
  unrecognized(_unrecognized, 'Court tracking was not possible for this video.');

  const CourtRefusalReason(this.wire, this.message);
  @override
  final String wire;
  final String message;
  static CourtRefusalReason? fromJsonOrNull(Object? raw) =>
      _nullableEnum(values, raw, unrecognized);
}

enum StabilityVerdict implements WireEnum {
  stable('stable'),
  shaky('shaky'),
  moving('moving'),
  unknown('unknown'),
  unrecognized(_unrecognized);

  const StabilityVerdict(this.wire);
  @override
  final String wire;
  static StabilityVerdict fromJson(Object? raw) => parseWireEnum(values, raw, unrecognized);
}

enum PlayerId implements WireEnum {
  playerA('player_a'),
  playerB('player_b'),
  unrecognized(_unrecognized);

  const PlayerId(this.wire);
  @override
  final String wire;
  static PlayerId fromJson(Object? raw) => parseWireEnum(values, raw, unrecognized);
  static PlayerId? fromJsonOrNull(Object? raw) => _nullableEnum(values, raw, unrecognized);
}

enum ScoreEventKind implements WireEnum {
  pointWon('point_won'),
  pointVoid('point_void'),
  pointUnresolved('point_unresolved'),
  unrecognized(_unrecognized);

  const ScoreEventKind(this.wire);
  @override
  final String wire;
  static ScoreEventKind fromJson(Object? raw) => parseWireEnum(values, raw, unrecognized);
}

enum ScoreSource implements WireEnum {
  inferred('inferred'),
  user('user'),
  unrecognized(_unrecognized);

  const ScoreSource(this.wire);
  @override
  final String wire;
  static ScoreSource fromJson(Object? raw) => parseWireEnum(values, raw, unrecognized);
}

enum InferenceBasis implements WireEnum {
  ballOut('ball_out', 'Out'),
  doubleBounce('double_bounce', 'Double bounce'),
  serveFault('serve_fault', 'Serve fault'),
  doubleFault('double_fault', 'Double fault'),
  net('net', 'Into the net'),
  tooClose('too_close', 'Too close to call'),
  lostTrack('lost_track', 'Lost track'),
  unseenBounce('unseen_bounce', 'Bounce not seen'),
  unrecognized(_unrecognized, 'Not known');

  const InferenceBasis(this.wire, this.label);
  @override
  final String wire;
  final String label;
  static InferenceBasis? fromJsonOrNull(Object? raw) =>
      _nullableEnum(values, raw, unrecognized);
}

double _asDouble(Object? json, String key) => asDoubleOrNull(json, key) ?? 0;

/// `[x, y]` court metres: x across the court, y along it, net at y = 0,
/// near baseline at y = -11.885.
class CourtPoint {
  const CourtPoint(this.x, this.y);
  final double x;
  final double y;

  static CourtPoint? fromJsonOrNull(Object? raw) {
    if (raw is! List || raw.length != 2) return null;
    final Object? x = raw[0];
    final Object? y = raw[1];
    if (x is! num || y is! num) return null;
    return CourtPoint(x.toDouble(), y.toDouble());
  }
}

/// 1-sigma uncertainty ellipse in metres (brief G16). The map draws it at
/// 1.96x, the same width the line call is judged at.
class BounceSigma {
  const BounceSigma({required this.major, required this.minor, required this.angleDeg});
  final double major;
  final double minor;
  final double angleDeg;

  factory BounceSigma.fromJson(Map<String, dynamic>? json) => BounceSigma(
        major: _asDouble(json, 'major'),
        minor: _asDouble(json, 'minor'),
        angleDeg: _asDouble(json, 'angle_deg'),
      );
}

class LineCall {
  const LineCall({
    required this.verdict,
    required this.decidingLine,
    required this.signedMarginM,
    required this.normalSigmaM,
    required this.uncertaintyRadiusM,
    required this.flags,
  });

  final LineVerdict verdict;
  final CourtLine decidingLine;

  /// Positive inside the target, negative beyond the line.
  final double signedMarginM;
  final double normalSigmaM;
  final double uncertaintyRadiusM;
  final List<String> flags;

  static LineCall? fromJsonOrNull(Map<String, dynamic>? json) {
    if (json == null) return null;
    return LineCall(
      verdict: LineVerdict.fromJson(json['verdict']),
      decidingLine: CourtLine.fromJson(json['deciding_line']),
      signedMarginM: _asDouble(json, 'signed_margin_m'),
      normalSigmaM: _asDouble(json, 'normal_sigma_m'),
      uncertaintyRadiusM: _asDouble(json, 'uncertainty_radius_m'),
      flags: asStringList(json, 'flags'),
    );
  }
}

class ShotLanding {
  const ShotLanding({
    required this.bounceId,
    required this.timeS,
    required this.courtXy,
    required this.sigma,
    required this.half,
    required this.hitterEnd,
    required this.hitter,
    required this.shotType,
    required this.isServe,
    required this.target,
    required this.lineCall,
    required this.depthZone,
    required this.depthZoneConfident,
    required this.depthProbabilities,
    required this.widthZone,
    required this.confidence,
    required this.rallyId,
    required this.shotIndexInRally,
    required this.flags,
    this.hitXy,
  });

  final String bounceId;
  final double timeS;
  final CourtPoint courtXy;
  final BounceSigma sigma;
  final CourtEnd half;
  final CourtEnd? hitterEnd;
  final PlayerId? hitter;
  final String? shotType;
  final bool isServe;
  final LineCallTarget target;

  /// Null = not measured (too few court taps for calls). Never read as in.
  final LineCall? lineCall;
  final DepthZone depthZone;
  final bool depthZoneConfident;
  final Map<DepthZone, double> depthProbabilities;
  final WidthZone widthZone;
  final double confidence;
  final String? rallyId;
  final int? shotIndexInRally;
  final List<String> flags;

  /// Where the hitter stood, from the pose feet. Null = not measured; the
  /// map then draws no path, and never guesses one from the hitter's end.
  final CourtPoint? hitXy;

  /// The verdict, or null when no call was made.
  LineVerdict? get verdict => lineCall?.verdict;

  factory ShotLanding.fromJson(Map<String, dynamic> json) {
    final Map<String, dynamic>? probabilities = asMap(json, 'depth_probabilities');
    return ShotLanding(
      bounceId: asString(json, 'bounce_id'),
      timeS: _asDouble(json, 'time_s'),
      courtXy: CourtPoint.fromJsonOrNull(json['court_xy_m']) ?? const CourtPoint(0, 0),
      sigma: BounceSigma.fromJson(asMap(json, 'sigma_m')),
      half: CourtEnd.fromJson(json['half']),
      hitterEnd: CourtEnd.fromJsonOrNull(json['hitter_end']),
      hitter: PlayerId.fromJsonOrNull(json['hitter']),
      shotType: asStringOrNull(json, 'shot_type'),
      isServe: asBool(json, 'is_serve'),
      target: LineCallTarget.fromJson(json['target']),
      lineCall: LineCall.fromJsonOrNull(asMap(json, 'line_call')),
      depthZone: DepthZone.fromJson(json['depth_zone']),
      depthZoneConfident: asBool(json, 'depth_zone_confident'),
      depthProbabilities: <DepthZone, double>{
        if (probabilities != null)
          for (final MapEntry<String, dynamic> e in probabilities.entries)
            if (e.value is num) DepthZone.fromJson(e.key): (e.value as num).toDouble(),
      },
      widthZone: WidthZone.fromJson(json['width_zone']),
      confidence: _asDouble(json, 'confidence'),
      rallyId: asStringOrNull(json, 'rally_id'),
      shotIndexInRally: asIntOrNull(json, 'shot_index_in_rally'),
      flags: asStringList(json, 'flags'),
      hitXy: CourtPoint.fromJsonOrNull(json['hit_xy_m']),
    );
  }
}

class ShotMapSummary {
  const ShotMapSummary({
    required this.count,
    required this.verdictCounts,
    required this.depthExpectedCounts,
    required this.widthCounts,
    required this.medianSigmaAlongM,
  });

  final int count;
  final Map<LineVerdict, int> verdictCounts;
  final Map<DepthZone, double> depthExpectedCounts;
  final Map<WidthZone, int> widthCounts;
  final double? medianSigmaAlongM;

  static Map<K, V> _enumMap<K, V>(
      Map<String, dynamic>? raw, K Function(Object?) key, V? Function(Object?) value) {
    final Map<K, V> out = <K, V>{};
    if (raw == null) return out;
    for (final MapEntry<String, dynamic> e in raw.entries) {
      final V? v = value(e.value);
      if (v != null) out[key(e.key)] = v;
    }
    return out;
  }

  factory ShotMapSummary.fromJson(Map<String, dynamic>? json) => ShotMapSummary(
        count: asInt(json, 'count'),
        verdictCounts: _enumMap<LineVerdict, int>(asMap(json, 'verdict_counts'),
            LineVerdict.fromJson, (Object? v) => v is num ? v.toInt() : null),
        depthExpectedCounts: _enumMap<DepthZone, double>(asMap(json, 'depth_expected_counts'),
            DepthZone.fromJson, (Object? v) => v is num ? v.toDouble() : null),
        widthCounts: _enumMap<WidthZone, int>(asMap(json, 'width_counts'),
            WidthZone.fromJson, (Object? v) => v is num ? v.toInt() : null),
        medianSigmaAlongM: asDoubleOrNull(json, 'median_sigma_along_m'),
      );
}

class Rally {
  const Rally({
    required this.rallyId,
    required this.startS,
    required this.endS,
    required this.lengthHits,
    required this.lengthMin,
    required this.lengthMax,
    required this.lengthConfidence,
    required this.hitsObserved,
    required this.hitsInferred,
    required this.endReason,
    required this.bounceIds,
    required this.flags,
  });

  final String rallyId;
  final double startS;
  final double endS;
  final int lengthHits;
  final int lengthMin;
  final int lengthMax;
  final LengthConfidence lengthConfidence;
  final int hitsObserved;
  final int hitsInferred;
  final RallyEndReason endReason;
  final List<String> bounceIds;
  final List<String> flags;

  /// True when the length is a range, not a single number.
  bool get isRange => lengthMin != lengthMax;

  factory Rally.fromJson(Map<String, dynamic> json) => Rally(
        rallyId: asString(json, 'rally_id'),
        startS: _asDouble(json, 'start_s'),
        endS: _asDouble(json, 'end_s'),
        lengthHits: asInt(json, 'length_hits', fallback: 1),
        lengthMin: asInt(json, 'length_min', fallback: 1),
        lengthMax: asInt(json, 'length_max', fallback: 1),
        lengthConfidence: LengthConfidence.fromJson(json['length_confidence']),
        hitsObserved: asInt(json, 'hits_observed'),
        hitsInferred: asInt(json, 'hits_inferred'),
        endReason: RallyEndReason.fromJson(json['end_reason']),
        bounceIds: asStringList(json, 'bounce_ids'),
        flags: asStringList(json, 'flags'),
      );
}

class CourtAnalysis {
  const CourtAnalysis({
    required this.status,
    required this.refusalReason,
    required this.courtEngineVersion,
    required this.isEstimate,
    required this.cameraVerdict,
    required this.fps,
    required this.summary,
    required this.landings,
    required this.rallies,
  });

  final CourtSessionStatus status;
  final CourtRefusalReason? refusalReason;
  final String courtEngineVersion;
  final bool isEstimate;
  final StabilityVerdict cameraVerdict;
  final double? fps;
  final ShotMapSummary summary;
  final List<ShotLanding> landings;
  final List<Rally> rallies;

  factory CourtAnalysis.fromJson(Map<String, dynamic> json) => CourtAnalysis(
        status: CourtSessionStatus.fromJson(json['status']),
        refusalReason: CourtRefusalReason.fromJsonOrNull(json['refusal_reason']),
        courtEngineVersion: asString(json, 'court_engine_version'),
        // Default true: a body that forgets the flag must not read as exact.
        isEstimate: asBool(json, 'is_estimate', fallback: true),
        cameraVerdict: StabilityVerdict.fromJson(asMap(json, 'camera')?['verdict']),
        fps: asDoubleOrNull(asMap(json, 'capture'), 'fps'),
        summary: ShotMapSummary.fromJson(asMap(json, 'summary')),
        landings: asMapList(json, 'landings').map(ShotLanding.fromJson).toList(),
        rallies: asMapList(json, 'rallies').map(Rally.fromJson).toList(),
      );
}

class ScoreState {
  const ScoreState({
    required this.sets,
    required this.games,
    required this.pointsDisplay,
    required this.pointLabels,
    required this.inTiebreak,
    required this.server,
    required this.matchWinner,
  });

  /// Completed sets, player A first.
  final List<(int, int)> sets;
  final (int, int) games;
  final String pointsDisplay;

  /// Brief G14: `[player_a, player_b]`, null on older bodies.
  final (String, String)? pointLabels;
  final bool inTiebreak;
  final PlayerId server;
  final PlayerId? matchWinner;

  static (int, int)? _pair(Object? raw) {
    if (raw is! List || raw.length != 2 || raw[0] is! num || raw[1] is! num) return null;
    return ((raw[0] as num).toInt(), (raw[1] as num).toInt());
  }

  factory ScoreState.fromJson(Map<String, dynamic>? json) {
    final Object? labels = json?['point_labels'];
    return ScoreState(
      sets: <(int, int)>[
        for (final Object? s in asList(json, 'sets'))
          if (_pair(s) != null) _pair(s)!,
      ],
      games: _pair(json?['games']) ?? (0, 0),
      pointsDisplay: asString(json, 'points_display'),
      pointLabels: labels is List && labels.length == 2 && labels.every((Object? l) => l is String)
          ? (labels[0] as String, labels[1] as String)
          : null,
      inTiebreak: asBool(json, 'in_tiebreak'),
      server: PlayerId.fromJson(json?['server']),
      matchWinner: PlayerId.fromJsonOrNull(json?['match_winner']),
    );
  }
}

class ScoreTimelineEntry {
  const ScoreTimelineEntry({
    required this.eventId,
    required this.rallyId,
    required this.kind,
    required this.winner,
    required this.source,
    required this.basis,
    required this.needsReview,
    required this.pointsDisplay,
  });

  final String eventId;
  final String? rallyId;
  final ScoreEventKind kind;
  final PlayerId? winner;
  final ScoreSource source;
  final InferenceBasis? basis;
  final bool needsReview;
  final String pointsDisplay;

  factory ScoreTimelineEntry.fromJson(Map<String, dynamic> json) => ScoreTimelineEntry(
        eventId: asString(json, 'event_id'),
        rallyId: asStringOrNull(json, 'rally_id'),
        kind: ScoreEventKind.fromJson(json['kind']),
        winner: PlayerId.fromJsonOrNull(json['winner']),
        source: ScoreSource.fromJson(json['source']),
        basis: InferenceBasis.fromJsonOrNull(json['basis']),
        needsReview: asBool(json, 'needs_review'),
        pointsDisplay: asString(json, 'points_display'),
      );
}

class ScoreSummary {
  const ScoreSummary({
    required this.isConfirmed,
    required this.needsReviewCount,
    required this.current,
    required this.timeline,
  });

  /// True only when the server says "confirmed" (every point user-sourced).
  /// Anything else, including an unknown status, is an estimate.
  final bool isConfirmed;
  final int needsReviewCount;
  final ScoreState current;
  final List<ScoreTimelineEntry> timeline;

  factory ScoreSummary.fromJson(Map<String, dynamic> json) => ScoreSummary(
        isConfirmed: asStringOrNull(json, 'status') == 'confirmed',
        needsReviewCount: asInt(json, 'needs_review_count'),
        current: ScoreState.fromJson(asMap(json, 'current')),
        timeline: asMapList(json, 'timeline').map(ScoreTimelineEntry.fromJson).toList(),
      );
}
