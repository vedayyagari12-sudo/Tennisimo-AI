/// The app is light-only, and a device in dark mode does not change that.
///
/// This is the guard on a deliberate decision. `MaterialApp` is given one
/// theme and no `darkTheme`, so there is no dark canvas for the framework to
/// choose; these tests pump the real app widget with the platform forced to
/// `Brightness.dark` and assert the light surface is still what gets painted.
/// They also assert the root status-bar overlay style, because a style keyed to
/// a brightness that no longer varies is where white-icons-on-near-white hid.
library;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/main.dart';
import 'package:tennisimo_ai/theme/app_theme.dart';
import 'package:tennisimo_ai/theme/brand.dart';

final AppPalette _light = paletteFor(kBrandFlavor);

/// The real app widget, with a stand-in home so nothing touches Supabase.
Widget _app() => const TennisimoApp(home: Scaffold(body: SizedBox.shrink()));

void main() {
  group('there is no dark theme to reach', () {
    testWidgets('the app supplies a light theme and no darkTheme',
        (WidgetTester tester) async {
      await tester.pumpWidget(_app());

      final MaterialApp app = tester.widget(find.byType(MaterialApp));
      expect(app.darkTheme, isNull);
      expect(app.highContrastDarkTheme, isNull);
      expect(app.themeMode, ThemeMode.light);
      expect(app.theme!.brightness, Brightness.light);
    });

    test('every palette the binary can compile is light', () {
      expect(kAllPalettes, hasLength(2));
      for (final AppPalette palette in kAllPalettes) {
        expect(palette.brightness, Brightness.light);
      }
    });
  });

  group('a device in dark mode still renders light', () {
    testWidgets('the theme, the palette and the scaffold are all the light set',
        (WidgetTester tester) async {
      addTearDown(tester.platformDispatcher.clearPlatformBrightnessTestValue);
      tester.platformDispatcher.platformBrightnessTestValue = Brightness.dark;

      await tester.pumpWidget(_app());
      await tester.pumpAndSettle();

      final BuildContext context = tester.element(find.byType(SizedBox).first);
      expect(MediaQuery.platformBrightnessOf(context), Brightness.dark);
      expect(Theme.of(context).brightness, Brightness.light);
      expect(context.palette, same(_light));

      final Material canvas = tester.widget(
        find.descendant(
          of: find.byType(Scaffold),
          matching: find.byType(Material),
        ),
      );
      expect(canvas.color, _light.surface);
    });

    testWidgets('the root overlay style is the light-canvas one',
        (WidgetTester tester) async {
      addTearDown(tester.platformDispatcher.clearPlatformBrightnessTestValue);
      tester.platformDispatcher.platformBrightnessTestValue = Brightness.dark;

      await tester.pumpWidget(_app());
      await tester.pumpAndSettle();

      final AnnotatedRegion<SystemUiOverlayStyle> region = tester.widget(
        find.byType(AnnotatedRegion<SystemUiOverlayStyle>).first,
      );
      expect(region.value, lightCanvasOverlayStyle());
      // Dark icons on a light bar, the whole point of the fix.
      expect(region.value.statusBarIconBrightness, Brightness.dark);
    });
  });
}
