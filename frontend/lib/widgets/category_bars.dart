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
    final double? score = category.score;
    final Color colour = scoreColor(score);

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
            Text(
              score == null ? 'Not measured' : score.round().toString(),
              style: score == null
                  ? theme.textTheme.bodySmall
                      ?.copyWith(color: theme.colorScheme.onSurfaceVariant)
                  : theme.textTheme.titleMedium?.copyWith(
                      color: colour,
                      fontFeatures: kTabularFigures,
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
                child: Container(color: colour),
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
