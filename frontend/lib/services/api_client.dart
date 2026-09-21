import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:supabase_flutter/supabase_flutter.dart';

import '../models/analysis_response.dart';
import '../models/ball_speed_calibration.dart';
import '../models/enums.dart';

/// REPLACE ME: base URL of the deployed FastAPI backend (Render).
const String baseUrl = 'https://replace-me.example.com';

/// Poll cadence and cap, per PIPELINE.md Stage 19 / Appendix A.
const Duration kPollInterval = Duration(seconds: 2);
const Duration kPollTimeout = Duration(seconds: 120);

/// What went wrong, at a granularity the screens actually branch on.
enum ApiFailureKind {
  /// No Supabase session. The request was NOT sent: the JWT is the identity and
  /// there is nothing meaningful to send without it.
  notSignedIn,

  /// Could not reach the server at all.
  network,

  /// Server answered with a non-2xx status.
  server,

  /// Server answered 2xx with a body this client could not read.
  badResponse,

  /// Polling hit the 120 s cap without the job finishing.
  timeout,
}

/// A typed failure. [errorCode] is the server `ErrorCode` string when present.
class ApiFailure {
  const ApiFailure({
    required this.kind,
    required this.message,
    this.errorCode,
    this.statusCode,
    this.retryable,
  });

  final ApiFailureKind kind;
  final String message;
  final String? errorCode;
  final int? statusCode;

  /// The server's own `retryable` flag when the body carried one.
  ///
  /// Null for failures the server never described: [ApiFailureKind.network],
  /// [ApiFailureKind.timeout], [ApiFailureKind.notSignedIn], and bodies that
  /// simply omit the key.
  final bool? retryable;

  /// Message fit for a user, resolving a known `error_code` to plain language.
  String get plainLanguage =>
      errorCodeToPlainLanguage(errorCode, fallback: message);
}

/// Success-or-failure wrapper. Exactly one of [data] / [failure] is non-null.
class ApiResult<T> {
  const ApiResult.ok(T this.data) : failure = null;
  const ApiResult.err(ApiFailure this.failure) : data = null;

  final T? data;
  final ApiFailure? failure;

  bool get isOk => failure == null;
}

/// A signed upload ticket. Mirrors PIPELINE.md 2.2 `UploadTicketResponse`.
class UploadTicket {
  const UploadTicket({
    required this.storagePath,
    required this.uploadUrl,
    required this.uploadToken,
  });

  final String storagePath;
  final String uploadUrl;
  final String uploadToken;
}

/// Mirrors PIPELINE.md 2.2 `CreateAnalysisResponse`.
class CreateAnalysisResult {
  const CreateAnalysisResult({
    required this.analysisId,
    required this.status,
    required this.estimatedSeconds,
    required this.ballSpeedRequested,
  });

  final String analysisId;
  final JobStatus status;
  final int? estimatedSeconds;
  final bool ballSpeedRequested;
}

/// One poll response: either still working, or the finished analysis.
class PollUpdate {
  const PollUpdate({
    required this.status,
    this.analysis,
    this.queuePosition,
    this.estimatedSecondsRemaining,
    this.failure,
  });

  final JobStatus status;

  /// Non-null once the job succeeded.
  final AnalysisResponse? analysis;
  final int? queuePosition;
  final int? estimatedSecondsRemaining;

  /// Non-null when the job itself failed, carrying the server `error_code`.
  final ApiFailure? failure;
}

/// The access token for the signed-in user, or null when signed out.
String? currentAccessToken() =>
    Supabase.instance.client.auth.currentSession?.accessToken;

Map<String, String> _authHeaders(String token, {bool json = false}) {
  return <String, String>{
    'Authorization': 'Bearer $token',
    if (json) 'Content-Type': 'application/json',
  };
}

/// Extracts `{code, message}` from an error body.
///
/// Assumes FastAPI's `detail` envelope, and also accepts a flat body or a plain
/// string detail, because Phase 5 has not fixed the shape yet.
ApiFailure _failureFromResponse(http.Response response) =>
    apiFailureFromErrorBody(response.body, response.statusCode);

