import 'package:flutter/material.dart';

import '../theme/app_theme.dart';
import '../theme/motion.dart';

/// The loading placeholder used by the dashboard and by history.
///
/// A block the shape of the content that is coming, with a slow highlight
/// sweeping across it — which says "this is loading, and it will look like
/// this" in a way a bare spinner cannot.
///
/// Under reduced motion the sweep is not merely paused: the controller is
/// never started, so the widget is a plain static block and a test that pumps
/// it settles immediately.
class SkeletonBlock extends StatefulWidget {
  const SkeletonBlock({super.key, required this.height});

  final double height;

  @override
  State<SkeletonBlock> createState() => _SkeletonBlockState();
}

class _SkeletonBlockState extends State<SkeletonBlock>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: kShimmerDuration,
  );

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    // Re-read on every dependency change, so toggling the accessibility
    // setting while the app is open takes effect on the next frame.
    if (prefersReducedMotion(context)) {
      _controller.stop();
      _controller.value = 0;
    } else if (!_controller.isAnimating) {
      _controller.repeat();
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final ColorScheme scheme = Theme.of(context).colorScheme;
    final BorderRadius radius =
        BorderRadius.circular(AppSpacing.cardRadius);
    // Colour OR gradient, never both: BoxDecoration ignores the colour when a
    // gradient is present, which would quietly change the resting tone.
    BoxDecoration decoration({Gradient? gradient}) => BoxDecoration(
          color: gradient == null ? scheme.surfaceContainer : null,
          gradient: gradient,
          borderRadius: radius,
          border: Border.all(color: scheme.outline),
        );

    if (prefersReducedMotion(context)) {
      return Container(height: widget.height, decoration: decoration());
    }

    return AnimatedBuilder(
      animation: _controller,
      builder: (BuildContext context, Widget? child) {
        // The highlight travels from off the left edge to off the right one.
        final double t = _controller.value * 2 - 1;
        return Container(
          height: widget.height,
          decoration: decoration(
            gradient: LinearGradient(
              begin: Alignment(t - 0.6, 0),
              end: Alignment(t + 0.6, 0),
              colors: <Color>[
                scheme.surfaceContainer,
                scheme.surfaceContainerHighest,
                scheme.surfaceContainer,
              ],
              stops: const <double>[0.0, 0.5, 1.0],
            ),
          ),
        );
      },
    );
  }
}
