import 'package:flutter/material.dart';

import '../models/swing_advice.dart';
import '../theme/app_theme.dart';

/// A swing's plain coaching bullets, each with its number set small beside
/// it for a player who wants it.
///
/// The sentence leads; the number follows in a quieter style. A beginner
/// reads "bend your knees a little more", not "171°". Marks are in ink tokens
/// only: whether a bullet is praise is carried by its icon shape AND its
/// words, never by colour.
class SwingAdviceList extends StatelessWidget {
  const SwingAdviceList({super.key, required this.bullets});

  final List<AdviceBullet> bullets;

  @override
  Widget build(BuildContext context) {
    if (bullets.isEmpty) return const SizedBox.shrink();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        for (int i = 0; i < bullets.length; i++) ...<Widget>[
          if (i > 0) const SizedBox(height: AppSpacing.md),
          _AdviceRow(bullet: bullets[i]),
        ],
      ],
    );
  }
}

class _AdviceRow extends StatelessWidget {
  const _AdviceRow({required this.bullet});

  final AdviceBullet bullet;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final Color muted = theme.colorScheme.onSurfaceVariant;
    final String? ideal = bullet.number.idealText;

    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Padding(
          // Centres the mark on the sentence's first line.
          padding: const EdgeInsets.only(top: 2),
          child: Icon(
            bullet.isPraise ? Icons.check : Icons.arrow_forward,
            size: 16,
            color: bullet.isPraise ? muted : theme.colorScheme.onSurface,
          ),
        ),
        const SizedBox(width: AppSpacing.sm),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Text(bullet.text, style: theme.textTheme.bodyMedium),
              const SizedBox(height: 2),
              Text(
                <String>[
                  '${bullet.number.label} ${bullet.number.valueText}',
                  if (ideal != null) 'ideal $ideal',
                ].join(' · '),
                style: theme.textTheme.labelSmall?.copyWith(
                  color: muted,
                  fontFeatures: kTabularFigures,
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}
