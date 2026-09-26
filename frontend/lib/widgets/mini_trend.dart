import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

/// A compact, titled, single-series sparkline.
///
/// This is the small-multiple unit of the dashboard: one of these per
/// category, per shot type, and one for ball speed. Every panel is one series
/// with its own title, which is why none of them carries a legend.
///
/// ## There is deliberately no series colour on this widget
///
/// The mark is ALWAYS the primary accent — there is no `lineColor` parameter
/// and no categorical palette anywhere on this screen. Small multiples give
/// every series its own titled frame, so identity is already carried by words;
/// a second hue would add a colour-carried distinction that has to clear the
/// CVD separation bar and buys nothing. It would also fail: the default
/// flavor's `primary` and `secondary` sit 0.026 apart in OKLab under
/// deuteranopia, well inside the 0.070 gate in `test/palette_test.dart`.
///
/// Two measures are never drawn on one of these. A score panel and a speed
/// panel are separate panels with separate scales; a second y-axis is never
/// added.
///
/// ## What it refuses to draw
///
/// [points] is chronological, oldest first, and nulls mean NOT MEASURED. They
/// are skipped, never plotted at zero — a point on the floor would invent a
/// bad session out of a missing measurement. With no measured point the panel
/// says so in words. With exactly one it shows the value and says a trend
/// needs two, because a line through one point is a line the data did not
/// draw.
///
/// ## Interrogation
///
/// Only the newest value is direct-labelled; a number on every point is
/// clutter. Tapping the panel selects the nearest point and shows ITS value
/// and position instead, so every point is reachable on a touch screen.
class MiniTrend extends StatefulWidget {
  const MiniTrend({
    super.key,
    required this.title,
    required this.points,
    this.unit,
    this.height = 40,
    this.emptyMessage = 'Not measured',
  });

  /// Names the series. A single-series chart needs no legend if it is titled.
  final String title;

  /// Oldest first. Nulls are unmeasured sessions and are never plotted.
  final List<double?> points;

  /// Appended to the direct label, e.g. `mph`.
  final String? unit;

  final double height;

  /// Shown instead of a chart when nothing in [points] was measured.
  final String emptyMessage;

  /// Copy for a series with exactly one measured point.
  static const String singlePointMessage = '1 session — need 2 for a trend';

  @override
  State<MiniTrend> createState() => _MiniTrendState();
}

class _MiniTrendState extends State<MiniTrend> {
  /// Index into the MEASURED points, or null to label the newest.
  int? _selected;

  @override
  void didUpdateWidget(MiniTrend oldWidget) {
    super.didUpdateWidget(oldWidget);
    // A new series invalidates any selection made against the old one.
    if (oldWidget.points.length != widget.points.length) _selected = null;
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final AppPalette palette = context.palette;
    final List<double> measured =
        widget.points.whereType<double>().toList(growable: false);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        _header(theme, measured),
        const SizedBox(height: AppSpacing.xs),
        if (measured.isEmpty)
          Text(
            widget.emptyMessage,
            style: theme.textTheme.labelSmall
                ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
          )
        else if (measured.length == 1)
          Text(
            MiniTrend.singlePointMessage,
            style: theme.textTheme.labelSmall
                ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
          )
        else
          _plot(measured, palette.primary),
      ],
    );
  }

  /// Title on the left, the one direct label on the right.
  Widget _header(ThemeData theme, List<double> measured) {
    final int? selected = _selected;
    final bool hasSelection =
        selected != null && selected >= 0 && selected < measured.length;
    final double? shown = measured.isEmpty
        ? null
        : measured[hasSelection ? selected : measured.length - 1];

    return Row(
      crossAxisAlignment: CrossAxisAlignment.baseline,
      textBaseline: TextBaseline.alphabetic,
      children: <Widget>[
        Expanded(
          child: Text(
            widget.title,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: theme.textTheme.labelMedium
                ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
          ),
        ),
        const SizedBox(width: AppSpacing.sm),
        Text(
          // An em dash, never a zero, when there is nothing to show.
          shown == null
              ? '—'
              : '${shown.round()}${widget.unit == null ? '' : ' ${widget.unit}'}',
          // Text wears a text token. The coloured line below carries the
          // identity; the numeral never does.
          style: theme.textTheme.titleSmall?.copyWith(
            color: shown == null
                ? theme.colorScheme.onSurfaceVariant
                : theme.colorScheme.onSurface,
            fontFeatures: kTabularFigures,
          ),
        ),
      ],
    );
  }

  Widget _plot(List<double> measured, Color line) {
    final AppPalette palette = context.palette;
    return SizedBox(
      height: widget.height,
      child: LayoutBuilder(
        builder: (BuildContext context, BoxConstraints constraints) {
          return GestureDetector(
            behavior: HitTestBehavior.opaque,
            onTapDown: (TapDownDetails details) => _selectNearest(
              details.localPosition.dx,
              constraints.maxWidth,
              measured.length,
            ),
            child: CustomPaint(
              size: Size(constraints.maxWidth, widget.height),
              painter: MiniTrendPainter(
                values: measured,
                selected: _selected,
                lineColor: line,
                baselineColor: palette.outlineVariant,
                dotFill: palette.surfaceContainerLowest,
              ),
            ),
          );
        },
      ),
    );
  }

  void _selectNearest(double dx, double width, int count) {
    if (count < 2 || width <= 0) return;
    final double usable = width - MiniTrendPainter.inset * 2;
    if (usable <= 0) return;
    final double t =
        ((dx - MiniTrendPainter.inset) / usable).clamp(0.0, 1.0);
    final int index = (t * (count - 1)).round();
    // Tapping the already-selected point clears it, so the panel goes back to
    // labelling the newest session.
    setState(() => _selected = _selected == index ? null : index);
  }
}

