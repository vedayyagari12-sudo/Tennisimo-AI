/// The web / mobile gate on the dark theme.
///
/// The mobile app is light-only and `light_only_test.dart` guards that on its
/// own terms. This file guards the gate itself: that a stored dark choice, a
/// live controller and a dark platform together still cannot reach a dark
/// canvas off the web, and that the web branch really does honour them.
///
/// ## What a VM test runner can and cannot prove
///
/// `flutter test` runs on the Dart VM, where the compile-time constant
/// `kIsWeb` is false; that is asserted below, so every test here that uses
/// the production default IS the mobile path, exactly as a phone runs it.
/// `kIsWeb` cannot be made true in this runner, so the web branch is driven
/// through `TennisimoApp.isWeb`, a `@visibleForTesting` parameter whose
/// default is `kIsWeb`. What that cannot cover is `main()` itself (it needs a
/// live Supabase and a real web build), so the one `if (kIsWeb)` in `main()`
/// is covered by reading, not by a test.
library;

import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:tennisimo_ai/main.dart';
import 'package:tennisimo_ai/screens/dashboard_screen.dart';
import 'package:tennisimo_ai/theme/app_theme.dart';
import 'package:tennisimo_ai/theme/brand.dart';
import 'package:tennisimo_ai/theme/theme_controller.dart';

import 'support/dashboard_fixtures.dart';

final AppPalette _light = paletteFor(kBrandFlavor);
final AppPalette _dark = darkPaletteFor(kBrandFlavor);

const Widget _home = Scaffold(body: SizedBox.shrink());

/// A controller that has loaded a stored DARK choice, as a returning web user
/// who picked dark would have.
Future<ThemeController> _storedDark() async {
  SharedPreferences.setMockInitialValues(<String, Object>{
    kThemeModePrefsKey: encodeThemeMode(ThemeMode.dark),
  });
  final ThemeController controller = ThemeController();
  await controller.load();
  expect(controller.mode, ThemeMode.dark, reason: 'the fixture is not dark');
  return controller;
}

String _hex(Color c) =>
    '#${(c.toARGB32() & 0xFFFFFF).toRadixString(16).padLeft(6, '0').toUpperCase()}';

Future<void> _openMenu(WidgetTester tester) async {
  await tester.tap(find.byIcon(Icons.more_vert));
  for (int i = 0; i < 10; i++) {
    await tester.pump(const Duration(milliseconds: 100));
  }
}

Future<void> _settle(WidgetTester tester) async {
  for (int i = 0; i < 20; i++) {
    await tester.pump(const Duration(milliseconds: 100));
  }
}

