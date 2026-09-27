import 'package:flutter/material.dart';

import '../models/dashboard_insights.dart';
import '../models/enums.dart';
import '../theme/app_theme.dart';
import 'app_card.dart';
import 'inline_stats.dart';
import 'mini_trend.dart';
import 'split_row.dart';

/// One shot type's own block: its counts, its records, its score trend, its
/// ball speed and its category small multiples.
///
/// Every number here is drawn from THIS shot type alone. Nothing is averaged
/// with another shot — the backend scores each shot type against a different
/// band table, so a combined mean would describe no swing the player played.
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
class ShotTypeSection extends StatelessWidget {
  const ShotTypeSection({super.key, required this.insight});

  final ShotTypeInsight insight;

  /// Copy for a shot type with sessions but no overall score on any of them.
  static const String noOverallScoreMessage =
      'No overall score was produced for these clips. That is not a score of '
      'zero — the scorecard needs several categories measurable at once. What '
      'was measured is below.';

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final AppPalette palette = context.palette;

    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          _title(theme),
          const SizedBox(height: AppSpacing.md),
          InlineStats(stats: <InlineStat>[
            InlineStat(label: 'Clips', value: insight.sessions.toString()),
            InlineStat(
              label: 'Best',
              value: insight.bestScore?.round().toString(),
              valueColor: palette.scoreColor(insight.bestScore),
            ),
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
              noOverallScoreMessage,
              style: theme.textTheme.bodySmall
                  ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
            )
          else ...<Widget>[
            MiniTrend(
              title: 'Overall score',
              points: insight.scorePoints,
            ),
            // With one scored clip the panel above already says a trend needs
            // two; a spread caption repeating it said the same thing twice.
            if (_consistencyLine() case final String line) ...<Widget>[
              const SizedBox(height: AppSpacing.xs),
              Text(
                line,
                style: theme.textTheme.labelSmall
                    ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
              ),
            ],
          ],
          const SizedBox(height: AppSpacing.lg),
          ..._speed(context),
          ..._categories(context),
          ..._coverage(context),
        ],
      ),
    );
  }

  Widget _title(ThemeData theme) {
    return SplitRow(
      label: Text(
        insight.shotType.label,
        maxLines: 2,
        overflow: TextOverflow.ellipsis,
        style: theme.textTheme.titleMedium,
      ),
      trailing: Text(
        '${insight.sessions} ${insight.sessions == 1 ? 'clip' : 'clips'}',
        textAlign: TextAlign.end,
        style: theme.textTheme.labelSmall
            ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
      ),
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
        '${insight.detailsInspected == 1 ? 'clip' : 'clips'}',
        style: theme.textTheme.labelSmall?.copyWith(
          color: theme.colorScheme.onSurfaceVariant,
          fontWeight: FontWeight.w600,
        ),
      ),
      const SizedBox(height: AppSpacing.sm),
      // Small multiples: five one-series panels on a shared form, rather than
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
                '${coverage.total} metrics. Film side-on with your whole body '
                'in frame to measure more.'
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
