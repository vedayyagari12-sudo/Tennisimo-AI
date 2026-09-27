/// The numbers behind the dashboard's charts.
///
/// PURE, like `dashboard_insights.dart`: payloads in, plain values out, no
/// `BuildContext`, no clock, no I/O. Every chart widget draws exactly what a
/// function here returned, so the decisions that could make a chart lie —
/// folding a tail into "Other", which clip counts as "previous", what "your
/// average" is an average OF — are made here, once, and tested on their own.
///
/// The two rules of the dashboard hold here too:
///
/// * `null` is "not measured" and never becomes `0`. A radar axis, a bar or a
///   line point with no measurement carries a null all the way to the painter,
///   which draws nothing for it.
/// * Nothing is averaged or compared across shot types. Every comparison below
///   takes clips of ONE shot type and refuses a pair that crosses types.
library;

import 'dart:math' as math;

import 'analysis_response.dart';
import 'dashboard_insights.dart';
import 'enums.dart';

// ---------------------------------------------------------------------------
// Wording
// ---------------------------------------------------------------------------

/// `1 metric`, `2 metrics`: [singular] for exactly one, else [pluralForm]
/// (or [singular] + `s`).
String plural(int count, String singular, [String? pluralForm]) =>
    count == 1 ? singular : (pluralForm ?? '${singular}s');

