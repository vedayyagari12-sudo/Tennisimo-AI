import 'dart:async';

import 'package:camera/camera.dart';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/models/enums.dart';
import 'package:tennisimo_ai/screens/record_screen.dart';
import 'package:tennisimo_ai/theme/app_theme.dart';

/// A file chooser the test drives by hand.
///
/// Subclassing [FilePickerPlatform] is enough to install it: its constructor
/// passes the interface's own private token, which is what the `instance`
/// setter verifies.
class _PendingPicker extends FilePickerPlatform {
  final Completer<PlatformFile?> completer = Completer<PlatformFile?>();

  @override
  Future<PlatformFile?> pickFile({
    String? dialogTitle,
    String? initialDirectory,
    FileType type = FileType.any,
    List<String>? allowedExtensions,
    Function(FilePickerStatus)? onFileLoading,
    int compressionQuality = 0,
    AndroidOptions androidOptions = const AndroidOptions(),
    DarwinOptions darwinOptions = const DarwinOptions(),
    WindowsOptions windowsOptions = const WindowsOptions(),
    LinuxOptions linuxOptions = const LinuxOptions(),
    WebOptions webOptions = const WebOptions(),
  }) =>
      completer.future;
}

/// The real chooser, put back after each test that swaps it out.
final FilePickerPlatform originalPicker = FilePickerPlatform.instance;

/// The handedness control, whichever position it is in.
SegmentedButton<Handedness> handednessControl(WidgetTester tester) =>
    tester.widget<SegmentedButton<Handedness>>(
      find.byType(SegmentedButton<Handedness>),
    );

