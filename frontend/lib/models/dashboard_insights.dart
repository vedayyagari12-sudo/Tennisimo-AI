/// The dashboard's derived numbers.
///
/// Everything in this file is PURE: functions take payloads the API client
/// already returned and give back plain values. No HTTP, no `BuildContext`, no
/// clock. That is what makes the aggregations testable on their own, and it is
/// why the widgets above them contain no arithmetic.
///
/// Two rules shape every type here.
///
/// **`null` is "not measured" and never becomes `0`.** A list of scores keeps
/// its nulls in place so a caller can tell "this session had no score" from
/// "this session scored nothing", and every derived statistic is itself
/// nullable rather than falling back to a zero nobody measured.
///
/// **Nothing is ever averaged across shot types.** A forehand score and a
/// serve score are not the same measurement — the backend applies a different
/// band table to each (`backend/app/analysis/rubric.py`), and for the four
/// unbanded shot types it scores `contact` and `follow_through` as null
/// outright. A mean over both is a number that describes no swing anybody
/// played. So the only aggregates in this file that cross shot types are
/// COUNTS and RECORDS — how many sessions, the fastest ball — never a mean and
/// never a spread.
library;

import 'dart:math' as math;

import 'analysis_response.dart';
import 'enums.dart';

/// Which way a series has moved across the window being looked at.
enum TrendDirection {
  improving,
  declining,

  /// Moved by less than [kSteadyBand]: real, but not a change worth acting on.
  steady,

  /// Fewer than two measured points. A trend needs two; with one we say so
  /// rather than inventing a direction out of a single number.
  unknown,
}

/// How many points a score has to move before the move is called a move.
///
/// Scores are 0-100 and carry per-clip measurement noise — framing, the
/// contact frame landing a frame either side. Calling a 0.4-point drift
/// "declining" would send a player to fix something that did not happen, so a
/// band is applied and its size is stated rather than hidden.
const double kSteadyBand = 2.0;

/// One scoring category's history within one shot type.
///
/// [points] is chronological, OLDEST FIRST, one entry per fetched analysis,
/// and holds nulls where the category could not be measured in that clip. For
/// the unbanded shot types that is the normal case for `contact` and
/// `follow_through`, and it is reported as "not measured", never as a zero.
class CategoryTrend {
  const CategoryTrend({
    required this.category,
    required this.displayName,
    required this.points,
  });

  /// The raw wire name, e.g. `swing_path`.
  final String category;

  /// The humanised name, taken from [CategoryScore.displayName].
  final String displayName;

  /// Oldest first. Nulls are kept, never zeroed.
  final List<double?> points;

  /// Only the points that were actually measured, oldest first.
  List<double> get measured => points.whereType<double>().toList();

  /// The most recent measured score, or null when nothing in the window
  /// measured this category.
  double? get latest => measured.isEmpty ? null : measured.last;

  /// The oldest measured score in the window.
  double? get earliest => measured.isEmpty ? null : measured.first;

  /// Newest minus oldest, or null when fewer than two points were measured.
  double? get delta {
    final List<double> m = measured;
    if (m.length < 2) return null;
    return m.last - m.first;
  }

  TrendDirection get direction => directionOf(delta);
}

/// [TrendDirection] for a signed change, or [TrendDirection.unknown] for null.
TrendDirection directionOf(double? delta) {
  if (delta == null) return TrendDirection.unknown;
  if (delta.abs() < kSteadyBand) return TrendDirection.steady;
  return delta > 0 ? TrendDirection.improving : TrendDirection.declining;
}

/// How widely one shot type's scores swing.
///
/// A different diagnosis from a low average: a player at 50 +/- 3 has a
/// technique problem, a player at 70 +/- 20 has a repeatability problem.
/// Always built from a single shot type's scores — see the library docstring.
class ConsistencySummary {
  const ConsistencySummary({required this.scores});

  /// The scored sessions considered, in any order.
  final List<double> scores;

  int get sampleSize => scores.length;

  /// Null until there are two scores: the spread of one number is not zero,
  /// it is unknown.
  double? get spread {
    if (scores.length < 2) return null;
    final double lo = scores.reduce(math.min);
    final double hi = scores.reduce(math.max);
    return hi - lo;
  }