/// Pure parser behind [_failureFromResponse]: decodes a raw error body string
/// into an [ApiFailure]. Kept separate so it is testable without an HTTP call.
ApiFailure apiFailureFromErrorBody(String rawBody, int statusCode) {
  String? code;
  bool? retryable;
  String message = 'Server error ($statusCode).';
  try {
    final Object? body = jsonDecode(rawBody);
    Object? node = body;
    if (node is Map && node['detail'] != null) node = node['detail'];
    if (node is Map) {
      final Object? rawCode = node['code'] ?? node['error_code'];
      if (rawCode is String) code = rawCode;
      final Object? rawMessage = node['message'] ?? node['detail'];
      if (rawMessage is String && rawMessage.isNotEmpty) message = rawMessage;
      final Object? rawRetryable = node['retryable'];
      if (rawRetryable is bool) retryable = rawRetryable;
    } else if (node is String && node.isNotEmpty) {
      message = node;
    }
  } catch (_) {
    // Body was not JSON. Keep the generic message.
  }
  return ApiFailure(
    kind: ApiFailureKind.server,
    message: message,
    errorCode: code,
    statusCode: statusCode,
    retryable: retryable,
  );
}

const ApiFailure _notSignedIn = ApiFailure(
  kind: ApiFailureKind.notSignedIn,
  message: 'You are not signed in. Sign in and try again.',
);

/// Step 1: ask the backend for a signed upload destination.
Future<ApiResult<UploadTicket>> requestUploadTicket({
  required String contentType,
  required int sizeBytes,
  required double durationS,
}) async {
  final String? token = currentAccessToken();
  if (token == null) return const ApiResult<UploadTicket>.err(_notSignedIn);

  try {
    final http.Response response = await http.post(
      Uri.parse('$baseUrl/v1/uploads/ticket'),
      headers: _authHeaders(token, json: true),
      body: jsonEncode(<String, dynamic>{
        'content_type': contentType,
        'size_bytes': sizeBytes,
        'duration_s': durationS,
      }),
    );
    if (response.statusCode < 200 || response.statusCode >= 300) {
      return ApiResult<UploadTicket>.err(_failureFromResponse(response));
    }
    final Object? body = jsonDecode(response.body);
    if (body is! Map) {
      return const ApiResult<UploadTicket>.err(ApiFailure(
        kind: ApiFailureKind.badResponse,
        message: 'The upload ticket could not be read.',
      ));
    }
    return ApiResult<UploadTicket>.ok(UploadTicket(
      storagePath: '${body['storage_path'] ?? ''}',
      uploadUrl: '${body['upload_url'] ?? ''}',
      uploadToken: '${body['upload_token'] ?? ''}',
    ));
  } catch (e) {
    return ApiResult<UploadTicket>.err(ApiFailure(
      kind: ApiFailureKind.network,
      message: 'Could not reach the server. Check your connection. ($e)',
    ));
  }
}

/// Step 3: queue the analysis. The user identity comes from the JWT only.
Future<ApiResult<CreateAnalysisResult>> createAnalysis({
  required String storagePath,
  required Handedness handednessHint,
  required ShotType labelHint,
  CameraView cameraViewHint = CameraView.sideOn,
  BallSpeedCalibration? calibration,
}) async {
  final String? token = currentAccessToken();
  if (token == null) {
    return const ApiResult<CreateAnalysisResult>.err(_notSignedIn);
  }

  try {
    final http.Response response = await http.post(
      Uri.parse('$baseUrl/v1/analyses'),
      headers: _authHeaders(token, json: true),
      body: jsonEncode(<String, dynamic>{
        'storage_path': storagePath,
        'handedness_hint': handednessHint.wire,
        'camera_view_hint': cameraViewHint.wire,
        'label_hint': labelHint.wire,
        if (calibration != null)
          'ball_speed_calibration': calibration.toJson(),
      }),
    );
    if (response.statusCode < 200 || response.statusCode >= 300) {
      return ApiResult<CreateAnalysisResult>.err(_failureFromResponse(response));
    }
    final Object? body = jsonDecode(response.body);
    if (body is! Map) {
      return const ApiResult<CreateAnalysisResult>.err(ApiFailure(
        kind: ApiFailureKind.badResponse,
        message: 'The server did not return an analysis id.',
      ));
    }
    final String id = '${body['analysis_id'] ?? ''}';
    if (id.isEmpty) {
      return const ApiResult<CreateAnalysisResult>.err(ApiFailure(
        kind: ApiFailureKind.badResponse,
        message: 'The server did not return an analysis id.',
      ));
    }
    final Object? eta = body['estimated_seconds'];
    return ApiResult<CreateAnalysisResult>.ok(CreateAnalysisResult(
      analysisId: id,
      status: JobStatus.fromJson(body['status']),
      estimatedSeconds: eta is num ? eta.toInt() : null,
      ballSpeedRequested: body['ball_speed_requested'] == true,
    ));
  } catch (e) {
    return ApiResult<CreateAnalysisResult>.err(ApiFailure(
      kind: ApiFailureKind.network,
      message: 'Could not reach the server. Check your connection. ($e)',
    ));
  }
}

