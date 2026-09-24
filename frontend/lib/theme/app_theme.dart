/// The app's dark design system, in two build-time flavors.
///
/// Both flavors share one dark neutral base: this is a video / sports app, the
/// widgets in `widgets/` were contrast-tested against a dark surface, and a
/// light base would have to be re-verified everywhere. Only the base's *hue*
/// and the accent roles move between flavors.
///
/// * Default ([BrandFlavor.tennisimo]) — the brand green (`#2E7D32`, the old
///   `colorSchemeSeed`) brightened to `#3ECF67` so it carries enough luminance
///   to read as an accent on a near-black surface, with the chartreuse
///   [AppColors.secondary] reserved for ball speed alone.
/// * Alternate ([BrandFlavor.school]) — powder blue primary, yellow secondary,
///   white text, over a base retuned from a green to a blue undertone so the
///   neutrals sit under the new accents instead of fighting them.
///
/// The score ramp is deliberately NOT part of either palette. See [scoreColor].
library;

import 'package:flutter/material.dart';

import 'brand.dart';

/// Raw design tokens. Widgets should prefer `Theme.of(context).colorScheme`;
/// these exist for the few places that need a token the scheme has no slot for.
///
/// Every token is a compile-time conditional on [kUseAlternatePalette], so the
/// whole palette stays `const` and an unrecognised `BRAND` value simply never
/// selects the alternate branch.
abstract final class AppColors {
  /// Scaffold background: near-black with a faint accent undertone.
  static const Color surface =
      kUseAlternatePalette ? Color(0xFF0E1318) : Color(0xFF0E1512);

  /// The standard card fill.
  static const Color surfaceContainer =
      kUseAlternatePalette ? Color(0xFF161D24) : Color(0xFF161F1B);

  /// A nested / elevated block inside a card.
  static const Color surfaceContainerHigh =
      kUseAlternatePalette ? Color(0xFF1E2833) : Color(0xFF1E2A25);

  /// Hairline borders and dividers.
  static const Color outline =
      kUseAlternatePalette ? Color(0xFF2A3746) : Color(0xFF2A3A33);

  /// The single vivid accent: green by default, powder blue in the alternate.
  static const Color primary =
      kUseAlternatePalette ? Color(0xFFA8D8F0) : Color(0xFF3ECF67);
  static const Color onPrimary =
      kUseAlternatePalette ? Color(0xFF04161F) : Color(0xFF05140A);

  /// Ball speed ONLY — never a second general-purpose accent. Tennis
  /// chartreuse by default, yellow in the alternate.
  static const Color secondary =
      kUseAlternatePalette ? Color(0xFFFFEB3B) : Color(0xFFCCFF4D);
  static const Color onSecondary =
      kUseAlternatePalette ? Color(0xFF211B00) : Color(0xFF142000);

  /// The ball in the brand mark. Same hue as [secondary]; named separately
  /// because the brand mark is not "ball speed" and must not be re-pointed if
  /// the speed accent ever moves.
  static const Color ballAccent =
      kUseAlternatePalette ? Color(0xFFFFEB3B) : Color(0xFFCCFF4D);

  static const Color onSurface =
      kUseAlternatePalette ? Color(0xFFF2F7FB) : Color(0xFFE8F0EB);

  /// Secondary text, captions, and every "not measured" string.
  static const Color onSurfaceVariant =
      kUseAlternatePalette ? Color(0xFFA2B4C6) : Color(0xFF9BAEA4);

  static const Color error = Color(0xFFFF6B6B);

  /// Top band of the score ramp. Identical in both flavors — see [scoreColor].
  static const Color scoreHigh = Color(0xFF3ECF67);

  /// The middle band of the score ramp.
  static const Color scoreMid = Color(0xFFF5C451);

  /// The bottom band of the score ramp.
  static const Color scoreLow = Color(0xFFFF6B6B);
}

/// Spacing scale. Small enough to keep in your head, large enough to be useful.
abstract final class AppSpacing {
  static const double xs = 4;
  static const double sm = 8;
  static const double md = 12;
  static const double lg = 16;
  static const double xl = 24;
  static const double xxl = 32;

  /// Corner radius of the standard card.
  static const double cardRadius = 20;

  /// Corner radius of blocks nested inside a card.
  static const double innerRadius = 12;

