/// The collapsible shot-type section, the score chart's version break, and
/// both on the assembled dashboard.
///
/// What is pinned here: a section starts closed unless told otherwise, its
/// header alone carries the shot, the clip count, the best and the direction
/// (a word and an icon); the header is a semantic button with an expanded
/// state; reduced motion jumps straight to the end state; the category scores
/// appear once; and a scoring change breaks the line instead of being read as
/// a trend.
library;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/models/dashboard_insights.dart';
import 'package:tennisimo_ai/models/enums.dart';
import 'package:tennisimo_ai/screens/dashboard_screen.dart';
import 'package:tennisimo_ai/theme/app_theme.dart';
import 'package:tennisimo_ai/widgets/comparison_bars.dart';
import 'package:tennisimo_ai/widgets/mini_trend.dart';
import 'package:tennisimo_ai/widgets/score_line_chart.dart';
import 'package:tennisimo_ai/widgets/shot_type_section.dart';
import 'package:tennisimo_ai/widgets/trend_direction_chip.dart';

import 'support/dashboard_fixtures.dart';

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

final Finder _scoreChart = find.byType(ScoreLineChart);

CategoryTrend _trend(String name, List<double?> points) =>
    CategoryTrend(category: name, displayName: name, points: points);

ShotTypeInsight _insight({
  List<double?> scores = const <double?>[62, 70],
  List<String?> versions = const <String?>[],
  List<CategoryTrend> categories = const <CategoryTrend>[],
  bool newestDetailLoaded = false,
  int detailsInspected = 2,
}) => ShotTypeInsight(
  shotType: ShotType.forehandTopspin,
  scorePoints: scores,
  speedPoints: List<double?>.filled(scores.length, null),
  categoryTrends: categories,
  detailsInspected: detailsInspected,
  latestCoverage: null,
  newestDetailLoaded: newestDetailLoaded,
  versions: versions,
);

/// The header's semantics node: the one carrying the button flag.
SemanticsNode _headerNode(WidgetTester tester) => tester.getSemantics(
  find
      .ancestor(
        of: find.text('Forehand topspin'),
        matching: find.byType(MergeSemantics),
      )
      .first,
);

