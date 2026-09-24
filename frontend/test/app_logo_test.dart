import 'dart:ui' show PictureRecorder;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/theme/app_theme.dart';
import 'package:tennisimo_ai/widgets/app_logo.dart';

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
            color: AppColors.ballAccent,
          )
          ..arc(
            rect: const Rect.fromLTRB(-37.5, 0, 45, 125),
            color: AppColors.surface,
            strokeWidth: 3.5,
            style: PaintingStyle.stroke,
          )
          ..arc(
            rect: const Rect.fromLTRB(80, 0, 162.5, 125),
            color: AppColors.surface,
            strokeWidth: 3.5,
            style: PaintingStyle.stroke,
          )
          // The oversized separation edge, then the bolt.
          ..path(color: AppColors.surface)
          ..path(color: AppColors.primary),
      );
    });

    testWidgets('the seams and bolt edge take the background override', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(
        _host(const AppLogo(size: 100, background: AppColors.surfaceContainer)),
      );

      expect(
        find.byType(AppLogo),
        paints
          ..circle(color: AppColors.ballAccent)
          ..arc(color: AppColors.surfaceContainer)
          ..arc(color: AppColors.surfaceContainer)
          ..path(color: AppColors.surfaceContainer)
          ..path(color: AppColors.primary),
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
    test('repaints only when the background colour changes', () {
      const AppLogoPainter a = AppLogoPainter(background: AppColors.surface);
      const AppLogoPainter b = AppLogoPainter(background: AppColors.surface);
      const AppLogoPainter c =
          AppLogoPainter(background: AppColors.surfaceContainer);

      expect(a.shouldRepaint(b), isFalse);
      expect(a.shouldRepaint(c), isTrue);
    });

    test('an empty box paints nothing rather than throwing', () {
      final PictureRecorder recorder = PictureRecorder();
      const AppLogoPainter(background: AppColors.surface)
          .paint(Canvas(recorder), Size.zero);

      expect(recorder.endRecording(), isNotNull);
    });
  });
}
