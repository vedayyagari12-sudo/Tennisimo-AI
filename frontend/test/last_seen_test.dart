/// Last-opened telemetry: the throttle, the platform label, the recorder's
/// never-throw contract, and the shell's lifecycle wiring. All offline — the
/// RPC transport is a fake, and Supabase is initialised with no session only
/// so the shell's tabs can render signed-out.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:tennisimo_ai/screens/home_shell.dart';
import 'package:tennisimo_ai/services/last_seen.dart';
import 'package:tennisimo_ai/theme/app_theme.dart';

class _Call {
  _Call(this.fn, this.params);

  final String fn;
  final Map<String, dynamic> params;
}

/// Steps through every state, as the engine does: lifecycle listeners
/// assert that no state is skipped.
void _toBackground(WidgetTester tester) {
  for (final AppLifecycleState s in <AppLifecycleState>[
    AppLifecycleState.inactive,
    AppLifecycleState.hidden,
    AppLifecycleState.paused,
  ]) {
    tester.binding.handleAppLifecycleStateChanged(s);
  }
}

void _toForeground(WidgetTester tester) {
  for (final AppLifecycleState s in <AppLifecycleState>[
    AppLifecycleState.hidden,
    AppLifecycleState.inactive,
    AppLifecycleState.resumed,
  ]) {
    tester.binding.handleAppLifecycleStateChanged(s);
  }
}

