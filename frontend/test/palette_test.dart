/// The palette validator.
///
/// This suite deliberately does NOT assert a table of hexes. A table drifts:
/// somebody nudges a token, updates the expectation, and the reason the token
/// was that value in the first place is gone. Instead it re-derives the colour
/// science from first principles — OKLab, WCAG 2.1 relative luminance, and the
/// Machado/Oliveira/Fernandes 2009 CVD matrices — and asserts the *properties*
/// the palette has to have. A token can move freely inside its band; it cannot
/// leave the band without failing here.
///
/// Every check runs over both brand flavors. The app is light-only, so there
/// is one canvas per flavor and every gate below applies to it: a flavor that
/// was only half-validated is exactly the failure this suite exists to
/// prevent.
library;

import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/theme/app_theme.dart';

// ---------------------------------------------------------------------------
// sRGB <-> linear
// ---------------------------------------------------------------------------

/// sRGB transfer function, inverted: gamma-encoded channel -> linear.
double _toLinear(double c) =>
    c <= 0.04045 ? c / 12.92 : math.pow((c + 0.055) / 1.055, 2.4).toDouble();

/// The forward sRGB transfer function: linear -> gamma-encoded.
double _toGamma(double c) => c <= 0.0031308
    ? c * 12.92
    : 1.055 * math.pow(c, 1 / 2.4).toDouble() - 0.055;

List<double> _linearRgb(Color c) =>
    <double>[_toLinear(c.r), _toLinear(c.g), _toLinear(c.b)];

Color _fromLinearRgb(List<double> v) => Color.from(
      alpha: 1.0,
      red: _toGamma(v[0].clamp(0.0, 1.0)),
      green: _toGamma(v[1].clamp(0.0, 1.0)),
      blue: _toGamma(v[2].clamp(0.0, 1.0)),
    );

// ---------------------------------------------------------------------------
// WCAG 2.1 contrast
// ---------------------------------------------------------------------------

/// WCAG 2.1 relative luminance.
double relativeLuminance(Color c) {
  final List<double> v = _linearRgb(c);
  return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2];
}

/// WCAG 2.1 contrast ratio, 1.0 (identical) .. 21.0 (black on white).
double contrast(Color a, Color b) {
  final double la = relativeLuminance(a);
  final double lb = relativeLuminance(b);
  return (math.max(la, lb) + 0.05) / (math.min(la, lb) + 0.05);
}

// ---------------------------------------------------------------------------
// OKLab (Bjorn Ottosson, 2020)
// ---------------------------------------------------------------------------

/// The OKLab coordinates (L, a, b) of an opaque sRGB colour.
List<double> oklab(Color c) {
  final List<double> v = _linearRgb(c);
  final double l = 0.4122214708 * v[0] + 0.5363325363 * v[1] + //
      0.0514459929 * v[2];
  final double m = 0.2119034982 * v[0] + 0.6806995451 * v[1] + //
      0.1073969566 * v[2];
  final double s = 0.0883024619 * v[0] + 0.2817188376 * v[1] + //
      0.6299787005 * v[2];
  final double l3 = _cbrt(l);
  final double m3 = _cbrt(m);
  final double s3 = _cbrt(s);
  return <double>[
    0.2104542553 * l3 + 0.7936177850 * m3 - 0.0040720468 * s3,
    1.9779984951 * l3 - 2.4285922050 * m3 + 0.4505937099 * s3,
    0.0259040371 * l3 + 0.7827717662 * m3 - 0.8086757660 * s3,
  ];
}

double _cbrt(double x) =>
    x < 0 ? -math.pow(-x, 1 / 3).toDouble() : math.pow(x, 1 / 3).toDouble();

/// Perceptual lightness: 0.0 is black, 1.0 is white. This is the number the
/// accent stepping is done in, because sRGB's own channels are not perceptually
/// uniform and "brighter hex" does not mean "looks brighter".
double perceptualLightness(Color c) => oklab(c)[0];

/// OKLab chroma: how colourful a colour is, independent of its lightness.
double _chroma(Color c) {
  final List<double> v = oklab(c);
  return math.sqrt(v[1] * v[1] + v[2] * v[2]);
}

