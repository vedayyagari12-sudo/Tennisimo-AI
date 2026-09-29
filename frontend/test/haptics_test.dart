import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/services/haptics.dart';

/// Records what a screen asked for, without a platform channel.
class _RecordingDriver implements HapticDriver {
  final List<HapticKind> calls = <HapticKind>[];

  @override
  Future<void> send(HapticKind kind) async => calls.add(kind);
}

/// Every semantic call the app makes, by name, so a new one cannot be added
/// without deciding what it feels like — or without being covered by the
/// "silent on web" sweep below.
Map<String, Future<void> Function(Haptics)> _allActions() =>
    <String, Future<void> Function(Haptics)>{
      'recordingStarted': (Haptics h) => h.recordingStarted(),
      'recordingStopped': (Haptics h) => h.recordingStopped(),
      'calibrationPointPlaced': (Haptics h) => h.calibrationPointPlaced(),
      'tabSelected': (Haptics h) => h.tabSelected(),
      'analysisComplete': (Haptics h) => h.analysisComplete(),
      'actionFailed': (Haptics h) => h.actionFailed(),
      'accountDeletionConfirmed': (Haptics h) => h.accountDeletionConfirmed(),
    };

void main() {
  group('the haptic map', () {
    late _RecordingDriver driver;
    late Haptics haptics;

    setUp(() {
      driver = _RecordingDriver();
      haptics = Haptics(driver: driver, isWeb: false);
    });

    test('starting a recording is the heaviest feel in the app', () async {
      await haptics.recordingStarted();
      expect(driver.calls, <HapticKind>[HapticKind.heavy]);
    });

    test('stopping a recording is a medium confirmation', () async {
      await haptics.recordingStopped();
      expect(driver.calls, <HapticKind>[HapticKind.medium]);
    });

    test('a placed calibration point is a selection tick', () async {
      await haptics.calibrationPointPlaced();
      expect(driver.calls, <HapticKind>[HapticKind.selection]);
    });

    test('a tab change is a selection tick, not an impact', () async {
      await haptics.tabSelected();
      expect(driver.calls, <HapticKind>[HapticKind.selection]);
      expect(driver.calls, isNot(contains(HapticKind.heavy)));
    });

    test('a finished analysis is a medium success feel', () async {
      await haptics.analysisComplete();
      expect(driver.calls, <HapticKind>[HapticKind.medium]);
    });

    test('a failed action is the lightest feel, never a thump', () async {
      await haptics.actionFailed();
      expect(driver.calls, <HapticKind>[HapticKind.light]);
      expect(driver.calls, isNot(contains(HapticKind.heavy)));
    });

    test('confirming account deletion is firm, but not the camera thump',
        () async {
      await haptics.accountDeletionConfirmed();
      expect(driver.calls, <HapticKind>[HapticKind.medium]);
    });
  });

  group('web is silent', () {
    test('not one action reaches the driver when kIsWeb is true', () async {
      final _RecordingDriver driver = _RecordingDriver();
      final Haptics haptics = Haptics(driver: driver, isWeb: true);

      for (final MapEntry<String, Future<void> Function(Haptics)> action
          in _allActions().entries) {
        await action.value(haptics);
        expect(
          driver.calls,
          isEmpty,
          reason: '${action.key} fired a haptic on web',
        );
      }
      expect(haptics.isSilent, isTrue);
    });

    test('the shipped instance gates on the platform itself', () {
      // The other tests pass `isWeb` in. This one pins the DEFAULT: the
      // singleton every screen uses takes its gate from `kIsWeb`, so nobody has
      // to remember to set anything. Run this file with
      // `flutter test --platform chrome` and the same assertion becomes the
      // direct proof that a real web build is silent.
      expect(haptics.isSilent, kIsWeb);
    });

    testWidgets('and nothing reaches the real platform channel either',
        (WidgetTester tester) async {
      // The strongest form of the claim: the production driver, the real
      // SystemChannels.platform, and still no HapticFeedback.vibrate message.
      final List<MethodCall> sent = <MethodCall>[];
      tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
        SystemChannels.platform,
        (MethodCall call) async {
          sent.add(call);
          return null;
        },
      );
      addTearDown(() => tester.binding.defaultBinaryMessenger
          .setMockMethodCallHandler(SystemChannels.platform, null));

      final Haptics haptics = Haptics(isWeb: true);
      for (final Future<void> Function(Haptics) action
          in _allActions().values) {
        await action(haptics);
      }
      await tester.pump();

      expect(
        sent.where((MethodCall c) => c.method == 'HapticFeedback.vibrate'),
        isEmpty,
      );
    });
  });

  group('PlatformHapticDriver', () {
    testWidgets('each kind maps to the documented platform feedback type',
        (WidgetTester tester) async {
      final List<String> types = <String>[];
      tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
        SystemChannels.platform,
        (MethodCall call) async {
          if (call.method == 'HapticFeedback.vibrate') {
            types.add(call.arguments as String);
          }
          return null;
        },
      );
      addTearDown(() => tester.binding.defaultBinaryMessenger
          .setMockMethodCallHandler(SystemChannels.platform, null));

      const PlatformHapticDriver driver = PlatformHapticDriver();
      for (final HapticKind kind in HapticKind.values) {
        await driver.send(kind);
      }

      expect(types, <String>[
        'HapticFeedbackType.selectionClick',
        'HapticFeedbackType.lightImpact',
        'HapticFeedbackType.mediumImpact',
        'HapticFeedbackType.heavyImpact',
      ]);
    });
  });
}
