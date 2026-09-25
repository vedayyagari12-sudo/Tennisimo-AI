import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

/// A circular score gauge.
///
/// A null [score] is the load-bearing case: the backend returns null for
/// "could not be measured", never 0, so the ring must not draw a zero-length
/// arc — that would read as a score of nothing earned. Null instead draws a
/// dashed, muted track with an em dash in the middle.
class ScoreRing extends StatelessWidget {
  const ScoreRing({
    super.key,
    required this.score,
    this.size = 148,
    this.strokeWidth = 12,
    this.label,
    this.delta,
  });

  /// 0-100, or null for not scored.
  final double? score;

  final double size;
  final double strokeWidth;

  /// Small caption under the numeral, shown when there is no [delta].
  final String? label;

  /// Difference against the previous session. Rendered as "+4.2 vs last".
  final double? delta;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final AppPalette palette = context.palette;
    final double? value = score;
    // The arc is a 12px-wide ring: a large filled area, so it takes the fill
    // tier, not the text step the numeral beside it would use.
    final Color arcColor = palette.scoreFillColor(value);
    final double target =
        value == null ? 0.0 : (value / 100).clamp(0.0, 1.0).toDouble();

    return SizedBox(
      width: size,
      height: size,
      child: TweenAnimationBuilder<double>(
        tween: Tween<double>(begin: 0.0, end: target),
        duration: const Duration(milliseconds: 600),
        curve: Curves.easeOutCubic,
        builder: (BuildContext context, double progress, Widget? child) {
          return CustomPaint(
            painter: _ScoreRingPainter(
              progress: progress,
              arcColor: arcColor,
              trackColor: theme.colorScheme.surfaceContainerHigh,
              dashedTrackColor: theme.colorScheme.outline,
              strokeWidth: strokeWidth,
              // No measurable score -> no arc at all, just a dashed track.
              dashed: value == null,
            ),
            child: child,
          );
        },
        child: Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              Text(
                // Never "0" for a null score.
                value == null ? '—' : value.round().toString(),
                style: theme.textTheme.displaySmall?.copyWith(
                  fontSize: size * 0.30,
                  height: 1.0,
                  color: value == null
                      ? theme.colorScheme.onSurfaceVariant
                      : theme.colorScheme.onSurface,
                  fontFeatures: kTabularFigures,
                ),
              ),
              const SizedBox(height: 4),
              _Caption(
                text: _captionText(),
                emphasised: delta != null,
                color: delta == null
                    ? theme.colorScheme.onSurfaceVariant
                    : (delta! >= 0
                        ? theme.colorScheme.primary
                        : theme.colorScheme.error),
              ),
            ],
          ),
        ),
      ),
    );
  }

  String? _captionText() {
    final double? d = delta;
    if (d != null) {
      final String sign = d >= 0 ? '+' : '−';
      return '$sign${d.abs().toStringAsFixed(1)} vs last';
    }
    return label;
  }
}

class _Caption extends StatelessWidget {
  const _Caption({
    required this.text,
    required this.emphasised,
    required this.color,
  });

  final String? text;
  final bool emphasised;
  final Color color;

  @override
  Widget build(BuildContext context) {
    final String? value = text;
    if (value == null) return const SizedBox.shrink();
    final ThemeData theme = Theme.of(context);
    return Text(
      value,
      textAlign: TextAlign.center,
      style: theme.textTheme.labelSmall?.copyWith(
        color: color,
        fontWeight: emphasised ? FontWeight.w600 : FontWeight.w500,
        fontFeatures: kTabularFigures,
      ),
    );
  }
}

class _ScoreRingPainter extends CustomPainter {
  const _ScoreRingPainter({
    required this.progress,
    required this.arcColor,
    required this.trackColor,
    required this.dashedTrackColor,
    required this.strokeWidth,
    required this.dashed,
  });

  final double progress;
  final Color arcColor;
  final Color trackColor;
  final Color dashedTrackColor;
  final double strokeWidth;
  final bool dashed;

  @override
  void paint(Canvas canvas, Size size) {
    final Offset centre = Offset(size.width / 2, size.height / 2);
    final double radius = (math.min(size.width, size.height) - strokeWidth) / 2;
    if (radius <= 0) return;
    final Rect rect = Rect.fromCircle(center: centre, radius: radius);

    if (dashed) {
      _paintDashedTrack(canvas, rect);
      return;
    }

    canvas.drawCircle(
      centre,
      radius,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = strokeWidth
        ..color = trackColor,
    );

    if (progress <= 0) return;
    canvas.drawArc(
      rect,
      -math.pi / 2,
      progress * 2 * math.pi,
      false,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeCap = StrokeCap.round
        ..strokeWidth = strokeWidth
        ..color = arcColor,
    );
  }

  void _paintDashedTrack(Canvas canvas, Rect rect) {
    const int dashes = 32;
    const double gapFraction = 0.42;
    final double step = 2 * math.pi / dashes;
    final Paint paint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round
      ..strokeWidth = strokeWidth * 0.55
      ..color = dashedTrackColor;
    for (int i = 0; i < dashes; i++) {
      canvas.drawArc(
        rect,
        -math.pi / 2 + i * step,
        step * (1 - gapFraction),
        false,
        paint,
      );
    }
  }

  @override
  bool shouldRepaint(_ScoreRingPainter old) =>
      old.progress != progress ||
      old.arcColor != arcColor ||
      old.trackColor != trackColor ||
      old.dashedTrackColor != dashedTrackColor ||
      old.strokeWidth != strokeWidth ||
      old.dashed != dashed;
}
