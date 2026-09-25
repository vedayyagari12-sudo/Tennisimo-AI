/// The colour tokens, one immutable set per (brand flavor x brightness).
///
/// ## Why this is a [ThemeExtension] and not a set of `const` globals
///
/// Brightness is a *runtime* value: the user can flip light/dark while the app
/// is running, and "follow the system" can flip underneath us at sunset. A
/// `const` token cannot depend on a runtime value, so the old
/// `static const Color surface = ...` shape could only ever describe one
/// canvas.
///
/// The alternative — a mutable global that a controller repoints, with getters
/// reading it — compiles, but it makes every colour read invisible to Flutter's
/// dependency tracking: a widget that painted before the switch keeps its old
/// colour until something else happens to rebuild it. That is the stale-colour
/// class of bug, and it is unfixable in general because there is no
/// subscription to miss.
///
/// Handing the tokens to the framework as a [ThemeExtension] makes the read
/// `Theme.of(context)`, which registers an [InheritedWidget] dependency. When
/// `MaterialApp.themeMode` changes, every widget that read a token is rebuilt,
/// by construction. No controller, no global, no staleness.
///
/// The cost is real and is paid openly: a token read is no longer a
/// compile-time constant, so call sites that used to be `const` — a
/// `const AppLogoPainter(background: AppColors.surface)` in a test, say — are
/// not any more. Brightness is a runtime value; something had to give, and
/// this is the thing that gave.
///
/// ## The two token tiers
///
/// Text and large fills need *different* steps of the same accent, and reusing
/// one constant for both is what makes a palette look wrong first:
///
/// * The **text tier** ([primary], [secondary], [error], [scoreHigh],
///   [scoreMid], [scoreLow]) is gated on WCAG 4.5:1 against every surface in
///   the palette. Small marks need contrast and can afford to be vivid.
/// * The **fill tier** ([primaryFill], [secondaryFill], [ballAccent],
///   [errorFill], [scoreHighFill], [scoreMidFill], [scoreLowFill]) is for the
///   score ring, the trend area, the category bars and the brand mark's ball.
///   It is gated on WCAG 3:1 *and* on a perceptual-lightness band (OKLab L
///   0.55-0.78 on dark, 0.32-0.60 on light), because a large area painted with
///   a text-calibrated accent glares on a dark canvas and muddies on a light
///   one.
///
/// Every number in this file was solved for, not eyeballed; `test/
/// palette_test.dart` re-derives the OKLab, WCAG and Machado 2009 CVD maths
/// from scratch and fails if any token drifts out of its band.
library;

import 'package:flutter/material.dart';

import 'brand.dart';

/// One complete set of colour tokens.
@immutable
class AppPalette extends ThemeExtension<AppPalette> {
  const AppPalette({
    required this.brightness,
    required this.surface,
    required this.surfaceContainerLowest,
    required this.surfaceContainerLow,
    required this.surfaceContainer,
    required this.surfaceContainerHigh,
    required this.surfaceContainerHighest,
    required this.onSurface,
    required this.onSurfaceVariant,
    required this.outline,
    required this.outlineVariant,
    required this.primary,
    required this.onPrimary,
    required this.primaryFill,
    required this.onPrimaryFill,
    required this.secondary,
    required this.onSecondary,
    required this.secondaryFill,
    required this.ballAccent,
    required this.error,
    required this.onError,
    required this.errorFill,
    required this.scoreHigh,
    required this.scoreMid,
    required this.scoreLow,
    required this.scoreHighFill,
    required this.scoreMidFill,
    required this.scoreLowFill,
  });

  /// The canvas this set was stepped against. Never inferred from a colour.
  final Brightness brightness;

  /// Scaffold background.
  final Color surface;
  final Color surfaceContainerLowest;
  final Color surfaceContainerLow;

  /// The standard card fill.
  final Color surfaceContainer;

  /// A nested / elevated block inside a card.
  final Color surfaceContainerHigh;
  final Color surfaceContainerHighest;

  final Color onSurface;

  /// Secondary text, captions, and every "not measured" string.
  final Color onSurfaceVariant;

