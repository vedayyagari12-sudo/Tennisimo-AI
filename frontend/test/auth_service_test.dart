import 'dart:io' show SocketException;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:supabase_flutter/supabase_flutter.dart'
    show
        AuthApiException,
        AuthResponse,
        AuthRetryableFetchException,
        User,
        UserIdentity;
import 'package:tennisimo_ai/screens/login_screen.dart';
import 'package:tennisimo_ai/services/api_client.dart';
import 'package:tennisimo_ai/services/auth_service.dart';
import 'package:tennisimo_ai/theme/app_theme.dart';

/// Every auth failure the mapper is expected to recognise, as the server
/// actually reports it. The codes and messages were taken from live responses
/// of this project's `/auth/v1` endpoints.
AuthApiException _api(
  String code, {
  String message = 'server text',
  int status = 400,
}) =>
    AuthApiException(message, statusCode: '$status', code: code);

/// Records every sign-up the screen attempts, and answers the way this
/// project's auth server answers a first-time sign-up with email confirmation
/// on: a user with one identity and no session.
class _RecordingAuth extends LoginAuthActions {
  final List<String> signUps = <String>[];
  String? lastPassword;

  @override
  Future<AuthResponse> signUp({
    required String email,
    required String password,
  }) async {
    signUps.add(email);
    lastPassword = password;
    return AuthResponse(
      user: User(
        id: 'new-user',
        appMetadata: const <String, dynamic>{},
        userMetadata: const <String, dynamic>{},
        aud: 'authenticated',
        email: email,
        createdAt: '2026-09-29T00:00:00Z',
        identities: <UserIdentity>[
          UserIdentity(
            id: 'new-user',
            userId: 'new-user',
            identityData: <String, dynamic>{'email': email},
            identityId: 'identity',
            provider: 'email',
            createdAt: '2026-09-29T00:00:00Z',
            lastSignInAt: '2026-09-29T00:00:00Z',
          ),
        ],
      ),
    );
  }
}

