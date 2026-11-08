import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/models/analysis_response.dart';
import 'package:tennisimo_ai/models/enums.dart';
import 'package:tennisimo_ai/screens/record_screen.dart';
import 'package:tennisimo_ai/screens/results_screen.dart';
import 'package:tennisimo_ai/services/capture_prefs.dart';
import 'package:tennisimo_ai/theme/app_theme.dart';

/// In-memory [CapturePrefsStore]. [handGate] lets a test hold the read open.
class _MemoryStore implements CapturePrefsStore {
  _MemoryStore({this.hand, this.delay = 0, this.handGate});

  Handedness? hand;
  int delay;
  final Completer<void>? handGate;
  final List<Object?> writes = <Object?>[];

  @override
  Future<Handedness?> readRacketHand() async {
    await handGate?.future;
    return hand;
  }

  @override
  Future<void> writeRacketHand(Handedness? h) async {
    hand = h;
    writes.add(h);
  }

  @override
  Future<int> readStartDelay() async => delay;

  @override
  Future<void> writeStartDelay(int seconds) async {
    delay = seconds;
    writes.add(seconds);
  }
}

class _ThrowingStore implements CapturePrefsStore {
  @override
  Future<Handedness?> readRacketHand() => Future<Handedness?>.error('disk');
  @override
  Future<void> writeRacketHand(Handedness? h) => Future<void>.error('disk');
  @override
  Future<int> readStartDelay() => Future<int>.error('disk');
  @override
  Future<void> writeStartDelay(int s) => Future<void>.error('disk');
}

SegmentedButton<Handedness> _hand(WidgetTester tester) =>
    tester.widget<SegmentedButton<Handedness>>(
      find.byType(SegmentedButton<Handedness>),
    );

SegmentedButton<int> _delay(WidgetTester tester) =>
    tester.widget<SegmentedButton<int>>(find.byType(SegmentedButton<int>));

Future<void> _pumpRecord(WidgetTester tester, CapturePrefsStore store) async {
  tester.view.physicalSize = const Size(1000, 3200);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    theme: buildAppTheme(),
    home: RecordScreen(prefsStore: store),
  ));
  await tester.pumpAndSettle();
}

Map<String, dynamic> _payload() => <String, dynamic>{
      'analysis_id': 'a1',
      'status': 'complete',
      'shot_type': 'forehand',
      'scorecard': <String, dynamic>{
        'overall_score': 68.0,
        'categories': <Map<String, dynamic>>[],
        'metrics': <Map<String, dynamic>>[],
      },
    };

