import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

/// One label/value pair in an [InlineStats] row.
///
/// A null [value] is an em dash. Callers pass null — never `'0'` — when the
/// statistic could not be derived.
@immutable
class InlineStat {
  const InlineStat({
    required this.label,
    required this.value,
    this.unit,
    this.valueColor,
  });

  final String label;

  /// Already formatted. Null renders as "—".
  final String? value;
  final String? unit;

  /// Overrides the numeral colour, for the score ramp only.
  final Color? valueColor;
}

/// A dense row of small statistics.
///
/// Deliberately not a card per number: that is the reason the old
/// dashboard felt empty. These pack four to a phone width and wrap instead of
/// overflowing, so a long label costs a line rather than a yellow-and-black
/// stripe.
class InlineStats extends StatelessWidget {
  const InlineStats({super.key, required this.stats});

  final List<InlineStat> stats;

  @override
  Widget build(BuildContext context) {
    if (stats.isEmpty) return const SizedBox.shrink();
    final ThemeData theme = Theme.of(context);

    return Wrap(
      spacing: AppSpacing.lg,
      runSpacing: AppSpacing.md,
      children: <Widget>[
        for (final InlineStat stat in stats)
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              Text(
                stat.label.toUpperCase(),
                style: theme.textTheme.labelSmall?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                  fontWeight: FontWeight.w600,
                ),
              ),
              const SizedBox(height: 2),
              Row(
                crossAxisAlignment: CrossAxisAlignment.baseline,
                textBaseline: TextBaseline.alphabetic,
                mainAxisSize: MainAxisSize.min,
                children: <Widget>[
                  Text(
                    stat.value ?? '—',
                    style: theme.textTheme.titleMedium?.copyWith(
                      fontFeatures: kTabularFigures,
                      color: stat.value == null
                          ? theme.colorScheme.onSurfaceVariant
                          : (stat.valueColor ?? theme.colorScheme.onSurface),
                    ),
                  ),
                  // A unit is dropped with its value: "— mph" would claim a
                  // speed of nothing.
                  if (stat.value != null && stat.unit != null) ...<Widget>[
                    const SizedBox(width: 3),
                    Text(
                      stat.unit!,
                      style: theme.textTheme.labelSmall
                          ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                    ),
                  ],
                ],
              ),
            ],
          ),
      ],
    );
  }
}
