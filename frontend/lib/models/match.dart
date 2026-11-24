// Match mode (2026-11-09): the `/v1/matches` bodies. Mirrors
// backend/app/models/match.py. Null means "not measured", never zero.

import 'coaching_feedback.dart';
import 'court.dart';
import 'json_utils.dart';

/// Matches and Rallies are the same job on the server. A rally also gets an
/// overall coaching summary, worded by AI from the measured numbers.
enum SessionKind {
  match('match'),
  rally('rally');

  const SessionKind(this.wire);
  final String wire;

  /// Records from before rallies existed have no kind: they are matches.
  static SessionKind fromJson(Object? raw) => raw == 'rally' ? rally : match;
}

enum MatchStatus {
  queued('queued'),
  running('running'),
  complete('complete'),
  failed('failed'),
  unrecognized('');

  const MatchStatus(this.wire);
  final String wire;

  bool get isFinished => this == complete || this == failed || this == unrecognized;

  static MatchStatus fromJson(Object? raw) => values.firstWhere(
        (MatchStatus s) => s.wire == raw && s != unrecognized,
        orElse: () => unrecognized,
      );
}

/// How far the court lines found in the video can be trusted.
enum CourtConfidence {
  good('good'),
  check('check'),
  poor('poor'),
  unknown('');

  const CourtConfidence(this.wire);
  final String wire;

  static CourtConfidence fromJson(Object? raw) => values.firstWhere(
        (CourtConfidence c) => c.wire == raw && c != unknown,
        orElse: () => unknown,
      );
}

/// Mirrors backend `CourtCheck`: written as soon as the court is found, so the
/// player can look at the lines before the analysis ends.
class CourtCheck {
  const CourtCheck({
    required this.confidence,
    required this.source,
    required this.support,
    required this.linesVisible,
    required this.cornersInFrame,
    required this.reasons,
    required this.callsAllowed,
  });

  final CourtConfidence confidence;
  final String source;
  final double? support;
  final int? linesVisible;
  final int cornersInFrame;

  /// `court_cut_off`, `few_lines`, `weak_match`, `calls_off`, `tap_error`.
  final List<String> reasons;
  final bool callsAllowed;

  /// True when the player should look at the lines before trusting the rest.
  bool get needsAttention => confidence != CourtConfidence.good;

  static CourtCheck? fromJsonOrNull(Map<String, dynamic>? json) {
    if (json == null) return null;
    final Object? raw = json['reasons'];
    return CourtCheck(
      confidence: CourtConfidence.fromJson(json['confidence']),
      source: asString(json, 'source', fallback: 'auto'),
      support: asDoubleOrNull(json, 'support'),
      linesVisible: asIntOrNull(json, 'lines_visible'),
      cornersInFrame: asInt(json, 'corners_in_frame'),
      reasons: <String>[if (raw is List) for (final Object? r in raw) '$r'],
      callsAllowed: asBool(json, 'calls_allowed', fallback: true),
    );
  }
}

/// What a running job is doing; drives the progress steps.
enum MatchStage {
  preparing('preparing'),
  court('court'),
  ball('ball'),
  players('players'),
  finishing('finishing');

  const MatchStage(this.wire);
  final String wire;

  /// Null for a stage this app does not know: the bar still moves, unlabelled.
  static MatchStage? fromJson(Object? raw) {
    for (final MatchStage s in values) {
      if (s.wire == raw) return s;
    }
    return null;
  }
}

class MatchStats {
  const MatchStats({
    required this.shotsLanded,
    required this.rallies,
    required this.longestRallyHits,
    required this.averageRallyHits,
    required this.inCount,
    required this.outCount,
    required this.inPercent,
    required this.deepPercent,
  });

  final int shotsLanded;
  final int rallies;
  final int? longestRallyHits;
  final double? averageRallyHits;
  final int inCount;
  final int outCount;
  final double? inPercent;
  final double? deepPercent;

  static MatchStats? fromJsonOrNull(Map<String, dynamic>? json) => json == null
      ? null
      : MatchStats(
          shotsLanded: asInt(json, 'shots_landed'),
          rallies: asInt(json, 'rallies'),
          longestRallyHits: asIntOrNull(json, 'longest_rally_hits'),
          averageRallyHits: asDoubleOrNull(json, 'average_rally_hits'),
          inCount: asInt(json, 'in_count'),
          outCount: asInt(json, 'out_count'),
          inPercent: asDoubleOrNull(json, 'in_percent'),
          deepPercent: asDoubleOrNull(json, 'deep_percent'),
        );
}

class MatchRecord {
  const MatchRecord({
    required this.matchId,
    this.kind = SessionKind.match,
    required this.status,
    required this.createdAt,
    required this.title,
    required this.courtFormat,
    required this.progress,
    required this.errorCode,
    required this.errorMessage,
    required this.stats,
    required this.calibrationSource,
    required this.hasCourtPicture,
    required this.durationS,
    required this.result,
    this.feedback,
    this.stage,
    this.etaS,
    this.courtCheck,
  });

  final String matchId;
  final SessionKind kind;
  final MatchStatus status;
  final DateTime? createdAt;
  final String? title;
  final String courtFormat;

  /// 0-1 while it runs; null when unknown.
  final double? progress;
  final String? errorCode;
  final String? errorMessage;
  final MatchStats? stats;

  /// `taps` or `auto`; null without a court.
  final String? calibrationSource;
  final bool hasCourtPicture;
  final double? durationS;

  /// Only on a single match, never in the list.
  final CourtAnalysis? result;

  /// Rallies only: the overall coaching summary. Null for a match.
  final CoachingFeedback? feedback;

  /// While running: the step it is on, and seconds left at the rate so far.
  /// Null when unknown (the estimate waits for enough progress).
  final MatchStage? stage;
  final double? etaS;

  /// Whether the court lines can be trusted; available while it runs.
  final CourtCheck? courtCheck;

  bool get courtFoundAutomatically => calibrationSource == 'auto';

  factory MatchRecord.fromJson(Map<String, dynamic> json) {
    final Map<String, dynamic>? result = asMap(json, 'result');
    final Map<String, dynamic>? feedback = asMap(json, 'feedback');
    final String created = asString(json, 'created_at');
    final String title = asString(json, 'title');
    final String code = asString(json, 'error_code');
    final String message = asString(json, 'error_message');
    final String source = asString(json, 'calibration_source');
    return MatchRecord(
      matchId: asString(json, 'match_id'),
      kind: SessionKind.fromJson(json['kind']),
      status: MatchStatus.fromJson(json['status']),
      createdAt: DateTime.tryParse(created)?.toLocal(),
      title: title.isEmpty ? null : title,
      courtFormat: asString(json, 'court_format', fallback: 'singles'),
      progress: asDoubleOrNull(json, 'progress'),
      errorCode: code.isEmpty ? null : code,
      errorMessage: message.isEmpty ? null : message,
      stats: MatchStats.fromJsonOrNull(asMap(json, 'stats')),
      calibrationSource: source.isEmpty ? null : source,
      hasCourtPicture: asBool(json, 'has_court_picture'),
      durationS: asDoubleOrNull(json, 'duration_s'),
      result: result == null ? null : CourtAnalysis.fromJson(result),
      feedback: feedback == null ? null : CoachingFeedback.fromJson(feedback),
      stage: MatchStage.fromJson(json['stage']),
      etaS: asDoubleOrNull(json, 'eta_s'),
      courtCheck: CourtCheck.fromJsonOrNull(asMap(json, 'court_check')),
    );
  }
}