  /// Population standard deviation, or null with fewer than two scores.
  double? get standardDeviation {
    if (scores.length < 2) return null;
    final double mean =
        scores.reduce((double a, double b) => a + b) / scores.length;
    final double variance = scores
            .map((double s) => (s - mean) * (s - mean))
            .reduce((double a, double b) => a + b) /
        scores.length;
    return math.sqrt(variance);
  }

  /// A word for [spread], or null when there is no spread to describe.
  ///
  /// The cuts are stated here rather than buried in a widget: under 8 points
  /// of range the swings sit inside clip-to-clip noise, over 20 the player is
  /// producing genuinely different swings from one session to the next.
  ConsistencyBand? get band {
    final double? s = spread;
    if (s == null) return null;
    if (s < 8) return ConsistencyBand.tight;
    if (s <= 20) return ConsistencyBand.moderate;
    return ConsistencyBand.wide;
  }
}

/// The wording bands behind [ConsistencySummary.band].
enum ConsistencyBand { tight, moderate, wide }

/// How much of the scorecard a clip actually supported.
///
/// Low coverage is a FILMING problem, not a technique problem, and telling the
/// two apart is the whole reason this is on the screen.
class CoverageSummary {
  const CoverageSummary({required this.available, required this.total});

  final int available;
  final int total;

  /// Null when the analysis declared no metrics at all — there is no fraction
  /// of nothing.
  double? get ratio => total <= 0 ? null : available / total;

  /// True when at least one metric could not be measured.
  bool get hasGaps => total > 0 && available < total;
}

/// Everything known about ONE shot type.
///
/// Counts, scores and speeds come from the history summaries, so they cover
/// every session of this type the server returned. The category trends come
/// from the full analyses that were actually fetched, which is a smaller
/// window — [detailsInspected] says how much smaller, and the UI quotes it
/// rather than implying the breakdown covers everything.
class ShotTypeInsight {
  const ShotTypeInsight({
    required this.shotType,
    required this.scorePoints,
    required this.speedPoints,
    required this.categoryTrends,
    required this.detailsInspected,
    required this.latestCoverage,
  });

  final ShotType shotType;

  /// Overall score per session of this type, OLDEST FIRST, nulls kept.
  final List<double?> scorePoints;

  /// Ball speed in mph per session of this type, OLDEST FIRST, nulls kept.
  final List<double?> speedPoints;

  /// One series per category the fetched analyses of this type carried.
  final List<CategoryTrend> categoryTrends;

  /// How many full analyses of this shot type the trends were built from.
  final int detailsInspected;

  /// Metric coverage of the newest fetched analysis of this type.
  final CoverageSummary? latestCoverage;

  /// Every session of this shot type, scored or not.
  int get sessions => scorePoints.length;

  List<double> get scores => scorePoints.whereType<double>().toList();

  /// True when NO session of this type produced an overall score.
  ///
  /// This is a real and expected state, not a bug: the backend only emits an
  /// overall score once at least three categories scored, and the four
  /// unbanded shot types can only ever score three. One unmeasurable category
  /// therefore drops the whole score. The UI says so in words instead of
  /// showing a blank chart or a zero.
  bool get hasNoScoreAtAll => scores.isEmpty;

  double? get bestScore => scores.isEmpty ? null : scores.reduce(math.max);

  /// The mean WITHIN this shot type only.
  double? get averageScore => scores.isEmpty
      ? null
      : scores.reduce((double a, double b) => a + b) / scores.length;

  ConsistencySummary get consistency => ConsistencySummary(scores: scores);

  /// Latest minus the one before it, for this shot type. Null unless both were
  /// scored.
  double? get latestDelta {
    if (scorePoints.length < 2) return null;
    final double? latest = scorePoints.last;
    final double? previous = scorePoints[scorePoints.length - 2];
    if (latest == null || previous == null) return null;
    return latest - previous;
  }

  List<double> get speeds => speedPoints.whereType<double>().toList();

  double? get topSpeed => speeds.isEmpty ? null : speeds.reduce(math.max);

  /// The category to work on: the lowest MEASURED latest score.
  ///
  /// Null when nothing in the window measured any category — a player with no
  /// measurable clips is told that, not handed an arbitrary category.
  CategoryTrend? get focus {
    CategoryTrend? worst;
    for (final CategoryTrend trend in categoryTrends) {
      final double? score = trend.latest;
      if (score == null) continue;
      if (worst == null || score < worst.latest!) worst = trend;
    }
    return worst;
  }
}

