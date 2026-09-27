import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../models/chart_data.dart';
import '../theme/app_theme.dart';
import '../theme/motion.dart';
import 'chart_legend.dart';

/// One swing's category scores as a radar, optionally over the previous swing
/// of the same shot type.
///
/// ## A null axis is NOT a zero
///
/// A vertex at the centre of a radar reads as "terrible". Plotting an
/// unmeasured category there would fabricate the worst result on the chart
/// out of a missing measurement. So an unmeasured axis gets:
///
/// * no vertex, and the outline is BROKEN there — segments are drawn only
///   between neighbouring axes that were both measured, and the area is only
///   filled when every axis was;
/// * a dashed spoke and a "not measured" line under its label;
/// * a sentence under the chart naming what was not measured.
///
/// With fewer than [kMinRadarAxes] measured axes there is no polygon to
/// speak of, so no radar is drawn: a sentence says why.
///
/// ## Colour
///
/// Slot 0 of [AppPalette.chartSeries] is this swing, slot 1 the previous one:
/// the same meaning those slots have on every chart. Labels are ink. Two
/// series always carry a legend.
///
/// Tapping near a spoke selects it and a readout line gives its exact scores.
class SkillRadar extends StatefulWidget {
  const SkillRadar({super.key, required this.profile, this.maxRadius = 92});

  final SkillProfile profile;
  final double maxRadius;

  static String tooFewMessage(int measured) =>
      'Only $measured ${plural(measured, 'category', 'categories')} '
      '${measured == 1 ? 'was' : 'were'} measured on this swing. A skill '
      'profile needs $kMinRadarAxes, so none is drawn.';

  static String unmeasuredNote(List<String> names) {
    final String list = names.length <= 1
        ? names.join()
        : '${names.sublist(0, names.length - 1).join(', ')} and ${names.last}';
    return '$list ${names.length == 1 ? 'was' : 'were'} not measured on this '
        'swing, so the shape is left open there rather than drawn at zero.';
  }

  static const String tapHint = 'Tap a spoke for its scores.';

  @override
  State<SkillRadar> createState() => _SkillRadarState();
}

class _SkillRadarState extends State<SkillRadar> {
  int? _selected;

  @override
  void didUpdateWidget(SkillRadar oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.profile.axes.length != widget.profile.axes.length) {
      _selected = null;
    }
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final AppPalette palette = context.palette;
    final SkillProfile p = widget.profile;
    final TextStyle? muted = theme.textTheme.bodySmall?.copyWith(
      color: theme.colorScheme.onSurfaceVariant,
    );

    if (!p.current.plottable) {
      return Text(
        SkillRadar.tooFewMessage(p.current.measuredCount),
        style: muted,
      );
    }

