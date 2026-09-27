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

/// The mark: a ball with two seams and a bolt through it.
///
/// The two fills follow the build-time brand flavor: the ball takes
/// [AppPalette.ballAccent] and the bolt [AppPalette.primaryFill], so the
/// default build draws a chartreuse ball with a green bolt and the alternate
/// palette draws a gold ball with a blue bolt. The ball keeps the
/// warm, ball-coloured token in both because a tennis ball that is not
/// ball-coloured stops reading as a ball; the bolt is the accent that moves.
///
/// Both are fill-tier tokens. The alternate flavor's gold ball is a light fill
/// (see [AppPalette]): it stands apart from the canvas by hue rather than by
/// luminance contrast.
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
    // Read through the theme, so flipping light/dark rebuilds the mark instead
    // of leaving a ball stepped for the other canvas on screen.
    final AppPalette palette = context.palette;
    final Color resolved = background ?? palette.surface;
    return SizedBox(
      width: size,
      height: size,
      child: CustomPaint(
        painter: AppLogoPainter(
          background: resolved,
          ball: palette.ballAccent,
          bolt: palette.primaryFill,
        ),
        isComplex: false,
      ),
    );
  }
}

/// Paints the mark. Public so it can be exercised directly in tests.
class AppLogoPainter extends CustomPainter {
  const AppLogoPainter({
    required this.background,
    required this.ball,
    required this.bolt,
  });

  /// The colour of the seams and of the bolt's separation edge.
  final Color background;

  /// The ball fill. Fill tier — this is a large solid area.
  final Color ball;

  /// The bolt fill. Fill tier, for the same reason.
  final Color bolt;

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

    // The ball. `ball`, not a token read here: a painter that reached for a
    // global would keep the pre-switch colour until something else repainted
    // it.
    canvas.drawCircle(
      centre,
      r,
      Paint()
        ..color = ball
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
    final List<Offset> boltOutline = _boltPoints(centre, r);

    canvas.drawPath(
      _polygon(_scaleAbout(boltOutline, centre, _boltEdgeScale)),
      Paint()
        ..color = background
        ..isAntiAlias = true,
    );
    canvas.drawPath(
      _polygon(boltOutline),
      Paint()
        ..color = bolt
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
      oldDelegate.background != background ||
      oldDelegate.ball != ball ||
      oldDelegate.bolt != bolt;
}
