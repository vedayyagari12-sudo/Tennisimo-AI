/// The ASSEMBLED dashboard, pumped with fixture data through the
/// [DashboardDataSource] seam — the whole screen, not its parts.
///
/// Two jobs:
///  * the overflow matrix, over every fixture state: a `RenderFlex` overflow
///    is an exception, so "does the whole screen hold at 320dp and 2.0x text"
///    is a deterministic question;
///  * honesty: the states where it would be easy to lie — a null overall
///    score, a failed newest detail — must read as what they are.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/screens/dashboard_screen.dart';
import 'package:tennisimo_ai/theme/app_theme.dart';

import 'dashboard_layout_matrix_test.dart' show kTextScales, kViewports;
import 'support/dashboard_fixtures.dart';

Future<void> _pumpDashboard(
  WidgetTester tester,
  DashboardFixture fixture, {
  Size size = const Size(412, 915),
  double textScale = 1.0,
}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);

  await tester.pumpWidget(
    MaterialApp(
      theme: buildAppTheme(),
      home: Builder(
        builder: (BuildContext context) => MediaQuery(
          data: MediaQuery.of(
            context,
          ).copyWith(textScaler: TextScaler.linear(textScale)),
          child: DashboardScreen(dataSource: fixture.source),
        ),
      ),
    ),
  );
  // The fake futures resolve on the next frames; the ring's sweep finishes
  // well inside two seconds. Not pumpAndSettle: skeletons may animate.
  for (int i = 0; i < 20; i++) {
    await tester.pump(const Duration(milliseconds: 100));
  }
}

/// Scrolls the whole list through the viewport so every lazily built child
/// is laid out at least once — an overflow below the fold is still an
/// overflow.
Future<void> _scrollThrough(WidgetTester tester) async {
  final Finder list = find.byType(Scrollable).first;
  for (int i = 0; i < 40; i++) {
    final ScrollableState state = tester.state(list);
    if (state.position.pixels >= state.position.maxScrollExtent) break;
    await tester.drag(list, const Offset(0, -400));
    await tester.pump();
  }
}

void main() {
  group('the whole screen holds at every size and text scale', () {
    for (final DashboardFixture fixture in allFixtures()) {
      for (final MapEntry<String, Size> viewport in kViewports.entries) {
        for (final double scale in kTextScales) {
          testWidgets('${fixture.name} — ${viewport.key} @ ${scale}x', (
            WidgetTester tester,
          ) async {
            await _pumpDashboard(
              tester,
              fixture,
              size: viewport.value,
              textScale: scale,
            );
            await _scrollThrough(tester);
            expect(tester.takeException(), isNull);
          });
        }
      }
    }
  });

  group('honesty', () {
    testWidgets('a brand-new user gets the empty state, not zeroes', (
      WidgetTester tester,
    ) async {
      await _pumpDashboard(tester, newUser());
      expect(find.text('No swings analysed yet'), findsOneWidget);
      expect(find.text('0'), findsNothing);
    });

    testWidgets('a serve with no overall score reads "Not scored", never 0', (
      WidgetTester tester,
    ) async {
      await _pumpDashboard(tester, unscoredServesAndVolleys());
      expect(find.text('Not scored'), findsOneWidget);
      expect(find.text('0'), findsNothing);
      // The newest serve's own coverage is on its card.
      expect(find.text('3 of 7 metrics measured'), findsOneWidget);
    });

    testWidgets(
      'a failed newest detail never shows an older clip as the latest',
      (WidgetTester tester) async {
        await _pumpDashboard(tester, partialDetailFailure());
        expect(
          find.textContaining('breakdown for your latest clip could not be'),
          findsOneWidget,
        );
        // No coverage on the hero, no swing notes: both would be fh1's.
        expect(find.textContaining('metrics measured'), findsNothing);
        expect(find.text('Swing notes'), findsNothing);
        expect(find.text('Latest coverage'.toUpperCase()), findsNothing);
      },
    );

    testWidgets('the forehand player sees plain swing notes, no torso units', (
      WidgetTester tester,
    ) async {
      await _pumpDashboard(tester, forehandOnly());
      await tester.scrollUntilVisible(
        find.text('Swing notes'),
        300,
        scrollable: find.byType(Scrollable).first,
      );
      expect(find.text('Swing notes'), findsOneWidget);
      expect(
        find.text(
          'Swing a bit more from low to high — brush up the back of '
          'the ball.',
        ),
        findsOneWidget,
      );
      expect(find.textContaining(' TU'), findsNothing);
    });

    testWidgets('never-recorded shots share one card', (
      WidgetTester tester,
    ) async {
      await _pumpDashboard(tester, forehandOnly());
      await _scrollThrough(tester);
      expect(find.text('NOT RECORDED YET'), findsOneWidget);
      expect(find.text('Volley'), findsOneWidget);
    });
  });
}
