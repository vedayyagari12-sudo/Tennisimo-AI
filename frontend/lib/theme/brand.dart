/// The build-time brand flavor: one codebase, two colour schemes.
///
/// Selected exactly the way `API_BASE_URL` is in `services/api_client.dart` —
/// a compile-time [String.fromEnvironment] constant supplied with
/// `--dart-define`, NOT `Platform.environment`, which would only ever see the
/// build machine's shell and does not exist on the web target at all.
///
/// ```
/// flutter build web                              # default flavor
/// flutter build web   --dart-define=BRAND=school # alternate colour scheme
/// flutter test        --dart-define=BRAND=school
/// ```
///
/// Only the palette changes. The product is named "Tennisimo" in both
/// flavors, and nothing else about the build differs.
library;

/// The selectable colour schemes.
enum BrandFlavor {
  /// The shipping palette: green primary, chartreuse ball-speed accent.
  tennisimo('tennisimo'),

  /// The alternate colour scheme: powder blue primary, yellow secondary,
  /// white text, on the same dark neutral base.
  school('school');

  const BrandFlavor(this.flag);

  /// The literal accepted by `--dart-define=BRAND=...`.
  final String flag;

  /// The flavor used when no flag is passed, and the landing place for any
  /// value that is not recognised.
  static const BrandFlavor fallback = BrandFlavor.tennisimo;

  /// Returns the flavor whose [flag] matches [raw], else [fallback].
  ///
  /// A typo, a stale flag, an empty string or null must never crash the app or
  /// leave it half-themed: an unrecognised flavor degrades to the shipping
  /// palette, the same discipline the wire enums in `models/enums.dart` apply
  /// to unknown backend strings.
  static BrandFlavor fromFlag(String? raw) {
    for (final BrandFlavor flavor in values) {
      if (flavor.flag == raw) return flavor;
    }
    return fallback;
  }
}

/// The raw `--dart-define=BRAND=...` value, or the default flag when unset.
const String kBrandFlag = String.fromEnvironment(
  'BRAND',
  defaultValue: 'tennisimo',
);

/// Whether this build uses the alternate colour scheme.
///
/// Exact equality against the one accepted literal is the only way in, so an
/// unrecognised value falls through to the default palette at compile time —
/// the const mirror of [BrandFlavor.fromFlag]. It is a `const bool` rather
/// than an enum comparison so every colour token below can stay `const`.
const bool kUseAlternatePalette = kBrandFlag == 'school';

/// The flavor this binary was compiled with.
const BrandFlavor kBrandFlavor =
    kUseAlternatePalette ? BrandFlavor.school : BrandFlavor.tennisimo;