    final Color currentColor = palette.chartSeries[0];
    final Color previousColor = palette.chartSeries[1];
    final RadarSeries? previous = p.previous;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        if (previous != null) ...<Widget>[
          ChartLegend(
            entries: <LegendEntry>[
              LegendEntry(
                color: currentColor,
                label: p.current.label,
                mark: LegendMark.line,
              ),
              LegendEntry(
                color: previousColor,
                label: previous.label,
                mark: LegendMark.line,
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.sm),
        ],
        Semantics(
          label: _semanticsLabel(p),
          child: ExcludeSemantics(child: _chart(context, palette)),
        ),
        const SizedBox(height: AppSpacing.xs),
        Text(
          _readout(p),
          style: _selected == null
              ? muted
              : theme.textTheme.bodySmall?.copyWith(
                  color: theme.colorScheme.onSurface,
                ),
        ),
        if (p.unmeasured.isNotEmpty) ...<Widget>[
          const SizedBox(height: AppSpacing.xs),
          Text(SkillRadar.unmeasuredNote(p.unmeasured), style: muted),
        ],
      ],
    );
  }

  Widget _chart(BuildContext context, AppPalette palette) {
    final ThemeData theme = Theme.of(context);
    // Axis labels are painted, and the ring cannot grow without limit, so
    // their scale is capped like the score ring's numeral. The readout and
    // the breakdown beside this chart carry the same words at full scale.
    final TextScaler scaler = MediaQuery.textScalerOf(
      context,
    ).clamp(maxScaleFactor: 1.3);
    final TextStyle base = (theme.textTheme.labelMedium ?? const TextStyle())
        .copyWith(color: theme.colorScheme.onSurfaceVariant);

    return LayoutBuilder(
      builder: (BuildContext context, BoxConstraints constraints) {
        final RadarLayout layout = RadarLayout.compute(
          width: constraints.maxWidth,
          profile: widget.profile,
          selected: _selected,
          style: base,
          selectedColor: theme.colorScheme.onSurface,
          scaler: scaler,
          maxRadius: widget.maxRadius,
        );
        return GestureDetector(
          behavior: HitTestBehavior.opaque,
          onTapDown: (TapDownDetails d) {
            final int axis = layout.axisNearest(d.localPosition);
            setState(() => _selected = _selected == axis ? null : axis);
          },
          child: TweenAnimationBuilder<double>(
            tween: Tween<double>(begin: 0, end: 1),
            duration: motionDuration(
              context,
              const Duration(milliseconds: 650),
            ),
            curve: kEnterCurve,
            builder: (BuildContext context, double t, _) => CustomPaint(
              size: Size(constraints.maxWidth, layout.height),
              painter: RadarPainter(
                layout: layout,
                current: widget.profile.current.values,
                previous: widget.profile.previous?.values,
                currentColor: palette.chartSeries[0],
                previousColor: palette.chartSeries[1],
                gridColor: palette.outlineVariant,
                nullSpokeColor: palette.outline,
                ringColor: palette.surfaceContainer,
                selected: _selected,
                progress: t,
              ),
            ),
          ),
        );
      },
    );
  }

  String _readout(SkillProfile p) {
    final int? i = _selected;
    if (i == null || i < 0 || i >= p.axes.length) return SkillRadar.tapHint;
    final double? now = p.current.values[i];
    final double? before = p.previous?.values[i];
    final String name = p.axes[i].displayName;
    final String nowText = now == null
        ? 'not measured on ${p.current.label.toLowerCase()}'
        : '${p.current.label.toLowerCase()} ${now.round()}';
    if (p.previous == null) return '$name: $nowText';
    final String beforeText = before == null
        ? 'not measured on the ${p.previous!.label.toLowerCase()}'
        : '${p.previous!.label.toLowerCase()} ${before.round()}';
    return '$name: $nowText · $beforeText';
  }

  String _semanticsLabel(SkillProfile p) => <String>[
    'Skill profile',
    for (int i = 0; i < p.axes.length; i++)
      '${p.axes[i].displayName} '
          '${p.current.values[i] == null ? 'not measured' : p.current.values[i]!.round()}',
  ].join('. ');
}

/// Where everything on the radar goes, for one width.
///
/// Computed once per layout and shared by the painter and the tap handler, so
/// what is hit is exactly what is drawn. Labels are laid out FIRST and the
/// ring radius is whatever is left, so a long label shrinks the ring instead
/// of running off the card.
class RadarLayout {
  RadarLayout._({
    required this.center,
    required this.radius,
    required this.height,
    required this.directions,
    required this.labels,
    required this.labelOffsets,
  });

  final Offset center;
  final double radius;
  final double height;

  /// Unit vectors, axis 0 straight up, then clockwise.
  final List<Offset> directions;
  final List<TextPainter> labels;
  final List<Offset> labelOffsets;

  /// Space between the outer ring and a label.
  static const double labelGap = 6;

  /// The ring never shrinks below this.
  static const double minRadius = 36;

