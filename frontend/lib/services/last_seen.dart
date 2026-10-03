/// "When did this user last open the app" — recorded for the owner's view in
/// the Supabase dashboard. Nothing here is ever shown to the user.
///
/// The client never sends a timestamp: the `touch_last_seen` database function
/// stamps the caller's row with the SERVER clock, so the recorded time cannot
/// be forged from the device. See `docs/DATABASE_SETUP.md`, Part 8.
///
/// Telemetry must never break or slow the app: [LastSeenRecorder] is
/// fire-and-forget, never throws, and never surfaces an error.
library;

import 'package:flutter/foundation.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

/// The shortest gap between two recording attempts triggered by resumes.
const Duration kLastSeenMinGap = Duration(minutes: 10);

/// The database function that stamps the caller's row.
const String kTouchLastSeenFunction = 'touch_last_seen';

/// Whether an app open at [now] should be recorded, given the time of the
/// previous attempt. No previous attempt always records.
bool shouldRecordOpen({
  required DateTime now,
  DateTime? lastAttempt,
  Duration minGap = kLastSeenMinGap,
}) {
  if (lastAttempt == null) return true;
  return now.difference(lastAttempt) >= minGap;
}

/// One of `'web'`, `'android'`, `'ios'` or `'other'`.
///
/// [isWeb] wins over [platform]: a browser on an Android phone is `'web'`.
/// The app passes `kIsWeb` and `defaultTargetPlatform`.
String lastSeenPlatform({
  required bool isWeb,
  required TargetPlatform platform,
}) {
  if (isWeb) return 'web';
  switch (platform) {
    case TargetPlatform.android:
      return 'android';
    case TargetPlatform.iOS:
      return 'ios';
    case TargetPlatform.fuchsia:
    case TargetPlatform.linux:
    case TargetPlatform.macOS:
    case TargetPlatform.windows:
      return 'other';
  }
}

/// Calls a database function by name with named parameters.
typedef LastSeenTransport =
    Future<void> Function(String fn, Map<String, dynamic> params);

/// The real transport: a Supabase RPC as the signed-in user.
Future<void> supabaseRpcTransport(
  String fn,
  Map<String, dynamic> params,
) async {
  await Supabase.instance.client.rpc<dynamic>(fn, params: params);
}

String _currentPlatform() =>
    lastSeenPlatform(isWeb: kIsWeb, platform: defaultTargetPlatform);

/// Records an app open through `touch_last_seen`.
///
/// [transport], [clock] and [platform] are test seams; the app passes none of
/// them and gets the live Supabase client, the wall clock and the real
/// platform. The clock only drives the local throttle — it is never sent.
class LastSeenRecorder {
  LastSeenRecorder({
    LastSeenTransport? transport,
    DateTime Function()? clock,
    String Function()? platform,
    this.minGap = kLastSeenMinGap,
  }) : _transport = transport ?? supabaseRpcTransport,
       _clock = clock ?? DateTime.now,
       _platform = platform ?? _currentPlatform;

  final LastSeenTransport _transport;
  final DateTime Function() _clock;
  final String Function() _platform;

  /// The throttle applied by [recordIfDue].
  final Duration minGap;

  DateTime? _lastAttempt;

  /// When the last attempt started, successful or not; null before the first.
  DateTime? get lastAttempt => _lastAttempt;

  /// Records now, unconditionally. Never throws.
  ///
  /// The attempt time is stored BEFORE the call, so a failing call (for
  /// example the database function does not exist yet) still counts, and is
  /// retried at most once per [minGap] rather than on every resume.
  Future<void> record() async {
    try {
      _lastAttempt = _clock();
      await _transport(kTouchLastSeenFunction, <String, dynamic>{
        'p_platform': _platform(),
      });
    } catch (_) {
      // Everything, Errors included: `Supabase.instance` throws an
      // AssertionError when Supabase is not initialised. Telemetry is silent.
    }
  }

  /// Records only if [minGap] has passed since the last attempt. Never throws.
  Future<void> recordIfDue() async {
    try {
      if (!shouldRecordOpen(
        now: _clock(),
        lastAttempt: _lastAttempt,
        minGap: minGap,
      )) {
        return;
      }
    } catch (_) {
      return;
    }
    await record();
  }
}
