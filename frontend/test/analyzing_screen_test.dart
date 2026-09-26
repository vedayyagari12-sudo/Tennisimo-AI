import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/screens/analyzing_screen.dart';

/// Coverage for what the user is TOLD when they back out of an analysis.
///
/// Backing out cancels this app's polling and nothing else: there is no cancel
/// endpoint, and once the clip is uploaded the server finishes the job whatever
/// this screen does. The copy has to say that, because the alternative —
/// letting the user believe they cancelled the analysis — is a lie the app
/// would be telling for free.
///
/// The polling side of the same fix is covered in `api_client_test.dart`
/// ("cancelling a poll").
void main() {
  group('leaveAnalysisMessage', () {
    test('after the upload, it says the analysis continues', () {
      final String message = leaveAnalysisMessage(uploaded: true);
      expect(message, contains('keeps running'));
      expect(message, contains('history'));
      // Must not imply the swing is thrown away, because it is not.
      expect(message, isNot(contains('nothing will be analysed')));
    });

    test('during the upload, it admits the swing is lost', () {
      final String message = leaveAnalysisMessage(uploaded: false);
      expect(message, contains('still uploading'));
      expect(message, contains('nothing will be analysed'));
      // No job exists yet, so promising a history entry would be false.
      expect(message, isNot(contains('history')));
    });

    test('the two halves are never the same sentence', () {
      expect(
        leaveAnalysisMessage(uploaded: true),
        isNot(leaveAnalysisMessage(uploaded: false)),
      );
    });
  });
}
