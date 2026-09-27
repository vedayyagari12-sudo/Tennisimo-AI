/// The dashboard's chart widgets: donut, radar, grouped bars, line chart.
///
/// The cases worth a test are the ones where a chart could quietly lie — a
/// null drawn at zero, a one-slice ring, a line through one point — plus the
/// contract every chart shares: text wears ink, never the series colour; a
/// tap reveals the exact value; reduced motion lands on the final frame.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/models/chart_data.dart';
import 'package:tennisimo_ai/models/enums.dart';
import 'package:tennisimo_ai/theme/app_theme.dart';
import 'package:tennisimo_ai/theme/brand.dart';
import 'package:tennisimo_ai/widgets/chart_legend.dart';
import 'package:tennisimo_ai/widgets/comparison_bars.dart';
import 'package:tennisimo_ai/widgets/score_line_chart.dart';
import 'package:tennisimo_ai/widgets/shot_mix_chart.dart';
import 'package:tennisimo_ai/widgets/skill_radar.dart';

final AppPalette _palette = paletteFor(kBrandFlavor);

Widget _host(Widget child, {bool reduceMotion = false}) => MaterialApp(
  theme: buildAppTheme(),
  home: Builder(
    builder: (BuildContext context) => MediaQuery(
      data: MediaQuery.of(context).copyWith(disableAnimations: reduceMotion),
      child: Scaffold(
        body: SingleChildScrollView(
          child: Padding(padding: const EdgeInsets.all(16), child: child),
        ),
      ),
    ),
  ),
);

/// Every [Text] under [root] — none may wear a series colour.
void _expectInkOnly(WidgetTester tester, Finder root) {
  final Set<Color> series = <Color>{..._palette.chartSeries};
  for (final Text t in tester.widgetList<Text>(
    find.descendant(of: root, matching: find.byType(Text)),
  )) {
    expect(
      series.contains(t.style?.color),
      isFalse,
      reason: '"${t.data}" is set in a series colour',
    );
  }
}

T _painter<T extends CustomPainter>(WidgetTester tester) => tester
    .widgetList<CustomPaint>(find.byType(CustomPaint))
    .map((CustomPaint p) => p.painter)
    .whereType<T>()
    .single;

Finder _painted<T extends CustomPainter>() =>
    find.byWidgetPredicate((Widget w) => w is CustomPaint && w.painter is T);

ShotMixSlice _slice(
  String label,
  int count,
  int percent, {
  bool other = false,
}) => ShotMixSlice(
  label: label,
  count: count,
  percent: percent,
  shotTypes: const <ShotType>[ShotType.serve],
  isOther: other,
);

SkillProfile _profile({
  List<double?> current = const <double?>[70, 72, 50, 68, 80],
  List<double?>? previous,
}) {
  const List<String> names = <String>[
    'Preparation',
    'Contact',
    'Swing path',
    'Balance',
    'Follow through',
  ];
  return SkillProfile(
    axes: <RadarAxis>[
      for (final String n in names) RadarAxis(category: n, displayName: n),
    ],
    current: RadarSeries(label: 'This swing', values: current),
    previous: previous == null
        ? null
        : RadarSeries(label: 'Previous swing', values: previous),
  );
}

