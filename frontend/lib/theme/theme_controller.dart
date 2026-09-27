/// The web app's light / dark choice, and the one seam that keeps the mobile
/// app light-only.
///
/// ## The gate
///
/// [resolveAppTheme] is the only function that decides what `MaterialApp`
/// gets for `theme`, `darkTheme` and `themeMode`. Given `isWeb: false` it
/// returns `darkTheme: null` and `themeMode: ThemeMode.light` WHATEVER mode is
/// asked for, so no stored preference, no controller and no platform
/// brightness can reach a dark canvas on a phone. `main.dart` calls it with
/// `kIsWeb`, and does not even construct a [ThemeController] or install a
/// [ThemeScope] off the web, so the dashboard's theme menu entries (which
/// render only under a [ThemeScope]) do not exist on mobile either. There is
/// no `kIsWeb` check anywhere in the UI.
///
/// ## Persistence
///
/// The choice goes through `shared_preferences` under [kThemeModePrefsKey].
/// On web that is `localStorage`, where the plugin stores it as
/// `flutter.theme_mode` with a JSON-encoded value (`"dark"`); `web/index.html`
/// reads exactly that entry before Flutter boots, to paint the right canvas
/// colour, and `test/web_theme_gating_test.dart` pins the two together.
///
/// `shared_preferences` was already in the dependency tree (pulled in by
/// `supabase_flutter` for its own session storage) and its Android plugin
/// declares no permissions, so making it a direct dependency adds no native
/// code and no permission to the APK.
library;

import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'app_theme.dart';

/// The `shared_preferences` key. On web the plugin prefixes it with
/// `flutter.` in `localStorage`.
const String kThemeModePrefsKey = 'theme_mode';

/// Where the choice is written down.
abstract interface class ThemeModeStore {
  /// The stored mode, or null if nothing was ever stored. May throw.
  Future<ThemeMode?> read();

  /// Records [mode]. May throw; [ThemeController] treats a failure as "the
  /// choice was not remembered" rather than propagating it.
  Future<void> write(ThemeMode mode);
}

/// The stored form of a [ThemeMode]. Explicit rather than `mode.index`, so
/// reordering the Dart enum can never repoint what is already stored, and
/// matched literally by the pre-boot script in `web/index.html`.
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

/// The inverse of [encodeThemeMode]. An unrecognised value means "never
/// chosen", which is [ThemeMode.system].
ThemeMode decodeThemeMode(String? raw) {
  for (final ThemeMode mode in ThemeMode.values) {
    if (encodeThemeMode(mode) == raw) return mode;
  }
  return ThemeMode.system;
}

/// [ThemeModeStore] on top of `shared_preferences`.
class PreferencesThemeModeStore implements ThemeModeStore {
  const PreferencesThemeModeStore();

  @override
  Future<ThemeMode?> read() async {
    final SharedPreferences prefs = await SharedPreferences.getInstance();
    final String? raw = prefs.getString(kThemeModePrefsKey);
    return raw == null ? null : decodeThemeMode(raw);
  }

  @override
  Future<void> write(ThemeMode mode) async {
    final SharedPreferences prefs = await SharedPreferences.getInstance();
    await prefs.setString(kThemeModePrefsKey, encodeThemeMode(mode));
  }
}

/// Holds the web app's [ThemeMode] and notifies when it changes.
///
/// Defaults to [ThemeMode.system]: the page follows the browser until the user
/// says otherwise, and "otherwise" is what gets written down.
class ThemeController extends ChangeNotifier {
  ThemeController({ThemeModeStore store = const PreferencesThemeModeStore()})
      : _store = store;

  final ThemeModeStore _store;
  ThemeMode _mode = ThemeMode.system;

  /// The mode the user asked for. What `MaterialApp` actually gets is
  /// [resolveAppTheme]'s decision, not this.
  ThemeMode get mode => _mode;

  /// Loads the remembered choice. Skipping it, or a store that throws, just
  /// means starting on [ThemeMode.system].
  Future<void> load() async {
    final ThemeMode? stored;
    try {
      stored = await _store.read();
    } catch (_) {
      return;
    }
    if (stored != null && stored != _mode) {
      _mode = stored;
      notifyListeners();
    }
  }

  /// Switches and remembers. The switch happens first and unconditionally;
  /// the write is best-effort, because forgetting the choice is a smaller
  /// failure than ignoring it.
  Future<void> setMode(ThemeMode mode) async {
    if (mode == _mode) return;
    _mode = mode;
    notifyListeners();
    try {
      await _store.write(mode);
    } catch (_) {
      // Not remembered; still honoured for this session.
    }
  }
}

/// Makes the [ThemeController] reachable below it. Installed by `main.dart`
/// on web only, so [maybeOf] returning null IS the mobile case.
class ThemeScope extends InheritedNotifier<ThemeController> {
  const ThemeScope({
    super.key,
    required ThemeController controller,
    required super.child,
  }) : super(notifier: controller);

  /// The controller, or null when no scope is installed: always on mobile,
  /// and in widget tests that mount a single screen.
  static ThemeController? maybeOf(BuildContext context) =>
      context.dependOnInheritedWidgetOfExactType<ThemeScope>()?.notifier;
}

/// What `MaterialApp` is given.
@immutable
class AppThemeConfig {
  const AppThemeConfig({
    required this.theme,
    required this.darkTheme,
    required this.themeMode,
  });

  final ThemeData theme;
  final ThemeData? darkTheme;
  final ThemeMode themeMode;
}

/// The gate. Off the web the answer is light-only regardless of [requested]:
/// no dark theme exists for `MaterialApp` to fall back to, and the mode says
/// so explicitly. On the web the dark theme is attached and [requested] is
/// honoured.
AppThemeConfig resolveAppTheme({
  required bool isWeb,
  required ThemeMode requested,
}) {
  if (!isWeb) {
    return AppThemeConfig(
      theme: buildAppTheme(),
      darkTheme: null,
      themeMode: ThemeMode.light,
    );
  }
  return AppThemeConfig(
    theme: buildAppTheme(),
    darkTheme: buildDarkAppTheme(),
    themeMode: requested,
  );
}