/// Paints [values] as a 2px polyline with a selected-point marker.
///
/// Public only so the widget test can assert on its inputs; nothing else
/// constructs one.
class MiniTrendPainter extends CustomPainter {
  MiniTrendPainter({
    required this.values,
    required this.selected,
    required this.lineColor,
    required this.baselineColor,
    required this.dotFill,
  });

  /// At least two, all measured.
  final List<double> values;

  /// The interrogated point, or null for "label the newest".
  final int? selected;

  final Color lineColor;
  final Color baselineColor;
  final Color dotFill;

  /// Horizontal breathing room so the end markers are not clipped.
  static const double inset = 6;

  @override
  void paint(Canvas canvas, Size size) {
    if (size.width <= 0 || size.height <= 0 || values.length < 2) return;

    final double left = inset;
    final double right = size.width - inset;
    final double top = 5;
    final double bottom = size.height - 5;
    if (right <= left || bottom <= top) return;

    double lo = values.reduce((double a, double b) => a < b ? a : b);
    double hi = values.reduce((double a, double b) => a > b ? a : b);
    final double span = hi - lo;
    final double pad = span == 0 ? 4.0 : span * 0.2;
    lo = lo - pad;
    hi = hi + pad;
    final double domain = hi - lo <= 0 ? 1.0 : hi - lo;

    // One recessive rule at the foot of the panel; no grid.
    canvas.drawLine(
      Offset(left, bottom),
      Offset(right, bottom),
      Paint()
        ..color = baselineColor
        ..strokeWidth = 1,
    );

    final List<Offset> plotted = <Offset>[
      for (int i = 0; i < values.length; i++)
        Offset(
          left + (right - left) * (i / (values.length - 1)),
          bottom -
              ((values[i] - lo) / domain).clamp(0.0, 1.0) * (bottom - top),
        ),
    ];

    final Path path = Path()..moveTo(plotted.first.dx, plotted.first.dy);
    for (final Offset point in plotted.skip(1)) {
      path.lineTo(point.dx, point.dy);
    }
    canvas.drawPath(
      path,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2
        ..strokeCap = StrokeCap.round
        ..strokeJoin = StrokeJoin.round
        ..color = lineColor,
    );

    // The marked point: whichever one the label is currently quoting.
    final int marked =
        (selected != null && selected! >= 0 && selected! < plotted.length)
            ? selected!
            : plotted.length - 1;
    canvas.drawCircle(
      plotted[marked],
      5,
      Paint()..color = dotFill,
    );
    canvas.drawCircle(
      plotted[marked],
      5,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2
        ..color = lineColor,
    );
  }

  @override
  bool shouldRepaint(MiniTrendPainter old) =>
      old.selected != selected ||
      old.lineColor != lineColor ||
      old.baselineColor != baselineColor ||
      old.dotFill != dotFill ||
      !_same(old.values, values);

  static bool _same(List<double> a, List<double> b) {
    if (a.length != b.length) return false;
    for (int i = 0; i < a.length; i++) {
      if (a[i] != b[i]) return false;
    }
    return true;
  }
}
