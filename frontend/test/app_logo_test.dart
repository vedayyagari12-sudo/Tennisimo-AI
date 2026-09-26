import 'dart:ui' show PictureRecorder;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/theme/app_theme.dart';
import 'package:tennisimo_ai/theme/brand.dart';
import 'package:tennisimo_ai/widgets/app_logo.dart';

/// The compiled flavor's palette — the one and only canvas. These tokens are
/// not `const` (the palette is reached through a function), which is why the
/// `const` is off the call sites below.
final AppPalette _palette = paletteFor(kBrandFlavor);

/// The mark has to be the *same drawing* as the launcher icon, so the geometry
/// is asserted numerically rather than eyeballed: ball radius 0.400 of the
/// side, seams as arcs of ovals reaching 1.25R vertically and 2.00R outward
/// with a 0.028 stroke, then the bolt's separation edge and the bolt itself.
Widget _host(Widget child, {Color? background}) => MaterialApp(
      theme: buildAppTheme(),
      home: Scaffold(
        backgroundColor: background,
        body: Center(child: child),
      ),
    );

void main() {
  group('AppLogo', () {
    testWidgets('occupies exactly the square it is given', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(_host(const AppLogo(size: 64)));

      expect(tester.getSize(find.byType(AppLogo)), const Size(64, 64));
    });

    testWidgets('draws the ball, both seams and the two bolt passes', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(_host(const AppLogo(size: 125)));

      // side 125 -> centre (62.5, 62.5), R = 50, seam stroke = 3.5.
      expect(
        find.byType(AppLogo),
        paints
          ..circle(
            x: 62.5,
            y: 62.5,
            radius: 50,
            color: _palette.ballAccent,
          )
          ..arc(
            rect: const Rect.fromLTRB(-37.5, 0, 45, 125),
            color: _palette.surface,
            strokeWidth: 3.5,
            style: PaintingStyle.stroke,
          )
          ..arc(
            rect: const Rect.fromLTRB(80, 0, 162.5, 125),
            color: _palette.surface,
            strokeWidth: 3.5,
            style: PaintingStyle.stroke,
          )
          // The oversized separation edge, then the bolt.
          ..path(color: _palette.surface)
          ..path(color: _palette.primaryFill),
      );
    });

    testWidgets('the seams and bolt edge take the background override', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(
        _host(AppLogo(size: 100, background: _palette.surfaceContainer)),
      );

      expect(
        find.byType(AppLogo),
        paints
          ..circle(color: _palette.ballAccent)
          ..arc(color: _palette.surfaceContainer)
          ..arc(color: _palette.surfaceContainer)
          ..path(color: _palette.surfaceContainer)
          ..path(color: _palette.primaryFill),
      );
    });

    testWidgets('scales purely by ratio: half the size, half the radius', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(_host(const AppLogo(size: 60)));

      expect(
        find.byType(AppLogo),
        paints..circle(x: 30, y: 30, radius: 24),
      );
    });
  });

  group('AppLogoPainter', () {
    test('repaints when any of its three colours changes', () {
      AppLogoPainter painter(AppPalette p, {Color? background}) =>
          AppLogoPainter(
            background: background ?? p.surface,
            ball: p.ballAccent,
            bolt: p.primaryFill,
          );

      final AppLogoPainter a = painter(_palette);
      final AppLogoPainter b = painter(_palette);
      final AppLogoPainter c =
          painter(_palette, background: _palette.surfaceContainer);
      // Ball and bolt changing while the background stays put: if
      // shouldRepaint only watched the background, the mark would keep the old
      // ball on screen.
      final AppLogoPainter d = painter(
        _palette.copyWith(
          ballAccent: _palette.secondary,
          primaryFill: _palette.primary,
        ),
        background: _palette.surface,
      );

      expect(a.shouldRepaint(b), isFalse);
      expect(a.shouldRepaint(c), isTrue);
      expect(a.shouldRepaint(d), isTrue);
    });

    test('an empty box paints nothing rather than throwing', () {
      final PictureRecorder recorder = PictureRecorder();
      AppLogoPainter(
        background: _palette.surface,
        ball: _palette.ballAccent,
        bolt: _palette.primaryFill,
      ).paint(Canvas(recorder), Size.zero);

      expect(recorder.endRecording(), isNotNull);
    });
  });
}
