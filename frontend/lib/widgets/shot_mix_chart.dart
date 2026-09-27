import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../models/chart_data.dart';
import '../theme/app_theme.dart';
import '../theme/motion.dart';
import 'chart_legend.dart';

/// Clips per shot type, as a donut beside a labelled legend.
///
/// The one job a donut is for: part-to-whole at a glance, at most
/// [kMaxMixSlices] slices ([buildShotMix] folds the tail). The total sits in
/// the hole. Every slice is named, with its count and whole percent, in the
/// legend beside it, so identity never rests on colour alone. Those labels
/// are ink on the card rather than type set inside the slices: several slice
/// hues cannot carry small text at AA in either ink or white.
///
/// Tapping a slice or a legend row selects it: the hole switches to that
/// slice's share and the other slices recede. Tapping it again, or the hole,
/// goes back to the total.
///
/// * No clips: nothing. The dashboard shows its empty state before it gets
///   here.
/// * One shot type: a full ring says nothing, so a sentence instead.
class ShotMixChart extends StatefulWidget {
  const ShotMixChart({super.key, required this.slices, this.size = 124});

  final List<ShotMixSlice> slices;

  /// Outer diameter of the ring.
  final double size;

  /// The sentence shown instead of a one-slice ring.
  static String singleTypeMessage(ShotMixSlice only) => only.count == 1
      ? 'Your only clip so far is ${only.label}. Film another shot type to '
            'see your mix.'
      : 'All ${only.count} clips so far are ${only.label}. Film another shot '
            'type to see your mix.';

  @override
  State<ShotMixChart> createState() => _ShotMixChartState();
}

class _ShotMixChartState extends State<ShotMixChart> {
  int? _selected;

  @override
  void didUpdateWidget(ShotMixChart oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.slices.length != widget.slices.length) _selected = null;
  }

  void _toggle(int? index) => setState(
    () => _selected = (index == null || index == _selected) ? null : index,
  );

  /// A slice's colour: fixed slot order for the named slices, the neutral for
  /// the folded tail. Never the score ramp.
  static Color colorFor(AppPalette palette, ShotMixSlice slice, int index) =>
      slice.isOther
      ? palette.chartOther
      : palette.chartSeries[index % palette.chartSeries.length];

  @override
  Widget build(BuildContext context) {
    final List<ShotMixSlice> slices = widget.slices;
    if (slices.isEmpty) return const SizedBox.shrink();
    final ThemeData theme = Theme.of(context);

    if (slices.length == 1) {
      return Text(
        ShotMixChart.singleTypeMessage(slices.single),
        style: theme.textTheme.bodySmall?.copyWith(
          color: theme.colorScheme.onSurfaceVariant,
        ),
      );
    }

    final AppPalette palette = context.palette;
    final List<Color> colors = <Color>[
      for (int i = 0; i < slices.length; i++) colorFor(palette, slices[i], i),
    ];
    final int total = slices.fold<int>(
      0,
      (int a, ShotMixSlice s) => a + s.count,
    );

    final Widget donut = _Donut(
      slices: slices,
      colors: colors,
      total: total,
      selected: _selected,
      size: widget.size,
      onSelect: _toggle,
    );
    final Widget legend = _Legend(
      slices: slices,
      colors: colors,
      selected: _selected,
      onSelect: _toggle,
    );

    final String spoken = <String>[
      for (final ShotMixSlice s in slices)
        '${s.label}: ${s.count} ${plural(s.count, 'clip')}, ${s.percent}%',
    ].join('. ');

    return Semantics(
      label: 'Shot mix. $spoken.',
      child: ExcludeSemantics(
        child: LayoutBuilder(
          builder: (BuildContext context, BoxConstraints constraints) {
            // Side by side when the legend keeps ~130dp at the reader's text
            // size; stacked below that, so long shot names wrap instead of
            // squeezing the counts off the card.
            final double legendMin =
                130 * MediaQuery.textScalerOf(context).scale(1);
            if (constraints.maxWidth >=
                widget.size + AppSpacing.lg + legendMin) {
              return Row(
                children: <Widget>[
                  donut,
                  const SizedBox(width: AppSpacing.lg),
                  Expanded(child: legend),
                ],
              );
            }
            return Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: <Widget>[
                Center(child: donut),
                const SizedBox(height: AppSpacing.md),
                legend,
              ],
            );
          },
        ),
      ),
    );
  }
}

