import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

/// How a legend key mirrors its mark.
enum LegendMark {
  /// A small filled square, for bars and slices.
  box,

  /// A short stroke with a dot, for lines and radar outlines.
  line,
}

/// One entry of a [ChartLegend].
@immutable
class LegendEntry {
  const LegendEntry({
    required this.color,
    required this.label,
    this.mark = LegendMark.box,
  });

  final Color color;
  final String label;
  final LegendMark mark;
}

/// The identity channel of a multi-series chart.
///
/// Present whenever a chart has two or more series, so identity never rests
/// on colour-matching alone. The LABEL wears an ink token; only the key beside
/// it carries the series colour, because a series hue is not calibrated as
/// text and some slots would fail AA as type.
class ChartLegend extends StatelessWidget {
  const ChartLegend({super.key, required this.entries});

  final List<LegendEntry> entries;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Wrap(
      spacing: AppSpacing.lg,
      runSpacing: AppSpacing.xs,
      children: <Widget>[
        for (final LegendEntry e in entries)
          Row(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              LegendKey(color: e.color, mark: e.mark),
              const SizedBox(width: 6),
              Flexible(
                child: Text(
                  e.label,
                  style: theme.textTheme.labelMedium?.copyWith(
                    color: theme.colorScheme.onSurfaceVariant,
                  ),
                ),
              ),
            ],
          ),
      ],
    );
  }
}

/// The coloured key alone: a 10px square, or a 16px stroke with a dot.
class LegendKey extends StatelessWidget {
  const LegendKey({super.key, required this.color, this.mark = LegendMark.box});

  final Color color;
  final LegendMark mark;

  @override
  Widget build(BuildContext context) {
    switch (mark) {
      case LegendMark.box:
        return Container(
          width: 10,
          height: 10,
          decoration: BoxDecoration(
            color: color,
            borderRadius: BorderRadius.circular(2),
          ),
        );
      case LegendMark.line:
        return SizedBox(
          width: 16,
          height: 10,
          child: Stack(
            alignment: Alignment.center,
            children: <Widget>[
              Container(height: 2, color: color),
              Container(
                width: 8,
                height: 8,
                decoration: BoxDecoration(color: color, shape: BoxShape.circle),
              ),
            ],
          ),
        );
    }
  }
}
