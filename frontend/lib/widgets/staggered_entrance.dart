import 'package:flutter/material.dart';

import '../theme/motion.dart';

/// Fades and lifts a list item in, a beat after the one above it.
///
/// One-shot: it plays when the item is first built and never again, so
/// scrolling a settled list does not keep re-animating rows. There is no
/// controller to leak — [TweenAnimationBuilder] runs the single pass and the
/// per-item delay is expressed as an [Interval] on the curve.
///
/// Under reduced motion the duration collapses to zero, so the item is simply
/// there on its first frame.
class StaggeredEntrance extends StatelessWidget {
  const StaggeredEntrance({
    super.key,
    required this.index,
    required this.child,
  });

  /// Position in the list. Clamped internally, so a long list still finishes
  /// arriving promptly instead of trickling in for several seconds.
  final int index;

  final Widget child;

  /// Past this position every item shares the last slot's delay.
  static const int maxStaggered = 8;

  @override
  Widget build(BuildContext context) {
    final int slot = index.clamp(0, maxStaggered);
    final Duration delay = kEntranceStagger * slot;
    final Duration total =
        motionDuration(context, kEntranceDuration + delay);

    if (total == Duration.zero) return child;

    final double delayFraction =
        delay.inMicroseconds / total.inMicroseconds;

    return TweenAnimationBuilder<double>(
      tween: Tween<double>(begin: 0.0, end: 1.0),
      duration: total,
      curve: Interval(delayFraction, 1.0, curve: kEnterCurve),
      builder: (BuildContext context, double t, Widget? child) {
        return Opacity(
          opacity: t,
          child: Transform.translate(
            offset: Offset(0, (1 - t) * 8),
            child: child,
          ),
        );
      },
      child: child,
    );
  }
}
