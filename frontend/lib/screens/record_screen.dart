import 'dart:async';

import 'package:camera/camera.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../models/ball_speed_calibration.dart';
import '../models/enums.dart';
import '../models/video_clip.dart';
import '../services/haptics.dart';
import '../services/video_file_picker.dart';
import '../services/video_intake.dart';
import '../widgets/content_width.dart';
import 'analyzing_screen.dart';
import 'calibration_screen.dart';

/// Hard recording cap. Well under the server's 60 s intake limit.
const Duration kMaxRecordingDuration = Duration(seconds: 15);

/// Turns a [CameraException] from camera setup into a user-facing message.
///
/// Pure: code in, string out. No I/O, no widget dependency, so the
/// permission-denial wording is unit-testable without a camera or a device.
///
/// The plugin passes the platform's error code through untouched
/// (`camera` 0.11.4 `CameraController._initializeWithDescription` rethrows as
/// `CameraException(e.code, e.message)`), so these are the literal codes
/// emitted by `camera_android_camerax` `CameraPermissionsManager` and
/// `camera_avfoundation` `CameraPermissionManager`.
///
/// Only the camera-permission family is handled. The audio family
/// (`AudioAccessDenied` and friends) is deliberately absent: both platforms
/// gate the microphone request on `enableAudio`, which this screen sets to
/// false, so those codes cannot be produced here. See [_setUpCamera].
///
/// Everything else — no camera hardware, camera held by another app, an
/// unexpected platform failure — keeps the previous generic behaviour of
/// showing the platform's own description.
String cameraSetupErrorMessage(CameraException e) {
  switch (e.code) {
    case 'CameraAccessDenied':
      // Android: the user dismissed or denied the runtime dialog; asking again
      // normally re-prompts. iOS: denied at the one and only prompt, after
      // which the OS will not ask again. The wording has to serve both.
      return 'Tennisimo needs camera access to record your swing. '
          'Allow camera access when asked, or enable it for Tennisimo in '
          'your device Settings, then try again.';
    case 'CameraAccessDeniedWithoutPrompt':
      // iOS only, and terminal: the OS will not show the prompt again, so
      // retrying in-app cannot succeed. Send the user to Settings.
      return 'Camera access for Tennisimo is turned off. Open your device '
          'Settings > Tennisimo and turn on Camera, then come back. '
          'Trying again here will not bring the permission prompt back.';
    case 'CameraAccessRestricted':
      // iOS only: Screen Time or a device-management profile. The user may not
      // even be able to grant it themselves, so do not promise a retry works.
      return 'Camera access is restricted on this device, usually by Screen '
          'Time or a device management profile. It has to be allowed in '
          'device Settings before Tennisimo can record.';
    default:
      return e.description ?? 'The camera could not be started.';
  }
}

/// Error codes that mean "this browser cannot record video", whatever else is
/// true about the camera.
///
/// `cameraNotSupported` is the literal string `camera_web` produces:
/// `CameraErrorCode.notSupported.toString()` is `'cameraNotSupported'`, and
/// `CameraWebPlugin.startVideoCapturing` rethrows it as
/// `PlatformException(code: e.code.toString())`, which `CameraController`
/// turns into `CameraException(e.code, e.message)`. It is raised by
/// `Camera._videoMimeType`, which asks `MediaRecorder.isTypeSupported` for
/// WebM-VP9, MP4 and WebM in turn and gives up if the browser supports none.
///
/// `notSupported` is accepted as well, purely so a future rename of that
/// `toString()` cannot silently turn a handled case back into a raw exception
/// on a user's screen.
const Set<String> kRecordingUnsupportedCodes = <String>{
  'cameraNotSupported',
  'notSupported',
};

/// Shown when the browser has no usable MediaRecorder.
///
/// Says what to do next instead of apologising, because there IS a complete
/// path forward: the file-pick intake runs the identical analysis.
const String kRecordingUnsupportedMessage =
    'This browser cannot record video. Film the swing with your normal camera '
    'app and choose the file below instead — it is analysed exactly the same '
    'way.';

