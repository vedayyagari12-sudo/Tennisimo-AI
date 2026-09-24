import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

/// A hand-painted line + area chart of overall score across sessions.
///
/// [scores] is chronological, oldest first, and may contain nulls: a session
/// the backend could not score is SKIPPED, never plotted at zero, because a
/// point on the floor would invent a bad session out of a missing measurement.
///
/// The y-range is padded around the real min/max rather than pinned to 0-100,
/// so a run of 60-65 scores reads as a line with shape instead of a flat
/// smear along the bottom.
class TrendChart extends StatelessWidget {
  const TrendChart({
    super.key,
    required this.scores,
    this.height = 150,
  });

  final List<double?> scores;
  final double height;

  /// Shown when nothing in the window could be scored.
  static const String emptyMessage =
      'No scored sessions yet — record a swing to start your trend.';

  /// Shown when exactly one session has a score: a trend needs two points.
  static const String singlePointMessage =
      'One session so far — record another to see a trend.';

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final List<double> points =
        scores.whereType<double>().toList(growable: false);

    if (points.isEmpty) {
      return SizedBox(
        height: height,
        child: Center(
          child: Text(
            emptyMessage,
            textAlign: TextAlign.center,
            style: theme.textTheme.bodySmall
                ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
          ),
        ),
      );
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        SizedBox(
          height: height,
          child: CustomPaint(
            painter: _TrendChartPainter(
              points: points,
              lineColor: theme.colorScheme.primary,
              baselineColor: theme.colorScheme.outline,
              dotFill: theme.colorScheme.surface,
            ),
          ),
        ),
        if (points.length == 1) ...<Widget>[
          const SizedBox(height: AppSpacing.sm),
          Text(
            singlePointMessage,
            textAlign: TextAlign.center,
            style: theme.textTheme.bodySmall
                ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
          ),
        ],
      ],
    );
  }
}

class _TrendChartPainter extends CustomPainter {
  _TrendChartPainter({
    required this.points,
    required this.lineColor,
    required this.baselineColor,
    required this.dotFill,
  });

  /// Always non-empty, always non-null values.
  final List<double> points;
  final Color lineColor;
  final Color baselineColor;
  final Color dotFill;

  static const double _inset = 10;

  @override
  void paint(Canvas canvas, Size size) {
    if (size.width <= 0 || size.height <= 0) return;

    final double top = _inset;
    final double bottom = size.height - _inset;
    final double left = _inset;
    final double right = size.width - _inset;
    if (right <= left || bottom <= top) return;

    // Faint baseline, the only chrome this chart gets.
    canvas.drawLine(
      Offset(left, bottom),
      Offset(right, bottom),
      Paint()
        ..color = baselineColor
        ..strokeWidth = 1,
    );

    double lo = points.reduce((double a, double b) => a < b ? a : b);
    double hi = points.reduce((double a, double b) => a > b ? a : b);
    final double span = hi - lo;
    final double pad = span == 0 ? 5.0 : span * 0.18;
    lo = (lo - pad).clamp(0.0, 100.0).toDouble();
    hi = (hi + pad).clamp(0.0, 100.0).toDouble();
    final double domain = hi - lo <= 0 ? 1.0 : hi - lo;

    double yFor(double value) =>
        bottom - ((value - lo) / domain).clamp(0.0, 1.0) * (bottom - top);

    // A single session has no horizontal span to walk, so it is centred.
    final List<Offset> plotted = <Offset>[
      for (int i = 0; i < points.length; i++)
        Offset(
          points.length == 1
              ? (left + right) / 2
              : left + (right - left) * (i / (points.length - 1)),
          yFor(points[i]),
        ),
    ];

    if (plotted.length > 1) {
      final Path line = Path()..moveTo(plotted.first.dx, plotted.first.dy);
      for (final Offset point in plotted.skip(1)) {
        line.lineTo(point.dx, point.dy);
      }

      final Path area = Path.from(line)
        ..lineTo(plotted.last.dx, bottom)
        ..lineTo(plotted.first.dx, bottom)
        ..close();

      canvas.drawPath(
        area,
        Paint()
          ..shader = LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: <Color>[
              lineColor.withValues(alpha: 0.28),
              lineColor.withValues(alpha: 0.0),
            ],
          ).createShader(Rect.fromLTRB(left, top, right, bottom)),
      );

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

    final Paint dotStroke = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2
      ..color = lineColor;
    final Paint dotCore = Paint()..color = dotFill;

    for (int i = 0; i < plotted.length; i++) {
      final bool isLast = i == plotted.length - 1;
      if (isLast) {
        // The newest session is the one the user came to see.
        canvas.drawCircle(
          plotted[i],
          9,
          Paint()..color = lineColor.withValues(alpha: 0.22),
        );
        canvas.drawCircle(plotted[i], 4.5, Paint()..color = lineColor);
      } else {
        canvas.drawCircle(plotted[i], 3, dotCore);
        canvas.drawCircle(plotted[i], 3, dotStroke);
      }
    }
  }

  @override
  bool shouldRepaint(_TrendChartPainter old) =>
      !_sameValues(old.points, points) ||
      old.lineColor != lineColor ||
      old.baselineColor != baselineColor ||
      old.dotFill != dotFill;

  static bool _sameValues(List<double> a, List<double> b) {
    if (a.length != b.length) return false;
    for (int i = 0; i < a.length; i++) {
      if (a[i] != b[i]) return false;
    }
    return true;
  }
}