  /// Borders that identify a control — outlined buttons, chips, inputs.
  /// Gated at WCAG 3:1 against the surfaces a control sits on.
  final Color outline;

  /// Decorative hairlines and dividers. Deliberately NOT gated at 3:1: WCAG
  /// 1.4.11 exempts purely decorative rules, and a 3:1 hairline on every card
  /// reads as a cage.
  final Color outlineVariant;

  /// The vivid accent as a small mark: text, icons, thin strokes.
  final Color primary;
  final Color onPrimary;

  /// The same accent re-stepped for large filled areas.
  final Color primaryFill;
  final Color onPrimaryFill;

  /// Ball speed ONLY — never a second general-purpose accent.
  final Color secondary;
  final Color onSecondary;

  /// Ball speed as a large fill.
  final Color secondaryFill;

  /// The ball in the brand mark. Always equal to [secondaryFill] — it is a
  /// large fill — but named separately because the brand mark is not "ball
  /// speed" and must not move if the speed accent ever does.
  final Color ballAccent;

  final Color error;
  final Color onError;
  final Color errorFill;

  /// Top band of the score ramp, as text.
  final Color scoreHigh;

  /// Middle band of the score ramp, as text.
  final Color scoreMid;

  /// Bottom band of the score ramp, as text.
  final Color scoreLow;

  /// Top band of the score ramp, as a large fill.
  final Color scoreHighFill;

  /// Middle band of the score ramp, as a large fill.
  final Color scoreMidFill;

  /// Bottom band of the score ramp, as a large fill.
  final Color scoreLowFill;

  /// The score -> text colour ramp.
  ///
  /// SEMANTIC, not decorative: green / amber / red is the whole signal that
  /// tells a player good / marginal / poor at a glance, so it does not follow
  /// the brand flavor — recolouring it into a two-accent palette would
  /// collapse the three bands towards one hue. It DOES follow brightness,
  /// because a ramp stepped against near-black glares on a tan canvas.
  ///
  /// A null score is NOT zero and never gets a "bad" colour: it means the
  /// swing could not be measured, which is a neutral fact, so it renders in
  /// the muted [onSurfaceVariant] used for all not-measured copy.
  Color scoreColor(double? score) {
    if (score == null) return onSurfaceVariant;
    if (score >= 80) return scoreHigh;
    if (score >= 60) return scoreMid;
    return scoreLow;
  }

  /// [scoreColor]'s fill-tier twin, for the ring arc and the category bars.
  Color scoreFillColor(double? score) {
    if (score == null) return onSurfaceVariant;
    if (score >= 80) return scoreHighFill;
    if (score >= 60) return scoreMidFill;
    return scoreLowFill;
  }

  @override
  AppPalette copyWith({
    Brightness? brightness,
    Color? surface,
    Color? surfaceContainerLowest,
    Color? surfaceContainerLow,
    Color? surfaceContainer,
    Color? surfaceContainerHigh,
    Color? surfaceContainerHighest,
    Color? onSurface,
    Color? onSurfaceVariant,
    Color? outline,
    Color? outlineVariant,
    Color? primary,
    Color? onPrimary,
    Color? primaryFill,
    Color? onPrimaryFill,
    Color? secondary,
    Color? onSecondary,
    Color? secondaryFill,
    Color? ballAccent,
    Color? error,
    Color? onError,
    Color? errorFill,
    Color? scoreHigh,
    Color? scoreMid,
    Color? scoreLow,
    Color? scoreHighFill,
    Color? scoreMidFill,
    Color? scoreLowFill,
  }) {
    return AppPalette(
      brightness: brightness ?? this.brightness,
      surface: surface ?? this.surface,
      surfaceContainerLowest:
          surfaceContainerLowest ?? this.surfaceContainerLowest,
      surfaceContainerLow: surfaceContainerLow ?? this.surfaceContainerLow,
      surfaceContainer: surfaceContainer ?? this.surfaceContainer,
      surfaceContainerHigh: surfaceContainerHigh ?? this.surfaceContainerHigh,
      surfaceContainerHighest:
          surfaceContainerHighest ?? this.surfaceContainerHighest,
      onSurface: onSurface ?? this.onSurface,
      onSurfaceVariant: onSurfaceVariant ?? this.onSurfaceVariant,
      outline: outline ?? this.outline,
      outlineVariant: outlineVariant ?? this.outlineVariant,
      primary: primary ?? this.primary,
      onPrimary: onPrimary ?? this.onPrimary,
      primaryFill: primaryFill ?? this.primaryFill,
      onPrimaryFill: onPrimaryFill ?? this.onPrimaryFill,
      secondary: secondary ?? this.secondary,
      onSecondary: onSecondary ?? this.onSecondary,
      secondaryFill: secondaryFill ?? this.secondaryFill,
      ballAccent: ballAccent ?? this.ballAccent,
      error: error ?? this.error,
      onError: onError ?? this.onError,
      errorFill: errorFill ?? this.errorFill,
      scoreHigh: scoreHigh ?? this.scoreHigh,
      scoreMid: scoreMid ?? this.scoreMid,
      scoreLow: scoreLow ?? this.scoreLow,
      scoreHighFill: scoreHighFill ?? this.scoreHighFill,
      scoreMidFill: scoreMidFill ?? this.scoreMidFill,
      scoreLowFill: scoreLowFill ?? this.scoreLowFill,
    );
  }

