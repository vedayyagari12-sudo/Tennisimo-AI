/// Motion tokens, and the one place the reduced-motion preference is read.
///
/// Every animation in the app asks [motionDuration] for its length instead of
/// hardcoding one, so "the user asked for less motion" is honoured in a single
/// place rather than remembered at each call site.
library;

import 'package:flutter/material.dart';

/// How long the score ring takes to sweep and count up.
const Duration kScoreRingDuration = Duration(milliseconds: 900);

/// The shimmer sweep across a loading placeholder.
const Duration kShimmerDuration = Duration(milliseconds: 1400);

/// One list item's entrance.
const Duration kEntranceDuration = Duration(milliseconds: 260);

/// The delay added per list position when staggering an entrance.
const Duration kEntranceStagger = Duration(milliseconds: 45);

/// The app's standard easing for something arriving.
const Curve kEnterCurve = Curves.easeOutCubic;

/// True when this device asks for less motion.
///
/// Two signals, both of which mean "do not animate at me":
/// * `disableAnimations` — the OS-level "Remove animations" / "Reduce motion"
///   setting, forwarded by the engine.
/// * `accessibleNavigation` — a screen reader is driving, where movement under
///   the focus is actively disorienting.
bool prefersReducedMotion(BuildContext context) =>
    MediaQuery.disableAnimationsOf(context) ||
    MediaQuery.accessibleNavigationOf(context);

/// [full], or [Duration.zero] when the user asked for reduced motion.
///
/// Zero rather than "shorter": a half-speed animation is still an animation.
/// The end state is identical either way, so nothing is lost by jumping to it.
Duration motionDuration(BuildContext context, Duration full) =>
    prefersReducedMotion(context) ? Duration.zero : full;
