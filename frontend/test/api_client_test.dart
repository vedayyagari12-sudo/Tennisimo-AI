import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/models/analysis_response.dart';
import 'package:tennisimo_ai/models/enums.dart';
import 'package:tennisimo_ai/services/api_client.dart';

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

  group('terminalPollFailure ends the loop on a status it cannot read', () {
    test('an unrecognised status is terminal, not polled as in-progress', () {
      // Regression: JobStatus.fromJson used to fall back to `queued`, and the
      // loop's terminal check only looked for `failed` — so an unknown status
      // fell through to onUpdate and kept polling for the full kPollTimeout.
      const PollUpdate update = PollUpdate(status: JobStatus.unrecognized);
      final ApiFailure? failure = terminalPollFailure(update);
      expect(failure, isNotNull, reason: 'must end the loop, not keep polling');
      expect(failure!.kind, ApiFailureKind.badResponse);
      // The message must name the real cause, not borrow another failure's.
      expect(failure.message, contains('does not recognise'));
      expect(failure.errorCode, isNull);
    });

    test('a status parsed from an unknown wire string is terminal', () {
      final PollUpdate update =
          PollUpdate(status: JobStatus.fromJson('cancelled'));
      expect(terminalPollFailure(update), isNotNull);
    });

    test('failed stays terminal and keeps the server error', () {
      const PollUpdate update = PollUpdate(
        status: JobStatus.failed,
        failure: ApiFailure(
          kind: ApiFailureKind.server,
          message: 'Worker died.',
          errorCode: 'worker_lost',
        ),
      );
      expect(terminalPollFailure(update)!.errorCode, 'worker_lost');
    });

    test('in-progress statuses keep polling', () {
      expect(terminalPollFailure(const PollUpdate(status: JobStatus.queued)),
          isNull);
      expect(terminalPollFailure(const PollUpdate(status: JobStatus.running)),
          isNull);
      expect(terminalPollFailure(const PollUpdate(status: JobStatus.succeeded)),
          isNull);
    });
  });

  group('looksLikeFinishedAnalysis reads the status through the enum', () {
    test('complete is finished', () {
      expect(looksLikeFinishedAnalysis(<String, dynamic>{'status': 'complete'}),
          isTrue);
    });

    test('partial is finished', () {
      expect(looksLikeFinishedAnalysis(<String, dynamic>{'status': 'partial'}),
          isTrue);
    });

    test('the deliberately tolerated succeeded body is still finished', () {
      // PIPELINE.md 4.4 says this endpoint never emits the JobStatus value
      // `succeeded`. The client has tolerated it anyway since 6511a7f, and
      // that tolerance must survive the move to AnalysisStatus.fromJson.
      expect(looksLikeFinishedAnalysis(<String, dynamic>{'status': 'succeeded'}),
          isTrue);
    });

    test('an unrelated status does not look finished', () {
      expect(
          looksLikeFinishedAnalysis(<String, dynamic>{'status': 'quarantined'}),
          isFalse);
      expect(looksLikeFinishedAnalysis(<String, dynamic>{'status': 'queued'}),
          isFalse);
    });

    test('a body falling through terminates instead of polling to the cap', () {
      // The nice property of routing through the enums: the same string that
      // is not "finished" is also not a known JobStatus, so the in-progress
      // path ends the loop rather than polling it for the full cap.
      const String raw = 'quarantined';
      expect(looksLikeFinishedAnalysis(<String, dynamic>{'status': raw}),
          isFalse);
      expect(JobStatus.fromJson(raw), JobStatus.unrecognized);
      expect(
        terminalPollFailure(PollUpdate(status: JobStatus.fromJson(raw))),
        isNotNull,
      );
    });

    test('a feedback or scorecard block is finished whatever the status', () {
      expect(
        looksLikeFinishedAnalysis(
            <String, dynamic>{'feedback': <String, dynamic>{}}),
        isTrue,
      );
      expect(
        looksLikeFinishedAnalysis(
            <String, dynamic>{'scorecard': <String, dynamic>{}}),
        isTrue,
      );
    });
  });

  group('poll cap is tied to the server heartbeat window', () {
    test('kPollTimeout outlives the server heartbeat staleness window', () {
      expect(kPollTimeout, const Duration(seconds: 210));
      expect(kPollTimeout.inSeconds, greaterThan(kServerHeartbeatStaleSeconds));
      // Not merely greater: greater with real margin, so a slow round trip
      // near the window still gets the server's own verdict through.
      expect(kPollTimeout.inSeconds - kServerHeartbeatStaleSeconds,
          greaterThanOrEqualTo(30));
    });

    test('the mirrored server constant is the documented 180 s', () {
      expect(kServerHeartbeatStaleSeconds, 180);
    });
  });

  group('baseUrl points at a real backend', () {
    test('baseUrl is no longer the placeholder', () {
      expect(baseUrl, isNot('https://replace-me.example.com'));
    });

    test('baseUrl is a well-formed https URL with a host', () {
      final Uri parsed = Uri.parse(baseUrl);
      expect(parsed.scheme, 'https');
      expect(parsed.host, isNotEmpty);
    });

    // Reads the SAME `--dart-define` key independently of api_client.dart, so
    // the assertion below is not just the constant compared against itself.
    //
    // Run with no flag, this pins the default (production stays production).
    // Run as
    //   flutter test --dart-define=API_BASE_URL=https://staging.example.com
    // it is the only thing that actually proves the override mechanism works:
    // the constant must come back as the injected value, not the default.
    // A compile-time constant's *source* is not inspectable at runtime, so a
    // second test invocation carrying the flag is the real coverage here.
    test('baseUrl resolves from the API_BASE_URL dart-define', () {
      const String injected = String.fromEnvironment('API_BASE_URL');
      if (injected.isEmpty) {
        expect(baseUrl, 'https://tennisform-api-143709056949.us-east1.run.app');
      } else {
        expect(baseUrl, injected);
      }
    });
  });

  group('cancelling a poll', () {
    test('cancel is a one-way switch', () {
      final PollCancellation cancellation = PollCancellation();
      expect(cancellation.isCancelled, isFalse);
      cancellation.cancel();
      expect(cancellation.isCancelled, isTrue);
      // Idempotent, and there is no way back: a stale closure cannot revive a
      // loop the screen already walked away from.
      cancellation.cancel();
      expect(cancellation.isCancelled, isTrue);
    });

    test('an already-cancelled token stops the loop before the first request',
        () async {
      // The regression this pins: before cancellation existed, leaving the
      // analysing screen left this loop hitting /v1/analyses/{id} every 2 s for
      // up to 210 s with nobody to receive the answer.
      //
      // The proof that no request went out is the RESULT: a request attempted
      // from a test with no Supabase session comes back `notSignedIn`, which is
      // a final poll failure. Getting `cancelled` back means the loop returned
      // at the pre-request check instead.
      final PollCancellation cancellation = PollCancellation()..cancel();
      final Stopwatch watch = Stopwatch()..start();

      final ApiResult<AnalysisResponse> result = await pollUntilComplete(
        'analysis-id',
        cancellation: cancellation,
      );
      watch.stop();

      expect(result.isOk, isFalse);
      expect(result.failure!.kind, ApiFailureKind.cancelled);
      expect(result.failure!.kind, isNot(ApiFailureKind.notSignedIn));
      // And promptly: not after another kPollInterval of dead waiting.
      expect(watch.elapsed, lessThan(kPollInterval));
    });

    test('the cancelled failure tells the truth about what happens next',
        () async {
      // Cancelling is CLIENT-SIDE ONLY. The copy must not imply the user
      // stopped the analysis, because they did not.
      final String message = kPollCancelledFailure.message;
      expect(message, contains('still running'));
      expect(message, contains('history'));
      expect(message.toLowerCase(), isNot(contains('cancelled the analysis')));
    });

    test('a cancelled result is not mistaken for a server failure', () {
      // Its own kind, so no screen can render it as an error and no retry
      // heuristic can treat it as something that went wrong upstream.
      expect(kPollCancelledFailure.kind, ApiFailureKind.cancelled);
      expect(
        kPollCancelledFailure.kind,
        isNot(anyOf(
          ApiFailureKind.server,
          ApiFailureKind.timeout,
          ApiFailureKind.network,
          ApiFailureKind.badResponse,
        )),
      );
      // Nothing came from the server, so there is nothing to report about one.
      expect(kPollCancelledFailure.statusCode, isNull);
      expect(kPollCancelledFailure.errorCode, isNull);
    });
  });
}
