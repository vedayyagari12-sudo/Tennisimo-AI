import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

/// The standard container every dashboard / history block sits in.
///
/// Padding, radius and the 1px hairline border are defined here once so no
/// other widget has to restate them.
class AppCard extends StatelessWidget {
  const AppCard({
    super.key,
    required this.child,
    this.padding = AppSpacing.cardPadding,
    this.onTap,
  });

  final Widget child;
  final EdgeInsetsGeometry padding;

  /// When non-null the whole card becomes tappable, with an ink ripple.
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final ColorScheme scheme = Theme.of(context).colorScheme;
    final BorderRadius radius = BorderRadius.circular(AppSpacing.cardRadius);

    return Material(
      color: scheme.surfaceContainer,
      clipBehavior: Clip.antiAlias,
      // `shape` carries the radius AND the hairline border. Material asserts
      // if `borderRadius` is also given, so the radius lives here only.
      shape: RoundedRectangleBorder(
        borderRadius: radius,
        side: BorderSide(color: scheme.outline),
      ),
      child: InkWell(
        onTap: onTap,
        borderRadius: radius,
        child: Padding(padding: padding, child: child),
      ),
    );
  }
}