/// Shown when a clip was captured but is not in a container we can send.
const String kRecordedFormatUnusableMessage =
    'This device recorded the swing in a format the analyser cannot read. '
    'Film it with your normal camera app and choose the file instead.';

/// PURE. True when [error] means video recording is unavailable here.
///
/// Takes `Object` rather than a typed exception because the failure arrives in
/// more than one shape. `CameraController.startVideoRecording` converts a
/// `PlatformException` into a `CameraException`, but `camera_web`'s own
/// `startVideoCapturing` is not `async` and returns `camera.startVideoRecording()`
/// without awaiting it, so a `CameraWebException` raised inside that future
/// escapes its `try` untranslated and arrives here as itself. Matching on the
/// string is the only way to catch that third shape without importing
/// `camera_web`, which is a web-only package and would not compile for Android.
bool isRecordingUnsupportedError(Object error) {
  final String? code = switch (error) {
    final CameraException e => e.code,
    final PlatformException e => e.code,
    _ => null,
  };
  if (code != null && kRecordingUnsupportedCodes.contains(code)) return true;

  // Untranslated CameraWebException: its toString carries the code and the
  // description listing the mime types the browser refused.
  final String text = error.toString();
  return text.contains('cameraNotSupported') ||
      text.contains('does not support any of the following video types');
}

/// PURE. The message to show when recording fails to start.
///
/// The unsupported case gets the fallback wording; everything else keeps the
/// platform's own description, which for a real camera fault is more useful
/// than anything this app could invent.
String recordingFailureMessage(Object error) {
  if (isRecordingUnsupportedError(error)) return kRecordingUnsupportedMessage;
  if (error is CameraException) {
    return error.description ?? 'Recording could not start.';
  }
  return 'Recording could not start. ($error)';
}

/// Shown when a clip is ready to analyse but no racket hand is chosen.
///
/// Says what to do, because there is exactly one thing to do. The clip itself
/// is not lost by the refusal: nothing has been uploaded, and choosing a hand
/// and pressing again re-runs the same intake.
const String kHandednessMissingMessage =
    'Choose your racket hand — right or left — before analysing this swing.';

/// PURE. Why an analysis cannot start yet, or null when it can.
///
/// Exists as a function rather than an inline `!` because it is the guard that
/// used to be `_handedness!`: a null-assertion inside a route builder, reachable
/// from two real timing windows. Pure, so the refusal is unit-testable without
/// a camera. See [kHandednessMissingMessage].
String? analysisBlockedReason(Handedness? handedness) =>
    handedness == null ? kHandednessMissingMessage : null;

/// Camera capture screen: shot-type and handedness hints, framing guidance,
/// optional court calibration, and a capped recording.
///
/// The camera is created at the device default frame rate. No frame rate is
/// requested, forced, or defaulted (PIPELINE.md 11.5).
class RecordScreen extends StatefulWidget {
  const RecordScreen({super.key});

  @override
  State<RecordScreen> createState() => _RecordScreenState();
}

class _RecordScreenState extends State<RecordScreen> {
  CameraController? _controller;
  String? _cameraError;
  bool _initializing = true;

  bool _isRecording = false;
  Duration _elapsed = Duration.zero;
  Timer? _ticker;

  /// Set once the browser has told us it cannot record. Recording controls are
  /// withdrawn rather than left to fail again, and the file-pick path is
  /// promoted in their place.
  bool _recordingUnsupported = false;

  /// True while the file chooser is open or the chosen file is being read and
  /// measured. Drives a real spinner over a real wait — there is no percentage,
  /// because nothing here reports one.
  bool _picking = false;

  /// True from the first press of Record or "Choose a video file" until that
  /// intake has been handed to the analysis screen or has given up.
  ///
  /// Wider than [_isRecording] on purpose, because [_isRecording] leaves two
  /// windows where the capture controls are live but a clip is already on its
  /// way to [_startAnalysis]:
  ///
  ///  * the file chooser is open — the OS sheet covers this screen, but the
  ///    handedness control underneath it was still enabled;
  ///  * the moment after `stopVideoRecording`, where `_isRecording` is already
  ///    false while the bytes are still being read and sniffed.
  ///
  /// Deselecting handedness in either window used to reach `_handedness!` with
  /// a null and throw inside a route builder. It also closes the double-tap
  /// window on the record button — see [_startRecording].
  bool _intakeInFlight = false;