void main() {
  group('decoding stored capture settings', () {
    test('only a real racket hand survives', () {
      expect(decodeRacketHand('right'), Handedness.right);
      expect(decodeRacketHand('left'), Handedness.left);
      expect(decodeRacketHand('unknown'), isNull);
      expect(decodeRacketHand('ambidextrous'), isNull);
      expect(decodeRacketHand(null), isNull);
    });

    test('a start delay outside the offered choices is off', () {
      expect(decodeStartDelay(5), 5);
      expect(decodeStartDelay(10), 10);
      expect(decodeStartDelay(30), 0);
      expect(decodeStartDelay(-5), 0);
      expect(decodeStartDelay(null), 0);
    });
  });

  group('record button label', () {
    test('says what pressing it will do', () {
      const Duration full = kMaxRecordingDuration;
      expect(
        recordButtonLabel(
            isRecording: false, countdown: null, remaining: full,
            startDelaySeconds: 0),
        'Record',
      );
      expect(
        recordButtonLabel(
            isRecording: false, countdown: null, remaining: full,
            startDelaySeconds: 10),
        'Record in 10s',
      );
      expect(
        recordButtonLabel(
            isRecording: false, countdown: 3, remaining: full,
            startDelaySeconds: 10),
        'Cancel (starts in 3s)',
      );
      expect(
        recordButtonLabel(
            isRecording: true, countdown: null,
            remaining: const Duration(seconds: 7), startDelaySeconds: 10),
        'Stop (7s left)',
      );
    });
  });

  group('remembered capture settings', () {
    setUp(() {
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(
        const MethodChannel('plugins.flutter.io/camera'),
        (MethodCall call) async =>
            call.method == 'availableCameras' ? <Object?>[] : null,
      );
    });

    tearDown(() {
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(
        const MethodChannel('plugins.flutter.io/camera'),
        null,
      );
    });

    testWidgets('a first-time player still has to choose a hand',
        (WidgetTester tester) async {
      await _pumpRecord(tester, _MemoryStore());
      expect(_hand(tester).selected, isEmpty);
      expect(find.text('Remembered from last time. Tap to change.'),
          findsNothing);
    });

    testWidgets('last session\'s hand is preselected and labelled as such',
        (WidgetTester tester) async {
      await _pumpRecord(tester, _MemoryStore(hand: Handedness.left, delay: 5));
      expect(_hand(tester).selected, <Handedness>{Handedness.left});
      expect(find.text('Remembered from last time. Tap to change.'),
          findsOneWidget);
      expect(_delay(tester).selected, <int>{5});
      // No camera here, so the button is disabled, but it already says what
      // a press will do.
      expect(find.text('Record in 5s'), findsOneWidget);
    });

    testWidgets('a choice is written down, and clears the remembered label',
        (WidgetTester tester) async {
      final _MemoryStore store = _MemoryStore(hand: Handedness.left);
      await _pumpRecord(tester, store);

      await tester.tap(find.text('Right'));
      await tester.pumpAndSettle();
      expect(store.hand, Handedness.right);
      expect(find.text('Remembered from last time. Tap to change.'),
          findsNothing);

      await tester.tap(find.text('10 s'));
      await tester.pumpAndSettle();
      expect(store.delay, 10);
    });

    testWidgets('a slow read never overwrites a hand picked meanwhile',
        (WidgetTester tester) async {
      final Completer<void> gate = Completer<void>();
      final _MemoryStore store =
          _MemoryStore(hand: Handedness.left, handGate: gate);
      await _pumpRecord(tester, store);

      await tester.tap(find.text('Right'));
      await tester.pumpAndSettle();
      gate.complete();
      await tester.pumpAndSettle();

      expect(_hand(tester).selected, <Handedness>{Handedness.right});
    });

    testWidgets('a broken store just means nothing is remembered',
        (WidgetTester tester) async {
      await _pumpRecord(tester, _ThrowingStore());
      expect(_hand(tester).selected, isEmpty);
      expect(_delay(tester).selected, <int>{0});

      await tester.tap(find.text('Right'));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(_hand(tester).selected, <Handedness>{Handedness.right});
    });
  });

  group('record another swing', () {
    testWidgets('offered after a fresh analysis, and returns to recording',
        (WidgetTester tester) async {
      await tester.pumpWidget(MaterialApp(
        theme: buildAppTheme(),
        home: Builder(
          builder: (BuildContext context) => Scaffold(
            body: Center(
              child: TextButton(
                onPressed: () => Navigator.of(context).push(
                  MaterialPageRoute<void>(
                    builder: (_) => ResultsScreen(
                      analysis: AnalysisResponse.fromJson(_payload()),
                      offerRecordAnother: true,
                    ),
                  ),
                ),
                child: const Text('record screen'),
              ),
            ),
          ),
        ),
      ));
      await tester.tap(find.text('record screen'));
      await tester.pumpAndSettle();

      expect(find.text('Record another swing'), findsOneWidget);
      await tester.tap(find.text('Record another swing'));
      await tester.pumpAndSettle();
      expect(find.text('record screen'), findsOneWidget);
      expect(find.byType(ResultsScreen), findsNothing);
    });

    testWidgets('not offered when opened from history',
        (WidgetTester tester) async {
      await tester.pumpWidget(MaterialApp(
        theme: buildAppTheme(),
        home: ResultsScreen(analysis: AnalysisResponse.fromJson(_payload())),
      ));
      await tester.pumpAndSettle();
      expect(find.text('Record another swing'), findsNothing);
    });
  });
}
