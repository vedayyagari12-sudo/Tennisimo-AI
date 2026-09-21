import 'enums.dart';
import 'json_utils.dart';

/// One prioritised coaching improvement.
///
/// Mirrors `backend/app/models/feedback.py::Improvement`.
class Improvement {
  const Improvement({
    required this.priority,
    required this.title,
    required this.why,
    required this.cue,
    required this.drill,
    required this.metricRefs,
  });

  final int priority;
  final String title;
  final String why;
  final String cue;
  final String drill;
  final List<String> metricRefs;

  factory Improvement.fromJson(Map<String, dynamic> json) {
    return Improvement(
      priority: asInt(json, 'priority', fallback: 3),
      title: asString(json, 'title'),
      why: asString(json, 'why'),
      cue: asString(json, 'cue'),
      drill: asString(json, 'drill'),
      metricRefs: asStringList(json, 'metric_refs'),
    );
  }
}

/// Outcome of the server-side numeric guard over the Gemini draft.
///
/// Mirrors `backend/app/models/feedback.py::NumericGuardReport`.
class NumericGuardReport {
  const NumericGuardReport({
    required this.passed,
    required this.rejectedTokens,
    required this.fieldsDiscarded,
    required this.fellBackToTemplate,
    required this.mphRule,
    required this.modelFinishReason,
  });

  final bool passed;
  final List<String> rejectedTokens;
  final List<String> fieldsDiscarded;
  final bool fellBackToTemplate;
  final String mphRule;

  /// The model's `finish_reason` when it is WHY the template was used, e.g.
  /// `MAX_TOKENS` for a draft cut off by the output token budget.
  ///
  /// Null is the ordinary case and is left as null deliberately: the backend
  /// sends nothing here when the model was not called, answered completely, or
  /// failed for a transport reason. An absent field is legitimately "no reason
  /// to report", not an unrecognised value, so there is no sentinel here.
  final String? modelFinishReason;

  /// A one-line diagnostic when the coaching text was replaced by the template
  /// because Gemini's draft was truncated, else null.
  ///
  /// Kept as a pure getter so the truncation case is testable without a widget
  /// or a network call; [ResultsScreen] is what prints it, in debug builds.
  String? get truncationDebugLine {
    if (!fellBackToTemplate || modelFinishReason == null) return null;
    return 'Coaching text fell back to the template: model finish_reason = '
        '$modelFinishReason'
        '${rejectedTokens.isEmpty ? '' : ', rejected ${rejectedTokens.join(', ')}'}.';
  }

  factory NumericGuardReport.fromJson(Map<String, dynamic> json) {
    return NumericGuardReport(
      passed: asBool(json, 'passed', fallback: true),
      rejectedTokens: asStringList(json, 'rejected_tokens'),
      fieldsDiscarded: asStringList(json, 'fields_discarded'),
      fellBackToTemplate: asBool(json, 'fell_back_to_template'),
      mphRule: asString(json, 'mph_rule', fallback: 'banned'),
      modelFinishReason: asStringOrNull(json, 'model_finish_reason'),
    );
  }
}

/// Coaching text. Mirrors `backend/app/models/feedback.py::CoachingFeedback`.
class CoachingFeedback {
  const CoachingFeedback({
    required this.summary,
    required this.strengths,
    required this.improvements,
    required this.source,
    required this.model,
    required this.guard,
    required this.latencyMs,
  });

  final String summary;
  final List<String> strengths;
  final List<Improvement> improvements;
  final FeedbackSource source;
  final String? model;

  /// Null when the server omitted the guard block entirely.
  final NumericGuardReport? guard;
  final int? latencyMs;

  factory CoachingFeedback.fromJson(Map<String, dynamic> json) {
    final Map<String, dynamic>? guardJson = asMap(json, 'guard');
    return CoachingFeedback(
      summary: asString(json, 'summary'),
      strengths: asStringList(json, 'strengths'),
      improvements: asMapList(json, 'improvements')
          .map(Improvement.fromJson)
          .toList()
        ..sort((Improvement a, Improvement b) => a.priority.compareTo(b.priority)),
      source: FeedbackSource.fromJson(json['source']),
      model: asStringOrNull(json, 'model'),
      guard: guardJson == null ? null : NumericGuardReport.fromJson(guardJson),
      latencyMs: asIntOrNull(json, 'latency_ms'),
    );
  }
}
