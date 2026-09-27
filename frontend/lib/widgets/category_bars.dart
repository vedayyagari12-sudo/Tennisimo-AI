import 'package:flutter/material.dart';

import '../models/analysis_response.dart';
import '../theme/app_theme.dart';

/// The five weighted scoring categories as horizontal bars.
///
/// A category with a null score gets the words "Not measured" and NO bar at
/// all. A zero-length bar would be a drawn claim that the player scored
/// nothing in that category, which is not what the backend said.
class CategoryBars extends StatelessWidget {
  const CategoryBars({super.key, required this.categories});

  final List<CategoryScore> categories;

  @override
  Widget build(BuildContext context) {
    if (categories.isEmpty) return const SizedBox.shrink();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        for (int i = 0; i < categories.length; i++) ...<Widget>[
          if (i > 0) const SizedBox(height: AppSpacing.lg),
          _CategoryBar(category: categories[i]),
        ],
      ],
    );
  }
}

class _CategoryBar extends StatelessWidget {
  const _CategoryBar({required this.category});

  final CategoryScore category;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final AppPalette palette = context.palette;
    final double? score = category.score;
    // Two tiers, two roles: the numeral is a small mark on the card, the bar
    // is a filled area. Using one step for both is what looks wrong first.
    final Color numeralColour = palette.scoreColor(score);
    final Color barColour = palette.scoreFillColor(score);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Row(
          children: <Widget>[
            Expanded(
              child: Text(
                // `swing_path` -> `Swing path`, straight off the model.
                category.displayName,
                style: theme.textTheme.bodyMedium,
              ),
            ),
            const SizedBox(width: AppSpacing.md),
            // Flexible, not a bare Text: "Not measured" is three times the
            // width of a numeral and at a 2.0x font scale it walked straight
            // off a 320dp row. It wraps rather than ellipsises — half of the
            // word "measured" would read as a truncated number.
            Flexible(
              child: Text(
                score == null ? 'Not measured' : score.round().toString(),
                textAlign: TextAlign.end,
                style: score == null
                    ? theme.textTheme.bodySmall
                        ?.copyWith(color: theme.colorScheme.onSurfaceVariant)
                    : theme.textTheme.titleMedium?.copyWith(
                        color: numeralColour,
                        fontFeatures: kTabularFigures,
                      ),
              ),
            ),
          ],
        ),
        if (score != null) ...<Widget>[
          const SizedBox(height: AppSpacing.sm),
          ClipRRect(
            borderRadius: BorderRadius.circular(999),
            child: Container(
              height: 6,
              color: theme.colorScheme.surfaceContainerHigh,
              child: FractionallySizedBox(
                alignment: Alignment.centerLeft,
                widthFactor: (score / 100).clamp(0.0, 1.0),
                child: Container(color: barColour),
              ),
            ),
          ),
        ],
        const SizedBox(height: AppSpacing.xs),
        Text(
          '${category.metricsAvailable} of ${category.metricsTotal} '
          'metrics measured',
          style: theme.textTheme.labelSmall
              ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
        ),
      ],
    );
  }
}
