import 'package:camera/camera.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/screens/record_screen.dart';

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