void main() {
  group('collapsed and expanded', () {
    testWidgets('closed by default: the header alone, no chart', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(_host(ShotTypeSection(insight: _insight())));

      expect(find.text('Forehand topspin'), findsOneWidget);
      expect(find.text('2 clips'), findsOneWidget);
      expect(find.text('Best 70'), findsOneWidget);
      // The direction is an icon and a word, never colour alone.
      expect(find.text('Improving +8.0'), findsOneWidget);
      expect(find.byIcon(Icons.trending_up), findsOneWidget);
      expect(_scoreChart, findsNothing);
      expect(find.text('AVERAGE'), findsNothing);
    });

    testWidgets('a tap opens the full block, and a second closes it', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(_host(ShotTypeSection(insight: _insight())));

      await tester.tap(find.text('Forehand topspin'));
      await tester.pumpAndSettle();
      expect(_scoreChart, findsOneWidget);
      expect(find.text('AVERAGE'), findsOneWidget);
      // Clips and best live in the header; the open block does not repeat
      // them.
      expect(find.text('CLIPS'), findsNothing);
      expect(find.text('BEST'), findsNothing);

      await tester.tap(find.text('Forehand topspin'));
      await tester.pumpAndSettle();
      expect(_scoreChart, findsNothing);
    });

    testWidgets('initiallyExpanded opens it from the first frame', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(
        _host(ShotTypeSection(insight: _insight(), initiallyExpanded: true)),
      );
      expect(_scoreChart, findsOneWidget);
    });

    testWidgets('the header is a button that reports its expanded state', (
      WidgetTester tester,
    ) async {
      final SemanticsHandle handle = tester.ensureSemantics();
      await tester.pumpWidget(_host(ShotTypeSection(insight: _insight())));

      expect(
        _headerNode(tester),
        matchesSemantics(
          isButton: true,
          hasExpandedState: true,
          isExpanded: false,
          hasTapAction: true,
          hasFocusAction: true,
          isFocusable: true,
          label: 'Forehand topspin\n2 clips\nBest 70\nImproving +8.0',
        ),
      );

      await tester.tap(find.text('Forehand topspin'));
      await tester.pumpAndSettle();
      expect(
        _headerNode(tester),
        isSemantics(isButton: true, isExpanded: true),
      );
      handle.dispose();
    });

    testWidgets('reduced motion opens in a single frame', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(
        _host(ShotTypeSection(insight: _insight()), reduceMotion: true),
      );
      await tester.tap(find.text('Forehand topspin'));
      await tester.pump();
      // One frame after the tap the card is already at its final height:
      // nothing is left for a later frame to grow.
      final double oneFrame = tester
          .getSize(find.byType(ShotTypeSection))
          .height;
      await tester.pumpAndSettle();
      expect(tester.getSize(find.byType(ShotTypeSection)).height, oneFrame);
      expect(_scoreChart, findsOneWidget);
    });

    testWidgets('with full motion the open is animated', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(_host(ShotTypeSection(insight: _insight())));
      await tester.tap(find.text('Forehand topspin'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));
      final double midway = tester.getSize(find.byType(ShotTypeSection)).height;
      await tester.pumpAndSettle();
      expect(
        tester.getSize(find.byType(ShotTypeSection)).height,
        greaterThan(midway),
      );
    });

    testWidgets('a never-scored shot says so in the header, with no chip', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(
        _host(
          ShotTypeSection(insight: _insight(scores: const <double?>[null])),
        ),
      );
      expect(find.text(ShotTypeSection.notScoredLabel), findsOneWidget);
      expect(find.byType(TrendDirectionChip), findsNothing);
      expect(find.textContaining('Best'), findsNothing);
      expect(find.text('0'), findsNothing);
    });
  });

  group('category scores appear once', () {
    final List<CategoryTrend> trends = <CategoryTrend>[
      _trend('Preparation', <double?>[60, 70, 76]),
      _trend('Balance', <double?>[50, null, 58]),
    ];

    testWidgets('the bars when the newest clip loaded, no sparklines', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(
        _host(
          ShotTypeSection(
            initiallyExpanded: true,
            insight: _insight(
              categories: trends,
              newestDetailLoaded: true,
              detailsInspected: 3,
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.byType(ComparisonBars), findsOneWidget);
      expect(find.byType(MiniTrend), findsNothing);

      // The earlier clips' own scores are still reachable.
      await tester.tap(find.text('Preparation'));
      await tester.pump();
      expect(
        find.text(
          'Preparation: latest clip 76, your average 65.0 over 2 clips '
          '(+11.0). Earlier clips, oldest first: 60, 70',
        ),
        findsOneWidget,
      );
    });

    testWidgets('the sparklines when the bars cannot be drawn', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(
        _host(
          ShotTypeSection(
            initiallyExpanded: true,
            insight: _insight(categories: trends, detailsInspected: 3),
          ),
        ),
      );
      expect(find.byType(ComparisonBars), findsNothing);
      expect(find.byType(MiniTrend), findsNWidgets(2));
      expect(find.text('Categories · last 3 clips'), findsOneWidget);
    });
  });

  group('a scoring change inside a shot type', () {
    testWidgets('the line breaks and the chip stays inside one version', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(
        _host(
          ShotTypeSection(
            initiallyExpanded: true,
            insight: _insight(
              scores: const <double?>[40, 45, 70, 73],
              versions: const <String?>['v2', 'v2', 'v3', 'v3'],
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      final ScoreLineChart chart = tester.widget(_scoreChart);
      expect(chart.points, <double?>[40, 45, 70, 73]);
      expect(chart.breaksBefore, <int>[2]);
      // 73 vs 70, not 73 vs 45; best is 73 either way, range is v3 only.
      expect(find.text('Improving +3.0'), findsOneWidget);
      expect(find.textContaining('3 points between'), findsOneWidget);
    });

    testWidgets('one comparable clip: "not enough history", never +40', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(
        _host(
          ShotTypeSection(
            insight: _insight(
              scores: const <double?>[38, 40, 80],
              versions: const <String?>['v2', 'v2', 'v3'],
            ),
          ),
        ),
      );
      expect(find.text('Not enough history'), findsOneWidget);
      expect(find.textContaining('Improving'), findsNothing);
      expect(find.text('Best 80'), findsOneWidget);
    });

    testWidgets('all-null versions: no break, and the comparison as before', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(
        _host(
          ShotTypeSection(
            initiallyExpanded: true,
            insight: _insight(
              scores: const <double?>[38, 40, 80],
              versions: const <String?>[null, null, null],
            ),
          ),
        ),
      );
      final ScoreLineChart chart = tester.widget(_scoreChart);
      expect(chart.breaksBefore, isEmpty);
      expect(find.text('Improving +40.0'), findsOneWidget);
    });
  });

  group('ScoreLineChart version break', () {
    test('runs split at a boundary and the marker sits between the clips', () {
      final ScoreLineGeometry g = ScoreLineGeometry(
        size: const Size(300, 132),
        values: const <double?>[40, 45, null, 70, 73],
        breaksBefore: const <int>[3],
        tickStyle: const TextStyle(fontSize: 11),
        scaler: TextScaler.noScaling,
      );
      expect(g.runs(), <List<int>>[
        <int>[0, 1],
        <int>[3, 4],
      ]);
      final ScoreLineGeometry h = ScoreLineGeometry(
        size: const Size(300, 132),
        values: const <double?>[40, 45, 70, 73],
        breaksBefore: const <int>[2],
        tickStyle: const TextStyle(fontSize: 11),
        scaler: TextScaler.noScaling,
      );
      expect(h.runs(), <List<int>>[
        <int>[0, 1],
        <int>[2, 3],
      ]);
      expect(h.markers, <int>[2]);
      expect(h.markerX(2), (h.xFor(1) + h.xFor(2)) / 2);
    });

    test('out-of-range boundaries draw no marker', () {
      final ScoreLineGeometry g = ScoreLineGeometry(
        size: const Size(300, 132),
        values: const <double?>[40, 45],
        breaksBefore: const <int>[0, 2, 7],
        tickStyle: const TextStyle(fontSize: 11),
        scaler: TextScaler.noScaling,
      );
      expect(g.markers, isEmpty);
      expect(g.runs(), <List<int>>[
        <int>[0, 1],
      ]);
    });

    test('the painter draws a dashed hairline at the marker', () {
      final ScoreLineGeometry g = ScoreLineGeometry(
        size: const Size(300, 132),
        values: const <double?>[40, 45, 70, 73],
        breaksBefore: const <int>[2],
        tickStyle: const TextStyle(fontSize: 11),
        scaler: TextScaler.noScaling,
      );
      final _LineCanvas canvas = _LineCanvas();
      const Color c = Color(0xFF000000);
      ScoreLinePainter(
        geometry: g,
        lineColor: c,
        areaColor: c,
        gridColor: c,
        crosshairColor: c,
        ringColor: c,
        tooltipFill: c,
        inkStrong: c,
        inkMuted: c,
        labelStyle: const TextStyle(fontSize: 11),
        scaler: TextScaler.noScaling,
        selected: null,
        selectedDate: null,
      ).paint(canvas, g.size);

      final double x = g.markerX(2);
      final List<(Offset, Offset)> dashes = canvas.lines
          .where(((Offset, Offset) l) => l.$1.dx == x && l.$2.dx == x)
          .toList();
      // Several short dashes, not one solid rule.
      expect(dashes.length, greaterThan(3));
      for (final (Offset, Offset) d in dashes) {
        expect(d.$2.dy - d.$1.dy, lessThanOrEqualTo(3));
      }
    });

    testWidgets('a tap still reads any clip across the break', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(
        _host(
          const ScoreLineChart(
            title: 'Overall score',
            points: <double?>[40, 45, 70, 73],
            breaksBefore: <int>[2],
          ),
        ),
      );
      await tester.pumpAndSettle();
      final Finder painted = find.byWidgetPredicate(
        (Widget w) => w is CustomPaint && w.painter is ScoreLinePainter,
      );
      final ScoreLinePainter p0 =
          tester.widget<CustomPaint>(painted).painter! as ScoreLinePainter;
      final Offset origin = tester.getTopLeft(painted);
      await tester.tapAt(
        origin + Offset(p0.geometry.xFor(1), p0.geometry.size.height / 2),
      );
      await tester.pump();
      final ScoreLinePainter p1 =
          tester.widget<CustomPaint>(painted).painter! as ScoreLinePainter;
      expect(p1.selected, 1);
    });
  });

  group('on the assembled dashboard', () {
    Future<void> pumpTall(WidgetTester tester, DashboardFixture f) async {
      // Tall enough that the lazy list builds every section at once.
      tester.view.physicalSize = const Size(412, 6000);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(
        MaterialApp(
          theme: buildAppTheme(),
          home: DashboardScreen(dataSource: f.source),
        ),
      );
      for (int i = 0; i < 20; i++) {
        await tester.pump(const Duration(milliseconds: 100));
      }
    }

    testWidgets('only the most recently played shot type starts open', (
      WidgetTester tester,
    ) async {
      await pumpTall(tester, severalShotTypes());
      final List<ShotTypeSection> sections = tester
          .widgetList<ShotTypeSection>(find.byType(ShotTypeSection))
          .toList();
      expect(sections, hasLength(3));
      expect(
        <ShotType>[
          for (final ShotTypeSection s in sections)
            if (s.initiallyExpanded) s.insight.shotType,
        ],
        <ShotType>[ShotType.forehandTopspin],
      );
      expect(_scoreChart, findsOneWidget);
    });

    testWidgets('mixed versions: the open forehand breaks its line, and the '
        'serve falls back to "need 2"', (WidgetTester tester) async {
      await pumpTall(tester, mixedVersions());

      final ScoreLineChart forehand = tester.widget(_scoreChart);
      expect(forehand.breaksBefore, <int>[5]);

      // The serve's only v3 clip has nothing to be compared with.
      final Finder serve = find.byWidgetPredicate(
        (Widget w) =>
            w is ShotTypeSection && w.insight.shotType == ShotType.serve,
      );
      expect(
        find.descendant(of: serve, matching: find.text('Not enough history')),
        findsOneWidget,
      );
      final Finder serveHeader = find.descendant(
        of: serve,
        matching: find.byType(InkWell),
      );
      await tester.tap(serveHeader.first);
      await tester.pumpAndSettle();
      expect(find.text(MiniTrend.singlePointMessage), findsWidgets);
      final ScoreLineChart serveChart = tester.widget(
        find.descendant(of: serve, matching: _scoreChart),
      );
      expect(serveChart.breaksBefore, <int>[2]);
      expect(tester.takeException(), isNull);
    });
  });
}

class _LineCanvas implements Canvas {
  final List<(Offset, Offset)> lines = <(Offset, Offset)>[];

  @override
  void drawLine(Offset p1, Offset p2, Paint paint) => lines.add((p1, p2));

  @override
  void noSuchMethod(Invocation invocation) {}
}
