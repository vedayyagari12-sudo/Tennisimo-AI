/// The app's dark design system.
///
/// The brand green (`#2E7D32`, the old `colorSchemeSeed`) is retained but
/// brightened to `#3ECF67` so it carries enough luminance to read as an accent
/// on a near-black surface. Nothing else competes with it: the chartreuse
/// [AppColors.secondary] is reserved for ball speed alone.
library;

import 'package:flutter/material.dart';

/// Raw design tokens. Widgets should prefer `Theme.of(context).colorScheme`;
/// these exist for the few places that need a token the scheme has no slot for.
abstract final class AppColors {
  /// Scaffold background: near-black with a faint green undertone.
  static const Color surface = Color(0xFF0E1512);

  /// The standard card fill.
  static const Color surfaceContainer = Color(0xFF161F1B);

  /// A nested / elevated block inside a card.
  static const Color surfaceContainerHigh = Color(0xFF1E2A25);

  /// Hairline borders and dividers.
  static const Color outline = Color(0xFF2A3A33);

  /// The single vivid accent.
  static const Color primary = Color(0xFF3ECF67);
  static const Color onPrimary = Color(0xFF05140A);

  /// Tennis chartreuse. Ball speed ONLY — never a second general-purpose accent.
  static const Color secondary = Color(0xFFCCFF4D);
  static const Color onSecondary = Color(0xFF142000);

  static const Color onSurface = Color(0xFFE8F0EB);

  /// Secondary text, captions, and every "not measured" string.
  static const Color onSurfaceVariant = Color(0xFF9BAEA4);

  static const Color error = Color(0xFFFF6B6B);

  /// The middle band of the score ramp.
  static const Color scoreMid = Color(0xFFF5C451);
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
/// A null score is NOT zero and never gets a "bad" colour: it means the swing
/// could not be measured, which is a neutral fact, so it renders in the muted
/// [AppColors.onSurfaceVariant] used for all not-measured copy.
Color scoreColor(double? score) {
  if (score == null) return AppColors.onSurfaceVariant;
  if (score >= 80) return AppColors.primary;
  if (score >= 60) return AppColors.scoreMid;
  return AppColors.error;
}

/// Digits that do not jitter as a value animates or changes between sessions.
const List<FontFeature> kTabularFigures = <FontFeature>[
  FontFeature.tabularFigures(),
];

const ColorScheme _scheme = ColorScheme.dark(
  brightness: Brightness.dark,
  primary: AppColors.primary,
  onPrimary: AppColors.onPrimary,
  primaryContainer: Color(0xFF123D22),
  onPrimaryContainer: Color(0xFF9FE9B5),
  secondary: AppColors.secondary,
  onSecondary: AppColors.onSecondary,
  secondaryContainer: Color(0xFF2B3A12),
  onSecondaryContainer: Color(0xFFE4FFA8),
  tertiary: Color(0xFF7FD8C0),
  onTertiary: Color(0xFF032018),
  tertiaryContainer: Color(0xFF17312B),
  onTertiaryContainer: Color(0xFFCDEDE2),
  error: AppColors.error,
  onError: Color(0xFF2B0A0A),
  errorContainer: Color(0xFF3A1B1B),
  onErrorContainer: Color(0xFFFFD7D7),
  surface: AppColors.surface,
  onSurface: AppColors.onSurface,
  onSurfaceVariant: AppColors.onSurfaceVariant,
  surfaceContainerLowest: Color(0xFF0A100D),
  surfaceContainerLow: Color(0xFF111A16),
  surfaceContainer: AppColors.surfaceContainer,
  surfaceContainerHigh: AppColors.surfaceContainerHigh,
  surfaceContainerHighest: Color(0xFF24312B),
  outline: AppColors.outline,
  outlineVariant: Color(0xFF223029),
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
