import 'package:flutter/material.dart';

import '../models/chart_data.dart';
import '../theme/app_theme.dart';
import '../theme/motion.dart';

/// Clips per shot type, as one stacked bar over a labelled legend, as in the
/// redesign.
///
/// Part-to-whole at a glance, at most [kMaxMixSlices] slices ([buildShotMix]
/// folds the tail). The total sits above the bar. Every slice is named, with
/// its count and whole percent, in the legend below it, so identity never
/// rests on colour alone.
///
/// Tapping a segment or a legend row selects it: the figure above the bar
/// switches to that slice's share and the other segments recede. Tapping it
/// again goes back to the total.
///
/// * No clips: nothing. The dashboard shows its empty state before it gets
///   here.
/// * One shot type: a full bar says nothing, so a sentence instead.
class ShotMixChart extends StatefulWidget {
  const ShotMixChart({super.key, required this.slices, this.barHeight = 14});

  final List<ShotMixSlice> slices;

  /// Thickness of the stacked bar.
  final double barHeight;

  /// The sentence shown instead of a one-slice bar.
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
  /// the folded tail.
  static Color colorFor(AppPalette palette, ShotMixSlice slice, int index) =>
      slice.isOther ? palette.chartOther : mixColors[index % mixColors.length];

  /// The redesign's shot-mix colours, in slice order: green, blue, orange,
  /// then violet. Fixed, like the court map's, in both canvases.
  static const List<Color> mixColors = <Color>[
    Color(0xFF0B7A3B),
    Color(0xFF1D6FD1),
    Color(0xFFC2620F),
    Color(0xFF6D28D9),
  ];

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

    final int? sel = _selected;
    final bool hasSel = sel != null && sel >= 0 && sel < slices.length;
    final List<double> values = <double>[
      for (final ShotMixSlice s in slices) s.count.toDouble(),
    ];

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        Row(
          crossAxisAlignment: CrossAxisAlignment.baseline,
          textBaseline: TextBaseline.alphabetic,
          children: <Widget>[
            Text(
              hasSel ? '${slices[sel].percent}%' : '$total',
              style: theme.textTheme.titleLarge?.copyWith(
                color: theme.colorScheme.onSurface,
                fontFeatures: kTabularFigures,
              ),
            ),
            const SizedBox(width: AppSpacing.xs),
            Text(
              hasSel
                  ? '${slices[sel].count} ${plural(slices[sel].count, 'clip')}'
                  : plural(total, 'clip'),
              style: theme.textTheme.bodySmall?.copyWith(
                color: theme.colorScheme.onSurfaceVariant,
              ),
            ),
          ],
        ),
        const SizedBox(height: AppSpacing.sm),
        LayoutBuilder(
          builder: (BuildContext context, BoxConstraints constraints) {
            final Size size = Size(constraints.maxWidth, widget.barHeight);
            return GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTapDown: (TapDownDetails d) =>
                  _toggle(MixBarPainter.segmentAt(d.localPosition, size, values)),
              child: TweenAnimationBuilder<double>(
                tween: Tween<double>(begin: 0, end: 1),
                duration:
                    motionDuration(context, const Duration(milliseconds: 700)),
                curve: kEnterCurve,
                builder: (BuildContext context, double t, Widget? _) =>
                    CustomPaint(
                  size: size,
                  painter: MixBarPainter(
                    values: values,
                    colors: colors,
                    gapColor: palette.surfaceContainer,
                    selected: hasSel ? sel : null,
                    progress: t,
                  ),
                ),
              ),
            );
          },
        ),
        const SizedBox(height: AppSpacing.md),
        _Legend(
          slices: slices,
          colors: colors,
          selected: _selected,
          onSelect: _toggle,
        ),
      ],
    );
  }
}

/// The legend under the bar, as in the redesign: a colour key, the shot
/// and its count, in one wrapping row. Tapping an entry selects it.
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
    return Wrap(
      spacing: AppSpacing.lg,
      runSpacing: AppSpacing.xs,
      children: <Widget>[
        for (int i = 0; i < slices.length; i++)
          InkWell(
            onTap: () => onSelect(i),
            borderRadius: BorderRadius.circular(6),
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 4),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: <Widget>[
                  Container(
                    width: 10,
                    height: 10,
                    decoration: BoxDecoration(
                      color: colors[i],
                      borderRadius: BorderRadius.circular(3),
                    ),
                  ),
                  const SizedBox(width: 6),
                  Text(
                    slices[i].label,
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: theme.colorScheme.onSurface,
                      fontWeight: selected == i ? FontWeight.w700 : FontWeight.w400,
                    ),
                  ),
                  const SizedBox(width: 4),
                  Text(
                    '${slices[i].count}',
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: theme.colorScheme.onSurfaceVariant,
                      fontFeatures: kTabularFigures,
                      fontWeight: selected == i ? FontWeight.w700 : FontWeight.w500,
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

/// Paints [values] as one rounded horizontal bar of proportional segments,
/// left to right, with a 2px gap in [gapColor] between neighbours.
///
/// Public so tests can check the hit geometry and the painted inputs.
class MixBarPainter extends CustomPainter {
  MixBarPainter({
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

  /// Which segment a tap at [p] lands on, or null outside the bar.
  static int? segmentAt(Offset p, Size size, List<double> values) {
    final double total = values.fold<double>(0, (double a, double b) => a + b);
    if (total <= 0 || size.width <= 0) return null;
    // A finger's width of slack above and below a thin bar.
    if (p.dx < 0 || p.dx > size.width || p.dy < -12 || p.dy > size.height + 12) {
      return null;
    }
    double acc = 0;
    for (int i = 0; i < values.length; i++) {
      acc += values[i] / total * size.width;
      if (p.dx <= acc) return i;
    }
    return values.length - 1;
  }

  @override
  void paint(Canvas canvas, Size size) {
    final double total = values.fold<double>(0, (double a, double b) => a + b);
    if (total <= 0 || size.isEmpty) return;
    final double width = size.width * progress.clamp(0.0, 1.0);
    final RRect clip = RRect.fromRectAndRadius(
      Offset.zero & Size(width, size.height),
      Radius.circular(size.height / 2),
    );
    canvas.save();
    canvas.clipRRect(clip);
    double x = 0;
    final List<double> boundaries = <double>[];
    for (int i = 0; i < values.length; i++) {
      final double w = values[i] / total * width;
      final bool dim = selected != null && selected != i;
      canvas.drawRect(
        Rect.fromLTWH(x, 0, w, size.height),
        Paint()..color = dim ? colors[i].withValues(alpha: 0.35) : colors[i],
      );
      x += w;
      if (i < values.length - 1) boundaries.add(x);
    }
    if (values.where((double v) => v > 0).length >= 2) {
      final Paint gap = Paint()
        ..color = gapColor
        ..strokeWidth = 2;
      for (final double bx in boundaries) {
        canvas.drawLine(Offset(bx, 0), Offset(bx, size.height), gap);
      }
    }
    canvas.restore();
  }

  @override
  bool shouldRepaint(MixBarPainter old) =>
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
