/// Pure logic behind the sign-in / sign-up screen.
///
/// Everything here is a function of its arguments: no Supabase client, no
/// network, no `BuildContext`. The screen does the I/O and hands the results to
/// these functions, which is what makes the validation rules and — more
/// importantly — the error mapping unit-testable without a live project.
library;

import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:supabase_flutter/supabase_flutter.dart' show AuthException;

import 'api_client.dart';

/// Which form the screen is showing. The two share one layout and one set of
/// fields; only the copy, the validation and a few error messages differ.
enum AuthMode {
  signIn,
  signUp;

  bool get isSignUp => this == AuthMode.signUp;
}

/// What actually happened when `auth.signUp` returned without throwing.
///
/// This exists because "signUp did not throw" is NOT the same as "the user is
/// signed in". With email confirmation switched on — which it is on this
/// project: `GET /auth/v1/settings` reports `mailer_autoconfirm: false` — a
/// successful sign-up returns a user and NO session, and the account does
/// nothing until the emailed link is opened. Showing a logged-in state there
/// would be a lie the app cannot back up.
enum SignUpOutcome {
  /// A session came back: the user is genuinely signed in right now.
  sessionStarted,

  /// The account was created but is inert until the emailed link is opened.
  confirmationRequired,

  /// The email already has an account.
  ///
  /// Supabase can report this two ways depending on the project's user
  /// enumeration setting: as a `user_already_exists` / `email_exists` error, or
  /// as a *success* carrying an obfuscated user with an empty `identities`
  /// list. [classifySignUp] catches the second shape; [authFailure] the first.
  alreadyRegistered,
}

/// Shortest password the auth server will accept, checked live against
/// `POST /auth/v1/signup`, which answers `weak_password` with "Password should
/// be at least 6 characters." below this.
///
/// Duplicating the server's rule client-side is only a courtesy — it turns one
/// round trip into instant feedback. The server stays the authority, and a
/// stricter server policy still surfaces verbatim through [authFailure].
const int kMinPasswordLength = 6;

/// Local email shape check: some text, an `@`, then a dotted domain.
///
/// Deliberately loose. The point is to catch a missing `@` or a half-typed
/// address before spending a round trip, not to adjudicate RFC 5322 — the
/// server's own `validation_failed` is the real verdict and is surfaced as
/// such.
final RegExp _emailShape = RegExp(r'^[^@\s]+@[^@\s]+\.[^@\s]+$');

/// Validation error for the email field, or null when it is acceptable.
String? validateEmail(String raw) {
  final String value = raw.trim();
  if (value.isEmpty) return 'Enter your email address.';
  if (!_emailShape.hasMatch(value)) {
    return 'That does not look like an email address.';
  }
  return null;
}

/// Validation error for the password field, or null when it is acceptable.
///
/// The length rule applies to [AuthMode.signUp] only. On sign-in the password
/// is a fact about an account that already exists, so refusing to even try a
/// short one would be this app inventing a verdict it is not entitled to.
String? validatePassword(String raw, {required AuthMode mode}) {
  if (raw.isEmpty) return 'Enter your password.';
  if (mode.isSignUp && raw.length < kMinPasswordLength) {
    return 'Use at least $kMinPasswordLength characters.';
  }
  return null;
}

/// Reads a sign-up response, or null if its shape says nothing conclusive.
///
/// Null is returned rather than a guess: a response with neither a session nor
/// a user is a case this client does not understand, and the screen says that
/// instead of claiming an account was made.
///
/// [identityCount] is `user.identities?.length`, passed as a number so this
/// stays free of gotrue model construction.
SignUpOutcome? classifySignUp({
  required bool hasSession,
  required bool hasUser,
  required int? identityCount,
}) {
  if (hasSession) return SignUpOutcome.sessionStarted;
  if (!hasUser) return null;
  if (identityCount == 0) return SignUpOutcome.alreadyRegistered;
  return SignUpOutcome.confirmationRequired;
}

/// Maps a thrown auth error onto the app's one failure type.
///
/// Every branch names the actual cause. There is no "something went wrong"
/// here: where the server told us what happened, the user is told what
/// happened, and the raw exception rides along in
/// [ApiFailure.technicalDetail] for debug builds only.
///
/// [isWeb] is forwarded to [unreachableServerMessage] purely so both of its
/// branches are reachable from the VM test runner.
ApiFailure authFailure(
  Object error, {
  required AuthMode mode,
  bool? isWeb,
}) {
  final String lead = mode.isSignUp
      ? 'Could not reach the sign-up server.'
      : 'Could not reach the sign-in server.';

  if (error is! AuthException) {
    // A `SocketException`, a browser `ClientException`, a DNS failure: nothing
    // reached the auth API, so this is the same story as any other dead
    // request and reuses that copy.
    return ApiFailure(
      kind: ApiFailureKind.network,
      message: unreachableServerMessage(
        lead: lead,
        isWeb: isWeb ?? kIsWeb,
      ),
      technicalDetail: '$error',
    );
  }

  // gotrue reports a transport failure as an AuthException subclass with no
  // code, so it is identified by its absent code plus a non-numeric status.
  if (error.code == null && int.tryParse(error.statusCode ?? '') == null) {
    return ApiFailure(
      kind: ApiFailureKind.network,
      message: unreachableServerMessage(
        lead: lead,
        isWeb: isWeb ?? kIsWeb,
      ),
      technicalDetail: '$error',
    );
  }

  final String? message = switch (error.code) {
    'invalid_credentials' =>
      'That email and password do not match an account. Check the password, '
          'or create an account if you do not have one yet.',
    'email_not_confirmed' =>
      'This account is not confirmed yet. Open the confirmation link in the '
          'email we sent you, then sign in.',
    'user_already_exists' || 'email_exists' =>
      'That email already has an account. Switch to sign in instead.',
    // The server's own weak-password text names the rule it enforced ("at
    // least 6 characters", or whatever the project policy says today), which
    // is more specific than anything this client could restate.
    'weak_password' => error.message,
    'validation_failed' =>
      'The server would not accept that email address. Check it for typos.',
    'over_email_send_rate_limit' =>
      'Too many confirmation emails have been requested for that address. '
          'Wait a minute, then try again.',
    'over_request_rate_limit' =>
      'Too many attempts from this device. Wait a minute, then try again.',
    'signup_disabled' => 'New accounts are switched off for this app.',
    'user_banned' => 'This account has been disabled.',
    'email_provider_disabled' =>
      'Email sign-in is switched off for this app.',
    _ => null,
  };

  return ApiFailure(
    kind: ApiFailureKind.server,
    // An unrecognised code still carries the server's own message rather than
    // a generic stand-in: it is written for humans and it is the only account
    // of the failure anyone has.
    message: message ?? error.message,
    errorCode: error.code,
    statusCode: int.tryParse(error.statusCode ?? ''),
    technicalDetail: '$error',
  );
}
