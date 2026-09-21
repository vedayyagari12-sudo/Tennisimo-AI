import 'package:flutter_test/flutter_test.dart';
import 'package:tennisform_ai/services/api_client.dart';

void main() {
  group('flat error envelope carries retryable', () {
    test('retryable: true is read off the flat body', () {
      final ApiFailure failure = apiFailureFromErrorBody(
        '{"error_code":"queue_full","message":"Queue is full.",'
        '"retryable":true,"request_id":"req_1"}',
        429,
      );
      expect(failure.retryable, isTrue);
      expect(failure.errorCode, 'queue_full');
      expect(failure.message, 'Queue is full.');
      expect(failure.statusCode, 429);
    });

    test('retryable: false is read as false, not as absent', () {
      final ApiFailure failure = apiFailureFromErrorBody(
        '{"error_code":"video_too_long","message":"Too long.",'
        '"retryable":false}',
        400,
      );
      expect(failure.retryable, isFalse);
    });

    test('an absent retryable stays null rather than defaulting', () {
      final ApiFailure failure = apiFailureFromErrorBody(
        '{"error_code":"internal_error","message":"Boom."}',
        500,
      );
      expect(failure.retryable, isNull);
    });

    test('retryable inside a FastAPI detail envelope is still read', () {
      final ApiFailure failure = apiFailureFromErrorBody(
        '{"detail":{"error_code":"queue_full","message":"Busy.",'
        '"retryable":true}}',
        429,
      );
      expect(failure.retryable, isTrue);
    });

    test('a non-JSON body yields a null retryable, not a throw', () {
      final ApiFailure failure = apiFailureFromErrorBody('<html>502</html>', 502);
      expect(failure.retryable, isNull);
      expect(failure.kind, ApiFailureKind.server);
    });

    test('a non-boolean retryable is ignored rather than coerced', () {
      final ApiFailure failure = apiFailureFromErrorBody(
        '{"error_code":"internal_error","retryable":"true"}',
        500,
      );
      expect(failure.retryable, isNull);
    });
  });

  group('job-status nested error object carries retryable', () {
    test('worker_lost keeps its retryable: true', () {
      final ApiFailure? failure = jobFailureFromErrorNode(<String, dynamic>{
        'code': 'worker_lost',
        'message': 'The worker died mid-analysis.',
        'stage': 'pose',
        'retryable': true,
      });
      expect(failure, isNotNull);
      expect(failure!.retryable, isTrue);
      expect(failure.errorCode, 'worker_lost');
      expect(failure.message, 'The worker died mid-analysis.');
    });

    test('a final job error keeps retryable: false', () {
      final ApiFailure? failure = jobFailureFromErrorNode(<String, dynamic>{
        'code': 'contact_not_found',
        'message': 'No contact frame.',
        'retryable': false,
      });
      expect(failure!.retryable, isFalse);
    });

    test('an error object with no retryable leaves it null', () {
      final ApiFailure? failure = jobFailureFromErrorNode(<String, dynamic>{
        'code': 'contact_not_found',
        'message': 'No contact frame.',
      });
      expect(failure!.retryable, isNull);
      expect(failure.errorCode, 'contact_not_found');
    });

    test('a non-map error node yields no failure', () {
      expect(jobFailureFromErrorNode(null), isNull);
      expect(jobFailureFromErrorNode('boom'), isNull);
    });
  });

  group('poll loop prefers retryable over the status-code heuristic', () {
    test('429 with retryable: true is transient, not final', () {
      final ApiFailure failure = apiFailureFromErrorBody(
        '{"error_code":"queue_full","message":"Busy.","retryable":true}',
        429,
      );
      // The old rule (statusCode < 500 => final) would have ended the loop.
      expect(failure.statusCode! < 500, isTrue);
      expect(isFinalPollFailure(failure), isFalse);
    });

    test('a 500 with retryable: false is final, not retried forever', () {
      const ApiFailure failure = ApiFailure(
        kind: ApiFailureKind.server,
        message: 'Unrecoverable.',
        statusCode: 500,
        retryable: false,
      );
      expect(isFinalPollFailure(failure), isTrue);
    });

    test('no retryable falls back to the unchanged 4xx/5xx heuristic', () {
      const ApiFailure fourOhFour = ApiFailure(
        kind: ApiFailureKind.server,
        message: 'Gone.',
        statusCode: 404,
      );
      const ApiFailure fiveHundred = ApiFailure(
        kind: ApiFailureKind.server,
        message: 'Boom.',
        statusCode: 500,
      );
      const ApiFailure network = ApiFailure(
        kind: ApiFailureKind.network,
        message: 'Offline.',
      );
      expect(isFinalPollFailure(fourOhFour), isTrue);
      expect(isFinalPollFailure(fiveHundred), isFalse);
      expect(isFinalPollFailure(network), isFalse);
    });

    test('not-signed-in stays final even if a body claimed retryable', () {
      const ApiFailure failure = ApiFailure(
        kind: ApiFailureKind.notSignedIn,
        message: 'Sign in.',
        retryable: true,
      );
      expect(isFinalPollFailure(failure), isTrue);
    });
  });
}