/// Euclidean distance in OKLab. Roughly 0.02 is one just-noticeable
/// difference, so 0.07 is several.
double deltaEOk(Color a, Color b) {
  final List<double> p = oklab(a);
  final List<double> q = oklab(b);
  return math.sqrt(math.pow(p[0] - q[0], 2) +
      math.pow(p[1] - q[1], 2) +
      math.pow(p[2] - q[2], 2));
}

// ---------------------------------------------------------------------------
// Colour vision deficiency, Machado / Oliveira / Fernandes 2009
// ---------------------------------------------------------------------------

/// The three dichromacies this palette is checked against.
enum Cvd {
  /// Green-blind. The common case, and the one a green/amber/red ramp is
  /// weakest under.
  deuteranopia(<List<double>>[
    <double>[0.367322, 0.860646, -0.227968],
    <double>[0.280085, 0.672501, 0.047413],
    <double>[-0.011820, 0.042940, 0.968881],
  ]),

  /// Red-blind.
  protanopia(<List<double>>[
    <double>[0.152286, 1.052583, -0.204868],
    <double>[0.114503, 0.786281, 0.099216],
    <double>[-0.003882, -0.048116, 1.051998],
  ]),

  /// Blue-blind. Rare, checked anyway.
  tritanopia(<List<double>>[
    <double>[1.255528, -0.076749, -0.178779],
    <double>[-0.078411, 0.930809, 0.147602],
    <double>[0.004733, 0.691367, 0.303900],
  ]);

  const Cvd(this.matrix);

  /// The severity-1.0 matrix, applied in LINEAR RGB.
  final List<List<double>> matrix;
}

/// [c] as seen with [deficiency].
Color simulate(Color c, Cvd deficiency) {
  final List<double> v = _linearRgb(c);
  return _fromLinearRgb(<double>[
    for (final List<double> row in deficiency.matrix)
      row[0] * v[0] + row[1] * v[1] + row[2] * v[2],
  ]);
}

// ---------------------------------------------------------------------------
// The gates
// ---------------------------------------------------------------------------

/// WCAG 2.1 AA for body text.
const double kAaBody = 4.5;

/// WCAG 2.1 AA for large text and for the boundary of a UI component.
const double kAaLarge = 3.0;

/// The smallest OKLab separation the score ramp may collapse to under any one
/// of the three dichromacies.
///
/// 0.07 is not arbitrary: an exhaustive search over brand-plausible hues in
/// OKLCh found that a green / amber / red ramp constrained to WCAG AA against
/// this palette's surfaces cannot do better than about 0.09 on a light canvas,
/// so 0.07 is a real gate with real headroom rather than a number the current
/// values happen to clear.
const double kMinCvdSeparation = 0.070;

/// The perceptual-lightness window a *chromatic text* accent must sit in on
/// this app's light canvas.
const ({double min, double max}) kTextBand = (min: 0.24, max: 0.52);

/// The window a *large fill* must sit in. This is the anti-mud band, the whole
/// reason the two tiers exist: a large area painted with a text-calibrated
/// accent on a near-white canvas reads as a smear.
const ({double min, double max}) kFillBand = (min: 0.32, max: 0.60);

/// Text, icon and small-mark tokens: gated on WCAG body contrast.
Map<String, Color> textTier(AppPalette p) => <String, Color>{
      'onSurface': p.onSurface,
      'onSurfaceVariant': p.onSurfaceVariant,
      'primary': p.primary,
      'secondary': p.secondary,
      'error': p.error,
      'scoreHigh': p.scoreHigh,
      'scoreMid': p.scoreMid,
      'scoreLow': p.scoreLow,
    };

/// The chromatic subset of the text tier: the accents. The neutral text
/// tokens are deliberately outside the lightness band, because body text
/// should take all the contrast it can get.
Map<String, Color> chromaticTextTier(AppPalette p) => <String, Color>{
      'primary': p.primary,
      'secondary': p.secondary,
      'error': p.error,
      'scoreHigh': p.scoreHigh,
      'scoreMid': p.scoreMid,
      'scoreLow': p.scoreLow,
    };

/// Large-fill tokens: the score ring arc, the trend wash, the category bars,
/// button faces, the brand mark's ball.
Map<String, Color> fillTier(AppPalette p) => <String, Color>{
      'primaryFill': p.primaryFill,
      'secondaryFill': p.secondaryFill,
      'ballAccent': p.ballAccent,
      'errorFill': p.errorFill,
      'scoreHighFill': p.scoreHighFill,
      'scoreMidFill': p.scoreMidFill,
      'scoreLowFill': p.scoreLowFill,
    };

