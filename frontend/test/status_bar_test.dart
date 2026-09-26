import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/theme/app_theme.dart';
import 'package:tennisimo_ai/theme/brand.dart';

/// Regression coverage for invisible status-bar icons.
///
/// The bug: `appBarTheme` sets a TRANSPARENT background and set no
/// `systemOverlayStyle`, so `AppBar` fell back to
/// `ThemeData.estimateBrightnessForColor(Colors.transparent)`. That calls
/// `Color.computeLuminance()`, which ignores alpha — `0x00000000` has RGB 0,
/// luminance 0, so every screen was judged a dark canvas and got WHITE icons,
/// unreadable on the light canvas this app actually ships.
///
/// The app is light-only, so the answer is a constant — which is precisely
/// why it has to be asserted: a style keyed to a brightness that never varies
/// is where this bug hid the first time. These tests assert the style is stated
/// explicitly, and stated for a light canvas.
void main() {
  group('the fallback that caused the bug', () {
    test('a transparent background really is estimated as dark', () {
      // Not an assumption about the framework: the actual call an AppBar with
      // no systemOverlayStyle makes, asserted here so this test starts failing
      // if Flutter ever fixes the guess and the workaround can be revisited.
      expect(
        ThemeData.estimateBrightnessForColor(Colors.transparent),
        Brightness.dark,
      );
    });
  });

  group('lightCanvasOverlayStyle', () {
    test('asks Android for DARK icons', () {
      expect(
        lightCanvasOverlayStyle().statusBarIconBrightness,
        Brightness.dark,
      );
    });

    test('tells iOS the BACKGROUND is light', () {
      // The inverted pair. iOS is told the brightness of the bar itself and
      // derives its icon colour; Android is told the icons directly. The
      // framework's own SystemUiOverlayStyle.dark — "intended for applications
      // with a light background" — pairs them exactly this way.
      expect(lightCanvasOverlayStyle().statusBarBrightness, Brightness.light);
      expect(
        lightCanvasOverlayStyle().statusBarIconBrightness,
        SystemUiOverlayStyle.dark.statusBarIconBrightness,
      );
      expect(
        lightCanvasOverlayStyle().statusBarBrightness,
        SystemUiOverlayStyle.dark.statusBarBrightness,
      );
    });

    test('the two fields are never equal', () {
      final SystemUiOverlayStyle style = lightCanvasOverlayStyle();
      expect(
        style.statusBarIconBrightness,
        isNot(style.statusBarBrightness),
        reason: 'the Android and iOS fields are inverted by definition',
      );
    });

    test('the navigation bar is painted from the light surface', () {
      expect(
        lightCanvasOverlayStyle().systemNavigationBarColor,
        paletteFor(kBrandFlavor).surface,
      );
    });
  });

  group('the app bar carries it', () {
    test('the theme states a style instead of leaving it to the guess', () {
      final ThemeData theme = buildAppTheme();
      expect(theme.appBarTheme.systemOverlayStyle, isNotNull);
      expect(theme.appBarTheme.systemOverlayStyle, lightCanvasOverlayStyle());
    });

    testWidgets('an AppBar renders dark icons, not white ones',
        (WidgetTester tester) async {
      await tester.pumpWidget(MaterialApp(
        theme: buildAppTheme(),
        home: Scaffold(appBar: AppBar(title: const Text('x'))),
      ));

      final AnnotatedRegion<SystemUiOverlayStyle> region = tester.widget(
        find.byType(AnnotatedRegion<SystemUiOverlayStyle>).first,
      );
      expect(region.value.statusBarIconBrightness, Brightness.dark);
    });
  });
}