  /// Padding inside the standard card.
  static const EdgeInsets cardPadding = EdgeInsets.all(lg);

  /// Horizontal padding of a screen's scrolling content.
  static const EdgeInsets screenPadding =
      EdgeInsets.symmetric(horizontal: lg, vertical: lg);
}

/// The one score -> colour ramp, used by every ring, bar and chip.
///
/// This ramp is SEMANTIC, not decorative: green / amber / red is the whole
/// signal that tells a player good / marginal / poor at a glance. It therefore
/// does not follow the brand flavor — recolouring it into a two-accent palette
/// would collapse the three bands towards one hue and destroy that signal. The
/// bands are the same bytes in both flavors, and each was checked for contrast
/// against both bases.
///
/// A null score is NOT zero and never gets a "bad" colour: it means the swing
/// could not be measured, which is a neutral fact, so it renders in the muted
/// [AppColors.onSurfaceVariant] used for all not-measured copy.
Color scoreColor(double? score) {
  if (score == null) return AppColors.onSurfaceVariant;
  if (score >= 80) return AppColors.scoreHigh;
  if (score >= 60) return AppColors.scoreMid;
  return AppColors.scoreLow;
}

/// Digits that do not jitter as a value animates or changes between sessions.
const List<FontFeature> kTabularFigures = <FontFeature>[
  FontFeature.tabularFigures(),
];

// Scheme-only tokens: roles no widget reaches for by name.
const Color _primaryContainer =
    kUseAlternatePalette ? Color(0xFF12354A) : Color(0xFF123D22);
const Color _onPrimaryContainer =
    kUseAlternatePalette ? Color(0xFFC6E8FA) : Color(0xFF9FE9B5);
const Color _secondaryContainer =
    kUseAlternatePalette ? Color(0xFF3A3208) : Color(0xFF2B3A12);
const Color _onSecondaryContainer =
    kUseAlternatePalette ? Color(0xFFFFF3A0) : Color(0xFFE4FFA8);
const Color _tertiary =
    kUseAlternatePalette ? Color(0xFF7FB8D8) : Color(0xFF7FD8C0);
const Color _onTertiary =
    kUseAlternatePalette ? Color(0xFF04161F) : Color(0xFF032018);
const Color _tertiaryContainer =
    kUseAlternatePalette ? Color(0xFF26333D) : Color(0xFF17312B);
const Color _onTertiaryContainer =
    kUseAlternatePalette ? Color(0xFFDDE9F2) : Color(0xFFCDEDE2);
const Color _surfaceContainerLowest =
    kUseAlternatePalette ? Color(0xFF0A0F14) : Color(0xFF0A100D);
const Color _surfaceContainerLow =
    kUseAlternatePalette ? Color(0xFF111820) : Color(0xFF111A16);
const Color _surfaceContainerHighest =
    kUseAlternatePalette ? Color(0xFF243140) : Color(0xFF24312B);
const Color _outlineVariant =
    kUseAlternatePalette ? Color(0xFF22303D) : Color(0xFF223029);

const ColorScheme _scheme = ColorScheme.dark(
  brightness: Brightness.dark,
  primary: AppColors.primary,
  onPrimary: AppColors.onPrimary,
  primaryContainer: _primaryContainer,
  onPrimaryContainer: _onPrimaryContainer,
  secondary: AppColors.secondary,
  onSecondary: AppColors.onSecondary,
  secondaryContainer: _secondaryContainer,
  onSecondaryContainer: _onSecondaryContainer,
  tertiary: _tertiary,
  onTertiary: _onTertiary,
  tertiaryContainer: _tertiaryContainer,
  onTertiaryContainer: _onTertiaryContainer,
  error: AppColors.error,
  onError: Color(0xFF2B0A0A),
  errorContainer: Color(0xFF3A1B1B),
  onErrorContainer: Color(0xFFFFD7D7),
  surface: AppColors.surface,
  onSurface: AppColors.onSurface,
  onSurfaceVariant: AppColors.onSurfaceVariant,
  surfaceContainerLowest: _surfaceContainerLowest,
  surfaceContainerLow: _surfaceContainerLow,
  surfaceContainer: AppColors.surfaceContainer,
  surfaceContainerHigh: AppColors.surfaceContainerHigh,
  surfaceContainerHighest: _surfaceContainerHighest,
  outline: AppColors.outline,
  outlineVariant: _outlineVariant,
);