/// Pure parser for the job-status `error` object (`{code, message, stage,
/// retryable}`). Returns null when the node is not an error map.
ApiFailure? jobFailureFromErrorNode(Object? error) {
  if (error is! Map) return null;
  final Object? rawRetryable = error['retryable'];
  return ApiFailure(
    kind: ApiFailureKind.server,
    message: '${error['message'] ?? 'The analysis failed.'}',
    errorCode: error['code'] is String ? error['code'] as String : null,
    retryable: rawRetryable is bool ? rawRetryable : null,
  );
}

/// Step 4: one poll. Distinguishes the job-status envelope from the finished
/// analysis by looking for the blocks only a finished analysis carries.
Future<ApiResult<PollUpdate>> fetchAnalysis(String analysisId) async {
  final String? token = currentAccessToken();
  if (token == null) return const ApiResult<PollUpdate>.err(_notSignedIn);

  try {
    final http.Response response = await http.get(
      Uri.parse('$baseUrl/v1/analyses/$analysisId'),
      headers: _authHeaders(token),
    );
    if (response.statusCode < 200 || response.statusCode >= 300) {
      return ApiResult<PollUpdate>.err(_failureFromResponse(response));
    }
    final Object? body = jsonDecode(response.body);
    if (body is! Map) {
      return const ApiResult<PollUpdate>.err(ApiFailure(
        kind: ApiFailureKind.badResponse,
        message: 'The analysis could not be read.',
      ));
    }
    final Map<String, dynamic> json = body.cast<String, dynamic>();

    final Object? rawStatus = json['status'];
    final bool looksFinished = json['feedback'] is Map ||
        json['scorecard'] is Map ||
        rawStatus == 'complete' ||
        rawStatus == 'partial' ||
        rawStatus == 'succeeded';

    if (looksFinished) {
      return ApiResult<PollUpdate>.ok(PollUpdate(
        status: JobStatus.succeeded,
        analysis: AnalysisResponse.fromJson(json),
      ));
    }

    final JobStatus status = JobStatus.fromJson(rawStatus);
    final ApiFailure? jobFailure = jobFailureFromErrorNode(json['error']);
    final Object? queue = json['queue_position'];
    final Object? remaining = json['estimated_seconds_remaining'];
    return ApiResult<PollUpdate>.ok(PollUpdate(
      status: status,
      queuePosition: queue is num ? queue.toInt() : null,
      estimatedSecondsRemaining: remaining is num ? remaining.toInt() : null,
      failure: jobFailure,
    ));
  } catch (e) {
    return ApiResult<PollUpdate>.err(ApiFailure(
      kind: ApiFailureKind.network,
      message: 'Could not reach the server. Check your connection. ($e)',
    ));
  }
}

/// Whether a poll failure ends the loop (true) or is a transient blip to retry.
///
/// Not-signed-in is always final: the request was never sent. Otherwise the
/// server's own `retryable` flag wins when it provided one; only when it did
/// not do we fall back to the "4xx is final, 5xx is transient" heuristic, which
/// is wrong for at least `429 queue_full` (`retryable: true`).
bool isFinalPollFailure(ApiFailure failure) {
  if (failure.kind == ApiFailureKind.notSignedIn) return true;
  final bool? retryable = failure.retryable;
  if (retryable != null) return !retryable;
  return failure.statusCode != null && failure.statusCode! < 500;
}

/// The failure an in-progress poll update ends the loop with, or null to keep
/// polling.
///
/// Extracted for the same reason as [isFinalPollFailure]: it is the loop's
/// terminal decision, and it is only testable without real 2 s delays on its
/// own. [JobStatus.unrecognized] is terminal — it is what [JobStatus.fromJson]
/// yields for a status this build does not know, and continuing to poll such a
/// job merely hides the outcome until the 120 s cap.
ApiFailure? terminalPollFailure(PollUpdate update) {
  switch (update.status) {
    case JobStatus.failed:
      return update.failure ??
          const ApiFailure(
            kind: ApiFailureKind.server,
            message: 'The analysis failed.',
          );
    case JobStatus.unrecognized:
      return const ApiFailure(
        kind: ApiFailureKind.badResponse,
        message: 'The server reported a job status this app version does not '
            'recognise. Update the app, or check your history in a minute.',
      );
    case JobStatus.queued:
    case JobStatus.running:
    case JobStatus.succeeded:
      return null;
  }
}