  static RadarLayout compute({
    required double width,
    required SkillProfile profile,
    required int? selected,
    required TextStyle style,
    required Color selectedColor,
    required TextScaler scaler,
    required double maxRadius,
  }) {
    final int n = profile.axes.length;
    final List<Offset> dirs = <Offset>[
      for (int i = 0; i < n; i++)
        Offset(
          math.cos(-math.pi / 2 + i * 2 * math.pi / n),
          math.sin(-math.pi / 2 + i * 2 * math.pi / n),
        ),
    ];

    // The widest a side label may be and still leave the ring its minimum:
    // from width/2 >= ux*(r + gap) + w/2*(1 + ux), at the most sideways axis.
    final double uxMax = dirs
        .map((Offset d) => d.dx.abs())
        .fold<double>(0, math.max);
    final double maxLabel = math.max(
      48.0,
      math.min(
        width * 0.21,
        2 * (width / 2 - uxMax * (minRadius + labelGap)) / (1 + uxMax),
      ),
    );
    final List<TextPainter> labels = <TextPainter>[
      for (int i = 0; i < n; i++)
        TextPainter(
          text: TextSpan(
            text: profile.axes[i].displayName,
            style: style.copyWith(
              color: selected == i ? selectedColor : style.color,
              fontWeight: selected == i ? FontWeight.w700 : FontWeight.w500,
            ),
            children: <InlineSpan>[
              if (profile.current.values[i] == null)
                TextSpan(
                  text: '\nnot measured',
                  style: style.copyWith(
                    fontStyle: FontStyle.italic,
                    fontWeight: FontWeight.w400,
                  ),
                ),
            ],
          ),
          textAlign: dirs[i].dx > 0.25
              ? TextAlign.left
              : dirs[i].dx < -0.25
              ? TextAlign.right
              : TextAlign.center,
          textDirection: TextDirection.ltr,
          textScaler: scaler,
        )..layout(
          // Wrap between words, never inside one: "Preparatio / n" is
          // worse than a slightly smaller ring.
          maxWidth: math.max(
            maxLabel,
            _longestWord(profile.axes[i].displayName, style, scaler) + 1,
          ),
        ),
    ];

    // The largest radius at which every label still fits horizontally.
    double r = maxRadius;
    for (int i = 0; i < n; i++) {
      final double ux = dirs[i].dx.abs();
      if (ux < 1e-6) continue;
      final double w = labels[i].width;
      final double fit = (width / 2 - w / 2 * (1 + ux)) / ux - labelGap;
      r = math.min(r, fit);
    }
    r = math.max(r, minRadius);

    // Vertical extents above and below the centre, labels included.
    double up = r;
    double down = r;
    final List<Offset> rel = <Offset>[];
    for (int i = 0; i < n; i++) {
      final Offset u = dirs[i];
      final double w = labels[i].width;
      final double h = labels[i].height;
      final Offset anchor = u * (r + labelGap);
      final Offset topLeft = Offset(
        anchor.dx - w / 2 + u.dx * w / 2,
        anchor.dy - h / 2 + u.dy * h / 2,
      );
      rel.add(topLeft);
      up = math.max(up, -topLeft.dy);
      down = math.max(down, topLeft.dy + h);
    }
    const double pad = 4;
    final Offset c = Offset(width / 2, up + pad);
    return RadarLayout._(
      center: c,
      radius: r,
      height: up + down + pad * 2,
      directions: dirs,
      labels: labels,
      labelOffsets: <Offset>[for (final Offset o in rel) c + o],
    );
  }

  static double _longestWord(String text, TextStyle style, TextScaler scaler) {
    double widest = 0;
    for (final String word in text.split(' ')) {
      final TextPainter p = TextPainter(
        text: TextSpan(
          text: word,
          style: style.copyWith(fontWeight: FontWeight.w700),
        ),
        textDirection: TextDirection.ltr,
        textScaler: scaler,
      )..layout();
      widest = math.max(widest, p.width);
    }
    return widest;
  }

  /// Where [value] (0-100) sits on axis [i]. Only ever called for a
  /// MEASURED value; there is no position for a null.
  Offset vertex(int i, double value, [double progress = 1]) =>
      center +
      directions[i] * (radius * (value.clamp(0, 100) / 100) * progress);

  /// The axis whose direction is closest to [p]'s angle from the centre.
  int axisNearest(Offset p) {
    final Offset d = p - center;
    if (d.distance < 1e-6) return 0;
    final Offset u = d / d.distance;
    int best = 0;
    double bestDot = -2;
    for (int i = 0; i < directions.length; i++) {
      final double dot = u.dx * directions[i].dx + u.dy * directions[i].dy;
      if (dot > bestDot) {
        bestDot = dot;
        best = i;
      }
    }
    return best;
  }
}

/// Paints the grid, up to two series and the labels of a [RadarLayout].
///
/// Public so tests can check what is and is not drawn for a null axis.
class RadarPainter extends CustomPainter {
  RadarPainter({
    required this.layout,
    required this.current,
    required this.previous,
    required this.currentColor,
    required this.previousColor,
    required this.gridColor,
    required this.nullSpokeColor,
    required this.ringColor,
    required this.selected,
    this.progress = 1,
  });

  final RadarLayout layout;
  final List<double?> current;
  final List<double?>? previous;
  final Color currentColor;
  final Color previousColor;
  final Color gridColor;
  final Color nullSpokeColor;

