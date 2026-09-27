import 'package:flutter/material.dart';

import '../models/dashboard_insights.dart';
import '../theme/app_theme.dart';
import 'app_card.dart';
import 'mini_trend.dart';
import 'split_row.dart';
import 'trend_direction_chip.dart';

/// The single most useful thing on the dashboard: the weakest category of the
/// shot the player last hit, and whether it is moving.
///
/// It is a hero block, not a chart, because the headline is ONE value — the
/// lowest category score — and a chart of one value is decoration. The
/// sparkline underneath answers the second half of the question ("is it
/// getting better?") and is there only because that question needs a series.
///
/// It is scoped to one shot type on purpose. "Your weakest category" across
/// forehands and serves together would be comparing two different band tables
/// and would send the player to practise the wrong thing.
class FocusCard extends StatelessWidget {
  const FocusCard({super.key, required this.insight});

  /// The shot type the newest session belongs to. Null before anything has
  /// been recorded.
  final ShotTypeInsight? insight;

  /// Copy for "we have the sessions but nothing in them was measurable".
  static const String nothingMeasuredMessage =
      'No category could be measured in these clips yet. Film side-on, with '
      'your whole body in frame, and the breakdown will fill in.';

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final ShotTypeInsight? shot = insight;
    final CategoryTrend? focus = shot?.focus;

    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          SplitRow(
            label: Text(
              'WHAT TO WORK ON',
              style: theme.textTheme.labelSmall?.copyWith(
                color: theme.colorScheme.onSurfaceVariant,
                fontWeight: FontWeight.w600,
              ),
            ),
            trailing: shot == null
                ? const SizedBox.shrink()
                : Text(
                    shot.shotType.label,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    textAlign: TextAlign.end,
                    style: theme.textTheme.labelSmall
                        ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                  ),
          ),
          const SizedBox(height: AppSpacing.sm),
          if (shot == null || focus == null)
            Text(
              nothingMeasuredMessage,
              style: theme.textTheme.bodySmall
                  ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
            )
          else
            ..._focus(context, shot, focus),
        ],
      ),
    );
  }

  List<Widget> _focus(
    BuildContext context,
    ShotTypeInsight shot,
    CategoryTrend focus,
  ) {
    final ThemeData theme = Theme.of(context);
    final AppPalette palette = context.palette;
    final double score = focus.latest!;
    final int measuredCategories = shot.categoryTrends
        .where((CategoryTrend t) => t.latest != null)
        .length;

    return <Widget>[
      Row(
        crossAxisAlignment: CrossAxisAlignment.baseline,
        textBaseline: TextBaseline.alphabetic,
        children: <Widget>[
          Expanded(
            child: Text(
              focus.displayName,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: theme.textTheme.headlineSmall,
            ),
          ),
          const SizedBox(width: AppSpacing.sm),
          Text(
            score.round().toString(),
            // The numeral takes the score ramp because the ramp IS this
            // value's status, not a series identity borrowed for decoration.
            style: theme.textTheme.headlineSmall?.copyWith(
              color: palette.scoreColor(score),
              fontFeatures: kTabularFigures,
            ),
          ),
        ],
      ),
      const SizedBox(height: AppSpacing.sm),
      Align(
        alignment: Alignment.centerLeft,
        child: TrendDirectionChip(
          direction: focus.direction,
          delta: focus.delta,
        ),
      ),
      const SizedBox(height: AppSpacing.md),
      MiniTrend(
        title: '${focus.displayName} over your last '
            '${shot.detailsInspected} ${shot.detailsInspected == 1 ? 'clip' : 'clips'}',
        points: focus.points,
      ),
      const SizedBox(height: AppSpacing.sm),
      Text(
        measuredCategories == 1
            ? 'The only category measured on this shot so far.'
            : 'Lowest of the $measuredCategories categories measured on this '
                'shot.',
        style: theme.textTheme.bodySmall
            ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
      ),
    ];
  }
}
