import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import 'screens/home_shell.dart';
import 'screens/login_screen.dart';
import 'theme/app_theme.dart';

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
  runApp(const TennisimoApp());
}

class TennisimoApp extends StatelessWidget {
  const TennisimoApp({super.key, this.home});

  /// The first screen. Only ever passed by a test that needs to inspect this
  /// widget's [MaterialApp] without a live Supabase session; production leaves
  /// it null and gets the [AuthGate].
  @visibleForTesting
  final Widget? home;

  @override
  Widget build(BuildContext context) {
    // Light only. One theme is supplied and no darkTheme, so a device in dark
    // mode cannot put this app on a dark canvas: with darkTheme null,
    // MaterialApp falls back to `theme` whatever the platform brightness is,
    // and themeMode states the intent rather than leaving it to that fallback.
    return MaterialApp(
      title: 'Tennisimo AI',
      theme: buildAppTheme(),
      themeMode: ThemeMode.light,
      // Four of this app's screens have no AppBar, so the app bar's own
      // systemOverlayStyle never reaches them. This region is the floor under
      // all of them. It is NOT keyed to Theme.of(context).brightness: the
      // canvas never varies, and a brightness that never varies is exactly
      // where the white-icons-on-white bug hid. See [lightCanvasOverlayStyle].
      builder: (BuildContext context, Widget? child) =>
          AnnotatedRegion<SystemUiOverlayStyle>(
        value: lightCanvasOverlayStyle(),
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
