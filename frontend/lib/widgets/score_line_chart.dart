import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../models/chart_data.dart';
import '../theme/app_theme.dart';
import '../theme/motion.dart';

/// A full line chart of one shot type's overall score, clip by clip.
///
/// One series, so one colour — the primary accent — and no legend: the title
/// says what is plotted. The chrome stays recessive: round-number ticks on a
/// left gutter, solid hairline gridlines, dates under the first and last
/// clip. The newest score is the one direct label.
///
/// * Clips are spaced evenly in recording order. An unscored clip keeps its
///   slot and BREAKS the line there — it is never plotted at zero and never
///   bridged by a line the data did not draw.
/// * Tapping anywhere snaps a crosshair to the nearest clip and shows its
///   score and date, or says it was not scored.
/// * With one scored clip there is no line: the value, and that a trend
///   needs two.
/// * At a pipeline-version change ([breaksBefore]) the line BREAKS and a
///   dashed marker labelled [versionMarkerLabel] stands between the two
///   clips. Every point stays visible and tappable; no stroke joins scores
///   that were measured differently.
class ScoreLineChart extends StatefulWidget {
  const ScoreLineChart({
    super.key,
    required this.title,
    required this.points,
    this.dates = const <DateTime?>[],
    this.breaksBefore = const <int>[],
    this.height = 132,
  });

  final String title;

  /// Oldest first; nulls are unscored clips.
  final List<double?> points;

  /// Aligned with [points], or empty when unknown.
  final List<DateTime?> dates;

  /// Indices where the pipeline version changes: `i` breaks the line between
  /// clip `i - 1` and clip `i`. See `version_segments.dart`.
  final List<int> breaksBefore;

  /// Height of the plot, excluding the date row under it.
  final double height;

  static const String singlePointMessage = '1 scored clip, need 2 for a trend';
  static const String emptyMessage = 'No scored clips yet';
  static const String versionMarkerLabel = 'Scoring updated';

  @override
  State<ScoreLineChart> createState() => _ScoreLineChartState();
}

class _ScoreLineChartState extends State<ScoreLineChart> {
  int? _selected;

