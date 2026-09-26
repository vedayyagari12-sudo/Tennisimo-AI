import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/theme/app_theme.dart';

/// Regression coverage for invisible status-bar icons.
///
/// The bug: `appBarTheme` sets a TRANSPARENT background and set no
/// `systemOverlayStyle`, so `AppBar` fell back to
/// `ThemeData.estimateBrightnessForColor(Colors.transparent)`. That calls
/// `Color.computeLuminance()`, which ignores alpha — `0x00000000` has RGB 0,
/// luminance 0, so every screen was judged a dark canvas and got WHITE icons,
/// including on the two light canvases where they are unreadable.
///
/// These tests assert the style is stated explicitly, and stated from the
/// canvas the theme actually is.
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

  group('systemOverlayStyleFor', () {
    test('a light canvas asks Android for DARK icons', () {
      expect(
        systemOverlayStyleFor(Brightness.light).statusBarIconBrightness,
        Brightness.dark,
      );
    });

    test('a light canvas tells iOS the BACKGROUND is light', () {
      // The inverted pair. iOS is told the brightness of the bar itself and
      // derives its icon colour; Android is told the icons directly. The
      // framework's own SystemUiOverlayStyle.dark — "intended for applications
      // with a light background" — pairs them exactly this way.
      expect(
        systemOverlayStyleFor(Brightness.light).statusBarBrightness,
        Brightness.light,
      );
      expect(
        systemOverlayStyleFor(Brightness.light).statusBarIconBrightness,
        SystemUiOverlayStyle.dark.statusBarIconBrightness,
      );
      expect(
        systemOverlayStyleFor(Brightness.light).statusBarBrightness,
        SystemUiOverlayStyle.dark.statusBarBrightness,
      );
    });

    test('a dark canvas is the exact mirror', () {
      final SystemUiOverlayStyle style = systemOverlayStyleFor(Brightness.dark);
      expect(style.statusBarIconBrightness, Brightness.light);
      expect(style.statusBarBrightness, Brightness.dark);
      expect(
        style.statusBarIconBrightness,
        SystemUiOverlayStyle.light.statusBarIconBrightness,
      );
    });

    test('the two fields are never equal, on either canvas', () {
      for (final Brightness brightness in Brightness.values) {
        final SystemUiOverlayStyle style = systemOverlayStyleFor(brightness);
        expect(
          style.statusBarIconBrightness,
          isNot(style.statusBarBrightness),
          reason: 'the Android and iOS fields are inverted by definition',
        );
      }
    });
  });

  group('the app bar carries it', () {
    for (final Brightness brightness in Brightness.values) {
      test('$brightness theme states a style instead of leaving it to the '
          'guess', () {
        final ThemeData theme = buildAppTheme(brightness: brightness);
        expect(theme.appBarTheme.systemOverlayStyle, isNotNull);
        expect(
          theme.appBarTheme.systemOverlayStyle,
          systemOverlayStyleFor(brightness),
        );
      });
    }

    testWidgets('a light-canvas AppBar renders dark icons, not white ones',
        (WidgetTester tester) async {
      await tester.pumpWidget(MaterialApp(
        theme: buildAppTheme(brightness: Brightness.light),
        home: Scaffold(appBar: AppBar(title: const Text('x'))),
      ));

      final AnnotatedRegion<SystemUiOverlayStyle> region = tester.widget(
        find.byType(AnnotatedRegion<SystemUiOverlayStyle>).first,
      );
      expect(region.value.statusBarIconBrightness, Brightness.dark);
    });
  });
}
