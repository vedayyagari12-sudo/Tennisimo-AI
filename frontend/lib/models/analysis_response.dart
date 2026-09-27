import 'ball_speed.dart';
import 'coaching_feedback.dart';
import 'enums.dart';
import 'json_utils.dart';
import 'metric_score.dart';

/// One weighted scoring category. Mirrors
/// `backend/app/models/responses.py::CategoryScore`.
///
/// [category] is kept as the raw wire string rather than a new enum: the five
/// documented values are labels, not behaviour — nothing in this client
/// branches on which category it is — so an enum would only add a fallback
/// question with no decision behind it. A category the backend adds later
/// still renders with its own name.
///
/// `score_0_100 == null` means NOT MEASURED for this clip: no metric in the
/// category could be measured. It is never rendered as 0, exactly as
/// [MetricScore] treats a null value.
class CategoryScore {
  const CategoryScore({
    required this.category,
    required this.score,
    required this.weight,
    required this.metricNames,
    required this.metricsAvailable,
    required this.metricsTotal,
  });

  final String category;

  /// 0-100, or null when no metric in this category was measurable.
  final double? score;

  /// This category's share of the overall score, 0.0-1.0.
  final double weight;
  final List<String> metricNames;
  final int metricsAvailable;
  final int metricsTotal;

  bool get isMeasured => score != null;

  factory CategoryScore.fromJson(Map<String, dynamic> json) {
    return CategoryScore(
      category: asString(json, 'category', fallback: 'category'),
      score: asDoubleOrNull(json, 'score_0_100'),
      weight: asDoubleOrNull(json, 'weight') ?? 0.0,
      metricNames: asStringList(json, 'metric_names'),
      metricsAvailable: asInt(json, 'metrics_available'),
      metricsTotal: asInt(json, 'metrics_total'),
    );
  }

  /// `follow_through` -> `Follow through`.
  String get displayName {
    final String cleaned = category.replaceAll('_', ' ').trim();
    if (cleaned.isEmpty) return category;
    return cleaned[0].toUpperCase() + cleaned.substring(1);
  }

  /// The score, or the not-measured wording. Never "0" for null.
  String get displayScore =>
      score == null ? 'not measured' : score!.round().toString();

  /// `3 of 5 metrics · 25% of the score`; `1 of 1 metric · ...` — the noun
  /// agrees with the total.
  String get displayCoverage =>
      '$metricsAvailable of $metricsTotal '
      '${metricsTotal == 1 ? 'metric' : 'metrics'} · '
      '${(weight * 100).round()}% of the score';
}

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
    required this.categories,
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

  /// The weighted category breakdown behind [overallScore]. Empty when the
  /// payload carried no `scorecard.categories`.
  final List<CategoryScore> categories;
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

    List<CategoryScore> categories = const <CategoryScore>[];
    if (scorecard != null) {
      categories = asMapList(scorecard, 'categories')
          .map(CategoryScore.fromJson)
          .toList();
    }

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
      categories: categories,
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
    this.pipelineVersion,
  });

  final String analysisId;
  final DateTime? createdAt;
  final ShotType shotType;
  final double? overallScore;

  /// Null means: show no speed at all for this row.
  final int? ballSpeedMph;

  /// The `pipeline_version` that produced this row's numbers, e.g. `v3`.
  ///
  /// Null when the list endpoint did not send one — backends deployed before
  /// the key was added. Deliberately NOT defaulted the way
  /// [AnalysisResponse.pipelineVersion] is: a made-up version here would draw
  /// a "Scoring updated" boundary that never happened. See
  /// `version_segments.dart` for how null is treated.
  final String? pipelineVersion;

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
      pipelineVersion: asStringOrNull(json, 'pipeline_version'),
    );
  }
}
