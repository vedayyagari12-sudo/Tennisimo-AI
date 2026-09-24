import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import '../models/analysis_response.dart';
import '../models/ball_speed_calibration.dart';
import '../models/enums.dart';
import '../models/video_clip.dart';
import '../services/api_client.dart';
import '../services/supabase_storage.dart';
import '../services/video_intake.dart';
import 'results_screen.dart';

/// Which half of the job broke, so the retry copy can say something true.
enum _Stage { uploading, analysing }

/// Uploads the clip, queues the analysis, then polls to completion.
///
/// Takes a [VideoClip], so the live-recording path and the file-pick path meet
/// here and share one implementation from this point on. Nothing below this
/// line can tell which one produced the clip, or which platform it is running
/// on — that is the point.
class AnalyzingScreen extends StatefulWidget {
  const AnalyzingScreen({
    super.key,
    required this.clip,
    required this.handednessHint,
    required this.labelHint,
    this.calibration,
  });

  final VideoClip clip;
  final Handedness handednessHint;
  final ShotType labelHint;
  final BallSpeedCalibration? calibration;

  @override
  State<AnalyzingScreen> createState() => _AnalyzingScreenState();
}

class _AnalyzingScreenState extends State<AnalyzingScreen> {
  _Stage _stage = _Stage.uploading;
  String _statusLine = 'Preparing your clip…';
  ApiFailure? _failure;
  _Stage? _failedStage;
  bool _running = false;

  @override
  void initState() {
    super.initState();
    _run();
  }

  Future<void> _run() async {
    if (_running) return;
    setState(() {
      _running = true;
      _failure = null;
      _failedStage = null;
      _stage = _Stage.uploading;
      _statusLine = 'Preparing your clip…';
    });

    // Last gate before anything leaves the device. The file-pick path already
    // ran these checks at pick time; a recording has never been checked at all,
    // and a long clip at a high bit rate really can clear 50 MiB. Failing here
    // costs the user nothing — no ticket is requested and no bytes are sent —
    // whereas the same clip refused by the server costs a whole upload first.
    final String? reason = videoRejectionReason(
      contentType: widget.clip.contentType,
      sizeBytes: widget.clip.sizeBytes,
      durationSeconds: widget.clip.durationSeconds,
    );
    if (reason != null) {
      _fail(
        _Stage.uploading,
        ApiFailure(kind: ApiFailureKind.badResponse, message: reason),
      );
      return;
    }

    _setStatus('Requesting an upload slot…');
    final ApiResult<UploadTicket> ticket = await requestUploadTicket(
      // Sniffed from the clip's own bytes. Never a per-platform assumption:
      // Android Chrome records WebM, iOS Safari records MP4, and a picked file
      // is whatever the user picked.
      contentType: widget.clip.contentType,
      sizeBytes: widget.clip.sizeBytes,
      durationS: widget.clip.durationSeconds,
    );
    if (!mounted) return;
    if (!ticket.isOk) {
      _fail(_Stage.uploading, ticket.failure!);
      return;
    }

    _setStatus('Uploading your clip…');
    final ApiFailure? uploadFailure = await uploadVideoToSignedUrl(
      storagePath: ticket.data!.storagePath,
      uploadToken: ticket.data!.uploadToken,
      bytes: widget.clip.bytes,
      contentType: widget.clip.contentType,
    );
    if (!mounted) return;
    if (uploadFailure != null) {
      _fail(_Stage.uploading, uploadFailure);
      return;
    }

    setState(() {
      _stage = _Stage.analysing;
      _statusLine = 'Queuing the analysis…';
    });

    final ApiResult<CreateAnalysisResult> created = await createAnalysis(
      storagePath: ticket.data!.storagePath,
      handednessHint: widget.handednessHint,
      labelHint: widget.labelHint,
      calibration: widget.calibration,
    );
    if (!mounted) return;
    if (!created.isOk) {
      _fail(_Stage.analysing, created.failure!);
      return;
    }

    _setStatus('Analysing your swing…');
    final ApiResult<AnalysisResponse> analysis = await pollUntilComplete(
      created.data!.analysisId,
      onUpdate: (PollUpdate update) {
        if (!mounted) return;
        final int? queue = update.queuePosition;
        _setStatus(
          update.status == JobStatus.queued && queue != null && queue > 0
              ? 'Waiting in the queue (position $queue)…'
              : 'Analysing your swing…',
        );
      },
    );
    if (!mounted) return;
    if (!analysis.isOk) {
      _fail(_Stage.analysing, analysis.failure!);
      return;
    }

    setState(() => _running = false);
    await Navigator.of(context).pushReplacement(
      MaterialPageRoute<void>(
        builder: (BuildContext context) =>
            ResultsScreen(analysis: analysis.data!),
      ),
    );
  }

  void _setStatus(String text) {
    if (!mounted) return;
    setState(() => _statusLine = text);
  }

  void _fail(_Stage stage, ApiFailure failure) {
    if (!mounted) return;
    setState(() {
      _running = false;
      _failure = failure;
      _failedStage = stage;
    });
  }

  @override
  Widget build(BuildContext context) {
    final ApiFailure? failure = _failure;
    return Scaffold(
      appBar: AppBar(title: const Text('Analysing')),
      body: Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: failure == null ? _buildProgress() : _buildError(failure),
        ),
      ),
    );
  }

  Widget _buildProgress() {
    final ThemeData theme = Theme.of(context);
    return Column(
      mainAxisAlignment: MainAxisAlignment.center,
      children: <Widget>[
        const CircularProgressIndicator(),
        const SizedBox(height: 24),
        Text(_statusLine, style: theme.textTheme.titleMedium),
        const SizedBox(height: 8),
        Text(
          _stage == _Stage.uploading
              ? (kIsWeb
                  ? 'Keep this tab open while the clip uploads.'
                  : 'Keep the app open while the clip uploads.')
              : 'This usually takes under a minute.',
          textAlign: TextAlign.center,
          style: theme.textTheme.bodySmall,
        ),
      ],
    );
  }

  Widget _buildError(ApiFailure failure) {
    final ThemeData theme = Theme.of(context);
    final bool duringUpload = _failedStage == _Stage.uploading;

    return Column(
      mainAxisAlignment: MainAxisAlignment.center,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        Icon(Icons.error_outline, size: 40, color: theme.colorScheme.error),
        const SizedBox(height: 16),
        Text(
          duringUpload ? 'Upload failed' : 'Analysis failed',
          textAlign: TextAlign.center,
          style: theme.textTheme.titleLarge,
        ),
        const SizedBox(height: 8),
        Text(
          duringUpload
              ? 'Your clip did not reach the server, so nothing was analysed.'
              : 'Your clip uploaded, but the analysis did not finish.',
          textAlign: TextAlign.center,
          style: theme.textTheme.bodyMedium,
        ),
        const SizedBox(height: 12),
        Text(
          failure.plainLanguage,
          textAlign: TextAlign.center,
          style: theme.textTheme.bodyMedium,
        ),
        if (failure.kind == ApiFailureKind.notSignedIn) ...<Widget>[
          const SizedBox(height: 8),
          Text(
            'Sign in again from the home screen, then retry.',
            textAlign: TextAlign.center,
            style: theme.textTheme.bodySmall,
          ),
        ],
        const SizedBox(height: 24),
        FilledButton.icon(
          onPressed: _run,
          icon: const Icon(Icons.refresh),
          label: Text(duringUpload ? 'Retry upload' : 'Retry analysis'),
        ),
        const SizedBox(height: 8),
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Back to recording'),
        ),
      ],
    );
  }
}
