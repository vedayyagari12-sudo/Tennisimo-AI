import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/theme/app_theme.dart';
import 'package:tennisimo_ai/theme/brand.dart';

/// WCAG 2.1 relative luminance of a single sRGB channel already in 0..1.
double _channel(double c) =>
    c <= 0.03928 ? c / 12.92 : math.pow((c + 0.055) / 1.055, 2.4).toDouble();

/// WCAG 2.1 relative luminance.
double _luminance(Color c) =>
    0.2126 * _channel(c.r) + 0.7152 * _channel(c.g) + 0.0722 * _channel(c.b);

/// WCAG 2.1 contrast ratio, 1.0 (identical) .. 21.0 (black on white).
double contrastRatio(Color a, Color b) {
  final double la = _luminance(a);
  final double lb = _luminance(b);
  final double hi = math.max(la, lb);
  final double lo = math.min(la, lb);
  return (hi + 0.05) / (lo + 0.05);
}

/// A luma-weighted RGB distance, 0.0 (identical) .. ~1.0.
///
/// Not a colour-science metric; it only has to be good enough to fail loudly if
/// two roles that must stay tellable apart ever drift towards one hue.
double _perceptualDistance(Color a, Color b) {
  final double dr = (a.r - b.r) * 0.30;
  final double dg = (a.g - b.g) * 0.59;
  final double db = (a.b - b.b) * 0.11;
  return math.sqrt(dr * dr + dg * dg + db * db);
}

void main() {
  group('BrandFlavor.fromFlag', () {
    test('recognises every flavor by its own flag', () {
      for (final BrandFlavor flavor in BrandFlavor.values) {
        expect(BrandFlavor.fromFlag(flavor.flag), flavor);
      }
    });

    test('an unrecognised flag degrades to the default flavor', () {
      // A typo, a stale flag, a case mismatch, an empty define and no define
      // at all must all land on the shipping palette rather than throwing or
      // leaving the app half-themed.
      for (final String? raw in <String?>[
        null,
        '',
        ' ',
        'School',
        'SCHOOL',
        'schoo',
        'school ',
        'alternate',
        'tennisimo-dark',
        '0',
      ]) {
        expect(
          BrandFlavor.fromFlag(raw),
          BrandFlavor.tennisimo,
          reason: 'flag $raw should have fallen back',
        );
      }
      expect(BrandFlavor.fallback, BrandFlavor.tennisimo);
    });
  });

  group('compile-time selection', () {
    test('the const selector agrees with the runtime parser', () {
      // Holds in every flavor: run this suite with
      // `--dart-define=BRAND=school` and it still passes.
      expect(kBrandFlavor, BrandFlavor.fromFlag(kBrandFlag));
      expect(kUseAlternatePalette, kBrandFlavor == BrandFlavor.school);
    });

    test('the compiled palette matches the compiled flavor', () {
      // Proves the define actually reaches the tokens: run the suite with
      // `--dart-define=BRAND=school` and these are the alternate hexes; run it
      // with a bogus value and they are the default ones again.
      switch (kBrandFlavor) {
        case BrandFlavor.tennisimo:
          expect(AppColors.primary, const Color(0xFF3ECF67));
          expect(AppColors.secondary, const Color(0xFFCCFF4D));
          expect(AppColors.surface, const Color(0xFF0E1512));
        case BrandFlavor.school:
          expect(AppColors.primary, const Color(0xFFA8D8F0));
          expect(AppColors.secondary, const Color(0xFFFFEB3B));
          expect(AppColors.surface, const Color(0xFF0E1318));
      }
    });

    test('an unset BRAND compiles to the default flavor', () {
      // The baseline `flutter test` run passes no define.
      const String unsetDefault = String.fromEnvironment(
        'A_DEFINE_NOBODY_PASSES',
        defaultValue: 'tennisimo',
      );
      expect(BrandFlavor.fromFlag(unsetDefault), BrandFlavor.tennisimo);
    });
  });

  group('the score ramp survives the flavor', () {
    test('the three bands stay clearly distinguishable', () {
      final List<Color> bands = <Color>[
        scoreColor(90),
        scoreColor(70),
        scoreColor(40),
      ];

      for (int i = 0; i < bands.length; i++) {
        for (int j = i + 1; j < bands.length; j++) {
          expect(bands[i], isNot(bands[j]));
          expect(
            _perceptualDistance(bands[i], bands[j]),
            greaterThan(0.10),
            reason: 'ramp bands $i and $j have collapsed towards one hue',
          );
        }
      }
    });

    test('the ramp is never recoloured into the brand accents', () {
      expect(scoreColor(90), AppColors.scoreHigh);
      expect(scoreColor(70), AppColors.scoreMid);
      expect(scoreColor(40), AppColors.scoreLow);
    });

    test('a null score is the muted token in either flavor', () {
      expect(scoreColor(null), AppColors.onSurfaceVariant);
      for (final Color band in <Color>[
        AppColors.scoreHigh,
        AppColors.scoreMid,
        AppColors.scoreLow,
      ]) {
        expect(scoreColor(null), isNot(band));
      }
    });
  });

  group('contrast of the compiled palette', () {
    // 4.5:1 is WCAG AA for body text; 3:1 is AA for large text and UI shapes.
    const double aaBody = 4.5;

    test('text and accents clear AA on both surfaces', () {
      final Map<String, Color> foregrounds = <String, Color>{
        'onSurface': AppColors.onSurface,
        'onSurfaceVariant': AppColors.onSurfaceVariant,
        'primary': AppColors.primary,
        'secondary': AppColors.secondary,
        'error': AppColors.error,
        'scoreHigh': AppColors.scoreHigh,
        'scoreMid': AppColors.scoreMid,
        'scoreLow': AppColors.scoreLow,
      };
      final Map<String, Color> backgrounds = <String, Color>{
        'surface': AppColors.surface,
        'surfaceContainer': AppColors.surfaceContainer,
        'surfaceContainerHigh': AppColors.surfaceContainerHigh,
      };

      foregrounds.forEach((String fg, Color fgColor) {
        backgrounds.forEach((String bg, Color bgColor) {
          expect(
            contrastRatio(fgColor, bgColor),
            greaterThanOrEqualTo(aaBody),
            reason: '$fg on $bg fails AA body contrast',
          );
        });
      });
    });

    test('label colours clear AA on the accent they sit on', () {
      expect(
        contrastRatio(AppColors.onPrimary, AppColors.primary),
        greaterThanOrEqualTo(aaBody),
      );
      expect(
        contrastRatio(AppColors.onSecondary, AppColors.secondary),
        greaterThanOrEqualTo(aaBody),
      );
    });

    test('the reference ratios are computed, not asserted', () {
      // Sanity-check the helper itself against the two known extremes.
      expect(contrastRatio(Colors.black, Colors.white), closeTo(21.0, 0.01));
      expect(contrastRatio(Colors.white, Colors.white), closeTo(1.0, 0.01));
    });
  });

  group('the brand mark follows the flavor', () {
    test('the ball and the bolt are never the same colour', () {
      expect(AppColors.ballAccent, isNot(AppColors.primary));
      expect(
        _perceptualDistance(AppColors.ballAccent, AppColors.primary),
        greaterThan(0.05),
      );
    });

    test('the ball tracks the ball-speed accent', () {
      expect(AppColors.ballAccent, AppColors.secondary);
    });
  });
}