  /// The card colour, drawn as a 2px ring around each vertex dot.
  final Color ringColor;
  final int? selected;
  final double progress;

  /// The outline segments to draw for [values]: index pairs of NEIGHBOURING
  /// axes that were both measured. A null axis breaks the outline on both
  /// sides of it; it is never bridged and never pulled to the centre.
  static List<(int, int)> segments(List<double?> values) {
    final int n = values.length;
    return <(int, int)>[
      for (int i = 0; i < n; i++)
        if (values[i] != null && values[(i + 1) % n] != null) (i, (i + 1) % n),
    ];
  }

  @override
  void paint(Canvas canvas, Size size) {
    final int n = layout.directions.length;
    final Offset c = layout.center;
    final double r = layout.radius;

    // Grid: four hairline rings at 25/50/75/100, solid and recessive.
    final Paint grid = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1
      ..color = gridColor;
    for (final double f in <double>[0.25, 0.5, 0.75, 1]) {
      final Path ring = Path();
      for (int i = 0; i < n; i++) {
        final Offset p = c + layout.directions[i] * (r * f);
        i == 0 ? ring.moveTo(p.dx, p.dy) : ring.lineTo(p.dx, p.dy);
      }
      canvas.drawPath(ring..close(), grid);
    }

    // Spokes. A measured axis gets a hairline; an unmeasured one a DASHED
    // spoke — the one place a dash is used, because here it means "no data
    // on this line", not grid.
    for (int i = 0; i < n; i++) {
      final Offset end = c + layout.directions[i] * r;
      if (current[i] == null) {
        _dashed(
          canvas,
          c,
          end,
          Paint()
            ..color = nullSpokeColor
            ..strokeWidth = 1.2,
        );
      } else {
        canvas.drawLine(
          c,
          end,
          Paint()
            ..color = selected == i ? nullSpokeColor : gridColor
            ..strokeWidth = selected == i ? 1.5 : 1,
        );
      }
    }

    // The comparison is an outline only; the current swing alone gets the
    // wash. Two overlapping washes mix into a third colour nobody plotted.
    if (previous != null) {
      _series(canvas, previous!, previousColor, fill: false, dotRadius: 3);
    }
    _series(canvas, current, currentColor, fill: true, dotRadius: 4);

    for (int i = 0; i < n; i++) {
      layout.labels[i].paint(canvas, layout.labelOffsets[i]);
    }
  }

  void _series(
    Canvas canvas,
    List<double?> values,
    Color color, {
    required bool fill,
    required double dotRadius,
  }) {
    final int n = values.length;
    // The area is only filled when EVERY axis was measured: filling across a
    // gap would draw a shape through a category nobody measured.
    final bool complete = values.every((double? v) => v != null);
    if (fill && complete) {
      final Path area = Path();
      for (int i = 0; i < n; i++) {
        final Offset p = layout.vertex(i, values[i]!, progress);
        i == 0 ? area.moveTo(p.dx, p.dy) : area.lineTo(p.dx, p.dy);
      }
      area.close();
      canvas.drawPath(area, Paint()..color = color.withValues(alpha: 0.12));
    }
    final Paint stroke = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2
      ..strokeCap = StrokeCap.round
      ..color = color;
    for (final (int a, int b) in segments(values)) {
      canvas.drawLine(
        layout.vertex(a, values[a]!, progress),
        layout.vertex(b, values[b]!, progress),
        stroke,
      );
    }
    for (int i = 0; i < n; i++) {
      final double? v = values[i];
      if (v == null) continue;
      final Offset p = layout.vertex(i, v, progress);
      final double dot = selected == i ? dotRadius + 1.5 : dotRadius;
      canvas.drawCircle(p, dot + 2, Paint()..color = ringColor);
      canvas.drawCircle(p, dot, Paint()..color = color);
    }
  }

  static void _dashed(Canvas canvas, Offset a, Offset b, Paint paint) {
    const double dash = 3;
    const double gap = 3;
    final double length = (b - a).distance;
    if (length <= 0) return;
    final Offset u = (b - a) / length;
    for (double t = 0; t < length; t += dash + gap) {
      canvas.drawLine(a + u * t, a + u * math.min(t + dash, length), paint);
    }
  }

  @override
  bool shouldRepaint(RadarPainter old) => true;
}
