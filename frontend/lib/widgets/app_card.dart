import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

/// The standard container every dashboard / history block sits in.
///
/// Padding, radius and edge are defined here once so no other widget has to
/// restate them. On the light canvas a card is a white sheet on court grey
/// with a soft shadow and no border (a hairline on every card reads as a
/// cage); on the dark canvas, where shadows vanish, a faint hairline edges it.
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

    final bool dark = scheme.brightness == Brightness.dark;

    return Material(
      color: scheme.surfaceContainer,
      clipBehavior: Clip.antiAlias,
      elevation: dark ? 0 : 1,
      shadowColor: const Color(0x33101512),
      surfaceTintColor: Colors.transparent,
      // `shape` carries the radius AND the edge. Material asserts if
      // `borderRadius` is also given, so the radius lives here only.
      shape: RoundedRectangleBorder(
        borderRadius: radius,
        side: dark
            ? BorderSide(color: scheme.outlineVariant)
            : BorderSide.none,
      ),
      child: InkWell(
        onTap: onTap,
        borderRadius: radius,
        child: Padding(padding: padding, child: child),
      ),
    );
  }
}
