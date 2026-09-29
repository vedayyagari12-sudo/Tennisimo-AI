/// Account deletion, end to end on the client: the `DELETE /v1/account`
/// call, the pure confirmation / failure-copy rules, the dashboard's
/// confirm-then-delete dialog, and what the login screen does afterwards —
/// including signing up again with the same email.
library;

import 'dart:async';
import 'dart:io' show SocketException;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:supabase_flutter/supabase_flutter.dart'
    show AuthResponse, User, UserIdentity;
import 'package:tennisimo_ai/models/analysis_response.dart';
import 'package:tennisimo_ai/screens/dashboard_screen.dart';
import 'package:tennisimo_ai/screens/login_screen.dart';
import 'package:tennisimo_ai/services/account_deletion.dart';
import 'package:tennisimo_ai/services/api_client.dart';
import 'package:tennisimo_ai/services/haptics.dart';
import 'package:tennisimo_ai/theme/app_theme.dart';

import 'support/dashboard_fixtures.dart';

// ---------------------------------------------------------------------------
// Fakes
// ---------------------------------------------------------------------------

class _RecordingDriver implements HapticDriver {
  final List<HapticKind> calls = <HapticKind>[];

  @override
  Future<void> send(HapticKind kind) async => calls.add(kind);
}

/// A dashboard source whose delete request the test answers by hand, and
/// whose sign-out flips [signedIn] the way the real auth stream flips AuthGate.
class _DeletingSource extends FakeDashboardDataSource {
  _DeletingSource() : super(history: const <AnalysisSummary>[]);

  final ValueNotifier<bool> signedIn = ValueNotifier<bool>(true);
  final List<Completer<ApiResult<void>>> deletes =
      <Completer<ApiResult<void>>>[];
  int signOuts = 0;

  @override
  Future<ApiResult<void>> deleteAccount() {
    final Completer<ApiResult<void>> request = Completer<ApiResult<void>>();
    deletes.add(request);
    return request.future;
  }

  @override
  Future<void> signOut() async {
    signOuts++;
    signedIn.value = false;
  }
}

/// Answers every sign-up the way this project's auth server answers a
/// FIRST-TIME sign-up with email confirmation on: a user with one identity
/// and no session. (An already-registered email comes back with zero
/// identities, or as a `user_already_exists` error.)
class _FirstTimeSignUpAuth extends LoginAuthActions {
  final List<String> signUps = <String>[];