/// Polls every 2 s until the analysis is done, failed, or the 120 s cap is hit.
///
/// [onUpdate] fires on each successful non-final poll so the screen can show
/// queue position. Transient network blips do not abort the loop; they are
/// retried until the cap.
Future<ApiResult<AnalysisResponse>> pollUntilComplete(
  String analysisId, {
  void Function(PollUpdate update)? onUpdate,
}) async {
  final DateTime deadline = DateTime.now().add(kPollTimeout);
  ApiFailure? lastTransient;

  while (DateTime.now().isBefore(deadline)) {
    final ApiResult<PollUpdate> result = await fetchAnalysis(analysisId);

    if (!result.isOk) {
      final ApiFailure failure = result.failure!;
      if (isFinalPollFailure(failure)) {
        return ApiResult<AnalysisResponse>.err(failure);
      }
      lastTransient = failure;
    } else {
      final PollUpdate update = result.data!;
      if (update.analysis != null) {
        return ApiResult<AnalysisResponse>.ok(update.analysis!);
      }
      final ApiFailure? terminal = terminalPollFailure(update);
      if (terminal != null) {
        return ApiResult<AnalysisResponse>.err(terminal);
      }
      onUpdate?.call(update);
    }

    await Future<void>.delayed(kPollInterval);
  }

  return ApiResult<AnalysisResponse>.err(ApiFailure(
    kind: ApiFailureKind.timeout,
    message: lastTransient == null
        ? 'The analysis is taking longer than expected. It may still finish — '
            'check your history in a minute.'
        : 'Lost contact with the server while analysing. '
            '${lastTransient.message}',
  ));
}

/// Past analyses, newest first.
Future<ApiResult<List<AnalysisSummary>>> fetchHistory({int limit = 50}) async {
  final String? token = currentAccessToken();
  if (token == null) {
    return const ApiResult<List<AnalysisSummary>>.err(_notSignedIn);
  }

  try {
    final http.Response response = await http.get(
      Uri.parse('$baseUrl/v1/analyses?limit=$limit'),
      headers: _authHeaders(token),
    );
    if (response.statusCode < 200 || response.statusCode >= 300) {
      return ApiResult<List<AnalysisSummary>>.err(
          _failureFromResponse(response));
    }
    final Object? body = jsonDecode(response.body);
    // Accepts a bare list or {"items": [...]} / {"analyses": [...]}.
    List<dynamic>? items;
    if (body is List) {
      items = body;
    } else if (body is Map) {
      final Object? node = body['items'] ?? body['analyses'] ?? body['results'];
      if (node is List) items = node;
    }
    if (items == null) {
      return const ApiResult<List<AnalysisSummary>>.err(ApiFailure(
        kind: ApiFailureKind.badResponse,
        message: 'Your history could not be read.',
      ));
    }
    return ApiResult<List<AnalysisSummary>>.ok(items
        .whereType<Map>()
        .map((Map<dynamic, dynamic> e) =>
            AnalysisSummary.fromJson(e.cast<String, dynamic>()))
        .toList());
  } catch (e) {
    return ApiResult<List<AnalysisSummary>>.err(ApiFailure(
      kind: ApiFailureKind.network,
      message: 'Could not reach the server. Check your connection. ($e)',
    ));
  }
}

/// Fetches one finished analysis, for tapping through from history.
Future<ApiResult<AnalysisResponse>> fetchAnalysisDetail(
    String analysisId) async {
  final ApiResult<PollUpdate> result = await fetchAnalysis(analysisId);
  if (!result.isOk) {
    return ApiResult<AnalysisResponse>.err(result.failure!);
  }
  final AnalysisResponse? analysis = result.data!.analysis;
  if (analysis == null) {
    return const ApiResult<AnalysisResponse>.err(ApiFailure(
      kind: ApiFailureKind.badResponse,
      message: 'That analysis is not finished yet.',
    ));
  }
  return ApiResult<AnalysisResponse>.ok(analysis);
}
