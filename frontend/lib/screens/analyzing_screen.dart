import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import '../models/analysis_response.dart';
import '../models/ball_speed_calibration.dart';
import '../models/enums.dart';
import '../models/video_clip.dart';
import '../services/api_client.dart';
import '../services/haptics.dart';
import '../services/supabase_storage.dart';
import '../services/video_intake.dart';
import '../widgets/content_width.dart';
import 'results_screen.dart';

/// Which half of the job broke, so the retry copy can say something true.
enum _Stage { uploading, analysing }

/// PURE. What leaving this screen actually costs, told straight.
///
/// The two halves are genuinely different and must not be papered over with
/// one vague sentence:
///
///  * Before the upload finishes there is no job yet, so leaving really does
///    throw the swing away.
///  * After it, the server runs the analysis to completion whatever this app
///    does. Leaving stops the polling and nothing else, and the result shows up
///    in history — so implying the user cancelled the analysis would be false.
String leaveAnalysisMessage({required bool uploaded}) => uploaded
    ? 'Your clip has already uploaded, so the analysis keeps running on the '
        'server. Leaving only stops this screen waiting for it — the result '
        'will be in your history when it finishes.'
    : 'Your clip is still uploading. Leaving now stops it, and nothing will '
        'be analysed.';

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

  /// The live poll loop's stop switch, held so [dispose] can flip it.
  ///
  /// Without this, backing out of this screen left `pollUntilComplete` hitting
  /// the API every 2 s for up to 210 s with nothing on screen to receive the
  /// answer — and starting over gave you a second loop alongside the first.
  /// `mounted` guards only stopped the `setState`s, never the requests.
  PollCancellation? _poll;

  @override
  void initState() {
    super.initState();
    _run();
  }

  @override
  void dispose() {
    // Tied to the screen's lifecycle, so EVERY way out — back button, pop,
    // replacement by the results screen — stops the requests.
    _poll?.cancel();
    super.dispose();
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
    final PollCancellation cancellation = PollCancellation();
    _poll = cancellation;
    final ApiResult<AnalysisResponse> analysis = await pollUntilComplete(
      created.data!.analysisId,
      cancellation: cancellation,
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
    // A cancelled poll is not a failure and gets no error screen: the user
    // left on purpose, was told the analysis continues, and this state object
    // is already on its way out.
    if (analysis.failure?.kind == ApiFailureKind.cancelled) return;
    if (!mounted) return;
    if (!analysis.isOk) {
      _fail(_Stage.analysing, analysis.failure!);
      return;
    }

    setState(() => _running = false);
    // The payoff arrived. The user has been waiting, very possibly with the
    // phone face-down in a bag, so this one is worth feeling.
    unawaited(haptics.analysisComplete());
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

  /// Asks before abandoning a job that is already in flight.
  ///
  /// The decision this dialog encodes: leaving stops the POLLING, not the
  /// analysis. There is no cancel endpoint, the server finishes the job either
  /// way, and the result lands in history — so pretending the user cancelled
  /// something would be a lie, and silently dropping it without saying so would
  /// be a quieter one. The copy therefore states what actually happens.
  Future<bool> _confirmLeave() async {
    final bool? leave = await showDialog<bool>(
      context: context,
      builder: (BuildContext context) => AlertDialog(
        title: const Text('Leave this analysis?'),
        content: Text(
          leaveAnalysisMessage(uploaded: _stage != _Stage.uploading),
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Keep waiting'),
          ),
          TextButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('Leave'),
          ),
        ],
      ),
    );
    return leave ?? false;
  }

  @override
  Widget build(BuildContext context) {
    final ApiFailure? failure = _failure;
    return PopScope<void>(
      // Only while work is genuinely in flight. A finished or failed screen
      // pops on the first press, as it always did.
      canPop: !_running,
      onPopInvokedWithResult: (bool didPop, void result) async {
        if (didPop) return;
        // Resolved before the dialog's await, so no BuildContext crosses it.
        final NavigatorState navigator = Navigator.of(context);
        if (!await _confirmLeave()) return;
        if (!mounted) return;
        // `dispose` cancels the poll; this is just the pop itself.
        navigator.pop();
      },
      child: Scaffold(
        appBar: AppBar(title: const Text('Analysing')),
        body: ContentWidth(
          child: Center(
            child: Padding(
              padding: const EdgeInsets.all(24),
              child: failure == null ? _buildProgress() : _buildError(failure),
            ),
          ),
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
          failure.plainLanguageWithDebugDetail,
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