class _Donut extends StatelessWidget {
  const _Donut({
    required this.slices,
    required this.colors,
    required this.total,
    required this.selected,
    required this.size,
    required this.onSelect,
  });

  final List<ShotMixSlice> slices;
  final List<Color> colors;
  final int total;
  final int? selected;
  final double size;
  final ValueChanged<int?> onSelect;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final AppPalette palette = context.palette;
    final int? sel = selected;
    final bool hasSel = sel != null && sel >= 0 && sel < slices.length;

    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTapDown: (TapDownDetails d) => onSelect(
        DonutPainter.sliceAt(d.localPosition, Size.square(size), <double>[
          for (final ShotMixSlice s in slices) s.count.toDouble(),
        ]),
      ),
      child: TweenAnimationBuilder<double>(
        tween: Tween<double>(begin: 0, end: 1),
        duration: motionDuration(context, const Duration(milliseconds: 700)),
        curve: kEnterCurve,
        builder: (BuildContext context, double t, Widget? child) => CustomPaint(
          size: Size.square(size),
          painter: DonutPainter(
            values: <double>[
              for (final ShotMixSlice s in slices) s.count.toDouble(),
            ],
            colors: colors,
            gapColor: palette.surfaceContainer,
            selected: hasSel ? sel : null,
            progress: t,
          ),
          child: child,
        ),
        child: SizedBox.square(
          dimension: size,
          child: Center(
            // The hole is a fixed ~72% of the ring; text inside it is capped
            // like the score ring's numeral so a 2.0x font cannot burst it.
            child: MediaQuery.withClampedTextScaling(
              maxScaleFactor: 1.3,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: <Widget>[
                  Text(
                    hasSel ? '${slices[sel].percent}%' : '$total',
                    style: theme.textTheme.titleLarge?.copyWith(
                      color: theme.colorScheme.onSurface,
                      fontWeight: FontWeight.w600,
                      height: 1.1,
                    ),
                  ),
                  Text(
                    hasSel
                        ? '${slices[sel].count} ${plural(slices[sel].count, 'clip')}'
                        : plural(total, 'clip'),
                    style: theme.textTheme.labelSmall?.copyWith(
                      color: theme.colorScheme.onSurfaceVariant,
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _Legend extends StatelessWidget {
  const _Legend({
    required this.slices,
    required this.colors,
    required this.selected,
    required this.onSelect,
  });

  final List<ShotMixSlice> slices;
  final List<Color> colors;
  final int? selected;
  final ValueChanged<int?> onSelect;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        for (int i = 0; i < slices.length; i++)
          InkWell(
            onTap: () => onSelect(i),
            borderRadius: BorderRadius.circular(6),
            child: Padding(
              // 4 + ~20 of text + 4 keeps each row near the 24dp minimum
              // touch height the marks spec asks for.
              padding: const EdgeInsets.symmetric(vertical: 4),
              child: Row(
                children: <Widget>[
                  LegendKey(color: colors[i]),
                  const SizedBox(width: AppSpacing.sm),
                  Expanded(
                    child: Text(
                      slices[i].label,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.bodySmall?.copyWith(
                        color: theme.colorScheme.onSurface,
                        fontWeight: selected == i
                            ? FontWeight.w700
                            : FontWeight.w400,
                      ),
                    ),
                  ),
                  const SizedBox(width: AppSpacing.sm),
                  Text(
                    '${slices[i].count} · ${slices[i].percent}%',
                    style: theme.textTheme.labelMedium?.copyWith(
                      color: theme.colorScheme.onSurfaceVariant,
                      fontFeatures: kTabularFigures,
                      fontWeight: selected == i
                          ? FontWeight.w700
                          : FontWeight.w500,
                    ),
                  ),
                ],
              ),
            ),
          ),
      ],
    );
  }
}

/// Paints a ring of [values] as proportional arcs, clockwise from 12 o'clock,
/// with a 2px gap in [gapColor] between neighbours.
///
/// Public so tests can check the hit geometry and the painted inputs.
class DonutPainter extends CustomPainter {
  DonutPainter({
    required this.values,
    required this.colors,
    required this.gapColor,
    required this.selected,
    this.progress = 1,
  });

  final List<double> values;
  final List<Color> colors;

  /// The card colour, so the gap reads as the surface showing through.
  final Color gapColor;

  final int? selected;

  /// 0..1, the entry sweep.
  final double progress;

  /// Ring thickness as a fraction of the diameter.
  static const double thicknessRatio = 0.14;

  /// Which slice a tap at [p] lands on, or null for the hole and outside.
  static int? sliceAt(Offset p, Size size, List<double> values) {
    final double total = values.fold<double>(0, (double a, double b) => a + b);
    if (total <= 0) return null;
    final Offset c = size.center(Offset.zero);
    final double r = size.shortestSide / 2;
    final double d = (p - c).distance;
    // Generous: from just inside the ring to a finger's width outside it.
    if (d < r * (1 - thicknessRatio * 2) - 6 || d > r + 12) return null;
    // Angle clockwise from 12 o'clock, 0..2pi.
    double a = math.atan2(p.dy - c.dy, p.dx - c.dx) + math.pi / 2;
    if (a < 0) a += math.pi * 2;
    double acc = 0;
    for (int i = 0; i < values.length; i++) {
      acc += values[i] / total * math.pi * 2;
      if (a <= acc) return i;
    }
    return values.length - 1;
  }

  @override
  void paint(Canvas canvas, Size size) {
    final double total = values.fold<double>(0, (double a, double b) => a + b);
    if (total <= 0 || size.isEmpty) return;
    final double thickness = size.shortestSide * thicknessRatio;
    final Offset c = size.center(Offset.zero);
    final double r = size.shortestSide / 2 - thickness / 2;
    final Rect ring = Rect.fromCircle(center: c, radius: r);
    final double sweepAll = math.pi * 2 * progress.clamp(0.0, 1.0);

    double start = -math.pi / 2;
    final List<double> boundaries = <double>[];
    for (int i = 0; i < values.length; i++) {
      final double sweep = values[i] / total * sweepAll;
      final bool dim = selected != null && selected != i;
      canvas.drawArc(
        ring,
        start,
        sweep,
        false,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = thickness
          ..color = dim ? colors[i].withValues(alpha: 0.35) : colors[i],
      );
      start += sweep;
      boundaries.add(start);
    }

    // The 2px surface gap between neighbours. None with a single slice: a
    // lone ring has no neighbour to separate.
    if (values.where((double v) => v > 0).length < 2) return;
    final Paint gap = Paint()
      ..color = gapColor
      ..strokeWidth = 2;
    final double inner = r - thickness / 2 - 1;
    final double outer = r + thickness / 2 + 1;
    for (final double a in <double>[-math.pi / 2, ...boundaries]) {
      final Offset u = Offset(math.cos(a), math.sin(a));
      canvas.drawLine(c + u * inner, c + u * outer, gap);
    }
  }

  @override
  bool shouldRepaint(DonutPainter old) =>
      old.progress != progress ||
      old.selected != selected ||
      old.gapColor != gapColor ||
      old.values.length != values.length ||
      old.colors.length != colors.length ||
      <int>[
        for (int i = 0; i < values.length; i++)
          if (old.values[i] != values[i] || old.colors[i] != colors[i]) i,
      ].isNotEmpty;
}
