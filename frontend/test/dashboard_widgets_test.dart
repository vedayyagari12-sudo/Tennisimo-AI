/// The dashboard's new widgets.
///
/// The cases worth a test here are the ones where a widget could quietly lie:
/// a gap rendered as a zero, a trend drawn through one point, a shot type with
/// no overall score made to look like a bad one — and a dense layout blowing
/// out at phone width.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/models/dashboard_insights.dart';
import 'package:tennisimo_ai/models/enums.dart';
import 'package:tennisimo_ai/theme/app_theme.dart';
import 'package:tennisimo_ai/theme/brand.dart';
import 'package:tennisimo_ai/widgets/focus_card.dart';
import 'package:tennisimo_ai/widgets/inline_stats.dart';
import 'package:tennisimo_ai/widgets/mini_trend.dart';
import 'package:tennisimo_ai/widgets/score_line_chart.dart';
import 'package:tennisimo_ai/widgets/shot_type_section.dart';
import 'package:tennisimo_ai/widgets/trend_direction_chip.dart';

/// The narrowest phone this app is expected to run on. Everything below is
/// laid out at this width, because a dense screen is the easiest kind to
/// overflow.
const Size kSmallPhone = Size(320, 640);

Widget _host(Widget child) => MaterialApp(
      theme: buildAppTheme(),
      home: Scaffold(
        body: SingleChildScrollView(
          child: Padding(padding: const EdgeInsets.all(16), child: child),
        ),
      ),
    );

/// The sparkline itself, as opposed to the many `CustomPaint`s Material
/// scatters through a Scaffold.
final Finder sparkline = find.byWidgetPredicate(
  (Widget w) => w is CustomPaint && w.painter is MiniTrendPainter,
);

ShotTypeInsight _insight({
  ShotType shotType = ShotType.forehandTopspin,
  List<double?> scores = const <double?>[62, 70],
  List<double?> speeds = const <double?>[null, null],
  List<CategoryTrend> categories = const <CategoryTrend>[],
  int detailsInspected = 2,
  CoverageSummary? coverage,
}) =>
    ShotTypeInsight(
      shotType: shotType,
      scorePoints: scores,
      speedPoints: speeds,
      categoryTrends: categories,
      detailsInspected: detailsInspected,
      latestCoverage: coverage,
    );

