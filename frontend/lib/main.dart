import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import 'screens/home_shell.dart';
import 'screens/login_screen.dart';
import 'theme/app_theme.dart';
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
  // The remembered light/dark choice, read before the first frame so the app
  // does not flash the wrong canvas on launch. A failure to read degrades to
  // ThemeMode.system rather than blocking startup.
  final ThemeController themeController = ThemeController();
  await themeController.load();

  runApp(TennisimoApp(themeController: themeController));
}

class TennisimoApp extends StatelessWidget {
  const TennisimoApp({super.key, required this.themeController});

  /// Owns the light/dark choice. Handed in rather than constructed here so a
  /// test can drive a theme switch directly.
  final ThemeController themeController;

  @override
  Widget build(BuildContext context) {
    // ThemeScope sits ABOVE MaterialApp so the overflow menu can reach the
    // controller, and ListenableBuilder sits above MaterialApp too so a mode
    // change rebuilds MaterialApp itself. That is what propagates the new
    // ThemeData — and with it the AppPalette extension — to every widget in
    // the tree, instead of leaving already-built widgets on the old colours.
    return ThemeScope(
      controller: themeController,
      child: ListenableBuilder(
        listenable: themeController,
        builder: (BuildContext context, Widget? child) {
          return MaterialApp(
            title: 'Tennisimo AI',
            theme: buildAppTheme(brightness: Brightness.light),
            darkTheme: buildAppTheme(brightness: Brightness.dark),
            themeMode: themeController.mode,
            // Four of this app's screens have no AppBar, so the app bar's own
            // systemOverlayStyle never reaches them. This region is the floor
            // under all of them: whichever canvas the framework picked, the
            // status bar matches it. See [systemOverlayStyleFor].
            builder: (BuildContext context, Widget? child) =>
                AnnotatedRegion<SystemUiOverlayStyle>(
              value: systemOverlayStyleFor(Theme.of(context).brightness),
              child: child ?? const SizedBox.shrink(),
            ),
            home: const AuthGate(),
          );
        },
      ),
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