void main() {
  group('shouldRecordOpen', () {
    final DateTime t0 = DateTime.utc(2026, 10, 3, 12);

    test('no previous attempt records', () {
      expect(shouldRecordOpen(now: t0), isTrue);
    });

    test('just under the gap does not record', () {
      expect(
        shouldRecordOpen(
          now: t0.add(kLastSeenMinGap - const Duration(milliseconds: 1)),
          lastAttempt: t0,
        ),
        isFalse,
      );
    });

    test('exactly at the gap records', () {
      expect(
        shouldRecordOpen(now: t0.add(kLastSeenMinGap), lastAttempt: t0),
        isTrue,
      );
    });

    test('over the gap records', () {
      expect(
        shouldRecordOpen(
          now: t0.add(kLastSeenMinGap + const Duration(seconds: 1)),
          lastAttempt: t0,
        ),
        isTrue,
      );
    });

    test('the default gap is ten minutes', () {
      expect(kLastSeenMinGap, const Duration(minutes: 10));
    });
  });

  group('lastSeenPlatform', () {
    test('web wins over every target platform', () {
      for (final TargetPlatform p in TargetPlatform.values) {
        expect(lastSeenPlatform(isWeb: true, platform: p), 'web');
      }
    });

    test('android and ios', () {
      expect(
        lastSeenPlatform(isWeb: false, platform: TargetPlatform.android),
        'android',
      );
      expect(
        lastSeenPlatform(isWeb: false, platform: TargetPlatform.iOS),
        'ios',
      );
    });

    test('everything else is other', () {
      for (final TargetPlatform p in <TargetPlatform>[
        TargetPlatform.fuchsia,
        TargetPlatform.linux,
        TargetPlatform.macOS,
        TargetPlatform.windows,
      ]) {
        expect(lastSeenPlatform(isWeb: false, platform: p), 'other');
      }
    });
  });

  group('LastSeenRecorder', () {
    final DateTime t0 = DateTime.utc(2026, 10, 3, 12);

    test('calls touch_last_seen with exactly {p_platform}', () async {
      final List<_Call> calls = <_Call>[];
      final LastSeenRecorder recorder = LastSeenRecorder(
        transport: (String fn, Map<String, dynamic> params) async =>
            calls.add(_Call(fn, params)),
        clock: () => t0,
        platform: () => 'android',
      );

      await recorder.record();

      expect(calls, hasLength(1));
      expect(calls.single.fn, 'touch_last_seen');
      expect(calls.single.params, <String, dynamic>{'p_platform': 'android'});
      // No timestamp of any kind leaves the device.
      for (final Object? value in calls.single.params.values) {
        expect(value, isNot(isA<DateTime>()));
      }
      expect(recorder.lastAttempt, t0);
    });

    test('swallows an Exception and still records the attempt', () async {
      final LastSeenRecorder recorder = LastSeenRecorder(
        transport: (String fn, Map<String, dynamic> params) async =>
            throw Exception('function does not exist'),
        clock: () => t0,
        platform: () => 'web',
      );

      await expectLater(recorder.record(), completes);
      expect(recorder.lastAttempt, t0);
    });

    test('swallows an AssertionError and still records the attempt', () async {
      final LastSeenRecorder recorder = LastSeenRecorder(
        transport: (String fn, Map<String, dynamic> params) async =>
            throw AssertionError('Supabase not initialised'),
        clock: () => t0,
        platform: () => 'web',
      );

      await expectLater(recorder.record(), completes);
      expect(recorder.lastAttempt, t0);
    });

    test('swallows a synchronous throw from the transport', () async {
      final LastSeenRecorder recorder = LastSeenRecorder(
        transport: (String fn, Map<String, dynamic> params) =>
            throw StateError('sync'),
        clock: () => t0,
        platform: () => 'web',
      );

      await expectLater(recorder.record(), completes);
      expect(recorder.lastAttempt, t0);
    });

    test('the real transport without Supabase is swallowed too', () async {
      // Supabase is not initialised in this group: Supabase.instance throws
      // an AssertionError, which must not escape.
      final LastSeenRecorder recorder = LastSeenRecorder(
        clock: () => t0,
        platform: () => 'web',
      );

      await expectLater(recorder.record(), completes);
      expect(recorder.lastAttempt, t0);
    });

    test('a failed attempt throttles the retry', () async {
      DateTime now = t0;
      int calls = 0;
      final LastSeenRecorder recorder = LastSeenRecorder(
        transport: (String fn, Map<String, dynamic> params) async {
          calls++;
          throw Exception('fails');
        },
        clock: () => now,
        platform: () => 'ios',
      );

      await recorder.recordIfDue();
      expect(calls, 1);

      now = t0.add(const Duration(minutes: 9));
      await recorder.recordIfDue();
      expect(calls, 1);

      now = t0.add(kLastSeenMinGap);
      await recorder.recordIfDue();
      expect(calls, 2);
    });
  });

  group('HomeShell', () {
    setUpAll(() async {
      SharedPreferences.setMockInitialValues(<String, Object>{});
      // No session: the shell's tabs take their signed-out path, which never
      // touches the network.
      await Supabase.initialize(
        url: 'https://example.invalid',
        publishableKey: 'test-key',
        authOptions: const FlutterAuthClientOptions(
          localStorage: EmptyLocalStorage(),
          detectSessionInUri: false,
          autoRefreshToken: false,
        ),
      );
    });

    testWidgets('records on open, on resume after the gap, never after '
        'dispose', (WidgetTester tester) async {
      final DateTime t0 = DateTime.utc(2026, 10, 3, 12);
      DateTime now = t0;
      final List<_Call> calls = <_Call>[];
      final LastSeenRecorder recorder = LastSeenRecorder(
        transport: (String fn, Map<String, dynamic> params) async =>
            calls.add(_Call(fn, params)),
        clock: () => now,
        platform: () => 'android',
      );

      await tester.pumpWidget(
        MaterialApp(
          theme: buildAppTheme(),
          home: HomeShell(lastSeenRecorder: recorder),
        ),
      );
      await tester.pump();

      // Once on first appearance.
      expect(calls, hasLength(1));
      expect(calls.single.fn, 'touch_last_seen');

      // inactive / hidden / paused never record, even once the gap has
      // elapsed.
      now = t0.add(kLastSeenMinGap);
      _toBackground(tester);
      await tester.pump();
      expect(calls, hasLength(1));

      // A resume inside the gap does not record.
      now = t0.add(kLastSeenMinGap - const Duration(seconds: 1));
      _toForeground(tester);
      await tester.pump();
      expect(calls, hasLength(1));

      // A resume once the gap has elapsed does.
      _toBackground(tester);
      now = t0.add(kLastSeenMinGap);
      _toForeground(tester);
      await tester.pump();
      expect(calls, hasLength(2));

      // Disposed: the observer is gone, so nothing records any more.
      await tester.pumpWidget(const SizedBox.shrink());
      _toBackground(tester);
      now = t0.add(kLastSeenMinGap * 5);
      _toForeground(tester);
      await tester.pump();
      expect(calls, hasLength(2));
    });
  });
}