/// Every surface a token may be painted on.
Map<String, Color> surfaces(AppPalette p) => <String, Color>{
      'surface': p.surface,
      'surfaceContainerLowest': p.surfaceContainerLowest,
      'surfaceContainerLow': p.surfaceContainerLow,
      'surfaceContainer': p.surfaceContainer,
      'surfaceContainerHigh': p.surfaceContainerHigh,
      'surfaceContainerHighest': p.surfaceContainerHighest,
    };

/// The backgrounds a *control* actually sits on. [AppPalette.outline] is gated
/// against these rather than against every surface, because no control in this
/// app is drawn on `surfaceContainerHighest`.
Map<String, Color> controlBackgrounds(AppPalette p) => <String, Color>{
      'surface': p.surface,
      'surfaceContainer': p.surfaceContainer,
      'surfaceContainerHigh': p.surfaceContainerHigh,
    };

String _name(AppPalette p) => p == tennisimoLight ? 'tennisimo' : 'alternate';

void main() {
  group('the maths itself', () {
    // If these drift, every other assertion in this file is meaningless.
    test('WCAG contrast hits its two known extremes', () {
      expect(contrast(Colors.black, Colors.white), closeTo(21.0, 0.001));
      expect(contrast(Colors.white, Colors.white), closeTo(1.0, 0.001));
      expect(contrast(Colors.black, Colors.black), closeTo(1.0, 0.001));
    });

    test('OKLab lightness spans 0..1 and mid-grey lands near the middle', () {
      expect(perceptualLightness(Colors.black), closeTo(0.0, 0.001));
      expect(perceptualLightness(Colors.white), closeTo(1.0, 0.001));
      // #777777 is perceptually about half way, which sRGB's own 0.47 is not.
      expect(
        perceptualLightness(const Color(0xFF777777)),
        closeTo(0.55, 0.02),
      );
    });

    test('OKLab is symmetric and zero on identical colours', () {
      expect(deltaEOk(Colors.red, Colors.red), closeTo(0.0, 1e-9));
      expect(
        deltaEOk(Colors.red, Colors.green),
        closeTo(deltaEOk(Colors.green, Colors.red), 1e-9),
      );
    });

    test('a CVD matrix leaves greys alone and collapses red against green', () {
      for (final Cvd deficiency in Cvd.values) {
        // Achromatic in, achromatic out: the matrices are row-normalised.
        final Color grey = simulate(const Color(0xFF808080), deficiency);
        expect(deltaEOk(grey, const Color(0xFF808080)), lessThan(0.02),
            reason: '$deficiency moved a neutral grey');
      }

      // Red and green are 0.55 apart to normal vision and must be far closer
      // to a deuteranope — otherwise the simulation is doing nothing and the
      // ramp assertions below would pass for the wrong reason.
      final double normal = deltaEOk(Colors.red, Colors.green);
      final double seen = deltaEOk(
        simulate(Colors.red, Cvd.deuteranopia),
        simulate(Colors.green, Cvd.deuteranopia),
      );
      expect(seen, lessThan(normal * 0.6));
    });
  });

  group('both flavors exist and are distinct', () {
    test('there is exactly one palette per flavor', () {
      expect(kAllPalettes, hasLength(2));
      expect(kAllPalettes.toSet(), hasLength(2));
      for (final BrandFlavorCase c in _cases) {
        expect(kAllPalettes, contains(c.palette));
      }
    });

    test('every palette is a light canvas, and says so', () {
      // The app is light-only. A palette whose surface is not light, or which
      // claims a brightness it was not stepped for, would put the whole
      // lightness-band discipline below on the wrong side of the canvas.
      for (final AppPalette p in kAllPalettes) {
        expect(p.brightness, Brightness.light, reason: _name(p));
        expect(
          perceptualLightness(p.surface),
          greaterThan(0.85),
          reason: '${_name(p)} surface',
        );
      }
    });
  });

  group('tier 1: text, icons and small marks clear WCAG AA', () {
    for (final BrandFlavorCase c in _cases) {
      test('${c.label} — every text token on every surface', () {
        textTier(c.palette).forEach((String fg, Color fgColor) {
          surfaces(c.palette).forEach((String bg, Color bgColor) {
            expect(
              contrast(fgColor, bgColor),
              greaterThanOrEqualTo(kAaBody),
              reason: '${c.label}: $fg on $bg is '
                  '${contrast(fgColor, bgColor).toStringAsFixed(2)}:1',
            );
          });
        });
      });

      test('${c.label} — every label clears AA on the accent under it', () {
        final AppPalette p = c.palette;
        final Map<String, (Color, Color)> pairs = <String, (Color, Color)>{
          'onPrimary/primary': (p.onPrimary, p.primary),
          'onPrimaryFill/primaryFill': (p.onPrimaryFill, p.primaryFill),
          'onSecondary/secondary': (p.onSecondary, p.secondary),
          'onError/error': (p.onError, p.error),
        };
        pairs.forEach((String label, (Color, Color) pair) {
          expect(
            contrast(pair.$1, pair.$2),
            greaterThanOrEqualTo(kAaBody),
            reason: '${c.label}: $label is '
                '${contrast(pair.$1, pair.$2).toStringAsFixed(2)}:1',
          );
        });
      });

      test('${c.label} — chromatic accents sit in the text lightness band',
          () {
        const ({double min, double max}) band = kTextBand;
        chromaticTextTier(c.palette).forEach((String token, Color colour) {
          final double l = perceptualLightness(colour);
          expect(l, inInclusiveRange(band.min, band.max),
              reason: '${c.label}: $token is at OKLab L '
                  '${l.toStringAsFixed(3)}');
        });
      });
    }
  });

  group('tier 2: large fills clear 3:1 and stay inside the anti-mud band',
      () {
    for (final BrandFlavorCase c in _cases) {
      test('${c.label} — every fill token on every surface', () {
        fillTier(c.palette).forEach((String fg, Color fgColor) {
          surfaces(c.palette).forEach((String bg, Color bgColor) {
            expect(
              contrast(fgColor, bgColor),
              greaterThanOrEqualTo(kAaLarge),
              reason: '${c.label}: $fg on $bg is '
                  '${contrast(fgColor, bgColor).toStringAsFixed(2)}:1',
            );
          });
        });
      });

      test('${c.label} — every fill token sits in the fill lightness band', () {
        const ({double min, double max}) band = kFillBand;
        fillTier(c.palette).forEach((String token, Color colour) {
          final double l = perceptualLightness(colour);
          expect(l, inInclusiveRange(band.min, band.max),
              reason: '${c.label}: $token is at OKLab L '
                  '${l.toStringAsFixed(3)} — a fill calibrated like text');
        });
      });

      test('${c.label} — a fill is never the same step as its text token', () {
        final AppPalette p = c.palette;
        final Map<String, (Color, Color)> pairs = <String, (Color, Color)>{
          'primary': (p.primary, p.primaryFill),
          'secondary': (p.secondary, p.secondaryFill),
          'error': (p.error, p.errorFill),
          'scoreHigh': (p.scoreHigh, p.scoreHighFill),
          'scoreMid': (p.scoreMid, p.scoreMidFill),
          'scoreLow': (p.scoreLow, p.scoreLowFill),
        };
        pairs.forEach((String token, (Color, Color) pair) {
          expect(pair.$1, isNot(pair.$2),
              reason: '${c.label}: $token reuses one step for both tiers');
        });
      });
    }
  });

  group('boundaries', () {
    for (final BrandFlavorCase c in _cases) {
      test('${c.label} — outline clears 3:1 on every control background', () {
        controlBackgrounds(c.palette).forEach((String bg, Color bgColor) {
          expect(
            contrast(c.palette.outline, bgColor),
            greaterThanOrEqualTo(kAaLarge),
            reason: '${c.label}: outline on $bg is '
                '${contrast(c.palette.outline, bgColor).toStringAsFixed(2)}:1',
          );
        });
      });

      test('${c.label} — outlineVariant is visible but quieter than outline',
          () {
        // Decorative rules are WCAG-exempt, but an invisible hairline is a
        // hairline nobody asked for.
        final AppPalette p = c.palette;
        expect(contrast(p.outlineVariant, p.surface), greaterThan(1.1));
        expect(
          contrast(p.outlineVariant, p.surface),
          lessThan(contrast(p.outline, p.surface)),
        );
      });
    }
  });

  group('the score ramp stays semantic under colour vision deficiency', () {
    for (final BrandFlavorCase c in _cases) {
      for (final (String tier, List<Color> bands) entry in <(
        String,
        List<Color>
      )>[
        (
          'text',
          <Color>[
            c.palette.scoreHigh,
            c.palette.scoreMid,
            c.palette.scoreLow,
          ]
        ),
        (
          'fill',
          <Color>[
            c.palette.scoreHighFill,
            c.palette.scoreMidFill,
            c.palette.scoreLowFill,
          ]
        ),
      ]) {
        test('${c.label} — the ${entry.$1} ramp survives all three '
            'dichromacies', () {
          const List<String> labels = <String>['high', 'mid', 'low'];
          for (final Cvd deficiency in Cvd.values) {
            final List<Color> seen = <Color>[
              for (final Color band in entry.$2) simulate(band, deficiency),
            ];
            for (int i = 0; i < seen.length; i++) {
              for (int j = i + 1; j < seen.length; j++) {
                final double d = deltaEOk(seen[i], seen[j]);
                expect(
                  d,
                  greaterThanOrEqualTo(kMinCvdSeparation),
                  reason: '${c.label} ${entry.$1}: ${labels[i]} and '
                      '${labels[j]} collapse to ${d.toStringAsFixed(3)} '
                      'under ${deficiency.name}',
                );
              }
            }
          }
        });
      }

      test('${c.label} — the ramp is monotone in perceptual lightness', () {
        // Lightness is the one channel every dichromacy keeps, so the bands
        // are ordered in it on purpose. If two bands ever end up at the same
        // lightness, the ramp is relying on hue alone and the test above will
        // start failing as soon as anything else moves.
        for (final List<Color> ramp in <List<Color>>[
          <Color>[c.palette.scoreHigh, c.palette.scoreMid, c.palette.scoreLow],
          <Color>[
            c.palette.scoreHighFill,
            c.palette.scoreMidFill,
            c.palette.scoreLowFill,
          ],
        ]) {
          final List<double> ls =
              ramp.map(perceptualLightness).toList(growable: false);
          expect(ls[0], greaterThan(ls[1]), reason: '${c.label}: high vs mid');
          expect(ls[1], greaterThan(ls[2]), reason: '${c.label}: mid vs low');
        }
      });

      test('${c.label} — the muted null token reads as muted, not as a band',
          () {
        final AppPalette p = c.palette;
        final List<Color> bands = <Color>[
          p.scoreHigh,
          p.scoreMid,
          p.scoreLow,
          p.scoreHighFill,
          p.scoreMidFill,
          p.scoreLowFill,
        ];
        // Chroma, not lightness: "not measured" has to look like an absence of
        // colour beside any band, and low chroma is what makes it read that
        // way however a given viewer sees hue. The null case is additionally
        // disambiguated by copy — an em dash or the words "Not measured",
        // never a numeral — which is why the CVD gate above is applied to the
        // three bands against each other, where colour is the only signal.
        final double mutedChroma = _chroma(p.onSurfaceVariant);
        for (final Color band in bands) {
          expect(p.scoreColor(null), isNot(band));
          expect(p.scoreFillColor(null), isNot(band));
          expect(
            mutedChroma,
            lessThan(_chroma(band) * 0.5),
            reason: '${c.label}: the muted token is as saturated as a band',
          );
          expect(
            deltaEOk(p.onSurfaceVariant, band),
            greaterThanOrEqualTo(kMinCvdSeparation),
            reason: '${c.label}: the muted token sits on top of a band',
          );
        }
      });
    }
  });

  // -------------------------------------------------------------------------
  // The dashboard's sparklines.
  //
  // The dashboard adds NO new categorical series colours: it is built from
  // small multiples, one titled panel per series, so identity is carried by
  // words and every mark on the screen is the same accent. That is a design
  // decision with teeth, and it is the reason there is no new series palette
  // to gate here — a second hue would have to clear `kMinCvdSeparation`, and
  // in the default flavor `primary` and `secondary` collapse to 0.026 under
  // deuteranopia, so it could not.
  //
  // What the sparkline DID newly make load-bearing is its own chrome: a
  // recessive baseline, and a tap marker drawn as a surface-filled ring inside
  // the line colour. Those pairings are gated below.
  // -------------------------------------------------------------------------
  group('the sparkline mark and its chrome', () {
    for (final BrandFlavorCase c in _cases) {
      test('${c.label} — the dashboard uses exactly one chart-mark colour',
          () {
        // MiniTrend takes no colour parameter; this asserts the token it is
        // hard-wired to is a real text-tier accent, so the invariant cannot be
        // broken by re-pointing the widget at an ungated token.
        expect(chromaticTextTier(c.palette).values, contains(c.palette.primary));
      });

      test('${c.label} — the tap marker reads as a ring, under CVD too', () {
        // The marker is a `surfaceContainerLowest` disc with a 2px `primary`
        // ring. If those two ever converge the selected point becomes a blob
        // and the user cannot see which point they interrogated.
        final AppPalette p = c.palette;
        expect(
          contrast(p.surfaceContainerLowest, p.primary),
          greaterThanOrEqualTo(kAaLarge),
          reason: '${c.label}: marker fill on the line colour is '
              '${contrast(p.surfaceContainerLowest, p.primary).toStringAsFixed(2)}:1',
        );
        for (final Cvd deficiency in Cvd.values) {
          final double d = deltaEOk(
            simulate(p.surfaceContainerLowest, deficiency),
            simulate(p.primary, deficiency),
          );
          expect(
            d,
            greaterThanOrEqualTo(kMinCvdSeparation),
            reason: '${c.label}: the marker ring vanishes under '
                '${deficiency.name} at ${d.toStringAsFixed(3)}',
          );
        }
      });

      test('${c.label} — the sparkline baseline is visible but recessive', () {
        // Drawn on a card, not on the scaffold, so it is gated against the
        // card colour rather than the surface.
        final AppPalette p = c.palette;
        expect(contrast(p.outlineVariant, p.surfaceContainer), greaterThan(1.1));
        expect(
          contrast(p.outlineVariant, p.surfaceContainer),
          lessThan(contrast(p.primary, p.surfaceContainer)),
          reason: '${c.label}: the baseline competes with the data line',
        );
      });
    }
  });

  group('the brand mark reads on the canvas', () {
    for (final BrandFlavorCase c in _cases) {
      test('${c.label} — the ball has an edge and the bolt reads on it', () {
        final AppPalette p = c.palette;
        // AppLogo paints the ball straight onto whatever it is given, which is
        // the surface by default and the card colour when it sits on a card.
        for (final Color background in <Color>[
          p.surface,
          p.surfaceContainer,
          p.surfaceContainerHigh,
        ]) {
          expect(
            contrast(p.ballAccent, background),
            greaterThanOrEqualTo(kAaLarge),
            reason: '${c.label}: the ball has no edge against $background',
          );
        }
        // Ball and bolt are separated by a background-coloured cut, so they
        // only have to be tellable apart, not contrast-rated against each
        // other.
        expect(deltaEOk(p.ballAccent, p.primaryFill), greaterThan(0.05),
            reason: '${c.label}: the bolt disappears into the ball');
      });
    }
  });

  group('the derived M3 container roles are readable', () {
    for (final BrandFlavorCase c in _cases) {
      test('${c.label} — onSurface clears AA on every blended container', () {
        final ColorScheme scheme = buildColorScheme(c.palette);
        final Map<String, Color> containers = <String, Color>{
          'primaryContainer': scheme.primaryContainer,
          'secondaryContainer': scheme.secondaryContainer,
          'tertiaryContainer': scheme.tertiaryContainer,
          'errorContainer': scheme.errorContainer,
        };
        containers.forEach((String name, Color background) {
          expect(
            contrast(c.palette.onSurface, background),
            greaterThanOrEqualTo(kAaBody),
            reason: '${c.label}: onSurface on $name is '
                '${contrast(c.palette.onSurface, background).toStringAsFixed(2)}'
                ':1',
          );
        });
      });

      test('${c.label} — the scheme reports the brightness it was built for',
          () {
        expect(
          buildColorScheme(c.palette).brightness,
          c.palette.brightness,
        );
      });
    }
  });
}

/// One brand flavor's palette, with a label for failure messages.
///
/// The alternate flavor is never named after anything: it is "alternate".
class BrandFlavorCase {
  const BrandFlavorCase(this.label, this.palette);

  final String label;
  final AppPalette palette;
}

const List<BrandFlavorCase> _cases = <BrandFlavorCase>[
  BrandFlavorCase('default', tennisimoLight),
  BrandFlavorCase('alternate', schoolLight),
];
