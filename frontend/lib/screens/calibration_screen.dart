import 'dart:async';

import 'package:camera/camera.dart';
import 'package:flutter/material.dart';

import '../models/ball_speed_calibration.dart';
import '../models/enums.dart';
import '../services/haptics.dart';
import '../widgets/content_width.dart';

/// Court calibration: tap the two ends of a court reference line on a frozen
/// preview, confirm the drawn segment, and hand back a [BallSpeedCalibration].
///
/// Skipping is a first-class outcome: pop with null and nothing is lost except
/// the ball-speed reading.
class CalibrationScreen extends StatefulWidget {
  const CalibrationScreen({super.key, required this.controller});

  /// An already-initialised controller owned by the record screen. This screen
  /// pauses and resumes the preview but never disposes it.
  final CameraController controller;

  @override
  State<CalibrationScreen> createState() => _CalibrationScreenState();
}

class _CalibrationScreenState extends State<CalibrationScreen> {
  CourtReference _reference = CourtReference.sidelineBaselineToNet;

  /// Taps, normalized to the preview surface (0-1, y down).
  Offset? _pointA;
  Offset? _pointB;

  /// Size of the tap surface in logical pixels, captured at tap time.
  Size _surfaceSize = Size.zero;

  @override
  void initState() {
    super.initState();
    // Freeze the preview so the user taps a still image.
    widget.controller.pausePreview();
  }

  @override
  void dispose() {
    widget.controller.resumePreview();
    super.dispose();
  }

  void _handleTap(TapDownDetails details, Size size) {
    if (size.width <= 0 || size.height <= 0) return;
    final Offset normalized = Offset(
      (details.localPosition.dx / size.width).clamp(0.0, 1.0),
      (details.localPosition.dy / size.height).clamp(0.0, 1.0),
    );
    // One tick per placed endpoint: the tap landed on a frozen image with no
    // other confirmation that it registered.
    unawaited(haptics.calibrationPointPlaced());
    setState(() {
      _surfaceSize = size;
      if (_pointA == null) {
        _pointA = normalized;
      } else if (_pointB == null) {
        _pointB = normalized;
      } else {
        _pointA = normalized;
        _pointB = null;
      }
    });
  }

  BallSpeedCalibration? _buildCalibration() {
    final Offset? a = _pointA;
    final Offset? b = _pointB;
    if (a == null || b == null) return null;
    if (_surfaceSize.width <= 0 || _surfaceSize.height <= 0) return null;

    final double dpr = MediaQuery.of(context).devicePixelRatio;
    return BallSpeedCalibration(
      pointA: NormalizedPoint(x: a.dx, y: a.dy),
      pointB: NormalizedPoint(x: b.dx, y: b.dy),
      reference: _reference,
      // The preview surface the taps were actually made on, in device pixels.
      captureWidthPx: (_surfaceSize.width * dpr).round(),
      captureHeightPx: (_surfaceSize.height * dpr).round(),
      // This client draws the preview unrotated, so there is nothing for the
      // server to invert.
      captureRotationDeg: 0,
      tappedAt: DateTime.now(),
    );
  }

  void _confirm() {
    final BallSpeedCalibration? calibration = _buildCalibration();
    if (calibration == null) return;
    Navigator.of(context).pop(calibration);
  }

