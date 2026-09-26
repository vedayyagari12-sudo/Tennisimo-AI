import 'package:flutter/material.dart';

import '../models/dashboard_insights.dart';
import '../theme/app_theme.dart';

/// "Improving +6", "Slipping −4", "Holding steady", "Not enough history".
///
/// The arrow and the word carry the meaning; the colour only reinforces it.
/// That matters twice over: green-up / red-down collapses under red-green
/// deficiency, and the score ramp is a status palette that must not be
/// borrowed as series identity. An icon plus a word survives both.
class TrendDirectionChip extends StatelessWidget {
  const TrendDirectionChip({
    super.key,
    required this.direction,
    required this.delta,
    this.unit,
  });

  final TrendDirection direction;

  /// The signed change. Null is the only value allowed with
  /// [TrendDirection.unknown].
  final double? delta;

  /// Appended after the number, e.g. `mph`.
  final String? unit;

  /// The words, exposed so a test asserts the copy and not a pixel.
  static String labelFor(TrendDirection direction) {
    switch (direction) {
      case TrendDirection.improving:
        return 'Improving';
      case TrendDirection.declining:
        return 'Slipping';
      case TrendDirection.steady:
        return 'Holding steady';
      case TrendDirection.unknown:
        return 'Not enough history';
    }
  }

  IconData get _icon {
    switch (direction) {
      case TrendDirection.improving:
        return Icons.trending_up;
      case TrendDirection.declining:
        return Icons.trending_down;
      case TrendDirection.steady:
        return Icons.trending_flat;
      case TrendDirection.unknown:
        return Icons.help_outline;
    }
  }

  Color _colour(BuildContext context) {
    final ColorScheme scheme = Theme.of(context).colorScheme;
    switch (direction) {
      case TrendDirection.improving:
        return scheme.primary;
      case TrendDirection.declining:
        return scheme.error;
      case TrendDirection.steady:
      case TrendDirection.unknown:
        return scheme.onSurfaceVariant;
    }
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final Color colour = _colour(context);
    final double? d = delta;
    // A magnitude is only shown when the direction has one. "Holding steady
    // +0.3" invites a reader to act on noise the band already rejected.
    final bool showsNumber = d != null &&
        (direction == TrendDirection.improving ||
            direction == TrendDirection.declining);

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: colour.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Icon(_icon, size: 14, color: colour),
          const SizedBox(width: 4),
          Flexible(
            child: Text(
              showsNumber
                  ? '${labelFor(direction)} '
                      '${d > 0 ? '+' : '−'}${d.abs().toStringAsFixed(1)}'
                      '${unit == null ? '' : ' $unit'}'
                  : labelFor(direction),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: theme.textTheme.labelSmall?.copyWith(
                color: colour,
                fontWeight: FontWeight.w600,
                fontFeatures: kTabularFigures,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
