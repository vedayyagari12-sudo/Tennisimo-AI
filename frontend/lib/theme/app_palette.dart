/// The colour tokens, one immutable set per brand flavor.
///
/// ## One canvas
///
/// This app is light-only. There is no dark set here to be selected, so there
/// is no second canvas for a token to be mis-stepped against, and no runtime
/// brightness for anything to go stale over.
///
/// ## Why this is a [ThemeExtension] and not a set of `const` globals
///
/// Handing the tokens to the framework makes a token read
/// `Theme.of(context)`, which registers an [InheritedWidget] dependency. The
/// alternative — a mutable global with getters reading it — compiles, but it
/// makes every colour read invisible to Flutter's dependency tracking: a
/// widget that painted before a theme change keeps its old colour until
/// something else happens to rebuild it. That is the stale-colour class of
/// bug, and it is unfixable in general because there is no subscription to
/// miss.
///
/// The cost is paid openly: a token read is not a compile-time constant, so
/// call sites that would like to be `const` — a
/// `const AppLogoPainter(background: AppColors.surface)` in a test, say — are
/// not.
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
///   0.32-0.60), because a large area painted with a text-calibrated accent
///   muddies on a light canvas.
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
    required this.chartSeries,
    required this.chartOther,
  });

  /// The canvas this set was stepped against. Never inferred from a colour,
  /// and always [Brightness.light]: this app has one canvas.
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

  /// The categorical chart palette: series IDENTITY, never status.
  ///
  /// Four hues in a FIXED order, assigned in sequence and never cycled. The
  /// order is the colour-blind-safety mechanism, not taste: every pair that
  /// can touch — neighbours, the wrap-around pair of a donut ring, and each
  /// end against [chartOther] — was stepped to stay apart under the Machado
  /// 2009 dichromacy simulations, and `test/palette_test.dart` gates it.
  ///
  /// Slot 0 is always "this swing" and slot 1 always the thing it is compared
  /// with (the previous swing, or the player's average), so one colour means
  /// one thing everywhere it appears.
  ///
  /// Never used for text: labels, values and legends wear the ink tokens and
  /// sit BESIDE a swatch of the series colour. Never green / amber / red
  /// either — those are the score ramp's, and a series painted in them would
  /// read as a verdict.
  final List<Color> chartSeries;

  /// The de-emphasised "Other" slice of a part-to-whole chart: a neutral that
  /// reads as "the rest", clear of every [chartSeries] slot it can touch.
  final Color chartOther;

  /// The score -> text colour ramp.
  ///
  /// SEMANTIC, not decorative: green / amber / red is the whole signal that
  /// tells a player good / marginal / poor at a glance, so it does not follow
  /// the brand flavor — recolouring it into a two-accent palette would
  /// collapse the three bands towards one hue.
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
    List<Color>? chartSeries,
    Color? chartOther,
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
      chartSeries: chartSeries ?? this.chartSeries,
      chartOther: chartOther ?? this.chartOther,
    );
  }

  /// Interpolates every token. Nothing in this app swaps one palette for
  /// another, but [ThemeExtension] requires it and Material animates theme
  /// changes generically. [brightness] is discrete and flips at the halfway
  /// point.
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
      chartSeries: <Color>[
        for (int i = 0; i < chartSeries.length; i++)
          i < other.chartSeries.length
              ? c(chartSeries[i], other.chartSeries[i])
              : chartSeries[i],
      ],
      chartOther: c(chartOther, other.chartOther),
    );
  }
}

// ---------------------------------------------------------------------------
// The score ramp: shared by both flavors, NOT part of either one's identity.
//
// The bands are the same bytes in both flavors on purpose (see
// AppPalette.scoreColor). They sit at OKLab L 0.47 / 0.38 / 0.29, stepped for
// the light canvas this app ships. The ramp was solved to maximise the minimum
// OKLab separation under all three Machado 2009 CVD simulations, which is why
// the amber band is a deep gold rather than a bright yellow — red-green
// deficiency leaves lightness as the only channel that still separates green
// from amber.
// ---------------------------------------------------------------------------

const Color _scoreHighLight = Color(0xFF006E27);
const Color _scoreMidLight = Color(0xFF5D3900);
const Color _scoreLowLight = Color(0xFF580000);
const Color _scoreHighFillLight = Color(0xFF128D42);
const Color _scoreMidFillLight = Color(0xFF824C00);
const Color _scoreLowFillLight = Color(0xFF7A0000);

/// Default flavor: warm tan, `#F6F0EA`-`#E8E0D5`, not corporate white, with
/// the brand green as the accent and chartreuse kept for ball speed — both
/// stepped into the band a light canvas can carry.
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
  // Cerulean, burnt orange, indigo, plum: cool/warm alternation so each
  // neighbour differs in hue AND lightness, stepped for the tan canvas.
  chartSeries: <Color>[
    Color(0xFF027FBB),
    Color(0xFFD14B0D),
    Color(0xFF5040A8),
    Color(0xFF912E6E),
  ],
  chartOther: Color(0xFF7B7261),
);

/// Alternate flavor: the powder-blue / yellow / white scheme, on a near-white
/// base with a blue undertone. Stepped and gated exactly as thoroughly as the
/// default flavor.
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
  // Royal blue, burnt orange, plum, lavender: the same alternation, stepped
  // for the blue-white canvas.
  chartSeries: <Color>[
    Color(0xFF154FAC),
    Color(0xFFCC5E35),
    Color(0xFF823073),
    Color(0xFFA063CA),
  ],
  chartOther: Color(0xFF64798A),
);

/// The token set for a flavor. Exhaustive over [BrandFlavor], so there is no
/// "half-themed" flavor to fall into.
AppPalette paletteFor(BrandFlavor flavor) {
  switch (flavor) {
    case BrandFlavor.tennisimo:
      return tennisimoLight;
    case BrandFlavor.school:
      return schoolLight;
  }
}

/// Every palette this binary can show, for tests that must cover both flavors.
const List<AppPalette> kAllPalettes = <AppPalette>[
  tennisimoLight,
  schoolLight,
];