const List<String> _months = <String>[
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', //
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

/// `12 Sep`. Deliberately without the year: the dashboard's window is weeks,
/// not years.
String shortDate(DateTime at) => '${at.day} ${_months[at.month - 1]}';

// ---------------------------------------------------------------------------
// Shot mix: a part-to-whole of clips per shot type
// ---------------------------------------------------------------------------

/// How many slices a donut may show, the folded tail included. Past five,
/// adjacent slices blur and the smallest become slivers nobody can read.
const int kMaxMixSlices = 5;

/// How many slices get a categorical hue. The last of [kMaxMixSlices] is
/// reserved for the neutral tail.
const int kNamedMixSlices = kMaxMixSlices - 1;

/// One slice of the shot mix.
class ShotMixSlice {
  const ShotMixSlice({
    required this.label,
    required this.count,
    required this.percent,
    required this.shotTypes,
    required this.isOther,
  });

  /// The shot's name, or "Other" when several were folded together.
  final String label;

  /// Clips in this slice.
  final int count;

  /// Whole percent of all clips; see [wholePercents].
  final int percent;

  /// The shot types this slice stands for: one, or several for "Other".
  final List<ShotType> shotTypes;

  /// True for the folded tail, which takes the neutral colour, not a hue.
  final bool isOther;
}

/// Clips per shot type as at most [kMaxMixSlices] slices, largest first.
///
/// Up to [kNamedMixSlices] shot types are shown by name. Anything past that
/// is folded into ONE trailing slice. When the fold holds a single shot type
/// it keeps that shot's name — "Other" would hide which shot it is — but it
/// still takes the neutral colour, because it is the tail.
///
/// Returns an empty list when there are no clips at all.
List<ShotMixSlice> buildShotMix(List<ShotTypeInsight> shotTypes) {
  final List<ShotTypeInsight> sorted =
      shotTypes.where((ShotTypeInsight i) => i.sessions > 0).toList()
        ..sort((ShotTypeInsight a, ShotTypeInsight b) {
          final int byCount = b.sessions.compareTo(a.sessions);
          return byCount != 0
              ? byCount
              : a.shotType.label.compareTo(b.shotType.label);
        });
  if (sorted.isEmpty) return const <ShotMixSlice>[];

  final bool fold = sorted.length > kNamedMixSlices;
  final List<ShotTypeInsight> named = fold
      ? sorted.take(kNamedMixSlices).toList()
      : sorted;
  final List<ShotTypeInsight> tail = fold
      ? sorted.skip(kNamedMixSlices).toList()
      : const <ShotTypeInsight>[];

  final List<int> counts = <int>[
    for (final ShotTypeInsight i in named) i.sessions,
    if (tail.isNotEmpty)
      tail.fold<int>(0, (int sum, ShotTypeInsight i) => sum + i.sessions),
  ];
  final List<int> percents = wholePercents(counts);

  return <ShotMixSlice>[
    for (int i = 0; i < named.length; i++)
      ShotMixSlice(
        label: named[i].shotType.label,
        count: counts[i],
        percent: percents[i],
        shotTypes: <ShotType>[named[i].shotType],
        isOther: false,
      ),
    if (tail.isNotEmpty)
      ShotMixSlice(
        label: tail.length == 1 ? tail.single.shotType.label : 'Other',
        count: counts.last,
        percent: percents.last,
        shotTypes: <ShotType>[for (final ShotTypeInsight i in tail) i.shotType],
        isOther: true,
      ),
  ];
}

/// Whole percentages of [counts], by largest remainder, never splitting a
/// tie.
///
/// Plain rounding of 1/3, 1/3, 1/3 gives 33+33+33 = 99, and labels that do
/// not add up read as a mistake, so the leftover points go to the largest
/// remainders. But equal counts ALWAYS get equal percents: 3 and 3 clips
/// shown as 38% and 37% would contradict the counts printed beside them. So
/// a group of equal counts takes its extra point together or not at all,
/// and in that rare case the total is 99 rather than a split tie. No counts,
/// or all zeros, give all zeros.
List<int> wholePercents(List<int> counts) {
  final int total = counts.fold<int>(0, (int a, int b) => a + b);
  if (total <= 0) return List<int>.filled(counts.length, 0);
  final List<int> result = <int>[
    for (final int c in counts) (c * 100) ~/ total,
  ];
  int left = 100 - result.fold<int>(0, (int a, int b) => a + b);
  // Distinct counts, largest remainder first (ties: larger count first).
  final List<int> groups = counts.toSet().toList()
    ..sort((int a, int b) {
      final int byRemainder = ((b * 100) % total).compareTo((a * 100) % total);
      return byRemainder != 0 ? byRemainder : b.compareTo(a);
    });
  for (final int count in groups) {
    if (left <= 0) break;
    if ((count * 100) % total == 0) continue;
    final List<int> members = <int>[
      for (int i = 0; i < counts.length; i++)
        if (counts[i] == count) i,
    ];
    if (members.length > left) continue;
    for (final int i in members) {
      result[i] += 1;
    }
    left -= members.length;
  }
  return result;
}

// ---------------------------------------------------------------------------
// Skill profile: the categories of one swing, on a radar
// ---------------------------------------------------------------------------

/// A polygon needs three vertices. Below that a radar draws a line or a dot,
/// which says nothing about a shape, so no radar is drawn at all.
const int kMinRadarAxes = 3;

/// One spoke of the radar.
class RadarAxis {
  const RadarAxis({required this.category, required this.displayName});

  /// The wire name, e.g. `swing_path`.
  final String category;
  final String displayName;
}

/// One swing's scores, aligned to [SkillProfile.axes]. Nulls stay nulls.
class RadarSeries {
  const RadarSeries({required this.label, required this.values});

  final String label;

  /// One entry per axis: 0-100, or null for "not measured".
  final List<double?> values;

  int get measuredCount => values.whereType<double>().length;

  /// Whether this series has enough measured axes to be drawn at all.
  bool get plottable => measuredCount >= kMinRadarAxes;
}

/// The radar's data: fixed axes, the current swing, and optionally the
/// previous swing of the SAME shot type.
class SkillProfile {
  const SkillProfile({
    required this.axes,
    required this.current,
    required this.previous,
  });

  final List<RadarAxis> axes;
  final RadarSeries current;

  /// Null when there is no earlier swing of this shot type, when its analysis
  /// was not loaded, or when it measured too little to draw.
  final RadarSeries? previous;

  /// The axes the CURRENT swing could not measure, by display name.
  List<String> get unmeasured => <String>[
    for (int i = 0; i < axes.length; i++)
      if (current.values[i] == null) axes[i].displayName,
  ];
}

/// Builds the radar for [current], overlaid with [previous] when that is a
/// drawable swing of the same shot type.
///
/// Returns null when [current] has no categories. A profile whose current
/// series is not [RadarSeries.plottable] is still returned, so the widget can
/// say WHY there is no radar instead of silently showing nothing.
SkillProfile? buildSkillProfile(
  AnalysisResponse current, {
  AnalysisResponse? previous,
  String currentLabel = 'This swing',
  String previousLabel = 'Previous swing',
}) {
  if (current.categories.isEmpty) return null;
  final List<RadarAxis> axes = <RadarAxis>[
    for (final CategoryScore c in current.categories)
      RadarAxis(category: c.category, displayName: c.displayName),
  ];

  RadarSeries? prior;
  // Two shot types are scored against different bands; their shapes are not
  // comparable, so a cross-type "previous" is refused outright.
  if (previous != null &&
      previous.shotType == current.shotType &&
      previous.analysisId != current.analysisId) {
    final RadarSeries candidate = RadarSeries(
      label: previousLabel,
      values: <double?>[
        for (final RadarAxis axis in axes) _categoryScore(previous, axis),
      ],
    );
    if (candidate.plottable) prior = candidate;
  }

  return SkillProfile(
    axes: axes,
    current: RadarSeries(
      label: currentLabel,
      values: <double?>[
        for (final CategoryScore c in current.categories) c.score,
      ],
    ),
    previous: prior,
  );
}

double? _categoryScore(AnalysisResponse analysis, RadarAxis axis) {
  for (final CategoryScore c in analysis.categories) {
    if (c.category == axis.category) return c.score;
  }
  return null;
}

/// The full analysis of the session BEFORE the newest one, of the same shot
/// type, or null.
///
/// Strictly the immediately previous session of that type: when its detail
/// was not fetched this returns null rather than reaching further back, so
/// "previous swing" never quietly means "some older swing".
///
/// Also null when that session came from a different pipeline version (read
/// from the history list, see `version_segments.dart`): its shape was
/// measured differently, so overlaying it would compare across the change.
AnalysisResponse? previousOfSameShot(
  List<AnalysisSummary> historyNewestFirst,
  List<AnalysisResponse> details,
) {
  if (historyNewestFirst.length < 2) return null;
  final AnalysisSummary newest = historyNewestFirst.first;
  final ShotType shot = newest.shotType;
  for (final AnalysisSummary item in historyNewestFirst.skip(1)) {
    if (item.shotType != shot) continue;
    if (item.pipelineVersion != newest.pipelineVersion) return null;
    for (final AnalysisResponse d in details) {
      if (d.analysisId == item.analysisId) return d;
    }
    return null;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Newest clip vs your earlier average, per category, within one shot type
// ---------------------------------------------------------------------------

/// One category's pair of bars.
class CategoryComparison {
  const CategoryComparison({
    required this.category,
    required this.displayName,
    required this.current,
    required this.average,
    required this.averageOf,
    this.earlier = const <double?>[],
  });

  final String category;
  final String displayName;

  /// The earlier clips' own scores, OLDEST FIRST, nulls kept. [average] is
  /// their mean; the individual values stay reachable through the chart's
  /// tap readout, which is what lets one chart replace the per-category
  /// sparklines that used to repeat these scores.
  final List<double?> earlier;

  /// The newest clip's score, or null when it did not measure this category.
  final double? current;

  /// The mean over the EARLIER clips that measured this category, or null
  /// when none did. The newest clip is not in it: comparing a number with an
  /// average that already contains it pulls the two together.
  final double? average;

  /// How many earlier clips [average] is the mean of.
  final int averageOf;

  /// Current minus average, or null unless both exist.
  double? get delta =>
      current == null || average == null ? null : current! - average!;
}

/// Per-category newest-vs-earlier-average for ONE shot type.
///
/// [trends] come from [ShotTypeInsight.categoryTrends], so every point is the
/// same shot type by construction; each trend's last point is the newest
/// loaded clip. Returns an empty list — no chart — when there is no earlier
/// clip, or when no category has both a newest score and an earlier average,
/// because then there is nothing to compare.
List<CategoryComparison> buildCategoryComparison(List<CategoryTrend> trends) {
  final List<CategoryComparison> rows = <CategoryComparison>[];
  for (final CategoryTrend t in trends) {
    if (t.points.length < 2) continue;
    final List<double> earlier = t.points
        .sublist(0, t.points.length - 1)
        .whereType<double>()
        .toList();
    rows.add(
      CategoryComparison(
        category: t.category,
        displayName: t.displayName,
        current: t.points.last,
        average: earlier.isEmpty
            ? null
            : earlier.reduce((double a, double b) => a + b) / earlier.length,
        averageOf: earlier.length,
        earlier: t.points.sublist(0, t.points.length - 1),
      ),
    );
  }
  if (!rows.any((CategoryComparison r) => r.delta != null)) {
    return const <CategoryComparison>[];
  }
  return rows;
}

// ---------------------------------------------------------------------------
// Line chart axis
// ---------------------------------------------------------------------------

/// A clean y-axis for a 0-100 score series: whole-ten ticks bracketing the
/// data, at least [minSpan] tall, never outside 0-100.
///
/// Scores cluster (a run of 58-71 is typical), so pinning the axis to 0-100
/// would flatten the line into a smear; this keeps the shape visible while
/// every tick is still a round number a reader can quote.
({double min, double max, double step}) scoreAxis(
  List<double> values, {
  double minSpan = 20,
}) {
  if (values.isEmpty) return (min: 0, max: 100, step: 25);
  double lo = (values.reduce(math.min) / 10).floor() * 10.0;
  double hi = (values.reduce(math.max) / 10).ceil() * 10.0;
  if (hi == lo) hi += 10;
  // Grow symmetrically, downward first, until the span is readable or the
  // axis already covers the whole 0-100 scale.
  while (hi - lo < minSpan && (lo > 0 || hi < 100)) {
    if (lo > 0) lo -= 10;
    if (hi - lo < minSpan && hi < 100) hi += 10;
  }
  lo = lo.clamp(0, 100).toDouble();
  hi = hi.clamp(0, 100).toDouble();
  final double step = hi - lo > 40 ? 20 : 10;
  return (min: lo, max: hi, step: step);
}