  @override
  void didUpdateWidget(ScoreLineChart oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.points.length != widget.points.length) _selected = null;
  }

  String _dateLabel(int i) {
    final DateTime? at = i < widget.dates.length ? widget.dates[i] : null;
    return at == null ? 'Clip ${i + 1}' : shortDate(at.toLocal());
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final AppPalette palette = context.palette;
    final List<double> measured = widget.points.whereType<double>().toList(
      growable: false,
    );
    final TextStyle? muted = theme.textTheme.labelSmall?.copyWith(
      color: theme.colorScheme.onSurfaceVariant,
    );

    final Widget title = Text(
      widget.title,
      maxLines: 1,
      overflow: TextOverflow.ellipsis,
      style: theme.textTheme.labelMedium?.copyWith(
        color: theme.colorScheme.onSurfaceVariant,
      ),
    );

    if (measured.length < 2) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Row(
            crossAxisAlignment: CrossAxisAlignment.baseline,
            textBaseline: TextBaseline.alphabetic,
            children: <Widget>[
              Expanded(child: title),
              const SizedBox(width: AppSpacing.sm),
              Text(
                measured.isEmpty ? '—' : measured.single.round().toString(),
                style: theme.textTheme.titleSmall?.copyWith(
                  color: measured.isEmpty
                      ? theme.colorScheme.onSurfaceVariant
                      : theme.colorScheme.onSurface,
                  fontFeatures: kTabularFigures,
                ),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.xs),
          Text(
            measured.isEmpty
                ? ScoreLineChart.emptyMessage
                : ScoreLineChart.singlePointMessage,
            style: muted,
          ),
        ],
      );
    }

    final int n = widget.points.length;
    final TextScaler scaler = MediaQuery.textScalerOf(
      context,
    ).clamp(maxScaleFactor: 1.3);
    final TextStyle tickStyle =
        (theme.textTheme.labelSmall ?? const TextStyle(fontSize: 11)).copyWith(
          color: theme.colorScheme.onSurfaceVariant,
          fontFeatures: kTabularFigures,
          letterSpacing: 0,
        );

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        Row(
          children: <Widget>[
            Expanded(child: title),
            const SizedBox(width: AppSpacing.sm),
            // Flexible: at a 2.0x font on a 320dp phone the title and the
            // count cannot both keep their natural width.
            Flexible(
              child: Text(
                '${measured.length} scored '
                '${plural(measured.length, 'clip')}',
                textAlign: TextAlign.end,
                style: muted,
              ),
            ),
          ],
        ),
        const SizedBox(height: AppSpacing.xs),
        SizedBox(
          height: widget.height,
          child: LayoutBuilder(
            builder: (BuildContext context, BoxConstraints constraints) {
              final ScoreLineGeometry geo = ScoreLineGeometry(
                size: Size(constraints.maxWidth, widget.height),
                values: widget.points,
                breaksBefore: widget.breaksBefore,
                tickStyle: tickStyle,
                scaler: scaler,
              );
              return GestureDetector(
                behavior: HitTestBehavior.opaque,
                onTapDown: (TapDownDetails d) {
                  final int i = geo.nearestIndex(d.localPosition.dx);
                  setState(() => _selected = _selected == i ? null : i);
                },
                child: TweenAnimationBuilder<double>(
                  tween: Tween<double>(begin: 0, end: 1),
                  duration: motionDuration(
                    context,
                    const Duration(milliseconds: 700),
                  ),
                  curve: kEnterCurve,
                  builder: (BuildContext context, double t, _) => CustomPaint(
                    size: geo.size,
                    painter: ScoreLinePainter(
                      geometry: geo,
                      lineColor: palette.primary,
                      areaColor: palette.primaryFill,
                      gridColor: palette.outlineVariant,
                      crosshairColor: palette.outline,
                      ringColor: palette.surfaceContainer,
                      tooltipFill: palette.surfaceContainerLowest,
                      inkStrong: theme.colorScheme.onSurface,
                      inkMuted: theme.colorScheme.onSurfaceVariant,
                      labelStyle: tickStyle,
                      scaler: scaler,
                      selected: _selected,
                      selectedDate: _selected == null
                          ? null
                          : _dateLabel(_selected!),
                      progress: t,
                    ),
                  ),
                ),
              );
            },
          ),
        ),
        const SizedBox(height: 2),
        // The x-axis band is part of the chart's own height, laid out as
        // text so it can wrap rather than be clipped at large font sizes.
        Padding(
          padding: EdgeInsets.only(
            left: ScoreLineGeometry.gutterFor(tickStyle, scaler),
          ),
          child: Row(
            children: <Widget>[
              Expanded(child: Text(_dateLabel(0), style: muted)),
              Expanded(
                child: Text(
                  _dateLabel(n - 1),
                  textAlign: TextAlign.end,
                  style: muted,
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

/// The chart's coordinate system, shared by painter and tap handler.
class ScoreLineGeometry {
  ScoreLineGeometry({
    required this.size,
    required this.values,
    this.breaksBefore = const <int>[],
    required TextStyle tickStyle,
    required TextScaler scaler,
  }) : axis = scoreAxis(values.whereType<double>().toList()),
       gutter = gutterFor(tickStyle, scaler);

  final Size size;
  final List<double?> values;

  /// Version boundaries: `i` means between clip `i - 1` and clip `i`.
  final List<int> breaksBefore;
  final ({double min, double max, double step}) axis;
  final double gutter;

  /// Room above the plot for the newest point's label, and to the right for
  /// its marker.
  static const double topRoom = 18;
  static const double rightRoom = 10;
  static const double bottomRoom = 6;

  /// The tick-label column: the width of "100" plus a little air.
  static double gutterFor(TextStyle style, TextScaler scaler) {
    final TextPainter p = TextPainter(
      text: TextSpan(text: '100', style: style),
      textDirection: TextDirection.ltr,
      textScaler: scaler,
    )..layout();
    return p.width + 8;
  }

  double get left => gutter;
  double get right => size.width - rightRoom;
  double get top => topRoom;
  double get bottom => size.height - bottomRoom;

  double xFor(int i) => values.length == 1
      ? (left + right) / 2
      : left + (right - left) * i / (values.length - 1);

  double yFor(double v) {
    final double span = axis.max - axis.min;
    final double f = span <= 0 ? 0.5 : ((v - axis.min) / span).clamp(0.0, 1.0);
    return bottom - f * (bottom - top);
  }

  /// A measured point's position; null for an unscored clip.
  Offset? pointAt(int i) {
    final double? v = values[i];
    return v == null ? null : Offset(xFor(i), yFor(v));
  }

  /// The clip whose x is nearest [dx]. Every clip is reachable, scored or
  /// not, because "not scored" is an answer too.
  int nearestIndex(double dx) {
    if (values.length <= 1 || right <= left) return 0;
    final double t = ((dx - left) / (right - left)).clamp(0.0, 1.0);
    return (t * (values.length - 1)).round();
  }

  /// The x of the marker for a version boundary before clip [i]: midway
  /// between the two clips it separates.
  double markerX(int i) => (xFor(i - 1) + xFor(i)) / 2;

  /// The boundaries that fall inside this series.
  Iterable<int> get markers =>
      breaksBefore.where((int i) => i > 0 && i < values.length);

  /// The line's pieces: runs of consecutive scored clips. An unscored clip
  /// ends one run and the next scored clip starts another; so does a
  /// version boundary, so no stroke joins two differently measured scores.
  List<List<int>> runs() {
    final Set<int> breaks = breaksBefore.toSet();
    final List<List<int>> out = <List<int>>[];
    List<int> run = <int>[];
    for (int i = 0; i < values.length; i++) {
      if (breaks.contains(i) && run.isNotEmpty) {
        out.add(run);
        run = <int>[];
      }
      if (values[i] == null) {
        if (run.isNotEmpty) out.add(run);
        run = <int>[];
      } else {
        run.add(i);
      }
    }
    if (run.isNotEmpty) out.add(run);
    return out;
  }
}

/// Paints a [ScoreLineGeometry]. Public for the geometry tests.
class ScoreLinePainter extends CustomPainter {
  ScoreLinePainter({
    required this.geometry,
    required this.lineColor,
    required this.areaColor,
    required this.gridColor,
    required this.crosshairColor,
    required this.ringColor,
    required this.tooltipFill,
    required this.inkStrong,
    required this.inkMuted,
    required this.labelStyle,
    required this.scaler,
    required this.selected,
    required this.selectedDate,
    this.progress = 1,
  });

  final ScoreLineGeometry geometry;
  final Color lineColor;
  final Color areaColor;
  final Color gridColor;
  final Color crosshairColor;
  final Color ringColor;
  final Color tooltipFill;
  final Color inkStrong;
  final Color inkMuted;
  final TextStyle labelStyle;
  final TextScaler scaler;
  final int? selected;
  final String? selectedDate;
  final double progress;

  TextPainter _text(String s, {Color? color, FontWeight? weight}) =>
      TextPainter(
        text: TextSpan(
          text: s,
          style: labelStyle.copyWith(color: color, fontWeight: weight),
        ),
        textDirection: TextDirection.ltr,
        textScaler: scaler,
      )..layout();

  @override
  void paint(Canvas canvas, Size size) {
    final ScoreLineGeometry g = geometry;
    if (g.right <= g.left || g.bottom <= g.top) return;

    // Gridlines and ticks: solid hairlines, one per round number.
    final Paint grid = Paint()
      ..color = gridColor
      ..strokeWidth = 1;
    for (double v = g.axis.min; v <= g.axis.max + 0.01; v += g.axis.step) {
      final double y = g.yFor(v);
      canvas.drawLine(Offset(g.left, y), Offset(size.width, y), grid);
      final TextPainter tick = _text(v.round().toString(), color: inkMuted);
      tick.paint(canvas, Offset(g.left - 6 - tick.width, y - tick.height / 2));
    }

    // Version boundaries: a dashed hairline, recessive like the grid.
    for (final int b in g.markers) {
      _dashedVertical(canvas, g.markerX(b), 0, g.bottom);
    }

    // The entry sweep reveals the line left to right.
    canvas.save();
    canvas.clipRect(
      Rect.fromLTRB(
        0,
        0,
        g.left + (size.width - g.left) * progress.clamp(0.0, 1.0),
        size.height,
      ),
    );

    for (final List<int> run in g.runs()) {
      if (run.length < 2) continue;
      final Path line = Path();
      for (int k = 0; k < run.length; k++) {
        final Offset p = g.pointAt(run[k])!;
        k == 0 ? line.moveTo(p.dx, p.dy) : line.lineTo(p.dx, p.dy);
      }
      final Path area = Path.from(line)
        ..lineTo(g.xFor(run.last), g.bottom)
        ..lineTo(g.xFor(run.first), g.bottom)
        ..close();
      // A wash, never a block.
      canvas.drawPath(area, Paint()..color = areaColor.withValues(alpha: 0.10));
      canvas.drawPath(
        line,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 2
          ..strokeCap = StrokeCap.round
          ..strokeJoin = StrokeJoin.round
          ..color = lineColor,
      );
    }

    final int last = _lastScored();
    for (int i = 0; i < g.values.length; i++) {
      final Offset? p = g.pointAt(i);
      if (p == null) continue;
      final bool big = i == last || i == selected;
      final double r = big ? 4.5 : 3;
      canvas.drawCircle(p, r + 2, Paint()..color = ringColor);
      canvas.drawCircle(p, r, Paint()..color = lineColor);
    }
    canvas.restore();

    if (progress < 1) return;

    // The one direct label: the newest score, unless a tooltip is up.
    final int? sel = selected;
    Rect? newestLabel;
    if (sel == null || sel != last) {
      final Offset p = g.pointAt(last)!;
      final TextPainter label = _text(
        g.values[last]!.round().toString(),
        color: inkStrong,
        weight: FontWeight.w700,
      );
      final double x = (p.dx - label.width / 2).clamp(
        g.left,
        size.width - label.width,
      );
      final double y = math.max(0, p.dy - 8 - label.height);
      label.paint(canvas, Offset(x, y));
      newestLabel = Offset(x, y) & label.size;
    }

    for (final int b in g.markers) {
      _markerLabel(canvas, size, g.markerX(b), avoid: newestLabel);
    }

    if (sel != null && sel >= 0 && sel < g.values.length) {
      _tooltip(canvas, size, sel);
    }
  }

  void _dashedVertical(Canvas canvas, double x, double top, double bottom) {
    final Paint paint = Paint()
      ..color = crosshairColor
      ..strokeWidth = 1;
    const double dash = 3;
    const double gap = 3;
    for (double y = top; y < bottom; y += dash + gap) {
      canvas.drawLine(
        Offset(x, y),
        Offset(x, math.min(y + dash, bottom)),
        paint,
      );
    }
  }

  /// "Scoring updated", in the top band beside its marker: to the right when
  /// it fits, else to the left, and on whichever side does not cover the
  /// newest score's label.
  void _markerLabel(Canvas canvas, Size size, double x, {Rect? avoid}) {
    final TextPainter label = _text(
      ScoreLineChart.versionMarkerLabel,
      color: inkMuted,
    );
    final Offset right = Offset(x + 4, 0);
    final Offset left = Offset(x - 4 - label.width, 0);
    bool fits(Offset o) =>
        o.dx >= geometry.left && o.dx + label.width <= size.width;
    bool clear(Offset o) => avoid == null || !(o & label.size).overlaps(avoid);
    final List<Offset> order = <Offset>[right, left];
    Offset chosen = fits(right) ? right : left;
    for (final Offset o in order) {
      if (fits(o) && clear(o)) {
        chosen = o;
        break;
      }
    }
    label.paint(
      canvas,
      Offset(
        chosen.dx.clamp(0.0, math.max(0.0, size.width - label.width)),
        chosen.dy,
      ),
    );
  }

  int _lastScored() {
    for (int i = geometry.values.length - 1; i >= 0; i--) {
      if (geometry.values[i] != null) return i;
    }
    return 0;
  }

  void _tooltip(Canvas canvas, Size size, int i) {
    final ScoreLineGeometry g = geometry;
    final double x = g.xFor(i);
    // The crosshair finds the clip; the reader never has to hit a 2px line.
    canvas.drawLine(
      Offset(x, g.top - 4),
      Offset(x, g.bottom),
      Paint()
        ..color = crosshairColor
        ..strokeWidth = 1,
    );
    final double? v = g.values[i];
    final TextPainter value = _text(
      v == null ? 'Not scored' : v.round().toString(),
      color: inkStrong,
      weight: FontWeight.w700,
    );
    final TextPainter date = _text(
      selectedDate ?? 'Clip ${i + 1}',
      color: inkMuted,
    );
    const double padH = 6;
    const double padV = 3;
    final double w = value.width + 6 + date.width + padH * 2;
    final double h = math.max(value.height, date.height) + padV * 2;
    final double anchorY = v == null
        ? g.top + (g.bottom - g.top) / 2
        : g.yFor(v);
    double top = anchorY - 10 - h;
    if (top < 0) top = anchorY + 10;
    top = top.clamp(0.0, math.max(0.0, size.height - h));
    final double left = (x - w / 2).clamp(0.0, math.max(0.0, size.width - w));
    final RRect box = RRect.fromRectAndRadius(
      Rect.fromLTWH(left, top, w, h),
      const Radius.circular(6),
    );
    canvas.drawRRect(box, Paint()..color = tooltipFill);
    canvas.drawRRect(
      box,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1
        ..color = crosshairColor,
    );
    // Value leads, date follows.
    value.paint(canvas, Offset(left + padH, top + padV));
    date.paint(
      canvas,
      Offset(
        left + padH + value.width + 6,
        top + padV + (value.height - date.height) / 2,
      ),
    );
  }

  @override
  bool shouldRepaint(ScoreLinePainter old) => true;
}
