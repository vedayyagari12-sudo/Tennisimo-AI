import 'package:flutter/material.dart';

import '../models/court.dart';
import '../theme/app_theme.dart';

/// "3:28" from seconds into the video.
String clockText(double seconds) {
  final int whole = seconds.isFinite && seconds > 0 ? seconds.floor() : 0;
  return '${whole ~/ 60}:${(whole % 60).toString().padLeft(2, '0')}';
}

/// "5 hits", or "5 hits (could be 6)" / "5 hits (could be 4 to 7)" when the
/// count is a range. The range is always shown: a lost track is real doubt.
String rallyLengthText(Rally rally) {
  final String base = '${rally.lengthHits} ${rally.lengthHits == 1 ? 'hit' : 'hits'}';
  if (!rally.isRange) return base;
  final List<int> others = <int>[
    for (int n = rally.lengthMin; n <= rally.lengthMax; n++)
      if (n != rally.lengthHits) n,
  ];
  final String could = others.length == 1
      ? '${others.single}'
      : '${rally.lengthMin} to ${rally.lengthMax}';
  return '$base (could be $could)';
}

String rallyHeaderText(List<Rally> rallies) {
  if (rallies.isEmpty) return 'Rallies';
  final List<int> lengths = rallies.map((Rally r) => r.lengthHits).toList()..sort();
  // Lower median, as the backend's median_low: never a half-hit.
  final int median = lengths[(lengths.length - 1) ~/ 2];
  return 'Rallies   Longest ${lengths.last}   Median $median';
}

/// Rallies in time order: a strip of relative lengths, then one row each.
class RallyTimeline extends StatelessWidget {
  const RallyTimeline({super.key, required this.rallies, this.onSelect});

  final List<Rally> rallies;
  final ValueChanged<Rally>? onSelect;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final AppPalette palette = context.palette;
    if (rallies.isEmpty) {
      return Text('No rallies found yet.', style: theme.textTheme.bodyMedium);
    }
    final int longest = rallies.map((Rally r) => r.lengthMax).reduce((int a, int b) => a > b ? a : b);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        Text(rallyHeaderText(rallies), style: theme.textTheme.titleMedium),
        const SizedBox(height: AppSpacing.sm),
        // Shots per rally, as in the redesign: one bar per rally in order,
        // taller for more shots, the longest in the ball lime.
        Semantics(
          label: 'Shots per rally, ${rallies.length} '
              '${rallies.length == 1 ? 'rally' : 'rallies'}, longest $longest',
          child: ExcludeSemantics(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: <Widget>[
                Text('SHOTS PER RALLY',
                    style: theme.textTheme.labelSmall?.copyWith(
                        color: theme.colorScheme.onSurfaceVariant, fontWeight: FontWeight.w600)),
                const SizedBox(height: AppSpacing.sm),
                SizedBox(
                  height: 90,
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: <Widget>[
                      for (final Rally r in rallies)
                        Expanded(
                          child: Container(
                            margin: const EdgeInsets.symmetric(horizontal: 2),
                            height: 90 * (r.lengthHits / longest).clamp(0.08, 1.0),
                            decoration: BoxDecoration(
                              color: r.lengthMax == longest
                                  ? palette.ballAccent
                                  : palette.primaryFill,
                              borderRadius: const BorderRadius.vertical(top: Radius.circular(4)),
                            ),
                          ),
                        ),
                    ],
                  ),
                ),
                const SizedBox(height: AppSpacing.xs),
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: <Widget>[
                    Text('Start', style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
                    Text('End of video', style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
                  ],
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: AppSpacing.sm),
        for (final Rally r in rallies)
          ListTile(
            key: ValueKey<String>('rally-${r.rallyId}'),
            contentPadding: EdgeInsets.zero,
            leading: Text(clockText(r.startS), style: theme.textTheme.labelLarge),
            title: Text(rallyLengthText(r)),
            subtitle: Text('Ended: ${r.endReason.label}'),
            trailing: onSelect == null ? null : const Icon(Icons.chevron_right),
            onTap: onSelect == null ? null : () => onSelect!(r),
          ),
      ],
    );
  }
}