void main() {
  group('MiniTrend', () {
    testWidgets('nothing measured is an em dash and a word, never a chart',
        (WidgetTester tester) async {
      await tester.pumpWidget(_host(const MiniTrend(
        title: 'Contact',
        points: <double?>[null, null, null],
      )));

      expect(find.text('—'), findsOneWidget);
      expect(find.text('Not measured'), findsOneWidget);
      expect(find.text('0'), findsNothing);
      expect(sparkline, findsNothing);
    });

    testWidgets('one measured point says a trend needs two',
        (WidgetTester tester) async {
      await tester.pumpWidget(_host(const MiniTrend(
        title: 'Balance',
        points: <double?>[null, 57],
      )));

      expect(find.text('57'), findsOneWidget);
      expect(find.text(MiniTrend.singlePointMessage), findsOneWidget);
      expect(sparkline, findsNothing);
    });

    testWidgets('two points plot, and only the newest is labelled',
        (WidgetTester tester) async {
      await tester.pumpWidget(_host(const MiniTrend(
        title: 'Swing path',
        points: <double?>[61, null, 74],
      )));

      expect(sparkline, findsOneWidget);
      // The newest value, and no number on the older point.
      expect(find.text('74'), findsOneWidget);
      expect(find.text('61'), findsNothing);
      expect(find.text(MiniTrend.singlePointMessage), findsNothing);
    });

    testWidgets('a unit rides along with a real value only',
        (WidgetTester tester) async {
      await tester.pumpWidget(_host(const MiniTrend(
        title: 'Ball speed',
        points: <double?>[62, 68],
        unit: 'mph',
      )));
      expect(find.text('68 mph'), findsOneWidget);

      await tester.pumpWidget(_host(const MiniTrend(
        title: 'Ball speed',
        points: <double?>[null],
        unit: 'mph',
      )));
      expect(find.text('—'), findsOneWidget);
      expect(find.textContaining('mph'), findsNothing);
    });

    testWidgets('tapping a point reveals its value', (WidgetTester tester) async {
      await tester.pumpWidget(_host(const MiniTrend(
        title: 'Preparation',
        points: <double?>[41, 55, 88],
      )));

      expect(find.text('88'), findsOneWidget);

      // Tap the far left of the plot: the oldest point.
      final Rect plot = tester.getRect(sparkline);
      await tester.tapAt(Offset(plot.left + 2, plot.center.dy));
      await tester.pump();

      expect(find.text('41'), findsOneWidget);
      expect(find.text('88'), findsNothing);
    });
    testWidgets('every panel on a section paints the same mark colour',
        (WidgetTester tester) async {
      // The dashboard carries no categorical palette: score panels and the
      // ball-speed panel are told apart by their titles, not by a hue. If a
      // second series colour is ever added this fails.
      await tester.pumpWidget(_host(ShotTypeSection(
        initiallyExpanded: true,
        insight: _insight(
          scores: const <double?>[60, 72],
          speeds: const <double?>[64, 70],
          categories: <CategoryTrend>[
            const CategoryTrend(
              category: 'balance',
              displayName: 'Balance',
              points: <double?>[50, 58],
            ),
          ],
        ),
      )));

      final Set<Color> marks = tester
          .widgetList<CustomPaint>(sparkline)
          .map((CustomPaint p) => (p.painter! as MiniTrendPainter).lineColor)
          .toSet();

      expect(marks, hasLength(1));
      expect(marks.single, paletteFor(kBrandFlavor).primary);
    });
  });

  group('InlineStats', () {
    testWidgets('a null value is an em dash and drops its unit',
        (WidgetTester tester) async {
      await tester.pumpWidget(_host(const InlineStats(stats: <InlineStat>[
        InlineStat(label: 'Top speed', value: null, unit: 'mph'),
        InlineStat(label: 'Clips', value: '4'),
      ])));

      expect(find.text('—'), findsOneWidget);
      expect(find.text('mph'), findsNothing);
      expect(find.text('TOP SPEED'), findsOneWidget);
      expect(find.text('4'), findsOneWidget);
    });
  });

  group('TrendDirectionChip', () {
    testWidgets('the direction is a word and an icon, not a colour',
        (WidgetTester tester) async {
      await tester.pumpWidget(_host(const Column(children: <Widget>[
        TrendDirectionChip(direction: TrendDirection.improving, delta: 6.2),
        TrendDirectionChip(direction: TrendDirection.declining, delta: -3.4),
        TrendDirectionChip(direction: TrendDirection.steady, delta: 0.4),
        TrendDirectionChip(direction: TrendDirection.unknown, delta: null),
      ])));

      expect(find.text('Improving +6.2'), findsOneWidget);
      expect(find.text('Slipping −3.4'), findsOneWidget);
      // A steady reading shows no magnitude: the band already rejected it.
      expect(find.text('Holding steady'), findsOneWidget);
      expect(find.text('Not enough history'), findsOneWidget);
      expect(find.byIcon(Icons.trending_up), findsOneWidget);
      expect(find.byIcon(Icons.trending_down), findsOneWidget);
      expect(find.byIcon(Icons.trending_flat), findsOneWidget);
    });
  });

  group('FocusCard', () {
    testWidgets('no measurable category names none', (WidgetTester tester) async {
      await tester.pumpWidget(_host(FocusCard(
        insight: _insight(categories: <CategoryTrend>[
          const CategoryTrend(
            category: 'balance',
            displayName: 'Balance',
            points: <double?>[null, null],
          ),
        ]),
      )));

      expect(find.text(FocusCard.nothingMeasuredMessage), findsOneWidget);
      expect(find.text('Balance'), findsNothing);
    });

    testWidgets('nothing recorded at all is handled the same way',
        (WidgetTester tester) async {
      await tester.pumpWidget(_host(const FocusCard(insight: null)));
      expect(find.text(FocusCard.nothingMeasuredMessage), findsOneWidget);
    });

    testWidgets('the weakest measured category is the headline',
        (WidgetTester tester) async {
      await tester.pumpWidget(_host(FocusCard(
        insight: _insight(categories: <CategoryTrend>[
          const CategoryTrend(
            category: 'preparation',
            displayName: 'Preparation',
            points: <double?>[80, 82],
          ),
          const CategoryTrend(
            category: 'balance',
            displayName: 'Balance',
            points: <double?>[40, 48],
          ),
          const CategoryTrend(
            category: 'contact',
            displayName: 'Contact',
            points: <double?>[null, null],
          ),
        ]),
      )));

      expect(find.text('Balance'), findsWidgets);
      expect(find.text('48'), findsWidgets);
      expect(find.text('Improving +8.0'), findsOneWidget);
      // Contact has no number, so it can never be "the weakest".
      expect(find.text('Contact'), findsNothing);
      expect(find.textContaining('Lowest of the 2 categories'), findsOneWidget);
    });
  });

  group('ShotTypeSection', () {
    testWidgets('a shot type with no overall score says so, and shows no zero',
        (WidgetTester tester) async {
      await tester.pumpWidget(_host(ShotTypeSection(
        initiallyExpanded: true,
        insight: _insight(
          shotType: ShotType.serve,
          scores: const <double?>[null, null],
          speeds: const <double?>[92, 96],
          categories: <CategoryTrend>[
            const CategoryTrend(
              category: 'preparation',
              displayName: 'Preparation',
              points: <double?>[64, 66],
            ),
            const CategoryTrend(
              category: 'contact',
              displayName: 'Contact',
              points: <double?>[null, null],
            ),
          ],
          coverage: const CoverageSummary(available: 6, total: 15),
        ),
      )));

      expect(find.text('Serve'), findsOneWidget);
      expect(find.text(ShotTypeSection.noOverallScoreMessage), findsOneWidget);
      expect(find.text('0'), findsNothing);
      expect(find.text('0.0'), findsNothing);
      // Best / Average / Range are all unknown; the speed is real.
      expect(find.text('—'), findsWidgets);
      expect(find.text('96 mph'), findsOneWidget);
      expect(find.text('66'), findsOneWidget);
      expect(find.textContaining('6 of 15 metrics'), findsOneWidget);
    });

    testWidgets('a scored shot type shows its own records and spread',
        (WidgetTester tester) async {
      await tester.pumpWidget(_host(ShotTypeSection(
        initiallyExpanded: true,
        insight: _insight(scores: const <double?>[60, null, 78]),
      )));

      expect(find.text('Forehand topspin'), findsOneWidget);
      expect(find.text('3 clips'), findsOneWidget);
      expect(find.text('Best 78'), findsOneWidget); // in the header
      expect(find.text('69.0'), findsOneWidget); // average WITHIN this shot
      expect(find.text('18'), findsOneWidget); // range
      expect(find.textContaining('Some variation'), findsOneWidget);
      expect(find.textContaining('No ball speed measured'), findsOneWidget);
    });

    testWidgets('one scored clip does not claim a spread',
        (WidgetTester tester) async {
      await tester.pumpWidget(_host(ShotTypeSection(
        initiallyExpanded: true,
        insight: _insight(scores: const <double?>[71], speeds: const <double?>[null]),
      )));

      // The score chart shows the value and says a trend needs two, with no
      // line; no spread is claimed, and it is not said twice.
      expect(find.text(ScoreLineChart.singlePointMessage), findsOneWidget);
      // Once as the header's best, once as the chart's lone value.
      expect(find.text('Best 71'), findsOneWidget);
      expect(find.text('71'), findsOneWidget);
      expect(
        find.byWidgetPredicate(
          (Widget w) => w is CustomPaint && w.painter is ScoreLinePainter,
        ),
        findsNothing,
      );
      expect(find.textContaining('between your best'), findsNothing);
      expect(find.text('0'), findsNothing);
    });

    testWidgets('never-recorded shot types are words only, no chart',
        (WidgetTester tester) async {
      await tester.pumpWidget(
        _host(const ShotTypesNotRecordedCard(shotTypes: <ShotType>[
          ShotType.backhandOneHanded,
          ShotType.serve,
        ])),
      );

      expect(find.text('Backhand (one-handed)'), findsOneWidget);
      expect(find.text('Serve'), findsOneWidget);
      expect(find.text('NOT RECORDED YET'), findsOneWidget);
      expect(sparkline, findsNothing);
      expect(find.text('0'), findsNothing);
      expect(find.text('—'), findsNothing);
    });
  });

  group('at 320dp, nothing overflows', () {
    // A RenderFlex overflow is an error in a test, so mounting at the
    // narrowest supported width IS the assertion here.
    Future<void> pumpAt(WidgetTester tester, Widget child) async {
      tester.view.physicalSize = kSmallPhone;
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(_host(child));
      await tester.pump();
    }

    testWidgets('the densest shot-type section', (WidgetTester tester) async {
      await pumpAt(
        tester,
        ShotTypeSection(
          initiallyExpanded: true,
          // The longest label the enum has, every stat populated, five
          // category panels.
          insight: _insight(
            shotType: ShotType.backhandTwoHanded,
            scores: const <double?>[61, 70, 88],
            speeds: const <double?>[70, 74, 81],
            detailsInspected: 3,
            coverage: const CoverageSummary(available: 11, total: 15),
            categories: <CategoryTrend>[
              for (final String name in <String>[
                'preparation',
                'swing_path',
                'contact',
                'follow_through',
                'balance',
              ])
                CategoryTrend(
                  category: name,
                  displayName: name.replaceAll('_', ' '),
                  points: const <double?>[55, 60, 72],
                ),
            ],
          ),
        ),
      );

      expect(tester.takeException(), isNull);
      expect(find.text('Backhand (two-handed)'), findsOneWidget);
    });

    testWidgets('the focus card with a long category name',
        (WidgetTester tester) async {
      await pumpAt(
        tester,
        FocusCard(
          insight: _insight(
            shotType: ShotType.backhandOneHanded,
            categories: <CategoryTrend>[
              const CategoryTrend(
                category: 'follow_through',
                displayName: 'Follow through',
                points: <double?>[52, 44],
              ),
            ],
          ),
        ),
      );

      expect(tester.takeException(), isNull);
    });

    testWidgets('a five-stat inline row', (WidgetTester tester) async {
      await pumpAt(
        tester,
        const InlineStats(stats: <InlineStat>[
          InlineStat(label: 'Clips', value: '12'),
          InlineStat(label: 'Best', value: '88'),
          InlineStat(label: 'Average', value: '71.4'),
          InlineStat(label: 'Range', value: '27'),
          InlineStat(label: 'Top speed', value: '104', unit: 'mph'),
        ]),
      );

      expect(tester.takeException(), isNull);
    });
  });
}