  /// Why the last chosen file was refused, shown inline next to the button that
  /// produced it. Null when there is nothing to say.
  String? _pickError;

  ShotType _shotType = ShotType.forehandTopspin;
  /// NO DEFAULT, deliberately. A defaulted hint is indistinguishable from a
  /// confirmed one on the server, and it OVERRIDES Stage 8 detection whenever
  /// detection confidence is below its floor (14 of 16 corpus clips). A
  /// left-handed user who never noticed this control would have had every
  /// racket-hand metric computed on the wrong arm, with no flag anywhere.
  /// See docs/PIPELINE.md section 8.1. Null until the user actually chooses.
  Handedness? _handedness;
  BallSpeedCalibration? _calibration;

  @override
  void initState() {
    super.initState();
    _setUpCamera();
  }

  @override
  void dispose() {
    _ticker?.cancel();
    _controller?.dispose();
    super.dispose();
  }

  Future<void> _setUpCamera() async {
    try {
      final List<CameraDescription> cameras = await availableCameras();
      if (cameras.isEmpty) {
        if (!mounted) return;
        setState(() {
          _cameraError = 'No camera was found on this device.';
          _initializing = false;
        });
        return;
      }
      final CameraDescription camera = cameras.firstWhere(
        (CameraDescription c) => c.lensDirection == CameraLensDirection.back,
        orElse: () => cameras.first,
      );
      // ResolutionPreset only; frame rate is left entirely to the device.
      //
      // `enableAudio: false` also means neither platform ever requests the
      // microphone: camerax asks for CAMERA only, and avfoundation skips
      // `requestAudioPermission` entirely. So no Audio* error code can reach
      // the catch below. See [cameraSetupErrorMessage].
      final CameraController controller = CameraController(
        camera,
        ResolutionPreset.high,
        enableAudio: false,
      );
      await controller.initialize();
      if (!mounted) {
        await controller.dispose();
        return;
      }
      setState(() {
        _controller = controller;
        _initializing = false;
      });
    } on CameraException catch (e) {
      if (!mounted) return;
      setState(() {
        _cameraError = cameraSetupErrorMessage(e);
        _initializing = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _cameraError = 'The camera could not be started. ($e)';
        _initializing = false;
      });
    }
  }

  Future<void> _startRecording() async {
    final CameraController? controller = _controller;
    if (controller == null || !controller.value.isInitialized) return;
    if (_isRecording || _intakeInFlight) return;

    // Set BEFORE the await, which is the whole point. `CameraController`
    // does guard re-entry — but with `if (value.isRecordingVideo)`, and it
    // only sets `isRecordingVideo` AFTER `startVideoCapturing` returns
    // (camera 0.11.4, lib/src/camera_controller.dart:576 and :598). Two taps
    // inside that await therefore both pass the plugin's guard and both start
    // a capture. This flag is what actually closes that window.
    setState(() => _intakeInFlight = true);

    try {
      await controller.startVideoRecording();
    } catch (e) {
      // Deliberately catches Object: on web the failure can arrive as a
      // CameraException, a PlatformException or an untranslated
      // CameraWebException. See [isRecordingUnsupportedError].
      if (!mounted) return;
      final bool unsupported = isRecordingUnsupportedError(e);
      setState(() {
        _intakeInFlight = false;
        _recordingUnsupported = unsupported;
        if (unsupported) _pickError = null;
      });
      if (!unsupported) {
        // A deliberate press produced nothing: worth one soft bump.
        unawaited(haptics.actionFailed());
        _showSnack(recordingFailureMessage(e));
      }
      return;
    }

    if (!mounted) return;
    // The most physical moment in the app: the camera is now rolling.
    unawaited(haptics.recordingStarted());
    setState(() {
      _isRecording = true;
      _elapsed = Duration.zero;
    });

    _ticker = Timer.periodic(const Duration(milliseconds: 100), (Timer timer) {
      if (!mounted) return;
      final Duration next = _elapsed + const Duration(milliseconds: 100);
      setState(() => _elapsed = next);
      if (next >= kMaxRecordingDuration) {
        // Hard cap: auto-stop, no user action required.
        unawaited(_stopRecording());
      }
    });
  }

