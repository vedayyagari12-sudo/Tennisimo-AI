import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../models/chart_data.dart';
import '../theme/app_theme.dart';
import '../theme/motion.dart';
import 'chart_legend.dart';

/// Grouped horizontal bars: the newest clip against the player's average of
/// the earlier clips, per category, for ONE shot type.
///
/// Horizontal because category names are long and a phone is narrow: the
/// name gets its own line and never has to be squeezed under a column.
///
/// * Both bars in a group grow from one zero baseline on a fixed 0-100 scale,
///   10px thick, a 2px gap between them, rounded only at the data end.
/// * A missing value is a GAP with words in it — "not measured", "no earlier
///   score" — never a zero-length bar that would look like a measurement.
/// * Slot 0 is the newest clip and slot 1 the comparison, as on every chart;
///   values and names are ink, beside the bars.
///
/// Tapping a group shows its exact numbers and the difference.
class ComparisonBars extends StatefulWidget {
  const ComparisonBars({
    super.key,
    required this.rows,
    required this.earlierClips,
  });

  final List<CategoryComparison> rows;

  /// How many earlier clips the averages draw from, for the legend.
  final int earlierClips;

  static const String currentLabel = 'Latest clip';
  static String averageLabel(int earlier) =>
      'Your average, $earlier earlier ${plural(earlier, 'clip')}';
  static const String notMeasured = 'not measured';
  static const String noEarlier = 'no earlier score';

  @override
  State<ComparisonBars> createState() => _ComparisonBarsState();
}

class _ComparisonBarsState extends State<ComparisonBars> {
  int? _selected;

  @override
  void didUpdateWidget(ComparisonBars oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.rows.length != widget.rows.length) _selected = null;
  }

  @override
  Widget build(BuildContext context) {
    if (widget.rows.isEmpty) return const SizedBox.shrink();
    final ThemeData theme = Theme.of(context);
    final AppPalette palette = context.palette;
    final Color current = palette.chartSeries[0];
    final Color average = palette.chartSeries[1];

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        ChartLegend(
          entries: <LegendEntry>[
            LegendEntry(color: current, label: ComparisonBars.currentLabel),
            LegendEntry(
              color: average,
              label: ComparisonBars.averageLabel(widget.earlierClips),
            ),
          ],
        ),
        const SizedBox(height: AppSpacing.sm),
        TweenAnimationBuilder<double>(
          tween: Tween<double>(begin: 0, end: 1),
          duration: motionDuration(context, const Duration(milliseconds: 600)),
          curve: kEnterCurve,
          builder: (BuildContext context, double t, _) => Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              for (int i = 0; i < widget.rows.length; i++)
                _Group(
                  row: widget.rows[i],
                  currentColor: current,
                  averageColor: average,
                  baseline: palette.outlineVariant,
                  selected: _selected == i,
                  progress: t,
                  onTap: () =>
                      setState(() => _selected = _selected == i ? null : i),
                ),
            ],
          ),
        ),
        if (_readout() case final String line) ...<Widget>[
          const SizedBox(height: AppSpacing.xs),
          Text(
            line,
            style: theme.textTheme.bodySmall?.copyWith(
              color: theme.colorScheme.onSurface,
            ),
          ),
        ],
      ],
    );
  }

  String? _readout() {
    final int? i = _selected;
    if (i == null || i < 0 || i >= widget.rows.length) return null;
    final CategoryComparison r = widget.rows[i];
    final String now = r.current == null
        ? 'latest clip not measured'
        : 'latest clip ${r.current!.round()}';
    final String avg = r.average == null
        ? 'no earlier score to compare'
        : 'your average ${r.average!.toStringAsFixed(1)} over ${r.averageOf} '
              '${plural(r.averageOf, 'clip')}';
    final double? d = r.delta;
    final String diff = d == null
        ? ''
        : ' (${d >= 0 ? '+' : '−'}${d.abs().toStringAsFixed(1)})';
    // The individual earlier scores, so the average is never the only trace
    // of the clips it summarises.
    final String each = r.earlier.isEmpty
        ? ''
        : '. Earlier clips, oldest first: ${r.earlier.map(_score).join(', ')}';
    return '${r.displayName}: $now, $avg$diff$each';
  }

  static String _score(double? v) =>
      v == null ? ComparisonBars.notMeasured : v.round().toString();
}

