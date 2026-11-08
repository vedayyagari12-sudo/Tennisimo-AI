import 'package:flutter/material.dart';

import '../models/court.dart';
import '../theme/app_theme.dart';

/// "Score (estimate)" until the server confirms every point was the user's.
String scoreTitle(ScoreSummary score) => score.isConfirmed ? 'Score' : 'Score (estimate)';

String reviewLine(int count) => switch (count) {
      0 => 'No points need review',
      1 => '1 point needs your review',
      _ => '$count points need your review',
    };

/// The score as the backend replayed it. This widget never computes tennis
/// logic: a correction goes out through [onCorrect] and the backend sends
/// back the new state.
class ScorePanel extends StatelessWidget {
  const ScorePanel({
    super.key,
    required this.score,
    this.onCorrect,
    this.playerALabel = 'You',
    this.playerBLabel = 'Opponent',
  });

  final ScoreSummary score;
  final void Function(String eventId, PlayerId? winner)? onCorrect;
  final String playerALabel;
  final String playerBLabel;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final AppPalette palette = context.palette;
    final ScoreState s = score.current;
    final (String, String)? points = s.pointLabels;

    TableRow row(String name, List<int> games, String? point) => TableRow(
          children: <Widget>[
            Padding(
              padding: const EdgeInsets.symmetric(vertical: AppSpacing.xs),
              child: Text(name, style: theme.textTheme.bodyLarge),
            ),
            for (final int g in games)
              Text('$g', textAlign: TextAlign.center, style: theme.textTheme.bodyLarge),
            Text(point ?? '', textAlign: TextAlign.center, style: theme.textTheme.titleSmall),
          ],
        );

    final List<ScoreTimelineEntry> review =
        score.timeline.where((ScoreTimelineEntry e) => e.needsReview).toList();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        Text(scoreTitle(score), style: theme.textTheme.titleMedium),
        const SizedBox(height: AppSpacing.sm),
        Table(
          defaultVerticalAlignment: TableCellVerticalAlignment.middle,
          columnWidths: const <int, TableColumnWidth>{0: FlexColumnWidth(3)},
          children: <TableRow>[
            row(playerALabel, <int>[for (final (int, int) set in s.sets) set.$1, s.games.$1], points?.$1),
            row(playerBLabel, <int>[for (final (int, int) set in s.sets) set.$2, s.games.$2], points?.$2),
          ],
        ),
        if (points == null && s.pointsDisplay.isNotEmpty) ...<Widget>[
          const SizedBox(height: AppSpacing.xs),
          Text('Points: ${s.pointsDisplay}', style: theme.textTheme.bodyMedium),
        ],
        const SizedBox(height: AppSpacing.sm),
        Text(reviewLine(score.needsReviewCount),
            style: theme.textTheme.bodyMedium?.copyWith(color: palette.onSurfaceVariant)),
        for (final ScoreTimelineEntry e in review) ...<Widget>[
          const Divider(height: AppSpacing.xl),
          Text(e.basis?.label ?? 'Point', style: theme.textTheme.titleSmall),
          const SizedBox(height: AppSpacing.xs),
          Text('Who won this point?', style: theme.textTheme.bodyMedium),
          const SizedBox(height: AppSpacing.xs),
          Wrap(
            spacing: AppSpacing.sm,
            children: <Widget>[
              OutlinedButton(
                onPressed: onCorrect == null ? null : () => onCorrect!(e.eventId, PlayerId.playerA),
                child: Text(playerALabel),
              ),
              OutlinedButton(
                onPressed: onCorrect == null ? null : () => onCorrect!(e.eventId, PlayerId.playerB),
                child: Text(playerBLabel),
              ),
              TextButton(
                onPressed: onCorrect == null ? null : () => onCorrect!(e.eventId, null),
                child: const Text('Replay'),
              ),
            ],
          ),
        ],
      ],
    );
  }
}