  /// Ends the capture and, if the clip is usable, hands it straight on.
  ///
  /// The intake is only "over" when this whole method is — including the byte
  /// read, the container sniff and the analysis screen. [_intakeInFlight] is
  /// cleared in the `finally` rather than next to `_isRecording = false`,
  /// because between those two points the user could previously clear their
  /// handedness while a clip was already on its way to [_startAnalysis].
  Future<void> _stopRecording() async {
    try {
      await _finishRecording();
    } finally {
      if (mounted) setState(() => _intakeInFlight = false);
    }
  }

  Future<void> _finishRecording() async {
    final CameraController? controller = _controller;
    if (controller == null || !_isRecording) return;

    _ticker?.cancel();
    _ticker = null;
    final Duration recorded = _elapsed;

    XFile file;
    try {
      file = await controller.stopVideoRecording();
    } on CameraException catch (e) {
      if (!mounted) return;
      setState(() => _isRecording = false);
      _showSnack(e.description ?? 'Recording could not be saved.');
      return;
    }

    if (!mounted) return;
    // Stopped, and the clip is in hand.
    unawaited(haptics.recordingStopped());
    setState(() {
      _isRecording = false;
      _elapsed = Duration.zero;
    });

    // Bytes, not a path. On web `XFile.path` is a `blob:` URL that no file API
    // can open, and the upload leg needs the bytes regardless.
    final Uint8List bytes;
    try {
      bytes = await file.readAsBytes();
    } catch (e) {
      if (!mounted) return;
      _showSnack('The recorded clip could not be read. ($e)');
      return;
    }

    // The recorder's container, read back out of what it actually wrote:
    // Android Chrome writes WebM, iOS Safari writes MP4, the native plugins
    // write MP4 or QuickTime. None of that is assumed here.
    final String? contentType = sniffVideoContentType(bytes);
    if (!mounted) return;
    if (contentType == null) {
      setState(() => _pickError = kRecordedFormatUnusableMessage);
      return;
    }

    await _startAnalysis(
      VideoClip(
        bytes: bytes,
        contentType: contentType,
        durationSeconds: recorded.inMilliseconds / 1000.0,
        displayName: 'Recorded swing',
      ),
    );
  }

  /// Opens the platform file chooser, then hands the result to the same
  /// downstream flow a recording uses.
  Future<void> _pickVideo() async {
    if (_handedness == null || _picking || _isRecording || _intakeInFlight) {
      return;
    }
    setState(() {
      _picking = true;
      // Held for longer than `_picking`: the chooser is a full-screen OS sheet,
      // and the handedness control sitting live underneath it was one of the
      // two ways to reach `_startAnalysis` with no racket hand chosen.
      _intakeInFlight = true;
      _pickError = null;
    });

    try {
      final VideoPickOutcome outcome = await pickVideoClip();
      if (!mounted) return;
      setState(() => _picking = false);

      switch (outcome) {
        case VideoPickCancelled():
          return;
        case VideoPickRejected(message: final String message):
          setState(() => _pickError = message);
        case VideoPickSucceeded(clip: final VideoClip clip):
          await _startAnalysis(clip);
      }
    } finally {
      if (mounted) {
        setState(() {
          _picking = false;
          _intakeInFlight = false;
        });
      }
    }
  }

