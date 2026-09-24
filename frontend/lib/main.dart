import 'package:flutter/material.dart';
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
  runApp(const TennisFormApp());
}

class TennisFormApp extends StatelessWidget {
  const TennisFormApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'TennisForm AI',
      theme: buildAppTheme(),
      home: const AuthGate(),
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