/// Coverage for the camera-setup error wording.
///
/// The widget's camera path cannot be driven in a unit test: `availableCameras`
/// and `CameraController.initialize` both go over a platform channel with no
/// injectable seam on this screen. The message selection is therefore a pure
/// function, and that is what is asserted here.
///
/// The codes below are the literal strings emitted by the plugins
/// (`camera_android_camerax` CameraPermissionsManager, `camera_avfoundation`
/// CameraPermissionManager) and passed through unchanged by
/// `CameraController.initialize`.
void main() {
  group('cameraSetupErrorMessage', () {
    test('a denied camera permission names the app, the reason, and Settings',
        () {
      final String message = cameraSetupErrorMessage(
        CameraException('CameraAccessDenied',
            'User denied the camera access request.'),
      );

      expect(message, contains('camera access'));
      expect(message, contains('Settings'));
      // The raw platform string is third-person and says nothing actionable.
      expect(message, isNot(contains('User denied')));
    });

    test('CameraAccessDeniedWithoutPrompt does not suggest retrying works', () {
      final String message = cameraSetupErrorMessage(
        CameraException(
          'CameraAccessDeniedWithoutPrompt',
          'User has previously denied the camera access request. Go to '
              'Settings to enable camera access.',
        ),
      );

      expect(message, contains('Settings'));
      expect(message, contains('will not bring the permission prompt back'));
    });

    test('CameraAccessRestricted explains the restriction and points at '
        'Settings', () {
      final String message = cameraSetupErrorMessage(
        CameraException('CameraAccessRestricted',
            'Camera access is restricted.'),
      );

      expect(message, contains('restricted'));
      expect(message, contains('Settings'));
      // Cryptic on its own; the user needs to know what imposes it.
      expect(message, contains('Screen Time'));
    });

    test('the three permission codes each get a distinct message', () {
      final Set<String> messages = <String>{
        cameraSetupErrorMessage(CameraException('CameraAccessDenied', null)),
        cameraSetupErrorMessage(
            CameraException('CameraAccessDeniedWithoutPrompt', null)),
        cameraSetupErrorMessage(
            CameraException('CameraAccessRestricted', null)),
      };

      expect(messages, hasLength(3));
    });

    test('a non-permission failure still shows the platform description', () {
      final String message = cameraSetupErrorMessage(
        CameraException('CameraAccessFailed', 'Camera is in use.'),
      );

      expect(message, 'Camera is in use.');
    });

    test('a failure with no description falls back to the generic message', () {
      final String message =
          cameraSetupErrorMessage(CameraException('cameraNotFound', null));

      expect(message, 'The camera could not be started.');
    });
  });

  /// The web recording fallback.
  ///
  /// A browser with no usable MediaRecorder — the case `camera_web` reports as
  /// `cameraNotSupported` — must land on the file-pick path with an
  /// explanation, not on a raw exception. The decision is a pure function for
  /// the same reason the setup wording is: the failure originates inside a
  /// browser API that no unit test can reach.
  group('isRecordingUnsupportedError', () {
    test('the CameraException the controller rethrows is recognised', () {
      // CameraController.startVideoRecording catches the plugin's
      // PlatformException and rethrows `CameraException(e.code, e.message)`,
      // so this is the shape the screen sees most of the time on web.
      expect(
        isRecordingUnsupportedError(
          CameraException(
            'cameraNotSupported',
            'The browser does not support any of the following video types: '
                'video/webm;codecs="vp9,opus",video/mp4,video/webm.',
          ),
        ),
        isTrue,
      );
    });

    test('the raw PlatformException from the web plugin is recognised', () {
      expect(
        isRecordingUnsupportedError(
          PlatformException(code: 'cameraNotSupported'),
        ),
        isTrue,
      );
    });

    test('an untranslated CameraWebException is still recognised', () {
      // camera_web's startVideoCapturing is not async and returns
      // camera.startVideoRecording() unawaited, so an exception raised inside
      // that future escapes its try/catch as itself. There is no typed way to
      // catch it without importing a web-only package, so the text is matched.
      expect(
        isRecordingUnsupportedError(
          _FakeCameraWebException(
            'CameraWebException(cameraNotSupported, The browser does not '
            'support any of the following video types: video/webm)',
          ),
        ),
        isTrue,
      );
    });

    test('a permission denial is NOT treated as unsupported', () {
      // Sending a user who merely blocked the camera to the file picker would
      // hide a problem they can fix in two taps.
      expect(
        isRecordingUnsupportedError(
          CameraException('CameraAccessDenied', 'User denied camera access.'),
        ),
        isFalse,
      );
    });

    test('an unrelated failure is NOT treated as unsupported', () {
      expect(
        isRecordingUnsupportedError(
          CameraException('CameraAccessFailed', 'Camera is in use.'),
        ),
        isFalse,
      );
      expect(isRecordingUnsupportedError(StateError('boom')), isFalse);
    });
  });

  group('recordingFailureMessage', () {
    test('an unsupported browser is told to choose a file instead', () {
      final String message = recordingFailureMessage(
        PlatformException(code: 'cameraNotSupported'),
      );

      expect(message, kRecordingUnsupportedMessage);
      expect(message, contains('choose the file'));
      // Never shown raw: a user cannot act on a platform error code.
      expect(message, isNot(contains('cameraNotSupported')));
    });

    test('any other camera failure keeps the platform description', () {
      expect(
        recordingFailureMessage(
          CameraException('CameraAccessFailed', 'Camera is in use.'),
        ),
        'Camera is in use.',
      );
    });

    test('a failure with nothing to describe still says something', () {
      expect(
        recordingFailureMessage(CameraException('CameraAccessFailed', null)),
        'Recording could not start.',
      );
      expect(
        recordingFailureMessage(StateError('boom')),
        contains('Recording could not start.'),
      );
    });
  });

  group('the handedness gate', () {
    // A1. `_startAnalysis` used to read `_handedness!`, reachable with null
    // from two timing windows, and it threw inside a route builder.
    test('no racket hand blocks the analysis with a sentence, not a crash', () {
      final String? reason = analysisBlockedReason(null);
      expect(reason, isNotNull);
      expect(reason, kHandednessMissingMessage);
      // Says what to do, and names both options.
      expect(reason, contains('right'));
      expect(reason, contains('left'));
    });

    test('a chosen racket hand blocks nothing', () {
      for (final Handedness hand in Handedness.values) {
        expect(analysisBlockedReason(hand), isNull);
      }
    });
  });

  group('controls while an intake is in flight', () {
    setUp(() {
      // No cameras: the screen renders its camera-error state and the full set
      // of hint controls, which is all this group is about.
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
      FilePickerPlatform.instance = originalPicker;
    });

    testWidgets('handedness cannot be cleared while the file chooser is open',
        (WidgetTester tester) async {
      // A1, the file-pick window. The chooser is a full-screen OS sheet, but
      // the control underneath it stayed live: deselect handedness there and
      // the resolving pick reached `_handedness!` with a null.
      final _PendingPicker picker = _PendingPicker();
      FilePickerPlatform.instance = picker;

      // Tall enough for the whole form: the controls live in a ListView and
      // an unbuilt off-screen row cannot be tapped.
      tester.view.physicalSize = const Size(1000, 3000);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);

      await tester.pumpWidget(MaterialApp(
        theme: buildAppTheme(),
        home: const RecordScreen(),
      ));
      await tester.pumpAndSettle();

      // Choosing a racket hand is what unlocks the file button.
      await tester.tap(find.text('Right'));
      await tester.pumpAndSettle();
      expect(handednessControl(tester).onSelectionChanged, isNotNull);

      await tester.tap(find.text('Choose a video file'));
      await tester.pump();

      // The chooser is open. The hint controls are now dead.
      expect(
        handednessControl(tester).onSelectionChanged,
        isNull,
        reason: 'handedness was still changeable while a pick was in flight',
      );

      // And the file button cannot be pressed a second time into the same
      // chooser.
      expect(
        tester
            .widget<OutlinedButton>(
              find.widgetWithText(OutlinedButton, 'Choose a video file'),
            )
            .onPressed,
        isNull,
      );

      // Let the chooser resolve as "cancelled" so nothing is left pending.
      picker.completer.complete(null);
      await tester.pumpAndSettle();

      // Back to normal afterwards: the lock lasts the intake, not forever.
      expect(handednessControl(tester).onSelectionChanged, isNotNull);
    });
  });
}

/// Stands in for `camera_web`'s CameraWebException, which cannot be imported
/// here: it is a web-only package and would not compile for the mobile targets
/// this suite also covers. Only its toString matters to the code under test.
class _FakeCameraWebException implements Exception {
  _FakeCameraWebException(this._text);

  final String _text;

  @override
  String toString() => _text;
}