  /// THE single downstream entry point. Both intake paths end here, so
  /// calibration, hints and analysis behave identically whichever was used.
  Future<void> _startAnalysis(VideoClip clip) async {
    // The guard at the point of use. The controls are also held disabled for
    // the whole of an intake (see [_intakeInFlight]), but a belt-and-braces
    // check here is what turns any remaining route into a sentence the user
    // can act on instead of a null-assertion crash.
    final Handedness? handedness = _handedness;
    final String? blocked = analysisBlockedReason(handedness);
    if (blocked != null) {
      _showSnack(blocked);
      return;
    }

    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (BuildContext context) => AnalyzingScreen(
          clip: clip,
          handednessHint: handedness!,
          labelHint: _shotType,
          calibration: _calibration,
        ),
      ),
    );
  }

  Future<void> _openCalibration() async {
    final CameraController? controller = _controller;
    if (controller == null || !controller.value.isInitialized) return;

    final BallSpeedCalibration? result =
        await Navigator.of(context).push<BallSpeedCalibration>(
      MaterialPageRoute<BallSpeedCalibration>(
        builder: (BuildContext context) =>
            CalibrationScreen(controller: controller),
      ),
    );
    if (!mounted) return;
    if (result != null) {
      setState(() => _calibration = result);
    }
  }

  void _showSnack(String message) {
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(message)));
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);

    return Scaffold(
      appBar: AppBar(title: const Text('Record a swing')),
      body: ContentWidth(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
          children: <Widget>[
            const _SetupHintCard(),
            const SizedBox(height: 16),
            _buildPreview(theme),
            const SizedBox(height: 16),
            _buildRecordControls(theme),
            const SizedBox(height: 24),
            Text('Shot type', style: theme.textTheme.titleMedium),
            const SizedBox(height: 4),
            Text(
              'A hint for the analysis. The shot is still inferred from your '
              'technique.',
              style: theme.textTheme.bodySmall,
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: ShotType.selectable.map((ShotType type) {
                return ChoiceChip(
                  label: Text(type.label),
                  selected: _shotType == type,
                  onSelected: _intakeInFlight
                      ? null
                      : (bool _) => setState(() => _shotType = type),
                );
              }).toList(),
            ),
            const SizedBox(height: 24),
            Text('Handedness', style: theme.textTheme.titleMedium),
            const SizedBox(height: 8),
            SegmentedButton<Handedness>(
              segments: const <ButtonSegment<Handedness>>[
                ButtonSegment<Handedness>(
                  value: Handedness.right,
                  label: Text('Right'),
                ),
                ButtonSegment<Handedness>(
                  value: Handedness.left,
                  label: Text('Left'),
                ),
              ],
              emptySelectionAllowed: true,
              selected: _handedness == null
                  ? const <Handedness>{}
                  : <Handedness>{_handedness!},
              // Disabled for the WHOLE intake, not just while the camera is
              // rolling: the hint travels with the clip, so changing it after
              // the clip exists is either meaningless or a crash.
              onSelectionChanged: _intakeInFlight
                  ? null
                  : (Set<Handedness> selection) => setState(
                      () => _handedness =
                          selection.isEmpty ? null : selection.first),
            ),
            if (_handedness == null)
              Padding(
                padding: const EdgeInsets.only(top: 6),
                child: Text(
                  'Choose your racket hand to record or choose a file.',
                  style: theme.textTheme.bodySmall
                      ?.copyWith(color: theme.colorScheme.error),
                ),
              ),
            const SizedBox(height: 24),
            _buildCalibrationSection(theme),
          ],
        ),
      ),
    );
  }

  Widget _buildPreview(ThemeData theme) {
    if (_initializing) {
      return const AspectRatio(
        aspectRatio: 3 / 4,
        child: Center(child: CircularProgressIndicator()),
      );
    }

    final String? error = _cameraError;
    if (error != null || _controller == null) {
      return Container(
        padding: const EdgeInsets.all(24),
        decoration: BoxDecoration(
          color: theme.colorScheme.surfaceContainerHighest,
          borderRadius: BorderRadius.circular(12),
        ),
        child: Column(
          children: <Widget>[
            const Icon(Icons.videocam_off_outlined),
            const SizedBox(height: 8),
            Text(
              error ?? 'The camera is unavailable.',
              textAlign: TextAlign.center,
              style: theme.textTheme.bodyMedium,
            ),
            const SizedBox(height: 12),
            OutlinedButton(
              onPressed: () {
                setState(() {
                  _initializing = true;
                  _cameraError = null;
                });
                _setUpCamera();
              },
              child: const Text('Try again'),
            ),
          ],
        ),
      );
    }

    final CameraController controller = _controller!;
    return ClipRRect(
      borderRadius: BorderRadius.circular(12),
      child: AspectRatio(
        aspectRatio: controller.value.aspectRatio,
        child: Stack(
          fit: StackFit.expand,
          children: <Widget>[
            CameraPreview(controller),
            if (_isRecording)
              Positioned(
                top: 12,
                left: 12,
                child: _ElapsedBadge(
                  elapsed: _elapsed,
                  limit: kMaxRecordingDuration,
                ),
              ),
          ],
        ),
      ),
    );
  }

  Widget _buildRecordControls(ThemeData theme) {
    // `_handedness != null` is part of readiness: recording must not start
    // until the racket hand is an explicit user choice rather than a default.
    final bool ready = _controller != null &&
        _controller!.value.isInitialized &&
        _handedness != null &&
        // Live while recording (that is the Stop button), dead at every other
        // point of an intake.
        (_isRecording || !_intakeInFlight);
    final Duration remaining = kMaxRecordingDuration - _elapsed;

    return Column(
      children: <Widget>[
        if (!_recordingUnsupported) ...<Widget>[
          if (_isRecording)
            LinearProgressIndicator(
              value: (_elapsed.inMilliseconds /
                      kMaxRecordingDuration.inMilliseconds)
                  .clamp(0.0, 1.0),
            ),
          if (_isRecording) const SizedBox(height: 8),
          FilledButton.icon(
            onPressed: !ready
                ? null
                : (_isRecording ? _stopRecording : _startRecording),
            icon: Icon(_isRecording ? Icons.stop : Icons.fiber_manual_record),
            label: Text(
              _isRecording
                  ? 'Stop (${remaining.inSeconds.clamp(0, 15)}s left)'
                  : 'Record',
            ),
          ),
          const SizedBox(height: 6),
          Text(
            'Recording stops automatically at '
            '${kMaxRecordingDuration.inSeconds} seconds.',
            style: theme.textTheme.bodySmall,
          ),
        ],
        if (!_isRecording) ...<Widget>[
          const SizedBox(height: 20),
          _buildFileIntake(theme),
        ],
      ],
    );
  }

  /// The second way in: a video the user already has.
  ///
  /// Weighting differs by platform, and only by platform. On a phone app the
  /// live camera is the point, so this stays a secondary outlined action. On
  /// web — and on any build where the browser has just told us it cannot record
  /// — it is the primary way in and is drawn as one, because presenting a
  /// disabled record button as the main action would be lying about what works.
  Widget _buildFileIntake(ThemeData theme) {
    final bool canPick =
        _handedness != null && !_picking && !_isRecording && !_intakeInFlight;
    final bool promote = kIsWeb || _recordingUnsupported;
    final String? error = _pickError;

    final Widget button = promote
        ? FilledButton.tonalIcon(
            onPressed: canPick ? _pickVideo : null,
            icon: const Icon(Icons.video_library_outlined),
            label: const Text('Choose a video file'),
          )
        : OutlinedButton.icon(
            onPressed: canPick ? _pickVideo : null,
            icon: const Icon(Icons.video_library_outlined),
            label: const Text('Choose a video file'),
          );

    return Column(
      children: <Widget>[
        if (_recordingUnsupported) ...<Widget>[
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: theme.colorScheme.errorContainer,
              borderRadius: BorderRadius.circular(12),
            ),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Icon(Icons.videocam_off_outlined,
                    size: 20, color: theme.colorScheme.onErrorContainer),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    kRecordingUnsupportedMessage,
                    style: theme.textTheme.bodyMedium?.copyWith(
                      color: theme.colorScheme.onErrorContainer,
                    ),
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 12),
        ],
        if (_picking) ...<Widget>[
          const SizedBox(
            height: 20,
            width: 20,
            child: CircularProgressIndicator(strokeWidth: 2),
          ),
          const SizedBox(height: 8),
          Text('Reading the video…', style: theme.textTheme.bodySmall),
          const SizedBox(height: 8),
        ],
        button,
        const SizedBox(height: 6),
        Text(
          'MP4, MOV or WebM, up to ${formatMegabytes(kMaxUploadBytes)} and '
          '${kMaxClipSeconds.toStringAsFixed(0)} seconds. A chosen file is '
          'analysed without ball speed — calibration needs the live camera.',
          textAlign: TextAlign.center,
          style: theme.textTheme.bodySmall,
        ),
        if (error != null) ...<Widget>[
          const SizedBox(height: 8),
          Text(
            error,
            textAlign: TextAlign.center,
            style: theme.textTheme.bodyMedium
                ?.copyWith(color: theme.colorScheme.error),
          ),
        ],
      ],
    );
  }

  Widget _buildCalibrationSection(ThemeData theme) {
    final bool ready =
        _controller != null && _controller!.value.isInitialized && !_isRecording;
    final BallSpeedCalibration? calibration = _calibration;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text('Ball speed (optional)', style: theme.textTheme.titleMedium),
            const SizedBox(height: 6),
            Text(
              calibration == null
                  ? 'Tap two court points to enable a ball-speed reading. '
                      'Everything else works exactly the same without it.'
                  : 'Calibrated: ${calibration.reference.label}.',
              style: theme.textTheme.bodyMedium,
            ),
            const SizedBox(height: 12),
            Row(
              children: <Widget>[
                OutlinedButton(
                  onPressed: ready ? _openCalibration : null,
                  child: Text(
                    calibration == null ? 'Calibrate court' : 'Redo taps',
                  ),
                ),
                if (calibration != null) ...<Widget>[
                  const SizedBox(width: 8),
                  TextButton(
                    onPressed: () => setState(() => _calibration = null),
                    child: const Text('Remove'),
                  ),
                ],
              ],
            ),
          ],
        ),
      ),
    );
  }
}

