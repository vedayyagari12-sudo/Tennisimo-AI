/// Every build ships both canvases: a device in dark mode gets the dark one
/// unless the player picked light in Profile › Appearance.
library;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/main.dart';
import 'package:tennisimo_ai/theme/app_theme.dart';
import 'package:tennisimo_ai/theme/brand.dart';

final AppPalette _light = paletteFor(kBrandFlavor);
final AppPalette _dark = darkPaletteFor(kBrandFlavor);

Widget _app() => const TennisimoApp(home: Scaffold(body: SizedBox.shrink()));

void main() {
  testWidgets('both themes are attached', (WidgetTester tester) async {
    await tester.pumpWidget(_app());
    final MaterialApp app = tester.widget(find.byType(MaterialApp));
    expect(app.theme!.brightness, Brightness.light);
    expect(app.darkTheme!.brightness, Brightness.dark);
  });

  for (final Brightness platform in Brightness.values) {
    testWidgets('a ${platform.name} device gets the ${platform.name} canvas',
        (WidgetTester tester) async {
      addTearDown(tester.platformDispatcher.clearPlatformBrightnessTestValue);
      tester.platformDispatcher.platformBrightnessTestValue = platform;

      await tester.pumpWidget(_app());
      await tester.pumpAndSettle();

      final BuildContext context = tester.element(find.byType(SizedBox).first);
      final bool dark = platform == Brightness.dark;
      expect(context.palette, same(dark ? _dark : _light));
      final Material canvas = tester.widget(
        find.descendant(
          of: find.byType(Scaffold),
          matching: find.byType(Material),
        ),
      );
      expect(canvas.color, (dark ? _dark : _light).surface);

      final AnnotatedRegion<SystemUiOverlayStyle> region = tester.widget(
        find.byType(AnnotatedRegion<SystemUiOverlayStyle>).first,
      );
      // Icons always contrast with the canvas behind them.
      expect(
        region.value.statusBarIconBrightness,
        dark ? Brightness.light : Brightness.dark,
      );
    });
  }

  test('the dark overlay style is the light one inverted', () {
    final SystemUiOverlayStyle d = darkCanvasOverlayStyle();
    expect(d.statusBarIconBrightness, Brightness.light);
    expect(d.statusBarBrightness, Brightness.dark);
    expect(d.systemNavigationBarColor, _dark.surface);
    expect(buildDarkAppTheme().appBarTheme.systemOverlayStyle, d);
  });
}
