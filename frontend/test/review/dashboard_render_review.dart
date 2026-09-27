/// Renders the assembled dashboard to PNG for a human to look at.
///
/// NOT part of the suite: the file name deliberately lacks `_test`, so a plain
/// `flutter test` never runs it. Run it explicitly, once per flavor:
///
/// ```
/// flutter test test/review/dashboard_render_review.dart
/// flutter test test/review/dashboard_render_review.dart --dart-define=BRAND=school
/// ```
///
/// Images land in `build/dashboard_review/<flavor>/`, which is gitignored.
/// There are no golden comparisons here on purpose: glyph rasterisation
/// differs between Windows and Linux, so a golden generated on one fails on
/// the other for reasons unrelated to layout. Regression protection is the
/// overflow matrix in `dashboard_layout_matrix_test.dart` and
/// `dashboard_screen_test.dart`; this file is a review tool.
///
/// ## Fonts
///
/// The test renderer draws every glyph as a solid box (the Ahem font) unless
/// real fonts are loaded. Roboto and the Material icon font ship with the
/// Flutter SDK, and are loaded from there before anything is pumped.
library;

import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/screens/dashboard_screen.dart';
import 'package:tennisimo_ai/screens/results_screen.dart';
import 'package:tennisimo_ai/theme/app_theme.dart';
import 'package:tennisimo_ai/theme/brand.dart';

import '../support/dashboard_fixtures.dart';

const Map<String, Size> _viewports = <String, Size>{
  '412x915': Size(412, 915),
  '360x800': Size(360, 800),
};

Future<void> _loadFonts() async {
  final String sdk = _flutterRoot();
  final String dir = '$sdk/bin/cache/artifacts/material_fonts';
  Future<ByteData> read(String file) async => ByteData.sublistView(
    Uint8List.fromList(await File('$dir/$file').readAsBytes()),
  );

  final FontLoader roboto = FontLoader('Roboto');
  for (final String weight in <String>[
    'light',
    'regular',
    'medium',
    'bold',
    'black',
  ]) {
    roboto.addFont(read('roboto-$weight.ttf'));
  }
  await roboto.load();

  final FontLoader icons = FontLoader('MaterialIcons')
    ..addFont(read('materialicons-regular.otf'));
  await icons.load();
}

