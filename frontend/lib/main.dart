import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import 'screens/home_shell.dart';
import 'screens/login_screen.dart';
import 'theme/app_theme.dart';
import 'theme/motion.dart';
import 'theme/theme_controller.dart';

/// REPLACE ME: the Supabase project URL, e.g. `https://your-ref.supabase.co`
const String supabaseUrl = 'https://qrjpheqayeudazcrqxpl.supabase.co';

/// REPLACE ME: the Supabase anon / publishable key. This is the only key that
/// may ever live in the client. Never put the service-role key here.
const String supabasePublishableKey = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFyanBoZXFheWV1ZGF6Y3JxeHBsIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODgwNzgyODcsImV4cCI6MjEwMzY1NDI4N30.VKGzCKDUYba6Uxb23kUkzDwpIZLsYqiQ9nKQhl5JPAs';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await Supabase.initialize(
    url: supabaseUrl,
    publishableKey: supabasePublishableKey,
  );
  // The light / dark / system choice, remembered across launches, on every
  // platform. Read before the first frame so a dark choice never flashes light.
  final ThemeController themeController = ThemeController();
  await themeController.load();
  runApp(TennisimoApp(themeController: themeController));
}

class TennisimoApp extends StatelessWidget {
  const TennisimoApp({
    super.key,
    this.home,
    this.themeController,
    this.isWeb = kIsWeb,
  });

  /// The first screen. Only ever passed by a test that needs to inspect this
  /// widget's [MaterialApp] without a live Supabase session; production leaves
  /// it null and gets the [AuthGate].
  @visibleForTesting
  final Widget? home;

  /// The light / dark / system choice. Null only in tests.
  final ThemeController? themeController;

  /// Whether this is the web build. Always [kIsWeb] in production; a
  /// parameter only so a VM test can drive the web branch, which it otherwise
  /// could not reach.
  @visibleForTesting
  final bool isWeb;

  @override
  Widget build(BuildContext context) {
    final ThemeController? controller = themeController;
    if (controller == null) {
      // Only a test passes no controller: follow the platform brightness.
      return _materialApp(
        resolveAppTheme(isWeb: isWeb, requested: ThemeMode.system),
        themeAnimationDuration: kThemeAnimationDuration,
      );
    }
    // The choice is live, and reachable below through ThemeScope (Profile ›
    // Appearance). The
    // cross-fade between canvases is skipped under reduced motion.
    return ThemeScope(
      controller: controller,
      child: MediaQuery.fromView(
        view: View.of(context),
        child: ListenableBuilder(
          listenable: controller,
          builder: (BuildContext context, Widget? _) => _materialApp(
            resolveAppTheme(isWeb: isWeb, requested: controller.mode),
            themeAnimationDuration:
                motionDuration(context, kThemeAnimationDuration),
          ),
        ),
      ),
    );
  }

  Widget _materialApp(
    AppThemeConfig config, {
    required Duration themeAnimationDuration,
  }) {
    return MaterialApp(
      title: 'Tennisimo',
      theme: config.theme,
      darkTheme: config.darkTheme,
      themeMode: config.themeMode,
      themeAnimationDuration: themeAnimationDuration,
      // Four of this app's screens have no AppBar, so the app bar's own
      // systemOverlayStyle never reaches them. This region is the floor under
      // all of them, keyed to the canvas actually painted: dark icons on the
      // light canvas, light icons on the dark one. See [canvasOverlayStyle].
      builder: (BuildContext context, Widget? child) =>
          AnnotatedRegion<SystemUiOverlayStyle>(
        value: canvasOverlayStyle(Theme.of(context).brightness),
        child: child ?? const SizedBox.shrink(),
      ),
      home: home ?? const AuthGate(),
    );
  }
}

/// Signed in -> home; signed out -> login.
class AuthGate extends StatelessWidget {
  const AuthGate({super.key});

  @override
  Widget build(BuildContext context) {
    return StreamBuilder<AuthState>(
      stream: Supabase.instance.client.auth.onAuthStateChange,
      builder: (BuildContext context, AsyncSnapshot<AuthState> snapshot) {
        final Session? session = snapshot.data?.session ??
            Supabase.instance.client.auth.currentSession;
        return session == null ? const LoginScreen() : const HomeShell();
      },
    );
  }
}
