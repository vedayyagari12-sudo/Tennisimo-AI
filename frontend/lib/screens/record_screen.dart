import 'dart:async';

import 'package:camera/camera.dart';
import 'package:flutter/material.dart';

import '../models/ball_speed_calibration.dart';
import '../models/enums.dart';
import 'analyzing_screen.dart';
import 'calibration_screen.dart';

/// Hard recording cap. Well under the server's 60 s intake limit.
const Duration kMaxRecordingDuration = Duration(seconds: 15);

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
        _cameraError = e.description ?? 'The camera could not be started.';
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
    if (_isRecording) return;

    try {
      await controller.startVideoRecording();
    } on CameraException catch (e) {
      if (!mounted) return;
      _showSnack(e.description ?? 'Recording could not start.');
      return;
    }

    if (!mounted) return;
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

  Future<void> _stopRecording() async {
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
    setState(() {
      _isRecording = false;
      _elapsed = Duration.zero;
    });

    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (BuildContext context) => AnalyzingScreen(
          videoPath: file.path,
          durationSeconds: recorded.inMilliseconds / 1000.0,
          handednessHint: _handedness!,
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
      body: ListView(
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
                onSelected: _isRecording
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
            onSelectionChanged: _isRecording
                ? null
                : (Set<Handedness> selection) => setState(
                    () => _handedness =
                        selection.isEmpty ? null : selection.first),
          ),
          if (_handedness == null)
            Padding(
              padding: const EdgeInsets.only(top: 6),
              child: Text(
                'Choose your racket hand to start recording.',
                style: theme.textTheme.bodySmall
                    ?.copyWith(color: theme.colorScheme.error),
              ),
            ),
          const SizedBox(height: 24),
          _buildCalibrationSection(theme),
        ],
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
        _handedness != null;
    final Duration remaining = kMaxRecordingDuration - _elapsed;

    return Column(
      children: <Widget>[
        if (_isRecording)
          LinearProgressIndicator(
            value: (_elapsed.inMilliseconds /
                    kMaxRecordingDuration.inMilliseconds)
                .clamp(0.0, 1.0),
          ),
        if (_isRecording) const SizedBox(height: 8),
        FilledButton.icon(
          onPressed:
              !ready ? null : (_isRecording ? _stopRecording : _startRecording),
          icon: Icon(_isRecording ? Icons.stop : Icons.fiber_manual_record),
          label: Text(
            _isRecording
                ? 'Stop (${remaining.inSeconds.clamp(0, 15)}s left)'
                : 'Record',
          ),
        ),
        const SizedBox(height: 6),
        Text(
          'Recording stops automatically at 15 seconds.',
          style: theme.textTheme.bodySmall,
        ),
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
