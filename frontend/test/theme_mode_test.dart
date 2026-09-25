/// The light / dark switch: the choice, its persistence, and the proof that
/// flipping it actually repaints the tree.
///
/// The last of those is the point. The risk in any theme-switch scheme is a
/// widget that captured a colour on its first build and never hears that the
/// canvas changed — a ring still drawn in the dark ramp on a tan background.
/// The runtime-switch group below pumps a real [MaterialApp], flips the mode,
/// and asserts the actual paint calls of two [CustomPainter]s and one
/// [Container], because those are the places a stale colour would survive.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/models/analysis_response.dart';
import 'package:tennisimo_ai/theme/app_theme.dart';
import 'package:tennisimo_ai/theme/brand.dart';
import 'package:tennisimo_ai/theme/theme_controller.dart';
import 'package:tennisimo_ai/widgets/app_logo.dart';
import 'package:tennisimo_ai/widgets/category_bars.dart';
import 'package:tennisimo_ai/widgets/score_ring.dart';

/// A store that lives in a field, so the controller can be exercised without a
/// plugin binding.
class _MemoryStore implements ThemeModeStore {
  _MemoryStore([this.stored]);

  ThemeMode? stored;
  int writes = 0;

  @override
  Future<ThemeMode?> read() async => stored;

  @override
  Future<void> write(ThemeMode mode) async {
    stored = mode;
    writes++;
  }
}

/// A store that fails both ways, the way a locked-down platform would.
class _BrokenStore implements ThemeModeStore {
  @override
  Future<ThemeMode?> read() async => throw StateError('no storage');

  @override
  Future<void> write(ThemeMode mode) async => throw StateError('no storage');
}

/// The same wiring `main.dart` uses: scope above, ListenableBuilder above
/// MaterialApp, both themes supplied, mode from the controller.
Widget _app(ThemeController controller, {required Widget home}) {
  return ThemeScope(
    controller: controller,
    child: ListenableBuilder(
      listenable: controller,
      builder: (BuildContext context, Widget? child) {
        return MaterialApp(
          theme: buildAppTheme(brightness: Brightness.light),
          darkTheme: buildAppTheme(brightness: Brightness.dark),
          themeMode: controller.mode,
          home: home,
        );
      },
    ),
  );
}

final AppPalette _dark = paletteFor(kBrandFlavor, Brightness.dark);
final AppPalette _light = paletteFor(kBrandFlavor, Brightness.light);

CategoryScore _category(double? score) => CategoryScore(
      category: 'swing_path',
      score: score,
      weight: 0.25,
      metricNames: const <String>['a_metric'],
      metricsAvailable: 1,
      metricsTotal: 1,
    );