  void _skip() => Navigator.of(context).pop();

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final BallSpeedCalibration? candidate = _buildCalibration();
    final bool bothTapped = _pointA != null && _pointB != null;
    final bool tooClose = candidate != null && !candidate.isFarEnoughApart;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Court calibration'),
        actions: <Widget>[
          TextButton(
            onPressed: _skip,
            child: const Text('Skip'),
          ),
        ],
      ),
      body: ContentWidth(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
          children: <Widget>[
            Text(
              'Optional. Calibration is only used for the ball-speed reading — '
              'skip it and your swing analysis is exactly the same.',
              style: theme.textTheme.bodyMedium,
            ),
            const SizedBox(height: 16),
            Text('Reference line', style: theme.textTheme.titleMedium),
            const SizedBox(height: 8),
            ...CourtReference.values.map((CourtReference reference) {
              final bool selected = _reference == reference;
              return ListTile(
                contentPadding: EdgeInsets.zero,
                leading: Icon(selected
                    ? Icons.radio_button_checked
                    : Icons.radio_button_unchecked),
                title: Text(reference.label),
                subtitle: Text(reference.description),
                selected: selected,
                onTap: () => setState(() => _reference = reference),
              );
            }),
            const SizedBox(height: 8),
            Text(
              _pointA == null
                  ? 'Tap the first end of the line on the image below.'
                  : (_pointB == null
                      ? 'Now tap the other end.'
                      : 'Tap again to start over.'),
              style: theme.textTheme.titleSmall,
            ),
            const SizedBox(height: 8),
            _buildTapSurface(),
            if (tooClose) ...<Widget>[
              const SizedBox(height: 8),
              Text(
                'Those two taps are too close together to give a usable scale. '
                'Tap the full length of the line.',
                style: theme.textTheme.bodySmall
                    ?.copyWith(color: theme.colorScheme.error),
              ),
            ],
            const SizedBox(height: 16),
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Text(
                  'Moving the phone after calibrating invalidates it. If you move '
                  'the camera, tap the two points again.',
                  style: theme.textTheme.bodyMedium,
                ),
              ),
            ),
            const SizedBox(height: 16),
            Row(
              children: <Widget>[
                Expanded(
                  child: FilledButton(
                    onPressed: (bothTapped && !tooClose) ? _confirm : null,
                    child: const Text('Use these points'),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: OutlinedButton(
                    onPressed: _skip,
                    child: const Text('Skip calibration'),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Center(
              child: TextButton(
                onPressed: bothTapped
                    ? () => setState(() {
                          _pointA = null;
                          _pointB = null;
                        })
                    : null,
                child: const Text('Clear taps'),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildTapSurface() {
    // The tap surface matches the camera aspect ratio exactly, so its aspect
    // matches the recorded frame's. A letterboxed or cropped surface would fail
    // the server's frame-shape check.
    return ClipRRect(
      borderRadius: BorderRadius.circular(12),
      child: AspectRatio(
        aspectRatio: widget.controller.value.aspectRatio,
        child: LayoutBuilder(
          builder: (BuildContext context, BoxConstraints constraints) {
            final Size size = Size(constraints.maxWidth, constraints.maxHeight);
            return GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTapDown: (TapDownDetails details) => _handleTap(details, size),
              child: Stack(
                fit: StackFit.expand,
                children: <Widget>[
                  CameraPreview(widget.controller),
                  CustomPaint(
                    painter: _SegmentPainter(
                      pointA: _pointA,
                      pointB: _pointB,
                      color: Theme.of(context).colorScheme.primary,
                    ),
                  ),
                ],
              ),
            );
          },
        ),
      ),
    );
  }
}

/// Draws the tapped endpoints and the segment between them for confirmation.
class _SegmentPainter extends CustomPainter {
  const _SegmentPainter({
    required this.pointA,
    required this.pointB,
    required this.color,
  });

  final Offset? pointA;
  final Offset? pointB;
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    Offset? toPixels(Offset? normalized) => normalized == null
        ? null
        : Offset(normalized.dx * size.width, normalized.dy * size.height);

    final Offset? a = toPixels(pointA);
    final Offset? b = toPixels(pointB);

    final Paint line = Paint()
      ..color = color
      ..strokeWidth = 3
      ..style = PaintingStyle.stroke;
    final Paint dot = Paint()..color = color;
    final Paint halo = Paint()
      ..color = Colors.white
      ..strokeWidth = 2
      ..style = PaintingStyle.stroke;

    if (a != null && b != null) {
      canvas.drawLine(a, b, line);
    }
    for (final Offset? point in <Offset?>[a, b]) {
      if (point == null) continue;
      canvas.drawCircle(point, 7, dot);
      canvas.drawCircle(point, 9, halo);
    }
  }

  @override
  bool shouldRepaint(_SegmentPainter oldDelegate) =>
      oldDelegate.pointA != pointA ||
      oldDelegate.pointB != pointB ||
      oldDelegate.color != color;
}