class _Group extends StatelessWidget {
  const _Group({
    required this.row,
    required this.currentColor,
    required this.averageColor,
    required this.baseline,
    required this.selected,
    required this.progress,
    required this.onTap,
  });

  final CategoryComparison row;
  final Color currentColor;
  final Color averageColor;
  final Color baseline;
  final bool selected;
  final double progress;
  final VoidCallback onTap;

  @override
  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final Widget name = Text(
      row.displayName,
      style: theme.textTheme.bodySmall?.copyWith(
        color: theme.colorScheme.onSurface,
        fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
      ),
    );
    // The shared zero baseline both bars grow from.
    final Widget bars = DecoratedBox(
      decoration: BoxDecoration(
        border: Border(left: BorderSide(color: baseline)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          _Bar(
            value: row.current,
            color: currentColor,
            emptyText: ComparisonBars.notMeasured,
            strong: true,
            progress: progress,
          ),
          // The 2px surface gap between the two touching bars.
          const SizedBox(height: 2),
          _Bar(
            value: row.average,
            color: averageColor,
            emptyText: ComparisonBars.noEarlier,
            strong: false,
            progress: progress,
          ),
        ],
      ),
    );
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(6),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 4),
        child: LayoutBuilder(
          builder: (BuildContext context, BoxConstraints constraints) {
            // Name beside its pair on a phone and up — half the height of a
            // name above every pair. Stacked only when too narrow for both.
            if (constraints.maxWidth < 240) {
              return Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: <Widget>[name, const SizedBox(height: 3), bars],
              );
            }
            return Row(
              children: <Widget>[
                SizedBox(
                  width: (constraints.maxWidth * 0.3).clamp(72.0, 120.0),
                  child: name,
                ),
                const SizedBox(width: AppSpacing.sm),
                Expanded(child: bars),
              ],
            );
          },
        ),
      ),
    );
  }
}

/// One bar with its value at the tip, or the gap words when [value] is null.
class _Bar extends StatelessWidget {
  const _Bar({
    required this.value,
    required this.color,
    required this.emptyText,
    required this.strong,
    required this.progress,
  });

  final double? value;
  final Color color;
  final String emptyText;
  final bool strong;
  final double progress;

  /// Bar thickness. Thin marks: the numbers do the talking.
  static const double thickness = 10;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final double? v = value;
    final TextStyle? label = theme.textTheme.labelSmall?.copyWith(
      color: strong
          ? theme.colorScheme.onSurface
          : theme.colorScheme.onSurfaceVariant,
      fontFeatures: kTabularFigures,
      fontWeight: strong ? FontWeight.w700 : FontWeight.w500,
      height: 1.0,
    );

    if (v == null) {
      // A gap, not a bar: the words sit where the bar would have started.
      return Padding(
        padding: const EdgeInsets.only(left: 6),
        child: Text(
          emptyText,
          style: theme.textTheme.labelSmall?.copyWith(
            color: theme.colorScheme.onSurfaceVariant,
            fontStyle: FontStyle.italic,
            height: 1.0,
          ),
        ),
      );
    }

    return LayoutBuilder(
      builder: (BuildContext context, BoxConstraints constraints) {
        // Room for the widest tip label, "100", at the current text scale.
        final TextPainter probe = TextPainter(
          text: TextSpan(text: '100', style: label),
          textDirection: TextDirection.ltr,
          textScaler: MediaQuery.textScalerOf(context),
        )..layout();
        final double track = math.max(
          0,
          constraints.maxWidth - probe.width - 6,
        );
        final double length =
            track * (v.clamp(0, 100) / 100) * progress.clamp(0.0, 1.0);
        return Row(
          children: <Widget>[
            Container(
              width: length,
              height: thickness,
              decoration: BoxDecoration(
                color: color,
                // Rounded at the data end only; square on the baseline.
                borderRadius: const BorderRadius.horizontal(
                  right: Radius.circular(4),
                ),
              ),
            ),
            const SizedBox(width: 4),
            Text(v.round().toString(), style: label),
          ],
        );
      },
    );
  }
}