/// Framing guidance. This is the instruction that actually determines
/// ball-speed accuracy (PIPELINE.md 10.6), so it sits above the preview.
class _SetupHintCard extends StatelessWidget {
  const _SetupHintCard();

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Card(
      color: theme.colorScheme.secondaryContainer,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                Icon(Icons.camera_outdoor_outlined,
                    color: theme.colorScheme.onSecondaryContainer),
                const SizedBox(width: 8),
                Text(
                  'Set up the phone',
                  style: theme.textTheme.titleMedium?.copyWith(
                    color: theme.colorScheme.onSecondaryContainer,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 10),
            _hint(theme, 'Film side-on — the camera to your side, not in front '
                'of or behind you.'),
            _hint(theme, 'Keep the phone level. Do not tilt it down more than '
                'a few degrees.'),
            _hint(theme, 'Stand the phone 5–10 m to the side, on a tripod or '
                'a steady surface.'),
            _hint(theme, 'Keep your whole body in frame, and hit down the '
                'line.'),
            const SizedBox(height: 8),
            Text(
              'Optional: recording at 60 fps can sharpen the ball-speed '
              'reading. Any frame rate works.',
              style: theme.textTheme.bodySmall?.copyWith(
                color: theme.colorScheme.onSecondaryContainer,
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _hint(ThemeData theme, String text) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text('•  ',
              style: theme.textTheme.bodyMedium?.copyWith(
                color: theme.colorScheme.onSecondaryContainer,
              )),
          Expanded(
            child: Text(
              text,
              style: theme.textTheme.bodyMedium?.copyWith(
                color: theme.colorScheme.onSecondaryContainer,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _ElapsedBadge extends StatelessWidget {
  const _ElapsedBadge({required this.elapsed, required this.limit});

  final Duration elapsed;
  final Duration limit;

  @override
  Widget build(BuildContext context) {
    final int seconds = elapsed.inSeconds.clamp(0, limit.inSeconds);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: Colors.black54,
        borderRadius: BorderRadius.circular(20),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          const Icon(Icons.fiber_manual_record, size: 12, color: Colors.red),
          const SizedBox(width: 6),
          Text(
            '${seconds}s / ${limit.inSeconds}s',
            style: const TextStyle(color: Colors.white, fontSize: 13),
          ),
        ],
      ),
    );
  }
}
