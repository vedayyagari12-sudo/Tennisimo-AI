import 'package:flutter/material.dart';

import '../models/analysis_response.dart';
import '../theme/app_theme.dart';

/// The five weighted scoring categories as horizontal bars, as in the
/// redesign: name and score on one line, a green bar under it, and the
/// weakest measured category in orange — the biggest gain.
///
/// A category with a null score gets the words "Not measured" and NO bar at
/// all. A zero-length bar would be a drawn claim that the player scored
/// nothing in that category, which is not what the backend said.
class CategoryBars extends StatelessWidget {
  const CategoryBars({super.key, required this.categories});

  final List<CategoryScore> categories;

  /// The redesign's two bar colours.
  static const Color barGreen = Color(0xFF0B7A3B);
  static const Color barOrange = Color(0xFFC2620F);

  @override
  Widget build(BuildContext context) {
    if (categories.isEmpty) return const SizedBox.shrink();
    final List<double> measured = <double>[
      for (final CategoryScore c in categories)
        if (c.score != null) c.score!,
    ];
    // Only meaningful with two or more measured: one bar is not "weakest".
    final double? lowest = measured.length < 2
        ? null
        : measured.reduce((double a, double b) => a < b ? a : b);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        for (int i = 0; i < categories.length; i++) ...<Widget>[
          if (i > 0) const SizedBox(height: AppSpacing.md + 2),
          _CategoryBar(
            category: categories[i],
            weakest: lowest != null && categories[i].score == lowest,
          ),
        ],
      ],
    );
  }
}

class _CategoryBar extends StatelessWidget {
  const _CategoryBar({required this.category, required this.weakest});

  final CategoryScore category;
  final bool weakest;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final double? score = category.score;
    final Color barColour =
        weakest ? CategoryBars.barOrange : CategoryBars.barGreen;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        Row(
          crossAxisAlignment: CrossAxisAlignment.end,
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
              textAlign: TextAlign.end,
              style: score == null
                  ? theme.textTheme.bodySmall
                      ?.copyWith(color: theme.colorScheme.onSurfaceVariant)
                  : theme.textTheme.bodyMedium?.copyWith(
                      fontWeight: FontWeight.w700,
                      fontFeatures: kTabularFigures,
                    ),
            ),
          ],
        ),
        if (score != null) ...<Widget>[
          const SizedBox(height: 6),
          ClipRRect(
            borderRadius: BorderRadius.circular(999),
            child: Container(
              width: double.infinity,
              height: 8,
              color: theme.colorScheme.surfaceContainerHigh,
              child: FractionallySizedBox(
                alignment: Alignment.centerLeft,
                widthFactor: (score / 100).clamp(0.0, 1.0),
                child: Container(color: barColour),
              ),
            ),
          ),
        ],
      ],
    );
  }
}