  @override
  Future<AuthResponse> signUp({
    required String email,
    required String password,
  }) async {
    signUps.add(email);
    return AuthResponse(
      user: User(
        id: 'fresh-user',
        appMetadata: const <String, dynamic>{},
        userMetadata: const <String, dynamic>{},
        aud: 'authenticated',
        email: email,
        createdAt: '2026-09-29T00:00:00Z',
        identities: <UserIdentity>[
          UserIdentity(
            id: 'fresh-user',
            userId: 'fresh-user',
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

/// A stand-in for AuthGate: dashboard while signed in, login once not.
Widget _app(_DeletingSource source, LoginAuthActions auth) => MaterialApp(
  theme: buildAppTheme(),
  home: ValueListenableBuilder<bool>(
    valueListenable: source.signedIn,
    builder: (BuildContext context, bool signedIn, Widget? _) => signedIn
        ? DashboardScreen(dataSource: source)
        : LoginScreen(auth: auth),
  ),
);

Future<void> _settle(WidgetTester tester) async {
  for (int i = 0; i < 10; i++) {
    await tester.pump(const Duration(milliseconds: 100));
  }
}

Future<void> _openDialog(WidgetTester tester) async {
  await tester.tap(find.byIcon(Icons.more_vert));
  await _settle(tester);
  await tester.tap(find.text('Delete account'));
  await _settle(tester);
}

Finder get _deleteButton => find.widgetWithText(TextButton, 'Delete forever');

TextButton _button(WidgetTester tester, String label) =>
    tester.widget<TextButton>(find.widgetWithText(TextButton, label));

const String _email = 'player@example.com';

/// Switches a login screen to sign-up and creates an account.
Future<void> _signUp(WidgetTester tester) async {
  await tester.tap(find.text('New here? Create an account'));
  await _settle(tester);
  await tester.enterText(find.byType(TextFormField).first, _email);
  await tester.enterText(
    find.widgetWithText(TextFormField, 'Password'),
    'abc123',
  );
  await tester.enterText(
    find.widgetWithText(TextFormField, 'Confirm password'),
    'abc123',
  );
  await tester.tap(find.widgetWithText(FilledButton, 'Create account'));
  await _settle(tester);
}

void main() {
  // -------------------------------------------------------------------------
  group('deleteAccount()', () {
    test(
      'sends DELETE /v1/account with the bearer token and no body',
      () async {
        late http.Request sent;
        final ApiResult<void> result = await deleteAccount(
          accessToken: () => 'jwt-123',
          client: MockClient((http.Request request) async {
            sent = request;
            return http.Response('', 204);
          }),
        );

        expect(result.isOk, isTrue);
        expect(sent.method, 'DELETE');
        expect(sent.url.toString(), '$baseUrl/v1/account');
        expect(sent.headers['Authorization'], 'Bearer jwt-123');
        expect(sent.body, isEmpty);
      },
    );

    test('with no session nothing is sent', () async {
      int requests = 0;
      final ApiResult<void> result = await deleteAccount(
        accessToken: () => null,
        client: MockClient((http.Request request) async {
          requests++;
          return http.Response('', 204);
        }),
      );

      expect(requests, 0);
      expect(result.failure!.kind, ApiFailureKind.notSignedIn);
    });

    test('an error envelope becomes the usual ApiFailure', () async {
      final ApiResult<void> result = await deleteAccount(
        accessToken: () => 'jwt',
        client: MockClient(
          (http.Request request) async => http.Response(
            '{"code":"storage_unavailable","message":"Storage is down.",'
            '"retryable":true}',
            503,
          ),
        ),
      );

      final ApiFailure failure = result.failure!;
      expect(failure.kind, ApiFailureKind.server);
      expect(failure.statusCode, 503);
      expect(failure.errorCode, 'storage_unavailable');
      expect(failure.retryable, isTrue);
    });

    test('a dead connection is a network failure', () async {
      final ApiResult<void> result = await deleteAccount(
        accessToken: () => 'jwt',
        client: MockClient(
          (http.Request request) async => throw const SocketException('nope'),
        ),
      );

      expect(result.failure!.kind, ApiFailureKind.network);
      expect(result.failure!.technicalDetail, contains('nope'));
    });
  });

  // -------------------------------------------------------------------------
  group('the typed confirmation', () {
    test('only the exact word unlocks it', () {
      expect(deleteConfirmationMatches('DELETE'), isTrue);
      expect(deleteConfirmationMatches('  DELETE '), isTrue);
      expect(deleteConfirmationMatches('delete'), isFalse);
      expect(deleteConfirmationMatches('DELET'), isFalse);
      expect(deleteConfirmationMatches('DELETE IT'), isFalse);
      expect(deleteConfirmationMatches(''), isFalse);
    });
  });

  // -------------------------------------------------------------------------
  group('failure copy', () {
    test(
      'a server failure leads with the server and says retrying is safe',
      () {
        final ApiFailure failure = accountDeletionFailure(
          const ApiFailure(
            kind: ApiFailureKind.server,
            message: 'Could not remove the stored videos.',
            statusCode: 500,
            retryable: true,
            technicalDetail: 'raw',
          ),
        );
        expect(
          failure.plainLanguage,
          startsWith('Could not remove the stored'),
        );
        expect(failure.plainLanguage, contains('not been fully deleted'));
        expect(failure.plainLanguage, contains('Trying again is safe'));
        expect(failure.technicalDetail, 'raw');
        expect(
          failure.plainLanguage.toLowerCase(),
          isNot(contains('something went wrong')),
        );
      },
    );

    test('a known error code is resolved, not shown raw', () {
      final ApiFailure failure = accountDeletionFailure(
        const ApiFailure(
          kind: ApiFailureKind.server,
          message: 'x',
          errorCode: 'storage_unavailable',
          statusCode: 503,
        ),
      );
      expect(
        failure.plainLanguage,
        startsWith('Video storage is temporarily unavailable.'),
      );
      expect(failure.plainLanguage, isNot(contains('storage_unavailable')));
    });

    test('a network failure admits the outcome is unknown', () {
      final String text = accountDeletionFailure(
        networkFailure(const SocketException('nope')),
      ).plainLanguage;
      expect(text, contains('cannot tell whether the request reached'));
      expect(text, contains('Trying again is safe'));
    });

    test(
      'a rejected token and a missing session both say nothing was deleted',
      () {
        expect(
          accountDeletionFailure(
            const ApiFailure(
              kind: ApiFailureKind.server,
              message: 'x',
              errorCode: 'auth_invalid_token',
              statusCode: 401,
            ),
          ).plainLanguage,
          contains('Nothing was deleted.'),
        );
        expect(
          accountDeletionFailure(
            const ApiFailure(
              kind: ApiFailureKind.notSignedIn,
              message: 'You are not signed in.',
            ),
          ).plainLanguage,
          contains('Nothing was deleted'),
        );
      },
    );

    test('a non-retryable failure does not promise a retry will help', () {
      final String text = accountDeletionFailure(
        const ApiFailure(
          kind: ApiFailureKind.server,
          message: 'No.',
          statusCode: 409,
          retryable: false,
        ),
      ).plainLanguage;
      expect(text, contains('not been fully deleted'));
      expect(text, isNot(contains('Trying again is safe')));
    });
  });

  // -------------------------------------------------------------------------
  group('the dashboard flow', () {
    late _RecordingDriver driver;
    late Haptics previousHaptics;

    setUp(() {
      driver = _RecordingDriver();
      previousHaptics = haptics;
      haptics = Haptics(driver: driver, isWeb: false);
      // Nothing from an earlier test may leak into this one.
      LoginNotice.take();
    });

    tearDown(() {
      haptics = previousHaptics;
      LoginNotice.take();
    });

    Future<_DeletingSource> pump(
      WidgetTester tester, {
      LoginAuthActions auth = const LoginAuthActions(),
    }) async {
      tester.view.physicalSize = const Size(412, 915);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);
      final _DeletingSource source = _DeletingSource();
      await tester.pumpWidget(_app(source, auth));
      await _settle(tester);
      return source;
    }

    testWidgets('the menu entry sits below Sign out in the error colour', (
      WidgetTester tester,
    ) async {
      await pump(tester);
      await tester.tap(find.byIcon(Icons.more_vert));
      await _settle(tester);

      final Finder delete = find.text('Delete account');
      expect(delete, findsOneWidget);
      expect(
        tester.getTopLeft(delete).dy,
        greaterThan(tester.getTopLeft(find.text('Sign out')).dy),
      );
      final Text label = tester.widget<Text>(delete);
      expect(
        label.style?.color,
        Theme.of(tester.element(delete)).colorScheme.error,
      );
    });

    testWidgets('choosing it only asks: the dialog states the stakes', (
      WidgetTester tester,
    ) async {
      final _DeletingSource source = await pump(tester);
      await _openDialog(tester);

      expect(find.text('Delete your account?'), findsOneWidget);
      final String body = tester
          .widgetList<Text>(find.byType(Text))
          .map((Text t) => t.data ?? '')
          .join(' ');
      expect(body, contains('permanently deletes your account'));
      expect(body, contains('every swing'));
      expect(body, contains('videos'));
      expect(body, contains('cannot be undone'));
      expect(source.deletes, isEmpty);
      expect(driver.calls, isEmpty);
    });

    testWidgets('the delete button stays locked until DELETE is typed', (
      WidgetTester tester,
    ) async {
      final _DeletingSource source = await pump(tester);
      await _openDialog(tester);

      expect(_button(tester, 'Delete forever').onPressed, isNull);
      await tester.enterText(find.byType(TextField).last, 'delete');
      await tester.pump();
      expect(_button(tester, 'Delete forever').onPressed, isNull);

      await tester.enterText(find.byType(TextField).last, 'DELETE');
      await tester.pump();
      expect(_button(tester, 'Delete forever').onPressed, isNotNull);
      expect(source.deletes, isEmpty);
    });

    testWidgets('Cancel closes it and deletes nothing', (
      WidgetTester tester,
    ) async {
      final _DeletingSource source = await pump(tester);
      await _openDialog(tester);
      await tester.enterText(find.byType(TextField).last, 'DELETE');
      await tester.tap(find.text('Cancel'));
      await _settle(tester);

      expect(find.text('Delete your account?'), findsNothing);
      expect(source.deletes, isEmpty);
      expect(source.signOuts, 0);
    });

    testWidgets(
      'while in flight: one request, one haptic, nothing can re-fire or close',
      (WidgetTester tester) async {
        final _DeletingSource source = await pump(tester);
        await _openDialog(tester);
        await tester.enterText(find.byType(TextField).last, 'DELETE');
        await tester.pump();

        // A double tap inside ONE frame: the second lands before any rebuild
        // has disabled the button, so only the synchronous guard stops it.
        await tester.tap(_deleteButton);
        await tester.tap(_deleteButton);
        await tester.pump();
        // And once rebuilt, a tap on the relabelled, disabled button.
        await tester.tap(
          find.widgetWithText(TextButton, 'Deleting…'),
          warnIfMissed: false,
        );
        await tester.pump();

        expect(source.deletes, hasLength(1));
        expect(driver.calls, <HapticKind>[HapticKind.medium]);
        expect(_button(tester, 'Deleting…').onPressed, isNull);
        expect(_button(tester, 'Cancel').onPressed, isNull);

        // Back is refused too.
        await tester.binding.handlePopRoute();
        await tester.pump();
        expect(find.text('Delete your account?'), findsOneWidget);

        // And no success is claimed while the answer is pending.
        expect(source.signOuts, 0);
        expect(find.byType(LoginScreen), findsNothing);

        source.deletes.single.complete(const ApiResult<void>.ok(null));
        await _settle(tester);
      },
    );

    testWidgets('success signs out and the login screen says what happened', (
      WidgetTester tester,
    ) async {
      final _DeletingSource source = await pump(tester);
      await _openDialog(tester);
      await tester.enterText(find.byType(TextField).last, 'DELETE');
      await tester.pump();
      await tester.tap(_deleteButton);
      await tester.pump();

      source.deletes.single.complete(const ApiResult<void>.ok(null));
      await _settle(tester);

      expect(source.signOuts, 1);
      expect(find.byType(DashboardScreen), findsNothing);
      expect(find.byType(LoginScreen), findsOneWidget);
      expect(find.text(kAccountDeletedNotice), findsOneWidget);
      // Shown once: it was consumed by the login screen that showed it.
      expect(LoginNotice.take(), isNull);
    });

    testWidgets('failure keeps the user signed in and says retrying is safe', (
      WidgetTester tester,
    ) async {
      final _DeletingSource source = await pump(tester);
      await _openDialog(tester);
      await tester.enterText(find.byType(TextField).last, 'DELETE');
      await tester.pump();
      await tester.tap(_deleteButton);
      await tester.pump();

      source.deletes.single.complete(
        const ApiResult<void>.err(
          ApiFailure(
            kind: ApiFailureKind.server,
            message: 'Could not remove the stored videos.',
            statusCode: 500,
            retryable: true,
          ),
        ),
      );
      await _settle(tester);

      expect(find.text('Delete your account?'), findsOneWidget);
      expect(
        find.textContaining('Could not remove the stored videos.'),
        findsOneWidget,
      );
      expect(find.textContaining('Trying again is safe'), findsOneWidget);
      expect(source.signOuts, 0);
      expect(LoginNotice.take(), isNull);
      expect(find.byType(DashboardScreen), findsOneWidget);

      // The retry is one tap away and really sends a second request.
      await tester.tap(find.widgetWithText(TextButton, 'Try again'));
      await tester.pump();
      expect(source.deletes, hasLength(2));
      source.deletes.last.complete(const ApiResult<void>.ok(null));
      await _settle(tester);
      expect(find.text(kAccountDeletedNotice), findsOneWidget);
    });

    testWidgets(
      'signing up again with the same email after deletion is an ordinary '
      'first-time sign-up',
      (WidgetTester tester) async {
        // The baseline: a first-time sign-up on a cold login screen, with no
        // deletion anywhere in its past.
        final _FirstTimeSignUpAuth coldAuth = _FirstTimeSignUpAuth();
        tester.view.physicalSize = const Size(412, 915);
        tester.view.devicePixelRatio = 1.0;
        addTearDown(tester.view.reset);
        await tester.pumpWidget(
          MaterialApp(
            theme: buildAppTheme(),
            home: LoginScreen(auth: coldAuth),
          ),
        );
        await _settle(tester);
        await _signUp(tester);
        final String coldOutcome = tester
            .widgetList<Text>(find.textContaining('Account created.'))
            .single
            .data!;
        expect(coldAuth.signUps, <String>[_email]);

        // Now: signed in as that email, delete the account, land on login.
        await tester.pumpWidget(const SizedBox());
        final _FirstTimeSignUpAuth auth = _FirstTimeSignUpAuth();
        final _DeletingSource source = await pump(tester, auth: auth);
        await _openDialog(tester);
        await tester.enterText(find.byType(TextField).last, 'DELETE');
        await tester.pump();
        await tester.tap(_deleteButton);
        await tester.pump();
        source.deletes.single.complete(const ApiResult<void>.ok(null));
        await _settle(tester);
        expect(find.text(kAccountDeletedNotice), findsOneWidget);

        // Sign up again with the SAME email.
        await _signUp(tester);

        // The request went out — nothing local stood in its way — and the
        // client reads the answer exactly as it read the cold one.
        expect(auth.signUps, <String>[_email]);
        expect(
          tester
              .widgetList<Text>(find.textContaining('Account created.'))
              .single
              .data,
          coldOutcome,
        );
        expect(find.textContaining('already has an account'), findsNothing);
        // The deletion notice was replaced, not left lingering.
        expect(find.text(kAccountDeletedNotice), findsNothing);
      },
    );
  });
}
