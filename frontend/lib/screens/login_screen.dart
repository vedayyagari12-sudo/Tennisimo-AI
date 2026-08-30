import 'package:flutter/material.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

/// Supabase email/password sign-in. The session it establishes is the only
/// identity the app ever sends: every backend call carries its JWT and nothing
/// else identifying.
class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final TextEditingController emailController = TextEditingController();
  final TextEditingController passwordController = TextEditingController();
  bool isLoading = false;
  bool isSignUp = false;
  String message = '';

  @override
  void dispose() {
    emailController.dispose();
    passwordController.dispose();
    super.dispose();
  }

  Future<void> handleAuth() async {
    setState(() {
      isLoading = true;
      message = '';
    });

    try {
      if (isSignUp) {
        await Supabase.instance.client.auth.signUp(
          email: emailController.text.trim(),
          password: passwordController.text.trim(),
        );
        if (!mounted) return;
        setState(() => message = 'Account created. Check your email to verify.');
      } else {
        await Supabase.instance.client.auth.signInWithPassword(
          email: emailController.text.trim(),
          password: passwordController.text.trim(),
        );
      }
    } on AuthException catch (e) {
      if (!mounted) return;
      setState(() => message = e.message);
    } catch (e) {
      if (!mounted) return;
      setState(() => message = 'Something went wrong. $e');
    }

    if (!mounted) return;
    setState(() => isLoading = false);
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(isSignUp ? 'Sign up' : 'Sign in')),
      body: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: <Widget>[
            Text('TennisForm AI', style: theme.textTheme.headlineMedium),
            const SizedBox(height: 40),
            TextField(
              controller: emailController,
              decoration: const InputDecoration(labelText: 'Email'),
              keyboardType: TextInputType.emailAddress,
              autofillHints: const <String>[AutofillHints.email],
            ),
            const SizedBox(height: 16),
            TextField(
              controller: passwordController,
              decoration: const InputDecoration(labelText: 'Password'),
              obscureText: true,
            ),
            const SizedBox(height: 24),
            FilledButton(
              onPressed: isLoading ? null : handleAuth,
              child: Text(isLoading
                  ? 'Working…'
                  : (isSignUp ? 'Sign up' : 'Sign in')),
            ),
            TextButton(
              onPressed: isLoading
                  ? null
                  : () => setState(() => isSignUp = !isSignUp),
              child: Text(isSignUp
                  ? 'Already have an account? Sign in'
                  : 'No account? Sign up'),
            ),
            if (message.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 16),
                child: Text(message, textAlign: TextAlign.center),
              ),
          ],
        ),
      ),
    );
  }
}
