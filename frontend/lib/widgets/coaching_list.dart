import 'package:flutter/material.dart';

import '../models/swing_coaching.dart';

/// Coaching items: the sentence leads, then the numbers that back it, then
/// a drill when there is one. Praise vs fix is carried by icon AND words,
/// never by colour alone.
class CoachingList extends StatelessWidget {
  const CoachingList({super.key, required this.items});

  final List<CoachingItem> items;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        for (int i = 0; i < items.length; i++) ...<Widget>[
          if (i > 0) const Divider(height: 24),
          _CoachingRow(item: items[i]),
        ],
      ],
    );
  }
}

class _CoachingRow extends StatelessWidget {
  const _CoachingRow({required this.item});

  final CoachingItem item;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final Color muted = theme.colorScheme.onSurfaceVariant;
    final String? target = item.targetText;
    final String? drill = item.drill;
    final bool fix = item.isOffTarget;

    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Padding(
          padding: const EdgeInsets.only(top: 2),
          child: Icon(
            fix ? Icons.arrow_forward : Icons.check,
            size: 18,
            color: fix ? theme.colorScheme.onSurface : muted,
          ),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Text(item.advice ?? '', style: theme.textTheme.bodyMedium),
              const SizedBox(height: 4),
              Text(
                <String>[
                  '${item.label}: ${item.valueText}',
                  if (target != null) 'target $target',
                  if (fix && item.gapText != null) item.gapText!,
                ].join(' · '),
                style: theme.textTheme.labelSmall?.copyWith(color: muted),
              ),
              if (fix && drill != null) ...<Widget>[
                const SizedBox(height: 8),
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(10),
                  decoration: BoxDecoration(
                    color: theme.colorScheme.surfaceContainerHighest,
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Icon(Icons.sports_tennis, size: 16, color: muted),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text.rich(
                          TextSpan(children: <InlineSpan>[
                            const TextSpan(
                              text: 'Try this: ',
                              style: TextStyle(fontWeight: FontWeight.w600),
                            ),
                            TextSpan(text: drill),
                          ]),
                          style: theme.textTheme.bodySmall,
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ],
          ),
        ),
      ],
    );
  }
}
