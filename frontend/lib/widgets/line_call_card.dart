import 'package:flutter/material.dart';

import '../models/court.dart';
import '../theme/app_theme.dart';

/// Whole centimetres, never "0 cm" for a non-zero distance.
String centimetres(double metres) {
  final double cm = metres.abs() * 100;
  if (cm > 0 && cm < 1) return 'under 1 cm';
  return '${cm.round()} cm';
}

/// The sentence under a call, e.g. "About 21 cm inside the line,
/// uncertainty about 59 cm." Null when no call was made.
String? lineCallSentence(LineCall? call) {
  if (call == null) return null;
  final String side = call.signedMarginM >= 0 ? 'inside' : 'beyond';
  return 'About ${centimetres(call.signedMarginM)} $side the line, '
      'uncertainty about ${centimetres(call.uncertaintyRadiusM)}.';
}

const String kNoCallText =
    'No line call. Tap at least 8 court points when you set up to get in or out calls.';

/// Backend flag on a landing at the net: which side it fell is not measurable.
const String kAtNetFlag = 'at_net';
const String kAtNetText =
    'Landed at the net. Which side it fell on cannot be measured, so it is not called.';

/// Why a landing has no call, in words the player can act on.
String noCallText(ShotLanding landing) =>
    landing.flags.contains(kAtNetFlag) ? kAtNetText : kNoCallText;

/// One landing's call. "Too close to call" is a calm, neutral state: the
/// ball was nearer the line than we can measure, which is not a failure.
class LineCallCard extends StatelessWidget {
  const LineCallCard({super.key, required this.landing, this.onViewVideo});

  final ShotLanding landing;

  /// Needs Phase 1 playback; the button is hidden while this is null.
  final VoidCallback? onViewVideo;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final AppPalette palette = context.palette;
    final LineCall? call = landing.lineCall;
    final String title = call?.verdict.label ?? 'Not called';
    final Color mark = switch (call?.verdict) {
      LineVerdict.inside => palette.primaryFill,
      LineVerdict.out => palette.errorFill,
      _ => palette.onSurfaceVariant,
    };

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.lg),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(title, style: theme.textTheme.titleMedium),
            if (call != null) ...<Widget>[
              const SizedBox(height: AppSpacing.md),
              SizedBox(
                height: 96,
                width: double.infinity,
                child: ExcludeSemantics(
                  child: CustomPaint(
                    painter: LinePatchPainter(
                      call: call,
                      sigma: landing.sigma,
                      lineColor: theme.colorScheme.outline,
                      markColor: mark,
                      background: theme.colorScheme.surfaceContainerHigh,
                    ),
                  ),
                ),
              ),
              const SizedBox(height: AppSpacing.md),
              Text(call.decidingLine.label, style: theme.textTheme.titleSmall),
              const SizedBox(height: AppSpacing.xs),
              Text(lineCallSentence(call)!, style: theme.textTheme.bodyMedium),
            ] else ...<Widget>[
              const SizedBox(height: AppSpacing.sm),
              Text(noCallText(landing), style: theme.textTheme.bodyMedium),
            ],
            if (onViewVideo != null) ...<Widget>[
              const SizedBox(height: AppSpacing.md),
              OutlinedButton(onPressed: onViewVideo, child: const Text('View in video')),
            ],
          ],
        ),
      ),
    );
  }
}

/// A zoomed patch in court metres: the 5 cm line band, the bounce point at
/// its signed margin, and the call's uncertainty along the line normal.
class LinePatchPainter extends CustomPainter {
  LinePatchPainter({
    required this.call,
    required this.sigma,
    required this.lineColor,
    required this.markColor,
    required this.background,
  });

  final LineCall call;
  final BounceSigma sigma;
  final Color lineColor;
  final Color markColor;
  final Color background;

  @override
  void paint(Canvas canvas, Size size) {
    canvas.drawRect(Offset.zero & size, Paint()..color = background);
    // Fit the margin and its uncertainty with room on both sides.
    final double spanM = (call.signedMarginM.abs() + call.uncertaintyRadiusM) * 2.4 + 0.2;
    final double pxPerM = size.width / spanM;
    final double lineX = size.width / 2;
    // Inside is to the left of the line.
    final double pointX = lineX - call.signedMarginM * pxPerM;
    final double cy = size.height / 2;
    canvas.drawRect(
      Rect.fromLTWH(lineX, 0, (0.05 * pxPerM).clamp(2, size.width), size.height),
      Paint()..color = lineColor,
    );
    final double r = call.uncertaintyRadiusM * pxPerM;
    final Rect band = Rect.fromCenter(
        center: Offset(pointX, cy), width: 2 * r, height: (size.height * 0.7).clamp(8, size.height));
    canvas.drawOval(band, Paint()..color = markColor.withValues(alpha: 0.16));
    canvas.drawOval(
        band,
        Paint()
          ..color = markColor.withValues(alpha: 0.5)
          ..style = PaintingStyle.stroke);
    canvas.drawCircle(Offset(pointX, cy), 4, Paint()..color = markColor);
  }

  @override
  bool shouldRepaint(LinePatchPainter old) => old.call != call || old.markColor != markColor;
}