/// Everything the dashboard derives from the payloads it fetched.
class DashboardInsights {
  const DashboardInsights({
    required this.shotTypes,
    required this.notRecorded,
    required this.totalSessions,
    required this.topSpeedMph,
    required this.latestShotType,
    required this.latestCoverage,
    required this.analysesInspected,
  });

  /// One entry per shot type the player has actually recorded, most sessions
  /// first.
  final List<ShotTypeInsight> shotTypes;

  /// Shot types with no sessions at all. [ShotType.unknown] is never listed
  /// here: "the technique could not be classified" is not a shot a player can
  /// go and record.
  final List<ShotType> notRecorded;

  final int totalSessions;

  /// The fastest ball ever measured. A RECORD, not a mean, so it is allowed to
  /// cross shot types. Null when nothing was ever calibrated.
  final int? topSpeedMph;

  /// The insight for the newest session's shot type — what the hero and the
  /// "what to work on" card are about.
  final ShotTypeInsight? latestShotType;

  /// The newest fetched analysis's metric coverage, whatever its shot type.
  final CoverageSummary? latestCoverage;

  /// How many full analyses were fetched in total.
  final int analysesInspected;

  /// How many distinct shot types have at least one session.
  int get shotTypesRecorded => shotTypes.length;
}

/// Builds every derived number the dashboard shows.
///
/// [history] is the summary list as the API returns it: NEWEST FIRST.
/// [details] are the full analyses fetched for a subset of that list, also
/// newest first, and may be empty when those requests failed.
DashboardInsights buildDashboardInsights({
  required List<AnalysisSummary> history,
  required List<AnalysisResponse> details,
}) {
  // Group the summaries. `history` is newest first, so reversing each group
  // gives the chronological series the charts read.
  final Map<ShotType, List<AnalysisSummary>> byType =
      <ShotType, List<AnalysisSummary>>{};
  for (final AnalysisSummary item in history) {
    byType.putIfAbsent(item.shotType, () => <AnalysisSummary>[]).add(item);
  }

  // Details are matched to a shot type by their own payload, not by the
  // summary row, so a detail whose id is missing from the page still lands in
  // the right group.
  final Map<ShotType, List<AnalysisResponse>> detailsByType =
      <ShotType, List<AnalysisResponse>>{};
  for (final AnalysisResponse detail in details) {
    detailsByType
        .putIfAbsent(detail.shotType, () => <AnalysisResponse>[])
        .add(detail);
  }

  final List<ShotTypeInsight> insights = <ShotTypeInsight>[
    for (final MapEntry<ShotType, List<AnalysisSummary>> entry
        in byType.entries)
      _insightFor(
        entry.key,
        newestFirst: entry.value,
        detailsNewestFirst:
            detailsByType[entry.key] ?? const <AnalysisResponse>[],
      ),
  ];

  insights.sort((ShotTypeInsight a, ShotTypeInsight b) {
    final int bySessions = b.sessions.compareTo(a.sessions);
    if (bySessions != 0) return bySessions;
    return a.shotType.label.compareTo(b.shotType.label);
  });

  final List<int> speeds = history
      .map((AnalysisSummary e) => e.ballSpeedMph)
      .whereType<int>()
      .toList();

  final ShotType? latestType =
      history.isEmpty ? null : history.first.shotType;

  return DashboardInsights(
    shotTypes: insights,
    notRecorded: <ShotType>[
      for (final ShotType type in ShotType.values)
        if (type != ShotType.unknown && !byType.containsKey(type)) type,
    ],
    totalSessions: history.length,
    topSpeedMph: speeds.isEmpty ? null : speeds.reduce(math.max),
    latestShotType: latestType == null
        ? null
        : insights.firstWhere(
            (ShotTypeInsight i) => i.shotType == latestType,
          ),
    latestCoverage: details.isEmpty ? null : coverageOf(details.first),
    analysesInspected: details.length,
  );
}

