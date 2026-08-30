import 'ball_speed.dart';
import 'coaching_feedback.dart';
import 'enums.dart';
import 'json_utils.dart';
import 'metric_score.dart';

/// The analysis envelope the client actually renders.
///
/// This is a NARROWED view of PIPELINE.md 2.4 `AnalysisResponse`: it carries the
/// blocks the screens use and ignores diagnostics (video meta, pose quality,
/// phases, timings) that no screen shows. Phase 5 of the backend is not built,
/// so nesting is read tolerantly:
///
///  * `shot_type` may be the nested `ShotTypeInference` object or a bare string
///    (the indexed `analyses.shot_type` column shape).
///  * scored metrics are read from `scorecard.metrics`, falling back to a
///    top-level `metrics` ONLY when that is a list — in PIPELINE.md 2.4 the
///    top-level `metrics` is `SwingMetrics`, a flat object, not a list.
///  * `ball_speed` may be absent entirely (v1-era rows) -> [ballSpeed] is null.
class AnalysisResponse {
  const AnalysisResponse({
    required this.analysisId,
    required this.createdAt,
    required this.status,
    required this.pipelineVersion,
    required this.shotType,
    required this.shotTypeConfidence,
    required this.overallScore,
    required this.metrics,
    required this.ballSpeed,
    required this.feedback,
    required this.warnings,
  });

  final String analysisId;
  final DateTime? createdAt;
  final AnalysisStatus status;
  final String pipelineVersion;
  final ShotType shotType;

  /// 0.0-1.0, or null when the server did not report one.
  final double? shotTypeConfidence;

  /// 0-100, or null when no score could be produced.
  final double? overallScore;
  final List<MetricScore> metrics;

  /// Null when the payload has no `ball_speed` block at all (v1-era rows).
  final BallSpeedResult? ballSpeed;
  final CoachingFeedback? feedback;
  final List<String> warnings;

  factory AnalysisResponse.fromJson(Map<String, dynamic> json) {
    final Map<String, dynamic>? scorecard = asMap(json, 'scorecard');
    final Map<String, dynamic>? shotTypeJson = asMap(json, 'shot_type');
    final Map<String, dynamic>? ballSpeedJson = asMap(json, 'ball_speed');
    final Map<String, dynamic>? feedbackJson = asMap(json, 'feedback');

    List<MetricScore> metrics = const <MetricScore>[];
    if (scorecard != null) {
      metrics =
          asMapList(scorecard, 'metrics').map(MetricScore.fromJson).toList();
    }
    if (metrics.isEmpty && json['metrics'] is List) {
      metrics = asMapList(json, 'metrics').map(MetricScore.fromJson).toList();
    }

    return AnalysisResponse(
      analysisId: asString(json, 'analysis_id', fallback: asString(json, 'id')),
      createdAt: asDateTimeOrNull(json, 'created_at'),
      status: AnalysisStatus.fromJson(json['status']),
      pipelineVersion: asString(json, 'pipeline_version', fallback: 'v1'),
      shotType: ShotType.fromJson(
        shotTypeJson != null ? shotTypeJson['shot_type'] : json['shot_type'],
      ),
      shotTypeConfidence: shotTypeJson != null
          ? asDoubleOrNull(shotTypeJson, 'confidence')
          : asDoubleOrNull(json, 'shot_type_confidence'),
      overallScore: asDoubleOrNull(scorecard, 'overall_score') ??
          asDoubleOrNull(json, 'overall_score'),
      metrics: metrics,
      ballSpeed:
          ballSpeedJson == null ? null : BallSpeedResult.fromJson(ballSpeedJson),
      feedback:
          feedbackJson == null ? null : CoachingFeedback.fromJson(feedbackJson),
      warnings: asStringList(json, 'warnings'),
    );
  }
}

/// One row in the history list.
///
/// Built from whatever the list endpoint returns; a full [AnalysisResponse]
/// body parses through this just as well, since every key it reads also exists
/// on the full payload.
class AnalysisSummary {
  const AnalysisSummary({
    required this.analysisId,
    required this.createdAt,
    required this.shotType,
    required this.overallScore,
    required this.ballSpeedMph,
  });

  final String analysisId;
  final DateTime? createdAt;
  final ShotType shotType;
  final double? overallScore;

  /// Null means: show no speed at all for this row.
  final int? ballSpeedMph;

  factory AnalysisSummary.fromJson(Map<String, dynamic> json) {
    final Map<String, dynamic>? shotTypeJson = asMap(json, 'shot_type');
    final Map<String, dynamic>? ballSpeedJson = asMap(json, 'ball_speed');
    final Map<String, dynamic>? scorecard = asMap(json, 'scorecard');

    return AnalysisSummary(
      analysisId: asString(json, 'analysis_id', fallback: asString(json, 'id')),
      createdAt: asDateTimeOrNull(json, 'created_at'),
      shotType: ShotType.fromJson(
        shotTypeJson != null ? shotTypeJson['shot_type'] : json['shot_type'],
      ),
      overallScore: asDoubleOrNull(json, 'overall_score') ??
          asDoubleOrNull(scorecard, 'overall_score'),
      ballSpeedMph: asIntOrNull(json, 'ball_speed_mph') ??
          asIntOrNull(ballSpeedJson, 'ball_speed_mph'),
    );
  }
}
