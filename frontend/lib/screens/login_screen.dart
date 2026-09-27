import 'package:flutter/material.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import '../services/api_client.dart';
import '../services/auth_service.dart';
import '../theme/app_theme.dart';
import '../widgets/app_logo.dart';
import '../widgets/content_width.dart';

/// Sign in and sign up, in one screen with a quiet toggle between them.
///
/// The session this establishes is the only identity the app ever sends: every
/// backend call carries its JWT and nothing else identifying.
///
/// The two modes share the layout and the fields because they differ by one
/// bit of intent, not by shape. What they do NOT share is the ending: sign-in
/// either produces a session or an error, while a successful sign-up on this
/// project produces no session at all — email confirmation is on — and says so
/// rather than pretending to log the user in. See [SignUpOutcome].
class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final GlobalKey<FormState> _formKey = GlobalKey<FormState>();
  final TextEditingController _emailController = TextEditingController();
  final TextEditingController _passwordController = TextEditingController();

  AuthMode _mode = AuthMode.signIn;
  bool _busy = false;
  bool _passwordVisible = false;

  /// The last failure, shown verbatim. Never replaced by a generic stand-in.
  ApiFailure? _failure;

  /// A true, non-error thing that happened — currently only "we emailed you a
  /// confirmation link". Kept separate from [_failure] so it is never styled or
  /// read as a problem.
  String? _notice;

  @override
  void dispose() {
    _emailController.dispose();
    _passwordController.dispose();
    super.dispose();
  }

  void _toggleMode() {
    // The password rules differ between the modes, so field errors raised
    // under the old mode's rules are no longer about anything. Form.reset()
    // clears them, but it also resets the fields to their (empty) initial
    // value, so what the user typed is put back afterwards.
    final String email = _emailController.text;
    final String password = _passwordController.text;
    _formKey.currentState?.reset();
    _emailController.text = email;
    _passwordController.text = password;

    setState(() {
      _mode = _mode.isSignUp ? AuthMode.signIn : AuthMode.signUp;
      _failure = null;
      _notice = null;
    });
  }

  Future<void> _submit() async {
    if (!(_formKey.currentState?.validate() ?? false)) return;

    setState(() {
      _busy = true;
      _failure = null;
      _notice = null;
    });

    final String email = _emailController.text.trim();
    final String password = _passwordController.text;
    final AuthMode mode = _mode;

    try {
      if (mode.isSignUp) {
        final AuthResponse response =
            await Supabase.instance.client.auth.signUp(
          email: email,
          password: password,
        );
        if (!mounted) return;
        _applySignUpOutcome(response);
      } else {
        // On success the auth state stream fires and AuthGate swaps this screen
        // for the app, so there is nothing to do here.
        await Supabase.instance.client.auth.signInWithPassword(
          email: email,
          password: password,
        );
        if (!mounted) return;
      }
    } catch (error) {
      if (!mounted) return;
      setState(() => _failure = authFailure(error, mode: mode));
    }

    if (!mounted) return;
    setState(() => _busy = false);
  }

  /// Turns a non-throwing sign-up response into what the user is told.
  void _applySignUpOutcome(AuthResponse response) {
    final SignUpOutcome? outcome = classifySignUp(
      hasSession: response.session != null,
      hasUser: response.user != null,
      identityCount: response.user?.identities?.length,
    );

    if (outcome == SignUpOutcome.sessionStarted) {
      // AuthGate is already replacing this screen. Say nothing.
      return;
    }
    if (outcome == SignUpOutcome.confirmationRequired) {
      setState(() {
        _notice = 'Account created. We sent a confirmation link to '
            '${_emailController.text.trim()} — open it, then sign in.';
        _mode = AuthMode.signIn;
        _passwordController.clear();
      });
      return;
    }
    if (outcome == SignUpOutcome.alreadyRegistered) {
      setState(() {
        _failure = const ApiFailure(
          kind: ApiFailureKind.server,
          message:
              'That email already has an account. Switch to sign in instead.',
        );
      });
      return;
    }
    setState(() {
      _failure = ApiFailure(
        kind: ApiFailureKind.badResponse,
        message: 'The sign-up server answered in a way this app could not '
            'read, so it is not clear whether the account was created. Try '
            'signing in before signing up again.',
        technicalDetail:
            'signUp: session=${response.session}, user=${response.user}',
      );
    });
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final ColorScheme colors = theme.colorScheme;
    final bool signUp = _mode.isSignUp;

    return Scaffold(
      body: ContentWidth(
        child: SafeArea(
          child: AutofillGroup(
            child: Form(
              key: _formKey,
              child: ListView(
                padding: const EdgeInsets.fromLTRB(
                  AppSpacing.xl,
                  AppSpacing.xl,
                  AppSpacing.xl,
                  AppSpacing.xxl,
                ),
                children: <Widget>[
                  const SizedBox(height: AppSpacing.xxl),
                  const Align(
                    alignment: Alignment.centerLeft,
                    child: AppLogo(size: 52),
                  ),
                  const SizedBox(height: AppSpacing.xxl),
                  Text(
                    signUp ? 'Start your swing log' : 'Welcome back',
                    style: theme.textTheme.displaySmall,
                  ),
                  const SizedBox(height: AppSpacing.md),
                  Text(
                    signUp
                        ? 'One account keeps every analysed swing, score and '
                            'trend in one place.'
                        : 'Sign in to pick up where your last swing left off.',
                    style: theme.textTheme.bodyLarge
                        ?.copyWith(color: colors.onSurfaceVariant),
                  ),
                  const SizedBox(height: AppSpacing.xxl),
                  TextFormField(
                    controller: _emailController,
                    enabled: !_busy,
                    decoration: const InputDecoration(
                      labelText: 'Email',
                      border: OutlineInputBorder(),
                    ),
                    keyboardType: TextInputType.emailAddress,
                    textInputAction: TextInputAction.next,
                    autocorrect: false,
                    autofillHints: const <String>[AutofillHints.email],
                    validator: (String? value) => validateEmail(value ?? ''),
                  ),
                  const SizedBox(height: AppSpacing.lg),
                  TextFormField(
                    controller: _passwordController,
                    enabled: !_busy,
                    obscureText: !_passwordVisible,
                    decoration: InputDecoration(
                      labelText: 'Password',
                      border: const OutlineInputBorder(),
                      suffixIcon: IconButton(
                        icon: Icon(_passwordVisible
                            ? Icons.visibility_off
                            : Icons.visibility),
                        tooltip: _passwordVisible
                            ? 'Hide password'
                            : 'Show password',
                        onPressed: () => setState(
                          () => _passwordVisible = !_passwordVisible,
                        ),
                      ),
                    ),
                    textInputAction: TextInputAction.done,
                    autofillHints: <String>[
                      if (signUp)
                        AutofillHints.newPassword
                      else
                        AutofillHints.password,
                    ],
                    onFieldSubmitted: (_) {
                      if (!_busy) _submit();
                    },
                    validator: (String? value) =>
                        validatePassword(value ?? '', mode: _mode),
                  ),
                  const SizedBox(height: AppSpacing.xl),
                  SizedBox(
                    width: double.infinity,
                    child: FilledButton(
                      onPressed: _busy ? null : _submit,
                      child: Text(_busy
                          ? 'Working…'
                          : (signUp ? 'Create account' : 'Sign in')),
                    ),
                  ),
                  const SizedBox(height: AppSpacing.sm),
                  Center(
                    child: TextButton(
                      onPressed: _busy ? null : _toggleMode,
                      // Centred, not just centred-as-a-box: at a 2.0x font
                      // scale this label wraps to two lines and without this
                      // the second line hung left under a centred first.
                      child: Text(
                        signUp
                            ? 'Already have an account? Sign in'
                            : 'New here? Create an account',
                        textAlign: TextAlign.center,
                      ),
                    ),
                  ),
                  if (_notice != null) ...<Widget>[
                    const SizedBox(height: AppSpacing.lg),
                    _AuthBanner(
                      icon: Icons.mark_email_unread_outlined,
                      text: _notice!,
                      background: colors.primaryContainer,
                      foreground: colors.onPrimaryContainer,
                    ),
                  ],
                  if (_failure != null) ...<Widget>[
                    const SizedBox(height: AppSpacing.lg),
                    _AuthBanner(
                      icon: Icons.error_outline,
                      text: _failure!.plainLanguageWithDebugDetail,
                      background: colors.errorContainer,
                      foreground: colors.onErrorContainer,
                    ),
                  ],
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// A tinted block for one message — an error, or the confirmation notice.
class _AuthBanner extends StatelessWidget {
  const _AuthBanner({
    required this.icon,
    required this.text,
    required this.background,
    required this.foreground,
  });

  final IconData icon;
  final String text;
  final Color background;
  final Color foreground;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(AppSpacing.lg),
      decoration: BoxDecoration(
        color: background,
        borderRadius: BorderRadius.circular(AppSpacing.innerRadius),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Icon(icon, size: 20, color: foreground),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: Text(
              text,
              style: Theme.of(context)
                  .textTheme
                  .bodyMedium
                  ?.copyWith(color: foreground),
            ),
          ),
        ],
      ),
    );
  }
}