/// Base typography, with tabular figures on every size a big numeral uses.
TextTheme _textTheme(TextTheme base) {
  TextStyle? tabular(TextStyle? style, {FontWeight? weight}) =>
      style?.copyWith(fontFeatures: kTabularFigures, fontWeight: weight);

  return base.copyWith(
    displayLarge: tabular(base.displayLarge, weight: FontWeight.w700),
    displayMedium: tabular(base.displayMedium, weight: FontWeight.w700),
    displaySmall: tabular(base.displaySmall, weight: FontWeight.w700),
    headlineLarge: tabular(base.headlineLarge, weight: FontWeight.w700),
    headlineMedium: tabular(base.headlineMedium, weight: FontWeight.w700),
    headlineSmall: tabular(base.headlineSmall, weight: FontWeight.w600),
    titleLarge: tabular(base.titleLarge, weight: FontWeight.w600),
    titleMedium: base.titleMedium?.copyWith(fontWeight: FontWeight.w600),
    titleSmall: base.titleSmall?.copyWith(fontWeight: FontWeight.w600),
    labelLarge: base.labelLarge?.copyWith(letterSpacing: 0.3),
    labelSmall: base.labelSmall?.copyWith(letterSpacing: 0.8),
  );
}

/// The app theme. Built once in `main.dart`.
ThemeData buildAppTheme() {
  final ThemeData base = ThemeData.dark(useMaterial3: true);

  return base.copyWith(
    colorScheme: _scheme,
    scaffoldBackgroundColor: AppColors.surface,
    canvasColor: AppColors.surface,
    textTheme: _textTheme(base.textTheme).apply(
      bodyColor: AppColors.onSurface,
      displayColor: AppColors.onSurface,
    ),
    appBarTheme: const AppBarTheme(
      backgroundColor: Colors.transparent,
      surfaceTintColor: Colors.transparent,
      foregroundColor: AppColors.onSurface,
      elevation: 0,
      scrolledUnderElevation: 0,
      centerTitle: false,
    ),
    cardTheme: CardThemeData(
      color: AppColors.surfaceContainer,
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppSpacing.cardRadius),
        side: const BorderSide(color: AppColors.outline),
      ),
    ),
    navigationBarTheme: NavigationBarThemeData(
      backgroundColor: AppColors.surfaceContainer,
      surfaceTintColor: Colors.transparent,
      indicatorColor: AppColors.primary.withValues(alpha: 0.16),
      elevation: 0,
      height: 68,
      labelBehavior: NavigationDestinationLabelBehavior.alwaysShow,
      labelTextStyle: WidgetStateProperty.resolveWith<TextStyle>(
        (Set<WidgetState> states) => TextStyle(
          fontSize: 12,
          fontWeight:
              states.contains(WidgetState.selected) ? FontWeight.w600 : null,
          color: states.contains(WidgetState.selected)
              ? AppColors.onSurface
              : AppColors.onSurfaceVariant,
        ),
      ),
    ),
    dividerTheme: const DividerThemeData(
      color: AppColors.outline,
      thickness: 1,
      space: 1,
    ),
    chipTheme: ChipThemeData(
      backgroundColor: AppColors.surfaceContainerHigh,
      side: const BorderSide(color: AppColors.outline),
      labelStyle: const TextStyle(color: AppColors.onSurface, fontSize: 12),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(999),
      ),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: AppColors.primary,
        foregroundColor: AppColors.onPrimary,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(999),
        ),
        padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 14),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        foregroundColor: AppColors.onSurface,
        side: const BorderSide(color: AppColors.outline),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(999),
        ),
        padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 14),
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(foregroundColor: AppColors.primary),
    ),
    progressIndicatorTheme: const ProgressIndicatorThemeData(
      color: AppColors.primary,
    ),
    snackBarTheme: const SnackBarThemeData(
      backgroundColor: AppColors.surfaceContainerHigh,
      contentTextStyle: TextStyle(color: AppColors.onSurface),
      behavior: SnackBarBehavior.floating,
    ),
    listTileTheme: const ListTileThemeData(
      textColor: AppColors.onSurface,
      iconColor: AppColors.onSurfaceVariant,
    ),
  );
}