void main() {
  group('email validation', () {
    test('an empty field asks for the address rather than guessing', () {
      expect(validateEmail(''), 'Enter your email address.');
      expect(validateEmail('   '), 'Enter your email address.');
    });

    test('a missing @ or a bare domain is rejected', () {
      const String expected = 'That does not look like an email address.';
      expect(validateEmail('notanemail'), expected);
      expect(validateEmail('a@b'), expected);
      expect(validateEmail('a b@c.com'), expected);
    });

    test('an ordinary address passes, surrounding space and all', () {
      expect(validateEmail('player@example.com'), isNull);
      expect(validateEmail('  player@example.co.uk  '), isNull);
    });
  });

  group('password validation', () {
    test('an empty field is rejected in both modes', () {
      for (final AuthMode mode in AuthMode.values) {
        expect(validatePassword('', mode: mode), 'Enter your password.');
      }
    });

    test('sign-up enforces the server length rule client-side', () {
      expect(
        validatePassword('abc12', mode: AuthMode.signUp),
        'Use at least 6 characters.',
      );
      expect(validatePassword('abc123', mode: AuthMode.signUp), isNull);
    });

    test('sign-in does NOT judge length: the account already exists', () {
      expect(validatePassword('abc', mode: AuthMode.signIn), isNull);
    });

    test('the advertised minimum is the one the message quotes', () {
      expect(kMinPasswordLength, 6);
      expect(
        validatePassword('a' * (kMinPasswordLength - 1), mode: AuthMode.signUp),
        contains('$kMinPasswordLength'),
      );
    });
  });

  group('password confirmation', () {
    test('a repeat of the same password passes', () {
      expect(
        validatePasswordConfirmation('abc123', password: 'abc123'),
        isNull,
      );
    });

    test('a different password is a mismatch', () {
      expect(
        validatePasswordConfirmation('abc124', password: 'abc123'),
        'The passwords do not match.',
      );
    });

    test('the comparison is exact: case and spaces count', () {
      expect(
        validatePasswordConfirmation('ABC123', password: 'abc123'),
        'The passwords do not match.',
      );
      expect(
        validatePasswordConfirmation('abc123 ', password: 'abc123'),
        'The passwords do not match.',
      );
    });

    test('an empty confirmation asks for it, whatever the password is', () {
      expect(
        validatePasswordConfirmation('', password: 'abc123'),
        'Enter your password again.',
      );
      expect(
        validatePasswordConfirmation('', password: ''),
        'Enter your password again.',
      );
    });
  });

  group('sign-up outcome', () {
    test('a session means genuinely signed in', () {
      expect(
        classifySignUp(hasSession: true, hasUser: true, identityCount: 1),
        SignUpOutcome.sessionStarted,
      );
    });

    test('a user with no session means the email must be confirmed', () {
      expect(
        classifySignUp(hasSession: false, hasUser: true, identityCount: 1),
        SignUpOutcome.confirmationRequired,
      );
    });

    test('an obfuscated user with no identities means already registered', () {
      expect(
        classifySignUp(hasSession: false, hasUser: true, identityCount: 0),
        SignUpOutcome.alreadyRegistered,
      );
    });

    test('neither session nor user is inconclusive, not a success', () {
      expect(
        classifySignUp(hasSession: false, hasUser: false, identityCount: null),
        isNull,
      );
    });
  });

  group('auth error mapping', () {
    test('a wrong password says so, and does not say "went wrong"', () {
      final ApiFailure failure = authFailure(
        _api('invalid_credentials', message: 'Invalid login credentials'),
        mode: AuthMode.signIn,
      );
      expect(failure.kind, ApiFailureKind.server);
      expect(failure.errorCode, 'invalid_credentials');
      expect(failure.statusCode, 400);
      expect(
        failure.plainLanguage,
        'That email and password do not match an account. Check the password, '
        'or create an account if you do not have one yet.',
      );
    });

    test('an unconfirmed account is told to open the emailed link', () {
      expect(
        authFailure(_api('email_not_confirmed'), mode: AuthMode.signIn)
            .plainLanguage,
        'This account is not confirmed yet. Open the confirmation link in the '
        'email we sent you, then sign in.',
      );
    });

    test('both already-registered codes map to one message', () {
      const String expected =
          'That email already has an account. Switch to sign in instead.';
      expect(
        authFailure(_api('user_already_exists'), mode: AuthMode.signUp)
            .plainLanguage,
        expected,
      );
      expect(
        authFailure(_api('email_exists'), mode: AuthMode.signUp).plainLanguage,
        expected,
      );
    });

    test('a weak password shows the server\'s own rule verbatim', () {
      expect(
        authFailure(
          _api(
            'weak_password',
            message: 'Password should be at least 6 characters.',
            status: 422,
          ),
          mode: AuthMode.signUp,
        ).plainLanguage,
        'Password should be at least 6 characters.',
      );
    });

    test('a server-rejected email blames the email, not the network', () {
      final ApiFailure failure = authFailure(
        _api(
          'validation_failed',
          message: 'Unable to validate email address: invalid format',
        ),
        mode: AuthMode.signUp,
      );
      expect(failure.kind, ApiFailureKind.server);
      expect(
        failure.plainLanguage,
        'The server would not accept that email address. Check it for typos.',
      );
    });

    test('rate limits are distinguished from each other', () {
      expect(
        authFailure(_api('over_email_send_rate_limit'), mode: AuthMode.signUp)
            .plainLanguage,
        startsWith('Too many confirmation emails'),
      );
      expect(
        authFailure(_api('over_request_rate_limit'), mode: AuthMode.signIn)
            .plainLanguage,
        startsWith('Too many attempts from this device'),
      );
    });

    test('disabled signup and banned users are named', () {
      expect(
        authFailure(_api('signup_disabled'), mode: AuthMode.signUp)
            .plainLanguage,
        'New accounts are switched off for this app.',
      );
      expect(
        authFailure(_api('user_banned'), mode: AuthMode.signIn).plainLanguage,
        'This account has been disabled.',
      );
    });

    test('an unrecognised code falls back to the server text, not a stand-in',
        () {
      final ApiFailure failure = authFailure(
        _api('some_future_code', message: 'A very specific new problem.'),
        mode: AuthMode.signIn,
      );
      expect(failure.plainLanguage, 'A very specific new problem.');
      expect(failure.errorCode, 'some_future_code');
    });

    test('a dead socket is a network failure with the sign-in lead', () {
      final ApiFailure failure = authFailure(
        const SocketException('nope'),
        mode: AuthMode.signIn,
        isWeb: false,
      );
      expect(failure.kind, ApiFailureKind.network);
      expect(
        failure.message,
        unreachableServerMessage(
          lead: 'Could not reach the sign-in server.',
          isWeb: false,
        ),
      );
    });

    test('sign-up names the sign-up server in its network copy', () {
      expect(
        authFailure(
          const SocketException('nope'),
          mode: AuthMode.signUp,
          isWeb: true,
        ).message,
        unreachableServerMessage(
          lead: 'Could not reach the sign-up server.',
          isWeb: true,
        ),
      );
    });

    test('gotrue\'s retryable fetch exception is a network failure', () {
      final ApiFailure failure = authFailure(
        AuthRetryableFetchException(),
        mode: AuthMode.signIn,
        isWeb: false,
      );
      expect(failure.kind, ApiFailureKind.network);
      expect(failure.message, contains('Could not reach the sign-in server.'));
    });

    test('the raw exception is kept as detail and never in the user copy', () {
      final ApiFailure failure = authFailure(
        _api('invalid_credentials', message: 'Invalid login credentials'),
        mode: AuthMode.signIn,
      );
      expect(failure.technicalDetail, contains('invalid_credentials'));
      expect(failure.message, isNot(contains('AuthApiException')));
    });
  });

  group('login screen form', () {
    Future<void> pump(WidgetTester tester) => tester.pumpWidget(
          MaterialApp(theme: buildAppTheme(), home: const LoginScreen()),
        );

    testWidgets('submitting empty fields reports both, with no network call',
        (WidgetTester tester) async {
      await pump(tester);
      await tester.tap(find.widgetWithText(FilledButton, 'Sign in'));
      await tester.pumpAndSettle();

      expect(find.text('Enter your email address.'), findsOneWidget);
      expect(find.text('Enter your password.'), findsOneWidget);
    });

    testWidgets('a bad email is caught before submitting',
        (WidgetTester tester) async {
      await pump(tester);
      await tester.enterText(find.byType(TextFormField).first, 'nope');
      await tester.enterText(find.byType(TextFormField).last, 'abc123');
      await tester.tap(find.widgetWithText(FilledButton, 'Sign in'));
      await tester.pumpAndSettle();

      expect(find.text('That does not look like an email address.'),
          findsOneWidget);
    });

    testWidgets('the toggle switches to sign up and keeps what was typed',
        (WidgetTester tester) async {
      await pump(tester);
      await tester.enterText(
          find.byType(TextFormField).first, 'player@example.com');
      await tester.tap(find.text('New here? Create an account'));
      await tester.pumpAndSettle();

      expect(find.widgetWithText(FilledButton, 'Create account'),
          findsOneWidget);
      expect(find.text('player@example.com'), findsOneWidget);
    });

    testWidgets('sign up enforces the password length in the form',
        (WidgetTester tester) async {
      await pump(tester);
      await tester.tap(find.text('New here? Create an account'));
      await tester.pumpAndSettle();
      await tester.enterText(
          find.byType(TextFormField).first, 'player@example.com');
      await tester.enterText(
          find.widgetWithText(TextFormField, 'Password'), 'abc');
      await tester.tap(find.widgetWithText(FilledButton, 'Create account'));
      await tester.pumpAndSettle();

      expect(find.text('Use at least 6 characters.'), findsOneWidget);
    });

    testWidgets('the password starts obscured and the toggle reveals it',
        (WidgetTester tester) async {
      await pump(tester);
      EditableText field() =>
          tester.widget<EditableText>(find.byType(EditableText).last);

      expect(field().obscureText, isTrue);
      await tester.tap(find.byIcon(Icons.visibility));
      await tester.pumpAndSettle();
      expect(field().obscureText, isFalse);
      expect(find.byIcon(Icons.visibility_off), findsOneWidget);
    });
  });

  group('confirm password on sign-up', () {
    Future<_RecordingAuth> pumpSignUp(WidgetTester tester) async {
      final _RecordingAuth auth = _RecordingAuth();
      await tester.pumpWidget(
        MaterialApp(theme: buildAppTheme(), home: LoginScreen(auth: auth)),
      );
      await tester.tap(find.text('New here? Create an account'));
      await tester.pumpAndSettle();
      return auth;
    }

    Finder confirmField() =>
        find.widgetWithText(TextFormField, 'Confirm password');

    testWidgets('the field exists on sign-up only', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(
        MaterialApp(theme: buildAppTheme(), home: const LoginScreen()),
      );
      expect(confirmField(), findsNothing);

      await tester.tap(find.text('New here? Create an account'));
      await tester.pumpAndSettle();
      expect(confirmField(), findsOneWidget);

      await tester.scrollUntilVisible(
        find.text('Already have an account? Sign in'),
        200,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.tap(find.text('Already have an account? Sign in'));
      await tester.pumpAndSettle();
      expect(confirmField(), findsNothing);
    });

    testWidgets('it is obscured, toggles on its own, and autofills as a new '
        'password', (WidgetTester tester) async {
      await pumpSignUp(tester);
      EditableText confirm() => tester.widget<EditableText>(
            find.descendant(
              of: confirmField(),
              matching: find.byType(EditableText),
            ),
          );
      EditableText password() => tester.widget<EditableText>(
            find.descendant(
              of: find.widgetWithText(TextFormField, 'Password'),
              matching: find.byType(EditableText),
            ),
          );

      expect(confirm().obscureText, isTrue);
      expect(confirm().autofillHints, <String>[AutofillHints.newPassword]);

      await tester.tap(
        find.descendant(of: confirmField(), matching: find.byType(IconButton)),
      );
      await tester.pumpAndSettle();
      expect(confirm().obscureText, isFalse);
      // Its own toggle: revealing the confirmation leaves the password hidden.
      expect(password().obscureText, isTrue);
    });

    testWidgets('a mismatch is an inline error and signUp is never called', (
      WidgetTester tester,
    ) async {
      final _RecordingAuth auth = await pumpSignUp(tester);
      await tester.enterText(
          find.byType(TextFormField).first, 'player@example.com');
      await tester.enterText(
          find.widgetWithText(TextFormField, 'Password'), 'abc123');
      await tester.enterText(confirmField(), 'abc124');
      await tester.tap(find.widgetWithText(FilledButton, 'Create account'));
      await tester.pumpAndSettle();

      expect(find.text('The passwords do not match.'), findsOneWidget);
      expect(auth.signUps, isEmpty);
    });

    testWidgets('an empty confirmation also blocks the submit', (
      WidgetTester tester,
    ) async {
      final _RecordingAuth auth = await pumpSignUp(tester);
      await tester.enterText(
          find.byType(TextFormField).first, 'player@example.com');
      await tester.enterText(
          find.widgetWithText(TextFormField, 'Password'), 'abc123');
      await tester.tap(find.widgetWithText(FilledButton, 'Create account'));
      await tester.pumpAndSettle();

      expect(find.text('Enter your password again.'), findsOneWidget);
      expect(auth.signUps, isEmpty);
    });

    testWidgets('matching passwords go through to signUp', (
      WidgetTester tester,
    ) async {
      final _RecordingAuth auth = await pumpSignUp(tester);
      await tester.enterText(
          find.byType(TextFormField).first, 'player@example.com');
      await tester.enterText(
          find.widgetWithText(TextFormField, 'Password'), 'abc123');
      await tester.enterText(confirmField(), 'abc123');
      await tester.tap(find.widgetWithText(FilledButton, 'Create account'));
      await tester.pumpAndSettle();

      expect(find.text('The passwords do not match.'), findsNothing);
      expect(auth.signUps, <String>['player@example.com']);
      expect(auth.lastPassword, 'abc123');
      expect(find.textContaining('Account created.'), findsOneWidget);
    });
  });
}
