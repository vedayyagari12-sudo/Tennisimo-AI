/// The web app's light / dark choice: its stored form, the controller, and
/// that the choice survives a restart through `shared_preferences`.
///
/// Whether the choice is ever HONOURED is a separate question, answered in
/// `web_theme_gating_test.dart`: off the web it never is.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:tennisimo_ai/theme/theme_controller.dart';

/// An in-memory store, optionally broken, that records what was written.
class _FakeStore implements ThemeModeStore {
  _FakeStore({this.stored, this.readThrows = false, this.writeThrows = false});

  ThemeMode? stored;
  final bool readThrows;
  final bool writeThrows;
  final List<ThemeMode> writes = <ThemeMode>[];

  @override
  Future<ThemeMode?> read() async {
    if (readThrows) throw StateError('no storage');
    return stored;
  }

  @override
  Future<void> write(ThemeMode mode) async {
    if (writeThrows) throw StateError('no storage');
    writes.add(mode);
    stored = mode;
  }
}

void main() {
  group('the stored form', () {
    test('every mode round-trips', () {
      for (final ThemeMode mode in ThemeMode.values) {
        expect(decodeThemeMode(encodeThemeMode(mode)), mode);
      }
    });

    test('the literals are the ones web/index.html matches on', () {
      expect(encodeThemeMode(ThemeMode.system), 'system');
      expect(encodeThemeMode(ThemeMode.light), 'light');
      expect(encodeThemeMode(ThemeMode.dark), 'dark');
    });

    test('absent or unrecognised means follow the system', () {
      expect(decodeThemeMode(null), ThemeMode.system);
      expect(decodeThemeMode(''), ThemeMode.system);
      expect(decodeThemeMode('Dark'), ThemeMode.system);
      expect(decodeThemeMode('2'), ThemeMode.system);
    });
  });

  group('ThemeController', () {
    test('defaults to following the system', () {
      expect(ThemeController(store: _FakeStore()).mode, ThemeMode.system);
    });

    test('load applies the stored choice and notifies', () async {
      final ThemeController controller =
          ThemeController(store: _FakeStore(stored: ThemeMode.dark));
      int notified = 0;
      controller.addListener(() => notified++);

      await controller.load();

      expect(controller.mode, ThemeMode.dark);
      expect(notified, 1);
    });

    test('nothing stored leaves it on system, silently', () async {
      final ThemeController controller = ThemeController(store: _FakeStore());
      int notified = 0;
      controller.addListener(() => notified++);

      await controller.load();

      expect(controller.mode, ThemeMode.system);
      expect(notified, 0);
    });

    test('a store that cannot be read does not stop the app theming', () async {
      final ThemeController controller =
          ThemeController(store: _FakeStore(readThrows: true));
      await controller.load();
      expect(controller.mode, ThemeMode.system);
    });

    test('setMode switches, notifies, and writes the choice down', () async {
      final _FakeStore store = _FakeStore();
      final ThemeController controller = ThemeController(store: store);
      int notified = 0;
      controller.addListener(() => notified++);

      await controller.setMode(ThemeMode.dark);

      expect(controller.mode, ThemeMode.dark);
      expect(notified, 1);
      expect(store.writes, <ThemeMode>[ThemeMode.dark]);
    });

    test('choosing the mode already in force is a no-op', () async {
      final _FakeStore store = _FakeStore();
      final ThemeController controller = ThemeController(store: store);
      int notified = 0;
      controller.addListener(() => notified++);

      await controller.setMode(ThemeMode.system);

      expect(notified, 0);
      expect(store.writes, isEmpty);
    });

    test('a failed write still switches for this session', () async {
      final ThemeController controller =
          ThemeController(store: _FakeStore(writeThrows: true));
      await controller.setMode(ThemeMode.light);
      expect(controller.mode, ThemeMode.light);
    });
  });

  group('persistence through shared_preferences', () {
    test('nothing stored reads as null', () async {
      SharedPreferences.setMockInitialValues(<String, Object>{});
      expect(await const PreferencesThemeModeStore().read(), isNull);
    });

    test('the choice is written under the pinned key', () async {
      SharedPreferences.setMockInitialValues(<String, Object>{});
      final ThemeController controller = ThemeController();

      await controller.setMode(ThemeMode.dark);

      final SharedPreferences prefs = await SharedPreferences.getInstance();
      expect(kThemeModePrefsKey, 'theme_mode');
      expect(prefs.getString(kThemeModePrefsKey), 'dark');
    });

    test('a fresh controller, as after a reload, picks the choice back up',
        () async {
      SharedPreferences.setMockInitialValues(<String, Object>{});
      await ThemeController().setMode(ThemeMode.light);

      final ThemeController reloaded = ThemeController();
      await reloaded.load();

      expect(reloaded.mode, ThemeMode.light);
    });
  });
}