void main() {
  test('this runner is the mobile path: kIsWeb is false', () {
    expect(kIsWeb, isFalse);
  });

  group('resolveAppTheme, the gate', () {
    for (final ThemeMode requested in ThemeMode.values) {
      test('off the web, "${requested.name}" still resolves light-only', () {
        final AppThemeConfig config =
            resolveAppTheme(isWeb: false, requested: requested);
        expect(config.darkTheme, isNull);
        expect(config.themeMode, ThemeMode.light);
        expect(config.theme.brightness, Brightness.light);
        expect(config.theme.extension<AppPalette>(), same(_light));
      });

      test('on the web, "${requested.name}" is honoured', () {
        final AppThemeConfig config =
            resolveAppTheme(isWeb: true, requested: requested);
        expect(config.themeMode, requested);
        expect(config.theme.extension<AppPalette>(), same(_light));
        expect(config.darkTheme, isNotNull);
        expect(config.darkTheme!.brightness, Brightness.dark);
        expect(config.darkTheme!.extension<AppPalette>(), same(_dark));
      });
    }
  });

  group('mobile ignores a stored dark choice', () {
    testWidgets('stored dark + dark platform + a controller: still light',
        (WidgetTester tester) async {
      final ThemeController controller = await _storedDark();
      addTearDown(tester.platformDispatcher.clearPlatformBrightnessTestValue);
      tester.platformDispatcher.platformBrightnessTestValue = Brightness.dark;

      await tester.pumpWidget(
        TennisimoApp(home: _home, themeController: controller),
      );
      await tester.pumpAndSettle();

      final MaterialApp app = tester.widget(find.byType(MaterialApp));
      expect(app.darkTheme, isNull);
      expect(app.themeMode, ThemeMode.light);

      final BuildContext context = tester.element(find.byType(SizedBox).first);
      expect(Theme.of(context).brightness, Brightness.light);
      expect(context.palette, same(_light));
      // No scope, so nothing below can find a controller to offer.
      expect(find.byType(ThemeScope), findsNothing);
      expect(ThemeScope.maybeOf(context), isNull);

      final AnnotatedRegion<SystemUiOverlayStyle> region = tester.widget(
        find.byType(AnnotatedRegion<SystemUiOverlayStyle>).first,
      );
      expect(region.value, lightCanvasOverlayStyle());
    });

    testWidgets('changing the choice at runtime changes nothing',
        (WidgetTester tester) async {
      final ThemeController controller = await _storedDark();
      await tester.pumpWidget(
        TennisimoApp(home: _home, themeController: controller),
      );
      await controller.setMode(ThemeMode.system);
      await controller.setMode(ThemeMode.dark);
      await tester.pumpAndSettle();

      final MaterialApp app = tester.widget(find.byType(MaterialApp));
      expect(app.darkTheme, isNull);
      expect(app.themeMode, ThemeMode.light);
    });

    testWidgets('the dashboard menu offers no theme choice',
        (WidgetTester tester) async {
      final ThemeController controller = await _storedDark();
      tester.view.physicalSize = const Size(412, 915);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);

      await tester.pumpWidget(
        TennisimoApp(
          themeController: controller,
          home: DashboardScreen(dataSource: forehandOnly().source),
        ),
      );
      await _settle(tester);
      await _openMenu(tester);

      expect(find.text('Sign out'), findsOneWidget);
      expect(find.text('Dark'), findsNothing);
      expect(find.text('Light'), findsNothing);
      expect(find.text('Follow system'), findsNothing);
    });
  });

  group('the web branch', () {
    testWidgets('a stored dark choice is attached and shown',
        (WidgetTester tester) async {
      final ThemeController controller = await _storedDark();

      await tester.pumpWidget(
        TennisimoApp(home: _home, themeController: controller, isWeb: true),
      );
      await tester.pumpAndSettle();

      final MaterialApp app = tester.widget(find.byType(MaterialApp));
      expect(app.darkTheme, isNotNull);
      expect(app.themeMode, ThemeMode.dark);
      final BuildContext context = tester.element(find.byType(SizedBox).first);
      expect(context.palette, same(_dark));
      expect(ThemeScope.maybeOf(context), same(controller));
    });

    testWidgets('a runtime change repaints, and system follows the platform',
        (WidgetTester tester) async {
      final ThemeController controller = await _storedDark();
      addTearDown(tester.platformDispatcher.clearPlatformBrightnessTestValue);

      await tester.pumpWidget(
        TennisimoApp(home: _home, themeController: controller, isWeb: true),
      );
      await controller.setMode(ThemeMode.light);
      await tester.pumpAndSettle();
      BuildContext context = tester.element(find.byType(SizedBox).first);
      expect(context.palette, same(_light));

      tester.platformDispatcher.platformBrightnessTestValue = Brightness.dark;
      await controller.setMode(ThemeMode.system);
      await tester.pumpAndSettle();
      context = tester.element(find.byType(SizedBox).first);
      expect(context.palette, same(_dark));
    });

    testWidgets('the canvas change is instant under reduced motion',
        (WidgetTester tester) async {
      addTearDown(
          tester.platformDispatcher.clearAccessibilityFeaturesTestValue);
      final ThemeController controller = ThemeController(
        store: const PreferencesThemeModeStore(),
      );
      SharedPreferences.setMockInitialValues(<String, Object>{});

      await tester.pumpWidget(
        TennisimoApp(home: _home, themeController: controller, isWeb: true),
      );
      MaterialApp app = tester.widget(find.byType(MaterialApp));
      expect(app.themeAnimationDuration, kThemeAnimationDuration);

      tester.platformDispatcher.accessibilityFeaturesTestValue =
          const FakeAccessibilityFeatures(disableAnimations: true);
      await tester.pump();
      app = tester.widget(find.byType(MaterialApp));
      expect(app.themeAnimationDuration, Duration.zero);
    });

    testWidgets('the dashboard menu offers the choice, and it persists',
        (WidgetTester tester) async {
      SharedPreferences.setMockInitialValues(<String, Object>{});
      final ThemeController controller = ThemeController();
      tester.view.physicalSize = const Size(412, 915);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);

      await tester.pumpWidget(
        TennisimoApp(
          themeController: controller,
          isWeb: true,
          home: DashboardScreen(dataSource: forehandOnly().source),
        ),
      );
      await _settle(tester);
      await _openMenu(tester);

      expect(find.text('Follow system'), findsOneWidget);
      expect(find.text('Light'), findsOneWidget);
      expect(find.text('Dark'), findsOneWidget);
      expect(find.text('Sign out'), findsOneWidget);
      final CheckedPopupMenuItem<String> system = tester.widget(
        find.ancestor(
          of: find.text('Follow system'),
          matching: find.byType(CheckedPopupMenuItem<String>),
        ),
      );
      expect(system.checked, isTrue, reason: 'the default is follow-system');

      await tester.tap(find.text('Dark'));
      await _settle(tester);

      expect(controller.mode, ThemeMode.dark);
      final SharedPreferences prefs = await SharedPreferences.getInstance();
      expect(prefs.getString(kThemeModePrefsKey), 'dark');
      final BuildContext context =
          tester.element(find.byType(DashboardScreen));
      expect(context.palette, same(_dark));
    });
  });

  group('web/index.html reads the same entry the app writes', () {
    final String html = File('web/index.html').readAsStringSync();

    test('the localStorage key is the plugin prefix plus the app key', () {
      // shared_preferences on web stores under `flutter.<key>`.
      expect(
        html,
        contains("localStorage.getItem('flutter.$kThemeModePrefsKey')"),
      );
      // ...JSON-encoded, so the script must parse it.
      expect(html, contains('JSON.parse(raw)'));
    });

    test('the mode literals match the stored form', () {
      expect(html, contains("mode === '${encodeThemeMode(ThemeMode.dark)}'"));
      expect(html, contains("mode !== '${encodeThemeMode(ThemeMode.light)}'"));
    });

    test('both canvases are the web build\'s real surfaces', () {
      // The web build is the alternate flavor (see README.md).
      expect(html, contains('background-color: ${_hex(schoolLight.surface)}'));
      expect(html, contains('background-color: ${_hex(schoolDark.surface)}'));
      expect(
        html,
        contains("setAttribute('content', '${_hex(schoolDark.surface)}')"),
      );
    });
  });
}
