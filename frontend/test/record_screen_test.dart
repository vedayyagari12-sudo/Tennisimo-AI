import 'package:camera/camera.dart';
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
}
