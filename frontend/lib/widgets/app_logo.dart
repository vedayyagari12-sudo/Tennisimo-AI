/// The Tennisimo AI brand mark, drawn in code.
///
/// This is the same drawing as the launcher icon: identical geometry, so the
/// home-screen icon and the in-app mark are one mark, not two that drift.
/// Everything is expressed as a ratio of the widget's side, so it stays crisp
/// at any size.
library;

import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

/// The mark: a chartreuse ball with two seams and a green bolt through it.
///
/// The seams and the bolt's separation edge are painted in [background], which
/// defaults to the theme surface — pass the card colour when the mark sits on
/// a card so those cuts read as gaps rather than as dark strokes.
class AppLogo extends StatelessWidget {
  const AppLogo({super.key, this.size = 40, this.background});

  /// The side of the square the mark is drawn in, in logical pixels.
  final double size;

  /// The colour the mark is sitting on. Defaults to the theme surface.
  final Color? background;

  @override
  Widget build(BuildContext context) {
    final Color resolved =
        background ?? Theme.of(context).colorScheme.surface;
    return SizedBox(
      width: size,
      height: size,
      child: CustomPaint(
        painter: AppLogoPainter(background: resolved),
        isComplex: false,
      ),
    );
  }
}

/// Paints the mark. Public so it can be exercised directly in tests.
class AppLogoPainter extends CustomPainter {
  const AppLogoPainter({required this.background});

  /// The colour of the seams and of the bolt's separation edge.
  final Color background;

  /// Ball radius, as a fraction of the side.
  static const double _ballRadiusRatio = 0.400;

  /// Seam stroke width, as a fraction of the side.
  static const double _seamWidthRatio = 0.028;

  /// The bolt outline in unit coordinates of its own box.
  static const List<Offset> _boltUnit = <Offset>[
    Offset(0.58, 0.00),
    Offset(0.14, 0.57),
    Offset(0.48, 0.57),
    Offset(0.50, 0.98),
    Offset(0.86, 0.41),
    Offset(0.54, 0.41),
    Offset(0.84, 0.00),
  ];

  /// The bolt box, as fractions of the ball radius.
  static const double _boltWidthRatio = 0.74;
  static const double _boltHeightRatio = 1.92;

  /// Clockwise rotation of the bolt about the centre, in degrees.
  static const double _boltRotationDegrees = 14;

  /// How much the separation edge oversizes the bolt.
  static const double _boltEdgeScale = 1.09;

  @override
  void paint(Canvas canvas, Size size) {
    final double side = math.min(size.width, size.height);
    if (side <= 0) return;

    final Offset centre = Offset(size.width / 2, size.height / 2);
    final double r = side * _ballRadiusRatio;

    // The ball.
    canvas.drawCircle(
      centre,
      r,
      Paint()
        ..color = AppColors.ballChartreuse
        ..isAntiAlias = true,
    );

    // Two seams in the outer third: arcs of tall ovals whose near edge sits at
    // 0.35R, so they curve across the ball without slicing through the centre.
    final Paint seam = Paint()
      ..color = background
      ..style = PaintingStyle.stroke
      ..strokeWidth = side * _seamWidthRatio
      ..isAntiAlias = true;

    canvas.drawArc(
      Rect.fromLTRB(
        centre.dx - 2.00 * r,
        centre.dy - 1.25 * r,
        centre.dx - 0.35 * r,
        centre.dy + 1.25 * r,
      ),
      _radians(-40),
      _radians(80),
      false,
      seam,
    );
    canvas.drawArc(
      Rect.fromLTRB(
        centre.dx + 0.35 * r,
        centre.dy - 1.25 * r,
        centre.dx + 2.00 * r,
        centre.dy + 1.25 * r,
      ),
      _radians(140),
      _radians(80),
      false,
      seam,
    );

    // The bolt, drawn twice: an oversized copy in the background colour cuts it
    // free of the ball, then the bolt itself on top.
    final List<Offset> bolt = _boltPoints(centre, r);

    canvas.drawPath(
      _polygon(_scaleAbout(bolt, centre, _boltEdgeScale)),
      Paint()
        ..color = background
        ..isAntiAlias = true,
    );
    canvas.drawPath(
      _polygon(bolt),
      Paint()
        ..color = AppColors.primary
        ..isAntiAlias = true,
    );
  }

  /// The bolt outline in canvas coordinates: unit box -> sized box centred on
  /// [centre], then rotated clockwise about [centre].
  List<Offset> _boltPoints(Offset centre, double r) {
    final double w = r * _boltWidthRatio;
    final double h = r * _boltHeightRatio;
    final Iterable<Offset> placed = _boltUnit.map(
      (Offset u) => Offset(
        centre.dx - w / 2 + u.dx * w,
        centre.dy - h / 2 + u.dy * h,
      ),
    );
    return _rotateAbout(placed, centre, _boltRotationDegrees);
  }

  static Path _polygon(List<Offset> points) =>
      Path()..addPolygon(points, true);

  static List<Offset> _scaleAbout(
    Iterable<Offset> points,
    Offset centre,
    double factor,
  ) {
    return points
        .map((Offset p) => centre + (p - centre) * factor)
        .toList(growable: false);
  }

  /// Rotates clockwise by [degrees] in screen coordinates (y grows downward).
  static List<Offset> _rotateAbout(
    Iterable<Offset> points,
    Offset centre,
    double degrees,
  ) {
    final double a = _radians(degrees);
    final double cosA = math.cos(a);
    final double sinA = math.sin(a);
    return points.map((Offset p) {
      final double dx = p.dx - centre.dx;
      final double dy = p.dy - centre.dy;
      return Offset(
        centre.dx + dx * cosA - dy * sinA,
        centre.dy + dx * sinA + dy * cosA,
      );
    }).toList(growable: false);
  }

  static double _radians(double degrees) => degrees * math.pi / 180.0;

  @override
  bool shouldRepaint(AppLogoPainter oldDelegate) =>
      oldDelegate.background != background;
}
