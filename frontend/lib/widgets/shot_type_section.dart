import 'package:flutter/material.dart';

import '../models/chart_data.dart';
import '../models/dashboard_insights.dart';
import '../models/enums.dart';
import '../theme/app_theme.dart';
import '../theme/motion.dart';
import 'app_card.dart';
import 'comparison_bars.dart';
import 'inline_stats.dart';
import 'mini_trend.dart';
import 'score_line_chart.dart';
import 'trend_direction_chip.dart';

/// One shot type's own block: a compact header that is always visible, and
/// behind it the full block — the score trend, ball speed, the latest clip
/// against the player's average, and coverage.
///
/// Every number here is drawn from THIS shot type alone. Nothing is averaged
/// with another shot — the backend scores each shot type against a different
/// band table, so a combined mean would describe no swing the player played.
/// Nor is anything compared across pipeline versions: the best, the average,
/// the range, the trend and the category comparison use only clips on the
/// newest clip's version, and the score chart breaks its line where the
/// version changes. See `version_segments.dart`.
///
/// ## Collapsed by default, one open
///
/// With several shot types the full blocks stacked to seven phone screens.
/// The header carries the four things a player scans for — the shot, how
/// many clips, the best score and which way it is moving (an icon and a
/// word, never colour alone) — and tapping it opens the rest. The dashboard
/// opens only the most recently played shot type. The header is one
/// semantic button that reports its expanded state; the open and close is
/// animated, and instant under reduced motion.
///
/// ## One category chart, not two
///
/// The block used to show the latest clip against the earlier average as
/// grouped bars AND the same category scores again as five sparklines. The
/// bars stay: they put every category on one shared 0-100 scale, where the
/// sparklines each scaled themselves and their slopes could not be compared.
/// The sparklines held at most three clips each (the detail budget), and the
/// earlier clips' individual scores remain reachable in the bars' tap
/// readout. The sparklines come back only when the bars cannot be drawn —
/// the newest clip's analysis failed to load, or there is no earlier clip —
/// so the category scores are never simply dropped.
///
/// ## The no-overall-score case
///
/// A shot type can legitimately have sessions and no overall score at all.
/// That is not missing data and it is not a zero: the server produces an
/// overall score only when enough categories were measurable, and for the shot
/// types whose bands are not authored yet that bar is easy to miss. When it
/// happens this section says so in words and shows what WAS measured —
/// speeds, per-category scores, coverage — rather than rendering an empty
/// chart that reads as "you scored nothing".
class ShotTypeSection extends StatefulWidget {
  const ShotTypeSection({
    super.key,
    required this.insight,
    this.initiallyExpanded = false,
  });

  final ShotTypeInsight insight;

  /// Whether the full block starts open. The dashboard opens only the shot
  /// type of the newest session.
  final bool initiallyExpanded;

  /// Copy for a shot type with sessions but no overall score on any of them.
  static const String noOverallScoreMessage =
      'No overall score was produced for these clips. That is not a score of '
      'zero — the scorecard needs several categories measurable at once. What '
      'was measured is below.';

  /// The header's wording for a shot type that has never been scored.
  static const String notScoredLabel = 'No overall score';

  @override
  State<ShotTypeSection> createState() => _ShotTypeSectionState();
}

class _ShotTypeSectionState extends State<ShotTypeSection> {
  late bool _expanded = widget.initiallyExpanded;

  ShotTypeInsight get insight => widget.insight;

  void _toggle() => setState(() => _expanded = !_expanded);