/// The SDK root, from the running `flutter_tester` binary's location.
String _flutterRoot() {
  final String? env = Platform.environment['FLUTTER_ROOT'];
  if (env != null && env.isNotEmpty) return env.replaceAll(r'\', '/');
  // .../flutter/bin/cache/artifacts/engine/<platform>/flutter_tester(.exe)
  Directory dir = File(Platform.resolvedExecutable).parent;
  while (!Directory(
    '${dir.path}/bin/cache/artifacts/material_fonts',
  ).existsSync()) {
    final Directory parent = dir.parent;
    if (parent.path == dir.path) {
      throw StateError('Could not find the Flutter SDK fonts.');
    }
    dir = parent;
  }
  return dir.path.replaceAll(r'\', '/');
}

/// The shell's bottom bar, so the first viewport is judged at its real height.
Widget _navBar(ColorScheme scheme) => NavigationBar(
  selectedIndex: 0,
  destinations: <Widget>[
    const NavigationDestination(
      icon: Icon(Icons.insights_outlined),
      selectedIcon: Icon(Icons.insights),
      label: 'Dashboard',
    ),
    NavigationDestination(
      icon: Container(
        width: 40,
        height: 40,
        decoration: BoxDecoration(
          color: scheme.primary,
          borderRadius: BorderRadius.circular(14),
        ),
        child: Icon(Icons.videocam, color: scheme.onPrimary, size: 22),
      ),
      label: 'Record',
    ),
    const NavigationDestination(
      icon: Icon(Icons.timeline_outlined),
      label: 'History',
    ),
  ],
);

final GlobalKey _boundary = GlobalKey();

Future<void> _pump(WidgetTester tester, Widget home, Size size) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1.0;
  final ThemeData theme = buildAppTheme();
  await tester.pumpWidget(
    RepaintBoundary(
      key: _boundary,
      child: MaterialApp(
        debugShowCheckedModeBanner: false,
        theme: theme,
        home: home,
      ),
    ),
  );
  // Let the fake futures resolve and every entrance animation finish.
  for (int i = 0; i < 20; i++) {
    await tester.pump(const Duration(milliseconds: 100));
  }
}

Future<void> _save(WidgetTester tester, String path, double pixelRatio) async {
  await tester.runAsync(() async {
    final RenderRepaintBoundary boundary =
        _boundary.currentContext!.findRenderObject()! as RenderRepaintBoundary;
    final ui.Image image = await boundary.toImage(pixelRatio: pixelRatio);
    final ByteData? png = await image.toByteData(
      format: ui.ImageByteFormat.png,
    );
    final File file = File(path)..createSync(recursive: true);
    file.writeAsBytesSync(png!.buffer.asUint8List());
  });
}

/// Grows the viewport until nothing is left to scroll. Returns false when the
/// first screenful already held everything.
///
/// Iterated rather than measured once: sections that arrive with the detail
/// requests change the extent after the first measurement.
Future<bool> _growToFit(WidgetTester tester, Widget home, Size start) async {
  Size size = start;
  bool grew = false;
  for (int i = 0; i < 6; i++) {
    final ScrollableState scrollable = tester.state(
      find.byType(Scrollable).first,
    );
    final double extent = scrollable.position.maxScrollExtent;
    if (extent <= 0.5) break;
    grew = true;
    size = Size(size.width, size.height + extent);
    await _pump(tester, home, size);
    expect(tester.takeException(), isNull);
  }
  if (!grew) return false;
  // A lazy list only ESTIMATES its extent until every child is built, so the
  // loop above overshoots. Now that everything is built, trim the frame to the
  // list's real length so no blank band is mistaken for layout.
  final ScrollableState scrollable = tester.state(
    find.byType(Scrollable).first,
  );
  final RenderSliver sliver = tester.renderObject<RenderSliver>(
    find
        .descendant(
          of: find.byWidget(scrollable.widget),
          matching: find.byType(SliverPadding),
        )
        .first,
  );
  final double chrome = size.height - scrollable.position.viewportDimension;
  await _pump(
    tester,
    home,
    Size(size.width, sliver.geometry!.scrollExtent + chrome),
  );
  expect(tester.takeException(), isNull);
  return true;
}

void main() {
  final String out = 'build/dashboard_review/${kBrandFlavor.flag}';

  setUpAll(_loadFonts);

  for (final DashboardFixture fixture in allFixtures()) {
    for (final MapEntry<String, Size> viewport in _viewports.entries) {
      testWidgets('render ${fixture.name} ${viewport.key}', (
        WidgetTester tester,
      ) async {
        addTearDown(tester.view.reset);
        Widget home(BuildContext context) => Scaffold(
          body: DashboardScreen(
            dataSource: fixture.source,
            onSeeAllHistory: () {},
          ),
          bottomNavigationBar: _navBar(Theme.of(context).colorScheme),
        );

        // The first screenful, exactly as a phone shows it.
        await _pump(tester, Builder(builder: home), viewport.value);
        expect(tester.takeException(), isNull);
        await _save(tester, '$out/${fixture.name}_${viewport.key}.png', 2);

        // The whole scroll length, so every section can be judged at once.
        if (await _growToFit(tester, Builder(builder: home), viewport.value)) {
          await _save(
            tester,
            '$out/${fixture.name}_${viewport.key}_full.png',
            1,
          );
        }
      });
    }
  }

  testWidgets('render results screen with key numbers', (
    WidgetTester tester,
  ) async {
    addTearDown(tester.view.reset);
    final DashboardFixture fixture = forehandOnly();
    final ResultsScreen screen = ResultsScreen(
      analysis: fixture.source.details['fh0']!,
    );
    await _pump(tester, screen, const Size(412, 915));
    await _growToFit(tester, screen, const Size(412, 915));
    await _save(tester, '$out/results_forehand_412_full.png', 1);
  });
}