void main() {
  group('the stored form of the choice', () {
    test('every mode round-trips through its own wire value', () {
      for (final ThemeMode mode in ThemeMode.values) {
        expect(decodeThemeMode(encodeThemeMode(mode)), mode);
      }
    });

    test('the wire values are words, not enum indices', () {
      // Reordering Dart's ThemeMode must not silently repoint what is on disk.
      expect(encodeThemeMode(ThemeMode.system), 'system');
      expect(encodeThemeMode(ThemeMode.light), 'light');
      expect(encodeThemeMode(ThemeMode.dark), 'dark');
    });

    test('anything unrecognised means "never chosen"', () {
      for (final String? raw in <String?>[
        null,
        '',
        ' ',
        'Dark',
        'DARK',
        '0',
        '1',
        'auto',
      ]) {
        expect(decodeThemeMode(raw), ThemeMode.system);
      }
    });
  });

  group('ThemeController', () {
    test('starts on the system canvas when nothing was ever chosen', () async {
      final ThemeController controller =
          ThemeController(store: _MemoryStore());
      expect(controller.mode, ThemeMode.system);

      await controller.load();
      expect(controller.mode, ThemeMode.system);
    });

    test('load applies a remembered choice and notifies once', () async {
      final ThemeController controller =
          ThemeController(store: _MemoryStore(ThemeMode.light));
      int notifications = 0;
      controller.addListener(() => notifications++);

      await controller.load();

      expect(controller.mode, ThemeMode.light);
      expect(notifications, 1);
    });

    test('setMode notifies and writes the choice down', () async {
      final _MemoryStore store = _MemoryStore();
      final ThemeController controller = ThemeController(store: store);
      int notifications = 0;
      controller.addListener(() => notifications++);

      await controller.setMode(ThemeMode.dark);

      expect(controller.mode, ThemeMode.dark);
      expect(store.stored, ThemeMode.dark);
      expect(notifications, 1);
    });

    test('re-choosing the current mode is not a change', () async {
      final _MemoryStore store = _MemoryStore();
      final ThemeController controller = ThemeController(store: store);
      int notifications = 0;
      controller.addListener(() => notifications++);

      await controller.setMode(ThemeMode.system);

      expect(notifications, 0);
      expect(store.writes, 0);
    });

    test('a store that throws never stops the app theming itself', () async {
      final ThemeController controller = ThemeController(
        initialMode: ThemeMode.dark,
        store: _BrokenStore(),
      );

      // Neither of these may escape: not remembering the choice is a smaller
      // problem than not starting.
      await controller.load();
      expect(controller.mode, ThemeMode.dark);
      await controller.setMode(ThemeMode.light);
      expect(controller.mode, ThemeMode.light);
    });
  });

  group('ThemeScope', () {
    testWidgets('hands the controller to anything below it',
        (WidgetTester tester) async {
      final ThemeController controller =
          ThemeController(store: _MemoryStore());
      late ThemeController? seen;

      await tester.pumpWidget(_app(
        controller,
        home: Builder(builder: (BuildContext context) {
          seen = ThemeScope.maybeOf(context);
          return const SizedBox.shrink();
        }),
      ));

      expect(seen, same(controller));
    });

    testWidgets('is null when nobody installed one, and does not throw',
        (WidgetTester tester) async {
      late ThemeController? seen;

      await tester.pumpWidget(MaterialApp(
        theme: buildAppTheme(),
        home: Builder(builder: (BuildContext context) {
          seen = ThemeScope.maybeOf(context);
          return const SizedBox.shrink();
        }),
      ));

      expect(seen, isNull);
    });
  });

  group('a runtime switch repaints the whole tree', () {
    testWidgets('the palette a widget reads flips with the mode',
        (WidgetTester tester) async {
      final ThemeController controller = ThemeController(
        initialMode: ThemeMode.dark,
        store: _MemoryStore(),
      );

      await tester.pumpWidget(_app(
        controller,
        home: const Scaffold(body: SizedBox.shrink()),
      ));

      BuildContext ctx() => tester.element(find.byType(SizedBox).first);
      expect(ctx().palette, same(_dark));
      expect(Theme.of(ctx()).brightness, Brightness.dark);

      await controller.setMode(ThemeMode.light);
      await tester.pumpAndSettle();

      expect(ctx().palette, same(_light));
      expect(Theme.of(ctx()).brightness, Brightness.light);
    });

    testWidgets('the score ring, the bars and the brand mark all repaint',
        (WidgetTester tester) async {
      final ThemeController controller = ThemeController(
        initialMode: ThemeMode.dark,
        store: _MemoryStore(),
      );

      await tester.pumpWidget(_app(
        controller,
        home: Scaffold(
          body: Column(
            children: <Widget>[
              const ScoreRing(score: 91),
              const AppLogo(size: 40),
              CategoryBars(categories: <CategoryScore>[_category(45)]),
            ],
          ),
        ),
      ));
      await tester.pumpAndSettle();

      // A 91 is the top band, and the arc is a fill, so it is the fill step.
      expect(
        find.byType(ScoreRing),
        paints..arc(color: _dark.scoreHighFill),
      );
      expect(
        find.byType(AppLogo),
        paints..circle(color: _dark.ballAccent),
      );
      // The 45 bar is the bottom band's fill step.
      expect(_barColours(tester), contains(_dark.scoreLowFill));

      await controller.setMode(ThemeMode.light);
      await tester.pumpAndSettle();

      // Every one of them is now the light set. A painter that had cached its
      // colour, or a shouldRepaint that only watched one field, fails here.
      expect(
        find.byType(ScoreRing),
        paints..arc(color: _light.scoreHighFill),
      );
      expect(
        find.byType(ScoreRing),
        isNot(paints..arc(color: _dark.scoreHighFill)),
      );
      expect(
        find.byType(AppLogo),
        paints..circle(color: _light.ballAccent),
      );
      expect(
        find.byType(AppLogo),
        isNot(paints..circle(color: _dark.ballAccent)),
      );
      expect(_barColours(tester), contains(_light.scoreLowFill));
      expect(_barColours(tester), isNot(contains(_dark.scoreLowFill)));
    });

    testWidgets('the scaffold itself changes canvas, not just its children',
        (WidgetTester tester) async {
      final ThemeController controller = ThemeController(
        initialMode: ThemeMode.dark,
        store: _MemoryStore(),
      );

      await tester.pumpWidget(_app(
        controller,
        home: const Scaffold(body: SizedBox.shrink()),
      ));

      Color background() => tester
          .widget<Material>(
            find.descendant(
              of: find.byType(Scaffold),
              matching: find.byType(Material),
            ),
          )
          .color!;

      expect(background(), _dark.surface);

      await controller.setMode(ThemeMode.light);
      await tester.pumpAndSettle();

      expect(background(), _light.surface);
    });

    testWidgets('ThemeMode.system follows the platform, both ways',
        (WidgetTester tester) async {
      final ThemeController controller =
          ThemeController(store: _MemoryStore());
      addTearDown(tester.platformDispatcher.clearPlatformBrightnessTestValue);

      tester.platformDispatcher.platformBrightnessTestValue = Brightness.dark;
      await tester.pumpWidget(_app(
        controller,
        home: const Scaffold(body: SizedBox.shrink()),
      ));
      await tester.pumpAndSettle();

      BuildContext ctx() => tester.element(find.byType(SizedBox).first);
      expect(ctx().palette, same(_dark));

      tester.platformDispatcher.platformBrightnessTestValue = Brightness.light;
      await tester.pumpAndSettle();

      expect(ctx().palette, same(_light));
    });
  });
}

/// The colours of the filled portions of every category bar on screen.
List<Color> _barColours(WidgetTester tester) => tester
    .widgetList<FractionallySizedBox>(find.byType(FractionallySizedBox))
    .map((FractionallySizedBox box) => (box.child! as Container).color!)
    .toList(growable: false);