  /// Interpolates every token, so a theme switch cross-fades instead of
  /// snapping. [brightness] is discrete and flips at the halfway point.
  @override
  AppPalette lerp(ThemeExtension<AppPalette>? other, double t) {
    if (other is! AppPalette) return this;
    Color c(Color a, Color b) => Color.lerp(a, b, t)!;
    return AppPalette(
      brightness: t < 0.5 ? brightness : other.brightness,
      surface: c(surface, other.surface),
      surfaceContainerLowest:
          c(surfaceContainerLowest, other.surfaceContainerLowest),
      surfaceContainerLow: c(surfaceContainerLow, other.surfaceContainerLow),
      surfaceContainer: c(surfaceContainer, other.surfaceContainer),
      surfaceContainerHigh: c(surfaceContainerHigh, other.surfaceContainerHigh),
      surfaceContainerHighest:
          c(surfaceContainerHighest, other.surfaceContainerHighest),
      onSurface: c(onSurface, other.onSurface),
      onSurfaceVariant: c(onSurfaceVariant, other.onSurfaceVariant),
      outline: c(outline, other.outline),
      outlineVariant: c(outlineVariant, other.outlineVariant),
      primary: c(primary, other.primary),
      onPrimary: c(onPrimary, other.onPrimary),
      primaryFill: c(primaryFill, other.primaryFill),
      onPrimaryFill: c(onPrimaryFill, other.onPrimaryFill),
      secondary: c(secondary, other.secondary),
      onSecondary: c(onSecondary, other.onSecondary),
      secondaryFill: c(secondaryFill, other.secondaryFill),
      ballAccent: c(ballAccent, other.ballAccent),
      error: c(error, other.error),
      onError: c(onError, other.onError),
      errorFill: c(errorFill, other.errorFill),
      scoreHigh: c(scoreHigh, other.scoreHigh),
      scoreMid: c(scoreMid, other.scoreMid),
      scoreLow: c(scoreLow, other.scoreLow),
      scoreHighFill: c(scoreHighFill, other.scoreHighFill),
      scoreMidFill: c(scoreMidFill, other.scoreMidFill),
      scoreLowFill: c(scoreLowFill, other.scoreLowFill),
    );
  }
}

// ---------------------------------------------------------------------------
// The score ramp: keyed on brightness, NOT on flavor.
//
// The bands are the same bytes in both flavors on purpose (see
// AppPalette.scoreColor), but they are re-stepped per canvas: the dark ramp
// sits at OKLab L 0.86 / 0.80 / 0.71 and the light one at 0.47 / 0.38 / 0.29,
// because three colours that read as good / marginal / poor on navy are three
// glares on tan. Each ramp was solved to maximise the minimum OKLab separation
// under all three Machado 2009 CVD simulations, which is why the amber band is
// a deep gold rather than a bright yellow — red-green deficiency leaves
// lightness as the only channel that still separates green from amber.
// ---------------------------------------------------------------------------

