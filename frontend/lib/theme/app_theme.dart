/// The app's design system: one light canvas x two build-time brand flavors.
///
/// The colour tokens themselves live in `app_palette.dart`, which also explains
/// why they are a [ThemeExtension] rather than `const` globals. This file turns
/// one [AppPalette] into a [ThemeData], and nothing here holds a colour of its
/// own.
///
/// * Default ([BrandFlavor.tennisimo]) — warm tan, the brand green as the
///   accent with chartreuse reserved for ball speed.
/// * Alternate ([BrandFlavor.school]) — a powder-blue canvas with white
///   cards, deep navy ink, and gold as a fill only (buttons, the nav pill, the
///   logo ball), always under navy ink.
///
/// The app is light-only: there is no dark [ThemeData] to build.
///
/// The score ramp is deliberately not part of either flavor's identity. See
/// [AppPalette.scoreColor].
library;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'app_palette.dart';
import 'brand.dart';

/// Re-exported so a widget needs one import for the whole design system.
export 'app_palette.dart';

/// Reads the palette carried by the theme in force.
///
/// This goes through `Theme.of`, so the caller takes an [InheritedWidget]
/// dependency and is rebuilt automatically when the theme changes. The
/// fallback keeps a widget mounted on a bare [MaterialApp] (as some tests do)
/// on the compiled flavor's palette instead of throwing.
extension AppPaletteAccess on BuildContext {
  AppPalette get palette {
    final ThemeData theme = Theme.of(this);
    return theme.extension<AppPalette>() ?? paletteFor(kBrandFlavor);
  }
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

/// Digits that do not jitter as a value animates or changes between sessions.
const List<FontFeature> kTabularFigures = <FontFeature>[
  FontFeature.tabularFigures(),
];

/// How strongly an accent tints its container role.
const double _containerTint = 0.20;

/// The same, for the quieter tertiary container.
const double _tertiaryTint = 0.12;

/// The M3 scheme for [palette].
///
/// The `*Container` roles are blended rather than hand-tuned: they are only
/// ever backgrounds for [AppPalette.onSurface] text, so deriving them keeps
/// four more tokens per palette out of the validator's way while still being
/// deterministic. `test/palette_test.dart` checks the blends it produces.
ColorScheme buildColorScheme(AppPalette palette) {
  Color tint(Color accent, Color base, double amount) =>
      Color.alphaBlend(accent.withValues(alpha: amount), base);

  return ColorScheme(
    brightness: palette.brightness,
    primary: palette.primary,
    onPrimary: palette.onPrimary,
    primaryContainer:
        tint(palette.primary, palette.surfaceContainerHigh, _containerTint),
    onPrimaryContainer: palette.onSurface,
    secondary: palette.secondary,
    onSecondary: palette.onSecondary,
    secondaryContainer:
        tint(palette.secondary, palette.surfaceContainerHigh, _containerTint),
    onSecondaryContainer: palette.onSurface,
    // No widget reaches for tertiary by name; it exists so Material's own
    // defaults never fall back to a colour from outside the palette.
    tertiary: palette.primaryFill,
    onTertiary: palette.onPrimaryFill,
    tertiaryContainer:
        tint(palette.primary, palette.surfaceContainer, _tertiaryTint),
    onTertiaryContainer: palette.onSurface,
    error: palette.error,
    onError: palette.onError,
    errorContainer:
        tint(palette.error, palette.surfaceContainerHigh, _containerTint),
    onErrorContainer: palette.onSurface,
    surface: palette.surface,
    onSurface: palette.onSurface,
    onSurfaceVariant: palette.onSurfaceVariant,
    surfaceContainerLowest: palette.surfaceContainerLowest,
    surfaceContainerLow: palette.surfaceContainerLow,
    surfaceContainer: palette.surfaceContainer,
    surfaceContainerHigh: palette.surfaceContainerHigh,
    surfaceContainerHighest: palette.surfaceContainerHighest,
    outline: palette.outline,
    outlineVariant: palette.outlineVariant,
  );
}

/// The status-bar style for this app's one light canvas.
///
/// This must be stated explicitly, and it must NOT be keyed to a brightness
/// the framework works out for itself. The app bar is deliberately
/// transparent, and when an [AppBar] has no `systemOverlayStyle` Flutter
/// guesses one with
/// `ThemeData.estimateBrightnessForColor(effectiveBackgroundColor)`. That runs
/// `Color.computeLuminance()`, which IGNORES alpha: `Colors.transparent` is
/// `0x00000000`, so its luminance is 0, so every screen was classified as a
/// dark canvas and got white status-bar icons — unreadable on a near-white
/// canvas. Hard-coding the light-canvas answer here is what keeps that fixed.
///
/// The two icon fields are inverted with respect to each other, which is worth
/// stating rather than remembering. Per the framework's own doc comments in
/// `services/system_chrome.dart`:
///
/// * `statusBarIconBrightness` — "the brightness of the top status bar icons",
///   Android only. Light canvas wants DARK icons.
/// * `statusBarBrightness` — "the brightness of top status bar", iOS only. It
///   describes the BACKGROUND, from which iOS derives its own icon colour.
///   Light canvas is LIGHT.
///
/// The framework's own [SystemUiOverlayStyle.dark] constant — documented as
/// "intended for applications with a light background" — pairs exactly that
/// way (`statusBarIconBrightness: dark`, `statusBarBrightness: light`), which
/// is the cross-check for the polarity above.
SystemUiOverlayStyle lightCanvasOverlayStyle() {
  return SystemUiOverlayStyle(
    statusBarColor: Colors.transparent,
    // Android.
    statusBarIconBrightness: Brightness.dark,
    // iOS: the canvas behind the icons, not the icons.
    statusBarBrightness: Brightness.light,
    systemNavigationBarColor: paletteFor(kBrandFlavor).surface,
    systemNavigationBarIconBrightness: Brightness.dark,
  );
}

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

/// The app theme. There is exactly one, and it is light: `main.dart` hands it
/// to [MaterialApp.theme] and supplies no [MaterialApp.darkTheme], so a dark
/// canvas is not something the framework can choose.
///
/// The palette travels with the theme as a [ThemeExtension], which is what
/// makes every widget that read a token pick up a theme change.
ThemeData buildAppTheme() {
  final AppPalette palette = paletteFor(kBrandFlavor);
  final ThemeData base = ThemeData(
    useMaterial3: true,
    brightness: Brightness.light,
  );
  final ColorScheme scheme = buildColorScheme(palette);

  return base.copyWith(
    colorScheme: scheme,
    extensions: <ThemeExtension<dynamic>>[palette],
    scaffoldBackgroundColor: palette.surface,
    canvasColor: palette.surface,
    textTheme: _textTheme(base.textTheme).apply(
      bodyColor: palette.onSurface,
      displayColor: palette.onSurface,
    ),
    appBarTheme: AppBarTheme(
      backgroundColor: Colors.transparent,
      surfaceTintColor: Colors.transparent,
      foregroundColor: palette.onSurface,
      elevation: 0,
      scrolledUnderElevation: 0,
      centerTitle: false,
      // Never left to Flutter's guess: see [lightCanvasOverlayStyle].
      systemOverlayStyle: lightCanvasOverlayStyle(),
    ),
    cardTheme: CardThemeData(
      color: palette.surfaceContainer,
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppSpacing.cardRadius),
        // A card edge is a decorative rule, not a control boundary.
        side: BorderSide(color: palette.outlineVariant),
      ),
    ),
    navigationBarTheme: NavigationBarThemeData(
      backgroundColor: palette.surfaceContainer,
      surfaceTintColor: Colors.transparent,
      indicatorColor: palette.navIndicator,
      elevation: 0,
      height: 68,
      labelBehavior: NavigationDestinationLabelBehavior.alwaysShow,
      labelTextStyle: WidgetStateProperty.resolveWith<TextStyle>(
        (Set<WidgetState> states) => TextStyle(
          fontSize: 12,
          fontWeight:
              states.contains(WidgetState.selected) ? FontWeight.w600 : null,
          color: states.contains(WidgetState.selected)
              ? palette.onSurface
              : palette.onSurfaceVariant,
        ),
      ),
    ),
    dividerTheme: DividerThemeData(
      color: palette.outlineVariant,
      thickness: 1,
      space: 1,
    ),
    chipTheme: ChipThemeData(
      backgroundColor: palette.surfaceContainerHigh,
      side: BorderSide(color: palette.outline),
      labelStyle: TextStyle(color: palette.onSurface, fontSize: 12),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(999),
      ),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        // A button face is a large filled area: fill tier, not text tier.
        backgroundColor: palette.actionFill,
        foregroundColor: palette.onActionFill,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(999),
        ),
        padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 14),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        foregroundColor: palette.onSurface,
        side: BorderSide(color: palette.outline),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(999),
        ),
        padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 14),
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(foregroundColor: palette.primary),
    ),
    progressIndicatorTheme: ProgressIndicatorThemeData(
      color: palette.primary,
    ),
    snackBarTheme: SnackBarThemeData(
      backgroundColor: palette.surfaceContainerHigh,
      contentTextStyle: TextStyle(color: palette.onSurface),
      behavior: SnackBarBehavior.floating,
    ),
    listTileTheme: ListTileThemeData(
      textColor: palette.onSurface,
      iconColor: palette.onSurfaceVariant,
    ),
  );
}