void main() {
  group('ShotMixChart', () {
    testWidgets('no slices draws nothing at all', (WidgetTester tester) async {
      await tester.pumpWidget(
        _host(const ShotMixChart(slices: <ShotMixSlice>[])),
      );
      expect(_painted<DonutPainter>(), findsNothing);
      expect(find.byType(Text), findsNothing);
    });

    testWidgets('one shot type is a sentence, never a full ring', (
      WidgetTester tester,
    ) async {
      final ShotMixSlice only = _slice('Serve', 4, 100);
      await tester.pumpWidget(
        _host(ShotMixChart(slices: <ShotMixSlice>[only])),
      );
      expect(_painted<DonutPainter>(), findsNothing);
      expect(find.text(ShotMixChart.singleTypeMessage(only)), findsOneWidget);
      expect(ShotMixChart.singleTypeMessage(only), contains('All 4 clips'));
      expect(
        ShotMixChart.singleTypeMessage(_slice('Serve', 1, 100)),
        contains('only clip'),
      );
    });

    testWidgets('names every slice with count and percent, total in the hole', (
      WidgetTester tester,
    ) async {
      final List<ShotMixSlice> slices = <ShotMixSlice>[
        _slice('Forehand topspin', 5, 56),
        _slice('Serve', 3, 33),
        _slice('Other', 1, 11, other: true),
      ];
      await tester.pumpWidget(_host(ShotMixChart(slices: slices)));
      await tester.pumpAndSettle();

      expect(_painted<DonutPainter>(), findsOneWidget);
      expect(find.text('Forehand topspin'), findsOneWidget);
      expect(find.text('5 · 56%'), findsOneWidget);
      expect(find.text('Other'), findsOneWidget);
      expect(find.text('9'), findsOneWidget); // the total, in the hole
      _expectInkOnly(tester, find.byType(ShotMixChart));

      // Fixed slot order for named slices, the neutral for the tail — never
      // the score ramp.
      final DonutPainter p = _painter<DonutPainter>(tester);
      expect(p.colors, <Color>[
        _palette.chartSeries[0],
        _palette.chartSeries[1],
        _palette.chartOther,
      ]);
      for (final Color c in p.colors) {
        expect(c, isNot(_palette.scoreHighFill));
        expect(c, isNot(_palette.scoreMidFill));
        expect(c, isNot(_palette.scoreLowFill));
      }
      expect(p.gapColor, _palette.surfaceContainer);
    });

    testWidgets('tapping a legend row shows that slice in the hole', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(
        _host(
          ShotMixChart(
            slices: <ShotMixSlice>[
              _slice('Forehand topspin', 5, 56),
              _slice('Serve', 4, 44),
            ],
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('Serve'));
      await tester.pump();
      expect(find.text('44%'), findsOneWidget);
      expect(find.text('4 clips'), findsOneWidget);
      expect(_painter<DonutPainter>(tester).selected, 1);
      // Again: back to the total.
      await tester.tap(find.text('Serve'));
      await tester.pump();
      expect(find.text('9'), findsOneWidget);
    });

    test('the tap geometry maps angle to slice, hole to nothing', () {
      const Size size = Size.square(100);
      final List<double> values = <double>[1, 1, 2]; // 90°, 90°, 180°
      // Just right of 12 o'clock, on the ring.
      expect(DonutPainter.sliceAt(const Offset(60, 3), size, values), 0);
      // 6 o'clock, on the ring: the second half is slice 2.
      expect(DonutPainter.sliceAt(const Offset(45, 97), size, values), 2);
      // The centre is the hole.
      expect(DonutPainter.sliceAt(const Offset(50, 50), size, values), isNull);
    });
  });

  group('SkillRadar', () {
    testWidgets('fewer than three measured axes: words, no radar', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(
        _host(
          SkillRadar(
            profile: _profile(current: <double?>[70, null, null, 60, null]),
          ),
        ),
      );
      expect(_painted<RadarPainter>(), findsNothing);
      expect(find.text(SkillRadar.tooFewMessage(2)), findsOneWidget);
    });

    testWidgets('a null axis gets no vertex and breaks the outline', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(
        _host(
          SkillRadar(
            profile: _profile(current: <double?>[70, null, 50, 68, null]),
          ),
        ),
      );
      await tester.pumpAndSettle();

      final RadarPainter p = _painter<RadarPainter>(tester);
      // The null axes are carried as nulls all the way to the painter.
      expect(p.current[1], isNull);
      expect(p.current[4], isNull);
      // Only neighbours that were BOTH measured are joined: swing path to
      // balance. Nothing bridges across contact or follow through.
      expect(RadarPainter.segments(p.current), <(int, int)>[(2, 3)]);
      expect(
        find.text(
          SkillRadar.unmeasuredNote(<String>['Contact', 'Follow through']),
        ),
        findsOneWidget,
      );
    });

    test('a complete swing is a closed outline', () {
      expect(RadarPainter.segments(<double?>[1, 2, 3, 4, 5]), hasLength(5));
      expect(RadarPainter.segments(<double?>[1, 2, null, 4, 5]), <(int, int)>[
        (0, 1),
        (3, 4),
        (4, 0),
      ]);
    });

    testWidgets('two series carry a legend; tapping a spoke reads it out', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(
        _host(
          SkillRadar(
            profile: _profile(previous: const <double?>[66, 70, 45, 64, 78]),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.byType(ChartLegend), findsOneWidget);
      expect(find.text('This swing'), findsOneWidget);
      expect(find.text('Previous swing'), findsOneWidget);
      expect(find.text(SkillRadar.tapHint), findsOneWidget);
      _expectInkOnly(tester, find.byType(SkillRadar));

      // Tap straight above the centre: the top spoke, Preparation.
      final RadarPainter p = _painter<RadarPainter>(tester);
      final Offset origin = tester.getTopLeft(_painted<RadarPainter>());
      await tester.tapAt(origin + p.layout.center + const Offset(0, -30));
      await tester.pump();
      expect(
        find.text('Preparation: this swing 70 · previous swing 66'),
        findsOneWidget,
      );
    });

    testWidgets('one series needs no legend', (WidgetTester tester) async {
      await tester.pumpWidget(_host(SkillRadar(profile: _profile())));
      expect(find.byType(ChartLegend), findsNothing);
    });

    testWidgets('reduced motion lands on the final frame at once', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(
        _host(SkillRadar(profile: _profile()), reduceMotion: true),
      );
      await tester.pump();
      expect(_painter<RadarPainter>(tester).progress, 1.0);
    });
  });

  group('ComparisonBars', () {
    const List<CategoryComparison> rows = <CategoryComparison>[
      CategoryComparison(
        category: 'preparation',
        displayName: 'Preparation',
        current: 76,
        average: 74.5,
        averageOf: 2,
      ),
      CategoryComparison(
        category: 'contact',
        displayName: 'Contact',
        current: null,
        average: 71,
        averageOf: 2,
      ),
      CategoryComparison(
        category: 'follow_through',
        displayName: 'Follow through',
        current: 80,
        average: null,
        averageOf: 0,
      ),
    ];

    testWidgets('a missing value is words in a gap, never a bar', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(
        _host(const ComparisonBars(rows: rows, earlierClips: 2)),
      );
      await tester.pumpAndSettle();

      expect(find.text(ComparisonBars.notMeasured), findsOneWidget);
      expect(find.text(ComparisonBars.noEarlier), findsOneWidget);
      // Four values, four bars: 76, 74.5, 71, 80. Two nulls, no bars.
      final Iterable<Container> bars = tester
          .widgetList<Container>(find.byType(Container))
          .where((Container c) {
            final Decoration? d = c.decoration;
            // Bars are rounded at the data end only; legend keys are not.
            return d is BoxDecoration &&
                _palette.chartSeries.contains(d.color) &&
                d.borderRadius ==
                    const BorderRadius.horizontal(right: Radius.circular(4));
          });
      expect(bars, hasLength(4));
      expect(find.text('76'), findsOneWidget);
      expect(find.text('75'), findsOneWidget); // 74.5 at the tip, rounded
      _expectInkOnly(tester, find.byType(ComparisonBars));
    });

    testWidgets('always has a legend, and a tap gives exact numbers', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(
        _host(const ComparisonBars(rows: rows, earlierClips: 2)),
      );
      await tester.pumpAndSettle();
      expect(find.text(ComparisonBars.currentLabel), findsOneWidget);
      expect(find.text(ComparisonBars.averageLabel(2)), findsOneWidget);

      await tester.tap(find.text('Preparation'));
      await tester.pump();
      expect(
        find.text(
          'Preparation: latest clip 76, your average 74.5 over 2 clips (+1.5)',
        ),
        findsOneWidget,
      );
    });

    testWidgets('no rows is no chart', (WidgetTester tester) async {
      await tester.pumpWidget(
        _host(
          const ComparisonBars(rows: <CategoryComparison>[], earlierClips: 0),
        ),
      );
      expect(find.byType(ChartLegend), findsNothing);
    });
  });

  group('ScoreLineChart', () {
    testWidgets('one scored clip is the value and words, not a line', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(
        _host(
          const ScoreLineChart(
            title: 'Overall score',
            points: <double?>[null, 64, null],
          ),
        ),
      );
      expect(_painted<ScoreLinePainter>(), findsNothing);
      expect(find.text('64'), findsOneWidget);
      expect(find.text(ScoreLineChart.singlePointMessage), findsOneWidget);
    });

    testWidgets('two or more: a chart with dates under it', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(
        _host(
          ScoreLineChart(
            title: 'Overall score',
            points: const <double?>[61, 66, null, 70, 71],
            dates: <DateTime?>[
              DateTime(2026, 9, 1),
              DateTime(2026, 9, 5),
              DateTime(2026, 9, 9),
              DateTime(2026, 9, 12),
              DateTime(2026, 9, 20),
            ],
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(_painted<ScoreLinePainter>(), findsOneWidget);
      expect(find.text('1 Sep'), findsOneWidget);
      expect(find.text('20 Sep'), findsOneWidget);
      expect(find.text('4 scored clips'), findsOneWidget);

      final ScoreLineGeometry g = _painter<ScoreLinePainter>(tester).geometry;
      // The unscored clip keeps its slot and breaks the line; it is never
      // plotted.
      expect(g.pointAt(2), isNull);
      expect(g.runs(), <List<int>>[
        <int>[0, 1],
        <int>[3, 4],
      ]);
      // Every tick is a round number.
      expect(g.axis.min % 10, 0);
      expect(g.axis.max % 10, 0);
    });

    testWidgets('a tap snaps to the nearest clip, scored or not', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(
        _host(
          const ScoreLineChart(
            title: 'Overall score',
            points: <double?>[61, null, 70],
          ),
        ),
      );
      await tester.pumpAndSettle();
      final ScoreLineGeometry g = _painter<ScoreLinePainter>(tester).geometry;
      final Offset origin = tester.getTopLeft(_painted<ScoreLinePainter>());

      await tester.tapAt(origin + Offset(g.xFor(1) + 3, g.size.height / 2));
      await tester.pump();
      final ScoreLinePainter p = _painter<ScoreLinePainter>(tester);
      expect(p.selected, 1);
      // No date known: the clip is named by position.
      expect(p.selectedDate, 'Clip 2');
    });

    testWidgets('the line wears the primary accent, not a series slot', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(
        _host(
          const ScoreLineChart(title: 'Overall score', points: <double?>[1, 2]),
          reduceMotion: true,
        ),
      );
      await tester.pump();
      final ScoreLinePainter p = _painter<ScoreLinePainter>(tester);
      expect(p.lineColor, _palette.primary);
      expect(p.progress, 1.0);
    });
  });
}