const Color _scoreHighDark = Color(0xFF7CEB9E);
const Color _scoreMidDark = Color(0xFFEEB23D);
const Color _scoreLowDark = Color(0xFFF6706A);
const Color _scoreHighFillDark = Color(0xFF50C277);
const Color _scoreMidFillDark = Color(0xFFD38E11);
const Color _scoreLowFillDark = Color(0xFFD04F4B);

const Color _scoreHighLight = Color(0xFF006E27);
const Color _scoreMidLight = Color(0xFF5D3900);
const Color _scoreLowLight = Color(0xFF580000);
const Color _scoreHighFillLight = Color(0xFF128D42);
const Color _scoreMidFillLight = Color(0xFF824C00);
const Color _scoreLowFillLight = Color(0xFF7A0000);

/// Default flavor, dark: the 2026 sports-app navy, `#0F172A`-`#1E293B`, with
/// the brand green re-stepped for it and chartreuse kept for ball speed.
const AppPalette tennisimoDark = AppPalette(
  brightness: Brightness.dark,
  surface: Color(0xFF0F172A),
  surfaceContainerLowest: Color(0xFF070D1F),
  surfaceContainerLow: Color(0xFF151E31),
  surfaceContainer: Color(0xFF182235),
  surfaceContainerHigh: Color(0xFF1E293B),
  surfaceContainerHighest: Color(0xFF253143),
  onSurface: Color(0xFFE6EEF4),
  onSurfaceVariant: Color(0xFFA5B2C3),
  outline: Color(0xFF6C7A8E),
  outlineVariant: Color(0xFF2C3748),
  primary: Color(0xFF58D377),
  onPrimary: Color(0xFF021706),
  primaryFill: Color(0xFF35AA56),
  onPrimaryFill: Color(0xFF001304),
  secondary: Color(0xFFBBE556),
  onSecondary: Color(0xFF111A00),
  secondaryFill: Color(0xFF92B824),
  ballAccent: Color(0xFF92B824),
  error: Color(0xFFF6706A),
  onError: Color(0xFF220807),
  errorFill: Color(0xFFD04F4B),
  scoreHigh: _scoreHighDark,
  scoreMid: _scoreMidDark,
  scoreLow: _scoreLowDark,
  scoreHighFill: _scoreHighFillDark,
  scoreMidFill: _scoreMidFillDark,
  scoreLowFill: _scoreLowFillDark,
);

/// Default flavor, light: warm tan, `#F6F0EA`-`#E8E0D5`, not corporate white.
/// The green and the chartreuse are the same hues as the dark set, stepped
/// down into the band a light canvas can carry.
const AppPalette tennisimoLight = AppPalette(
  brightness: Brightness.light,
  surface: Color(0xFFF6F0EA),
  surfaceContainerLowest: Color(0xFFFFFCF8),
  surfaceContainerLow: Color(0xFFF1E9E1),
  surfaceContainer: Color(0xFFECE5DB),
  surfaceContainerHigh: Color(0xFFE8E0D5),
  surfaceContainerHighest: Color(0xFFE1D9CD),
  onSurface: Color(0xFF121C14),
  onSurfaceVariant: Color(0xFF5B544C),
  outline: Color(0xFF7B7261),
  outlineVariant: Color(0xFFCEC6B9),
  primary: Color(0xFF006A27),
  onPrimary: Color(0xFFFFFFFF),
  primaryFill: Color(0xFF008236),
  onPrimaryFill: Color(0xFFFFFFFF),
  secondary: Color(0xFF485F00),
  onSecondary: Color(0xFFFFFFFF),
  secondaryFill: Color(0xFF688609),
  ballAccent: Color(0xFF688609),
  error: Color(0xFF9C1A20),
  onError: Color(0xFFFFFFFF),
  errorFill: Color(0xFFBC2C30),
  scoreHigh: _scoreHighLight,
  scoreMid: _scoreMidLight,
  scoreLow: _scoreLowLight,
  scoreHighFill: _scoreHighFillLight,
  scoreMidFill: _scoreMidFillLight,
  scoreLowFill: _scoreLowFillLight,
);

