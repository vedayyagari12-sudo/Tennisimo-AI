/// Pure logic behind "Delete account".
///
/// No network, no `BuildContext`: the dashboard does the I/O and hands the
/// results here, so the confirmation rule and the failure copy are testable on
/// their own.
library;

import 'api_client.dart';

/// What the user must type to unlock the delete button.
///
/// A typed word, not just a second tap, because this is the one action in the
/// app that cannot be undone and it sits one row below "Sign out" in the same
/// menu. A plain OK/Cancel dialog is answered by reflex; typing a word is not.
/// "DELETE" rather than the account's email: the email can be long, awkward on
/// a phone keyboard, and is not always known to the client, while one fixed
/// word is equally deliberate and always available.
const String kDeleteConfirmationPhrase = 'DELETE';

/// Whether [typed] unlocks the delete button.
///
/// Surrounding whitespace is forgiven (keyboards add it); anything else must
/// match exactly, case included, so the word is typed on purpose.
bool deleteConfirmationMatches(String typed) =>
    typed.trim() == kDeleteConfirmationPhrase;

/// The one-time message the login screen shows after a confirmed deletion.
///
/// Only ever posted after the server answered success, so it states a fact.
const String kAccountDeletedNotice =
    'Your account and every recorded swing and video have been permanently '
    'deleted.';

/// What the user is told when the delete request did not succeed.
///
/// The server's (or network's) own explanation always leads — never a generic
/// stand-in — and a second paragraph says plainly what state the account is
/// in, because "did my data get deleted or not?" is the question the user is
/// left with. The server side is built so a repeat request finishes whatever a
/// failed one left behind, so where the outcome is uncertain the copy says
/// retrying is safe and expected.
///
/// The error code is resolved into [ApiFailure.message] here and not carried
/// forward, so [ApiFailure.plainLanguage] of the result is exactly this text.
/// [ApiFailure.technicalDetail] rides along for debug builds.
ApiFailure accountDeletionFailure(ApiFailure failure) {
  return ApiFailure(
    kind: failure.kind,
    message: '${failure.plainLanguage}\n\n${_accountState(failure)}',
    statusCode: failure.statusCode,
    retryable: failure.retryable,
    technicalDetail: failure.technicalDetail,
  );
}

String _accountState(ApiFailure failure) {
  const String retrySafe =
      'Trying again is safe, and is the expected fix: it '
      'picks up wherever this attempt stopped.';

  if (failure.kind == ApiFailureKind.notSignedIn) {
    return 'Nothing was deleted: the request was never sent.';
  }
  if (failure.kind == ApiFailureKind.network) {
    return 'This app cannot tell whether the request reached the server, so '
        'your account may be untouched or partly deleted. $retrySafe';
  }
  // The server rejects a bad token before it touches any data.
  if (failure.statusCode == 401) {
    return 'Nothing was deleted.';
  }
  if (failure.retryable == false) {
    return 'Your account has not been fully deleted.';
  }
  return 'Your account has not been fully deleted, and some of your data may '
      'already be gone. $retrySafe';
}
