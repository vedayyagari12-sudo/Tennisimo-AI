import 'package:flutter/material.dart';

import '../models/analysis_response.dart';
import '../models/chart_data.dart';
import '../theme/app_theme.dart';
import 'split_row.dart';

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
          if (i > 0) const SizedBox(height: AppSpacing.md),
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

    // A category with no metric banded for this shot type is not "0 of 0
    // measured"; it has nothing to measure against.
    final String coverage = category.metricsTotal <= 0
        ? 'No reference ranges for this shot'
        : '${category.metricsAvailable} of ${category.metricsTotal} '
            '${plural(category.metricsTotal, 'metric')} measured';

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        // Name and coverage share a line, the numeral is pinned to the right
        // edge: one line per category instead of two, and every numeral in
        // the card on one vertical.
        SplitRow(
          gap: AppSpacing.md,
          crossAxisAlignment: CrossAxisAlignment.end,
          label: Wrap(
            spacing: AppSpacing.sm,
            runSpacing: 2,
            crossAxisAlignment: WrapCrossAlignment.end,
            children: <Widget>[
              Text(
                // `swing_path` -> `Swing path`, straight off the model.
                category.displayName,
                style: theme.textTheme.bodyMedium,
              ),
              Padding(
                // Nudges the small caption onto the name's baseline.
                padding: const EdgeInsets.only(bottom: 1),
                child: Text(
                  coverage,
                  style: theme.textTheme.labelSmall
                      ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                ),
              ),
            ],
          ),
          // "Not measured" is three times the width of a numeral; SplitRow
          // caps it at half the row, so at a 2.0x font scale it wraps rather
          // than walking off a 320dp card. Wraps, never ellipsises — half of
          // the word "measured" would read as a truncated number.
          trailing: Text(
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
        if (score != null) ...<Widget>[
          const SizedBox(height: AppSpacing.xs + 2),
          ClipRRect(
            borderRadius: BorderRadius.circular(999),
            child: Container(
              // The track is what makes a bar readable as "out of 100". It
              // never showed: inside this start-aligned Column the container
              // shrink-wrapped to its own fill, so the track was exactly as
              // long as the bar. A full width fixes that; the hairline token
              // (surfaceContainerHigh sat one step off the card) keeps it
              // visible in both flavors without competing with the fill.
              width: double.infinity,
              height: 6,
              color: theme.colorScheme.outlineVariant,
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