/// Alternate flavor, dark: powder blue primary, yellow secondary, white text,
/// over a base with a blue rather than a green undertone.
const AppPalette schoolDark = AppPalette(
  brightness: Brightness.dark,
  surface: Color(0xFF0B1924),
  surfaceContainerLowest: Color(0xFF040F19),
  surfaceContainerLow: Color(0xFF111F2B),
  surfaceContainer: Color(0xFF132430),
  surfaceContainerHigh: Color(0xFF182B38),
  surfaceContainerHighest: Color(0xFF1F3340),
  onSurface: Color(0xFFF0F5F9),
  onSurfaceVariant: Color(0xFFA6B6C3),
  outline: Color(0xFF657C8D),
  outlineVariant: Color(0xFF263947),
  primary: Color(0xFF92CAE6),
  onPrimary: Color(0xFF00131E),
  primaryFill: Color(0xFF4EA4CA),
  onPrimaryFill: Color(0xFF00111C),
  secondary: Color(0xFFE7D652),
  onSecondary: Color(0xFF1A1500),
  secondaryFill: Color(0xFFBBAA10),
  ballAccent: Color(0xFFBBAA10),
  error: Color(0xFFF6706A),
  onError: Color(0xFF220807),
  errorFill: Color(0xFFD04F4B),
  scoreHigh: _scoreHighDark,
  scoreMid: _scoreMidDark,
  scoreLow: _scoreLowDark,
  scoreHighFill: _scoreHighFillDark,
  scoreMidFill: _scoreMidFillDark,
  scoreLowFill: _scoreLowFillDark,
);

/// Alternate flavor, light: the white half of the powder-blue / yellow /
/// white scheme. A theme switch must not produce a broken half, so this set is
/// stepped and gated exactly as thoroughly as the dark one.
const AppPalette schoolLight = AppPalette(
  brightness: Brightness.light,
  surface: Color(0xFFF6FAFD),
  surfaceContainerLowest: Color(0xFFFFFFFF),
  surfaceContainerLow: Color(0xFFEDF4F9),
  surfaceContainer: Color(0xFFE6EFF6),
  surfaceContainerHigh: Color(0xFFDFEAF2),
  surfaceContainerHighest: Color(0xFFD7E3ED),
  onSurface: Color(0xFF0E1C28),
  onSurfaceVariant: Color(0xFF4C5D6C),
  outline: Color(0xFF64798A),
  outlineVariant: Color(0xFFC0CED9),
  primary: Color(0xFF006289),
  onPrimary: Color(0xFFFFFFFF),
  primaryFill: Color(0xFF0078A3),
  onPrimaryFill: Color(0xFFFFFFFF),
  secondary: Color(0xFF615500),
  onSecondary: Color(0xFFFFFFFF),
  secondaryFill: Color(0xFF877900),
  ballAccent: Color(0xFF877900),
  error: Color(0xFF9C1A20),
  onError: Color(0xFFFFFFFF),
  errorFill: Color(0xFFBC2C30),
  scoreHigh: _scoreHighLight,
  scoreMid: _scoreMidLight,
  scoreLow: _scoreLowLight,
  scoreHighFill: _scoreHighFillLight,
  scoreMidFill: _scoreMidFillLight,
  scoreLowFill: _scoreLowFillLight,
);

/// The token set for a flavor on a canvas. Exhaustive over both enums, so
/// there is no "half-themed" combination to fall into.
AppPalette paletteFor(BrandFlavor flavor, Brightness brightness) {
  switch (flavor) {
    case BrandFlavor.tennisimo:
      return brightness == Brightness.dark ? tennisimoDark : tennisimoLight;
    case BrandFlavor.school:
      return brightness == Brightness.dark ? schoolDark : schoolLight;
  }
}

/// Every palette this binary can show, for tests that must cover all four.
const List<AppPalette> kAllPalettes = <AppPalette>[
  tennisimoDark,
  tennisimoLight,
  schoolDark,
  schoolLight,
];
