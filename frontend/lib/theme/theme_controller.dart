/// The light / dark choice: which one is in force, and remembering it.
///
/// The default is [ThemeMode.system] — the app follows the phone until the user
/// says otherwise, and "otherwise" is what gets written down.
///
/// Persistence goes through `shared_preferences`, which was already in this
/// project's dependency tree (pulled in by `supabase_flutter` for its own
/// session storage) and whose Android plugin manifest declares no permissions
/// at all. Promoting it to a direct dependency therefore adds no native code
/// and no permission to the APK.
///
/// The store is behind [ThemeModeStore] so the controller is unit-testable
/// without a plugin binding. A store is allowed to throw — a locked-down
/// platform, a missing plugin binding — and [ThemeController] swallows it: not
/// being able to remember the choice must never stop the app from honouring it
/// for this session.
library;

import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Where the choice is written down.
abstract interface class ThemeModeStore {
  /// The stored mode, or null if nothing was ever stored. May throw.
  Future<ThemeMode?> read();

  /// Records [mode]. May throw; [ThemeController] treats a failure as "the
  /// choice was not remembered" rather than propagating it.
  Future<void> write(ThemeMode mode);
}

/// The wire form of a [ThemeMode]. Kept explicit rather than using
/// `mode.index`, so reordering the Dart enum can never silently repoint what
/// is already on disk.
@visibleForTesting
String encodeThemeMode(ThemeMode mode) {
  switch (mode) {
    case ThemeMode.system:
      return 'system';
    case ThemeMode.light:
      return 'light';
    case ThemeMode.dark:
      return 'dark';
  }
}

/// The inverse of [encodeThemeMode]. An unrecognised or absent value means
/// "never chosen", which is [ThemeMode.system] — the same degrade-to-the-
/// default discipline `BrandFlavor.fromFlag` applies to a stale flag.
@visibleForTesting
ThemeMode decodeThemeMode(String? raw) {
  for (final ThemeMode mode in ThemeMode.values) {
    if (encodeThemeMode(mode) == raw) return mode;
  }
  return ThemeMode.system;
}

/// [ThemeModeStore] on top of `shared_preferences`.
class PreferencesThemeModeStore implements ThemeModeStore {
  const PreferencesThemeModeStore();

  static const String _key = 'theme_mode';

  @override
  Future<ThemeMode?> read() async {
    final SharedPreferences prefs = await SharedPreferences.getInstance();
    final String? raw = prefs.getString(_key);
    return raw == null ? null : decodeThemeMode(raw);
  }

  @override
  Future<void> write(ThemeMode mode) async {
    final SharedPreferences prefs = await SharedPreferences.getInstance();
    await prefs.setString(_key, encodeThemeMode(mode));
  }
}

/// Holds the current [ThemeMode] and notifies when it changes.
class ThemeController extends ChangeNotifier {
  ThemeController({
    ThemeMode initialMode = ThemeMode.system,
    ThemeModeStore store = const PreferencesThemeModeStore(),
  })  : _mode = initialMode,
        _store = store;

  final ThemeModeStore _store;
  ThemeMode _mode;

  /// The mode [MaterialApp.themeMode] should be given.
  ThemeMode get mode => _mode;

  /// Loads the remembered choice. Safe to await before `runApp`, and safe to
  /// skip entirely — skipping just means the app starts on [ThemeMode.system].
  Future<void> load() async {
    ThemeMode? stored;
    try {
      stored = await _store.read();
    } catch (_) {
      // No storage is not a reason to refuse to theme.
      return;
    }
    if (stored != null && stored != _mode) {
      _mode = stored;
      notifyListeners();
    }
  }

  /// Switches the app and remembers the choice.
  ///
  /// The switch happens first and unconditionally: the write is best-effort,
  /// and forgetting the choice is a smaller failure than ignoring it.
  Future<void> setMode(ThemeMode mode) async {
    if (mode == _mode) return;
    _mode = mode;
    notifyListeners();
    try {
      await _store.write(mode);
    } catch (_) {
      // Forgetting the choice is better than crashing on it.
    }
  }
}

/// Makes the one [ThemeController] reachable from anywhere below it.
///
/// An [InheritedNotifier] rather than a global: the control lives in the
/// dashboard's overflow menu, several widgets deep, and this way that menu
/// depends on the controller through the element tree like everything else.
class ThemeScope extends InheritedNotifier<ThemeController> {
  const ThemeScope({
    super.key,
    required ThemeController controller,
    required super.child,
  }) : super(notifier: controller);

  /// The controller, or null when no scope is installed — which is the case in
  /// widget tests that mount a single screen, and must not crash it.
  static ThemeController? maybeOf(BuildContext context) => context
      .dependOnInheritedWidgetOfExactType<ThemeScope>()
      ?.notifier;
}