  @override
  Widget build(BuildContext context) {
    final Duration duration = motionDuration(context, kExpandDuration);
    final Widget body = _expanded
        ? Padding(
            padding: const EdgeInsets.fromLTRB(
              AppSpacing.lg,
              0,
              AppSpacing.lg,
              AppSpacing.lg,
            ),
            child: _body(context),
          )
        : const SizedBox(width: double.infinity);
    return AppCard(
      padding: EdgeInsets.zero,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          _header(context, duration),
          // Under reduced motion the body is swapped in directly: an
          // AnimatedSize with a zero duration re-dirties itself mid-layout.
          if (duration == Duration.zero)
            body
          else
            AnimatedSize(
              duration: duration,
              curve: kEnterCurve,
              alignment: Alignment.topCenter,
              child: body,
            ),
        ],
      ),
    );
  }

  /// Shot, clip count, best score and direction, and the disclosure chevron.
  ///
  /// One merged semantic node: a screen reader hears the whole summary as a
  /// single button, with its expanded state.
  Widget _header(BuildContext context, Duration duration) {
    final ThemeData theme = Theme.of(context);
    final TextStyle? meta = theme.textTheme.bodySmall
        ?.copyWith(color: theme.colorScheme.onSurfaceVariant);
    final double? best = insight.bestScore;
    final double? delta = insight.latestDelta;

    return MergeSemantics(
      child: Semantics(
        button: true,
        expanded: _expanded,
        child: InkWell(
          onTap: _toggle,
          child: Padding(
            padding: AppSpacing.cardPadding,
            child: Row(
              children: <Widget>[
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(
                        insight.shotType.label,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: theme.textTheme.titleMedium,
                      ),
                      const SizedBox(height: AppSpacing.xs),
                      Wrap(
                        spacing: AppSpacing.md,
                        runSpacing: AppSpacing.xs,
                        crossAxisAlignment: WrapCrossAlignment.center,
                        children: <Widget>[
                          Text(
                            '${insight.sessions} '
                            '${plural(insight.sessions, 'clip')}',
                            style: meta,
                          ),
                          if (insight.hasNoScoreAtAll)
                            Text(ShotTypeSection.notScoredLabel, style: meta)
                          else ...<Widget>[
                            Text.rich(
                              TextSpan(
                                text: 'Best ',
                                children: <InlineSpan>[
                                  TextSpan(
                                    // An em dash, never a zero.
                                    text: best == null
                                        ? '—'
                                        : best.round().toString(),
                                    style: TextStyle(
                                      color: theme.colorScheme.onSurface,
                                      fontWeight: FontWeight.w600,
                                      fontFeatures: kTabularFigures,
                                    ),
                                  ),
                                ],
                              ),
                              style: meta,
                            ),
                            TrendDirectionChip(
                              direction: directionOf(delta),
                              delta: delta,
                            ),
                          ],
                        ],
                      ),
                    ],
                  ),
                ),
                const SizedBox(width: AppSpacing.sm),
                AnimatedRotation(
                  turns: _expanded ? 0.5 : 0,
                  duration: duration,
                  curve: kEnterCurve,
                  child: Icon(
                    Icons.expand_more,
                    color: theme.colorScheme.onSurfaceVariant,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _body(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final AppPalette palette = context.palette;
    final TextStyle? note = theme.textTheme.labelSmall
        ?.copyWith(color: theme.colorScheme.onSurfaceVariant);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        // Clips and best are in the header, always visible; not repeated.
        InlineStats(stats: <InlineStat>[
          InlineStat(
            label: 'Average',
            value: insight.averageScore?.toStringAsFixed(1),
            valueColor: palette.scoreColor(insight.averageScore),
          ),
          InlineStat(
            label: 'Range',
            value: insight.consistency.spread?.round().toString(),
          ),
          InlineStat(
            label: 'Top speed',
            value: insight.topSpeed?.round().toString(),
            unit: 'mph',
          ),
        ]),
        const SizedBox(height: AppSpacing.lg),
        if (insight.hasNoScoreAtAll)
          Text(
            ShotTypeSection.noOverallScoreMessage,
            style: theme.textTheme.bodySmall
                ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
          )
        else ...<Widget>[
          // The full chart, not a sparkline: this is the one number per
          // shot type a player tracks, so it gets ticks, dates and a
          // crosshair — and every clip, across any scoring change.
          ScoreLineChart(
            title: 'Overall score',
            points: insight.scorePoints,
            dates: insight.sessionDates,
            breaksBefore: insight.versionBoundaries,
          ),
          // With one scored clip the panel above already says a trend needs
          // two; a spread caption repeating it said the same thing twice.
          if (_consistencyLine() case final String line) ...<Widget>[
            const SizedBox(height: AppSpacing.xs),
            Text(line, style: note),
          ],
        ],
        const SizedBox(height: AppSpacing.lg),
        ..._speed(context),
        ..._categoryScores(context),
        ..._coverage(context),
      ],
    );
  }

  /// Spread, in words. Null — silent — when there is only one score to
  /// spread.
  String? _consistencyLine() {
    final ConsistencySummary c = insight.consistency;
    final double? spread = c.spread;
    if (spread == null) return null;
    switch (c.band!) {
      case ConsistencyBand.tight:
        return 'Repeatable: ${spread.round()} points between your best and '
            'worst.';
      case ConsistencyBand.moderate:
        return 'Some variation: ${spread.round()} points between your best '
            'and worst.';
      case ConsistencyBand.wide:
        return 'Swinging widely: ${spread.round()} points between your best '
            'and worst. Repeatability is the problem here, not the average.';
    }
  }

  List<Widget> _speed(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    if (insight.speeds.isEmpty) {
      return <Widget>[
        Text(
          'No ball speed measured on this shot. Tap two court reference '
          'points when you record to measure it.',
          style: theme.textTheme.labelSmall
              ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
        ),
        const SizedBox(height: AppSpacing.lg),
      ];
    }
    return <Widget>[
      // A SEPARATE panel from the score, never a second axis on it: mph and a
      // 0-100 score share no scale and overlaying them would invent crossings.
      MiniTrend(
        title: 'Ball speed',
        points: insight.speedPoints,
        unit: 'mph',
      ),
      const SizedBox(height: AppSpacing.lg),
    ];
  }

  /// The category scores, once: the latest-vs-average bars when they can be
  /// drawn, else the per-category sparklines. See the class docs.
  List<Widget> _categoryScores(BuildContext context) {
    final List<Widget> bars = _comparison(context);
    return bars.isNotEmpty ? bars : _categories(context);
  }

  /// The newest clip against the average of this shot's earlier clips.
  ///
  /// Only when the newest clip's own analysis loaded: otherwise the last
  /// point of each trend is an older clip, and labelling it "latest" would
  /// pass one swing off as another.
  List<Widget> _comparison(BuildContext context) {
    if (!insight.newestDetailLoaded) return const <Widget>[];
    final List<CategoryComparison> rows =
        buildCategoryComparison(insight.categoryTrends);
    if (rows.isEmpty) return const <Widget>[];
    final ThemeData theme = Theme.of(context);
    return <Widget>[
      Text(
        'Latest clip vs your average',
        style: theme.textTheme.labelSmall?.copyWith(
          color: theme.colorScheme.onSurfaceVariant,
          fontWeight: FontWeight.w600,
        ),
      ),
      const SizedBox(height: AppSpacing.sm),
      ComparisonBars(
        rows: rows,
        earlierClips: insight.detailsInspected - 1,
      ),
    ];
  }

  List<Widget> _categories(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    if (insight.categoryTrends.isEmpty) {
      return <Widget>[
        Text(
          'Category breakdown not loaded for this shot.',
          style: theme.textTheme.labelSmall
              ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
        ),
      ];
    }

    return <Widget>[
      Text(
        'Categories · last ${insight.detailsInspected} '
        '${plural(insight.detailsInspected, 'clip')}',
        style: theme.textTheme.labelSmall?.copyWith(
          color: theme.colorScheme.onSurfaceVariant,
          fontWeight: FontWeight.w600,
        ),
      ),
      const SizedBox(height: AppSpacing.sm),
      // Small multiples: one-series panels on a shared form, rather than
      // five coloured lines in one frame. No categorical palette is needed and
      // none is invented. They take the DEFAULT panel height — a shorter one
      // here squashed a 12px marker into 22px of plot band and made the tap
      // target for point interrogation smaller than a fingertip.
      LayoutBuilder(
        builder: (BuildContext context, BoxConstraints constraints) {
          // 260, not 300: a 360dp phone leaves 296dp inside this card, and
          // the old cut sent the most common Android width to one column —
          // five full-width panels stacked, for no gain in legibility.
          final bool twoUp = constraints.maxWidth >= 260;
          final double width = twoUp
              ? (constraints.maxWidth - AppSpacing.lg) / 2
              : constraints.maxWidth;
          return Wrap(
            spacing: AppSpacing.lg,
            runSpacing: AppSpacing.md,
            children: <Widget>[
              for (final CategoryTrend trend in insight.categoryTrends)
                SizedBox(
                  width: width,
                  child: MiniTrend(
                    title: trend.displayName,
                    points: trend.points,
                  ),
                ),
            ],
          );
        },
      ),
    ];
  }

  List<Widget> _coverage(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final CoverageSummary? coverage = insight.latestCoverage;
    if (coverage == null || coverage.total <= 0) return const <Widget>[];

    return <Widget>[
      const SizedBox(height: AppSpacing.md),
      Text(
        coverage.hasGaps
            ? 'Latest clip measured ${coverage.available} of '
                '${coverage.total} ${plural(coverage.total, 'metric')}. Film '
                'side-on with your whole body in frame to measure more.'
            : coverage.total == 1
                ? 'Latest clip measured its 1 metric.'
                : 'Latest clip measured all ${coverage.total} metrics.',
        style: theme.textTheme.labelSmall
            ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
      ),
    ];
  }
}

/// The shot types the player has never recorded, together in one card.
///
/// Deliberately copy and nothing else. A zeroed chart or a greyed-out stat
/// row here would look like measured data at a glance, and it would be a lie:
/// there is no clip. One card rather than one per shot: five near-empty cards
/// stacked took more height than a recorded shot's whole stat row.
class ShotTypesNotRecordedCard extends StatelessWidget {
  const ShotTypesNotRecordedCard({super.key, required this.shotTypes});

  final List<ShotType> shotTypes;

  @override
  Widget build(BuildContext context) {
    if (shotTypes.isEmpty) return const SizedBox.shrink();
    final ThemeData theme = Theme.of(context);
    return AppCard(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.lg,
        vertical: AppSpacing.md,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            'NOT RECORDED YET',
            style: theme.textTheme.labelSmall?.copyWith(
              color: theme.colorScheme.onSurfaceVariant,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: AppSpacing.xs),
          // Separate runs, not one dot-joined string: a joined line wrapped
          // with a dangling separator at its end.
          Wrap(
            spacing: AppSpacing.lg,
            runSpacing: 2,
            children: <Widget>[
              for (final ShotType s in shotTypes)
                Text(
                  s.label,
                  style: theme.textTheme.bodyMedium
                      ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                ),
            ],
          ),
        ],
      ),
    );
  }
}