ShotTypeInsight _insightFor(
  ShotType shotType, {
  required List<AnalysisSummary> newestFirst,
  required List<AnalysisResponse> detailsNewestFirst,
}) {
  final List<AnalysisSummary> oldestFirst = newestFirst.reversed.toList();
  return ShotTypeInsight(
    shotType: shotType,
    scorePoints:
        oldestFirst.map((AnalysisSummary e) => e.overallScore).toList(),
    speedPoints: oldestFirst
        .map((AnalysisSummary e) => e.ballSpeedMph?.toDouble())
        .toList(),
    categoryTrends:
        buildCategoryTrends(detailsNewestFirst.reversed.toList()),
    detailsInspected: detailsNewestFirst.length,
    latestCoverage: detailsNewestFirst.isEmpty
        ? null
        : coverageOf(detailsNewestFirst.first),
  );
}

/// Turns a chronological run of analyses into one series per category.
///
/// [oldestFirst] must be ordered oldest -> newest. A category missing from an
/// analysis entirely contributes a null at that position, exactly as a
/// present-but-unmeasured category does: in both cases there is no number.
List<CategoryTrend> buildCategoryTrends(List<AnalysisResponse> oldestFirst) {
  if (oldestFirst.isEmpty) return const <CategoryTrend>[];

  // Keyed in the order the NEWEST analysis lists them, so the current
  // scorecard's own ordering is what the player sees.
  final Map<String, String> names = <String, String>{};
  for (final AnalysisResponse analysis in oldestFirst.reversed) {
    for (final CategoryScore category in analysis.categories) {
      names.putIfAbsent(category.category, () => category.displayName);
    }
  }

  return <CategoryTrend>[
    for (final MapEntry<String, String> entry in names.entries)
      CategoryTrend(
        category: entry.key,
        displayName: entry.value,
        points: <double?>[
          for (final AnalysisResponse analysis in oldestFirst)
            _scoreFor(analysis, entry.key),
        ],
      ),
  ];
}

double? _scoreFor(AnalysisResponse analysis, String category) {
  for (final CategoryScore c in analysis.categories) {
    if (c.category == category) return c.score;
  }
  return null;
}

/// Sums one analysis's per-category metric counts.
CoverageSummary coverageOf(AnalysisResponse analysis) {
  int available = 0;
  int total = 0;
  for (final CategoryScore c in analysis.categories) {
    available += c.metricsAvailable;
    total += c.metricsTotal;
  }
  return CoverageSummary(available: available, total: total);
}

/// Which analyses the dashboard should fetch in full, newest first.
///
/// The history endpoint carries only `{id, created_at, shot_type,
/// overall_score, ball_speed_mph}` — no categories and no metric coverage —
/// so every per-category number on this screen costs one extra request. That
/// makes the selection a BUDGET decision, and it is made here, in a pure
/// function, so the cost is visible and testable instead of hidden in a
/// `Future.wait` somewhere.
///
/// The policy, in priority order, deduplicated and then truncated to
/// [budget]:
///
///  1. the newest analysis of EVERY shot type — breadth first, so a section is
///     never empty merely because that shot was last played a while ago;
///  2. the newest [recent] analyses overall — depth on what the player is
///     working on now;
///  3. rounds 2..[perShotType] of each shot type, oldest round last.
///
/// Priority order matters more than it looks: truncating a plain newest-first
/// union to a budget silently drops exactly the old-but-only shot types that
/// step 1 exists to protect. Worst case the dashboard makes `1 + budget`
/// requests.
List<String> detailIdsToFetch(
  List<AnalysisSummary> history, {
  int recent = 6,
  int perShotType = 3,
  int budget = 12,
}) {
  // rounds[n] is the (n+1)-th newest analysis of each shot type, in history
  // order.
  final List<List<String>> rounds = <List<String>>[
    for (int i = 0; i < math.max(perShotType, 0); i++) <String>[],
  ];
  final Map<ShotType, int> seen = <ShotType, int>{};
  for (final AnalysisSummary item in history) {
    final int round = seen[item.shotType] ?? 0;
    if (round >= rounds.length) continue;
    seen[item.shotType] = round + 1;
    rounds[round].add(item.analysisId);
  }

  final List<String> ordered = <String>[
    if (rounds.isNotEmpty) ...rounds.first,
    for (final AnalysisSummary item in history.take(recent)) item.analysisId,
    for (final List<String> round in rounds.skip(1)) ...round,
  ];

  final Set<String> emitted = <String>{};
  final List<String> result = <String>[];
  for (final String id in ordered) {
    if (result.length >= budget) break;
    if (!emitted.add(id)) continue;
    result.add(id);
  }
  return result;
}
