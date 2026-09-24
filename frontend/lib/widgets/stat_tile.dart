import 'package:flutter/material.dart';

import '../theme/app_theme.dart';
import 'app_card.dart';

/// A compact labelled statistic: uppercase label, big tabular value, optional
/// unit suffix and delta chip.
///
/// A null [value] renders an em dash. Callers pass null — never "0" — when the
/// statistic could not be derived, e.g. no session has ever carried a ball
/// speed.
class StatTile extends StatelessWidget {
  const StatTile({
    super.key,
    required this.label,
    required this.value,
    this.unit,
    this.delta,
    this.valueColor,
    this.accent,
  });

  final String label;

  /// Already formatted. Null means "not available" and renders as "—".
  final String? value;

  final String? unit;

  /// Small +/- chip beside the value.
  final double? delta;

  /// Overrides the numeral colour, e.g. the score ramp for a best score.
  final Color? valueColor;

  /// Colour of the label. Used to mark the one chartreuse ball-speed tile.
  final Color? accent;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final String? shown = value;
    final bool available = shown != null;
    final double? d = delta;

    return AppCard(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.md,
        vertical: AppSpacing.md,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Text(
            label.toUpperCase(),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: theme.textTheme.labelSmall?.copyWith(
              color: accent ?? theme.colorScheme.onSurfaceVariant,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: AppSpacing.sm),
          Row(
            crossAxisAlignment: CrossAxisAlignment.baseline,
            textBaseline: TextBaseline.alphabetic,
            children: <Widget>[
              Flexible(
                child: Text(
                  shown ?? '—',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.headlineSmall?.copyWith(
                    fontFeatures: kTabularFigures,
                    color: available
                        ? (valueColor ?? theme.colorScheme.onSurface)
                        : theme.colorScheme.onSurfaceVariant,
                  ),
                ),
              ),
              if (available && unit != null) ...<Widget>[
                const SizedBox(width: 4),
                Text(
                  unit!,
                  style: theme.textTheme.labelSmall
                      ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                ),
              ],
            ],
          ),
          if (available && d != null) ...<Widget>[
            const SizedBox(height: AppSpacing.sm),
            _DeltaChip(delta: d),
          ],
        ],
      ),
    );
  }
}

class _DeltaChip extends StatelessWidget {
  const _DeltaChip({required this.delta});

  final double delta;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final bool up = delta >= 0;
    final Color colour =
        up ? theme.colorScheme.primary : theme.colorScheme.error;

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: colour.withValues(alpha: 0.14),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        '${up ? '+' : '−'}${delta.abs().toStringAsFixed(1)}',
        style: theme.textTheme.labelSmall?.copyWith(
          color: colour,
          fontWeight: FontWeight.w600,
          fontFeatures: kTabularFigures,
        ),
      ),
    );
  }
}
