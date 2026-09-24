import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tennisform_ai/models/analysis_response.dart';
import 'package:tennisform_ai/theme/app_theme.dart';
import 'package:tennisform_ai/widgets/category_bars.dart';
import 'package:tennisform_ai/widgets/score_ring.dart';
import 'package:tennisform_ai/widgets/stat_tile.dart';
import 'package:tennisform_ai/widgets/trend_chart.dart';

/// Every widget under test is mounted on the real app theme, because the
/// score ramp and the "not measured" muted style both come from it.
Widget _host(Widget child) => MaterialApp(
      theme: buildAppTheme(),
      home: Scaffold(body: Center(child: child)),
    );

CategoryScore _category({
  required String name,
  required double? score,
  int available = 1,
  int total = 1,
}) =>
    CategoryScore(
      category: name,
      score: score,
      weight: 0.25,
      metricNames: const <String>['a_metric'],
      metricsAvailable: available,
      metricsTotal: total,
    );

void main() {
  group('scoreColor ramp', () {
    test('the boundaries fall on the documented side', () {
      expect(scoreColor(100), AppColors.primary);
      expect(scoreColor(80), AppColors.primary);
      expect(scoreColor(79.9), AppColors.scoreMid);
      expect(scoreColor(60), AppColors.scoreMid);
      expect(scoreColor(59.9), AppColors.error);
      expect(scoreColor(0), AppColors.error);
    });

    test('a null score is muted, never the "bad" red', () {
      // Null means not measurable. It is not a failing score.
      expect(scoreColor(null), AppColors.onSurfaceVariant);
      expect(scoreColor(null), isNot(AppColors.error));
    });
  });

  group('ScoreRing', () {
    testWidgets('a real score renders the rounded numeral',
        (WidgetTester tester) async {
      await tester.pumpWidget(_host(const ScoreRing(score: 72.4)));
      await tester.pumpAndSettle();

      expect(find.text('72'), findsOneWidget);
      expect(find.text('—'), findsNothing);
    });

    testWidgets('a null score renders an em dash, never a zero',
        (WidgetTester tester) async {
      await tester.pumpWidget(
        _host(const ScoreRing(score: null, label: 'Not scored')),
      );
      await tester.pumpAndSettle();

      expect(find.text('—'), findsOneWidget);
      expect(find.text('0'), findsNothing);
      expect(find.text('Not scored'), findsOneWidget);
    });

    testWidgets('a delta is rendered beneath the numeral',
        (WidgetTester tester) async {
      await tester.pumpWidget(_host(const ScoreRing(score: 68, delta: 4.2)));
      await tester.pumpAndSettle();

      expect(find.text('+4.2 vs last'), findsOneWidget);
    });
  });

  group('TrendChart', () {
    testWidgets('no points at all shows the empty message, not a chart',
        (WidgetTester tester) async {
      await tester.pumpWidget(_host(const TrendChart(scores: <double?>[])));

      expect(find.textContaining('No scored sessions yet'), findsOneWidget);
      // Nothing is plotted and no trend caveat is claimed either.
      expect(find.text(TrendChart.singlePointMessage), findsNothing);
    });

    testWidgets('sessions with null scores are skipped, not plotted at zero',
        (WidgetTester tester) async {
      // Three sessions, none of them scorable: that is an EMPTY chart, not a
      // flat line along the floor.
      await tester.pumpWidget(
        _host(const TrendChart(scores: <double?>[null, null, null])),
      );

      expect(find.textContaining('No scored sessions yet'), findsOneWidget);
    });

    testWidgets('exactly one point explains that a trend needs two',
        (WidgetTester tester) async {
      await tester.pumpWidget(
        _host(const TrendChart(scores: <double?>[null, 64, null])),
      );

      expect(find.text(TrendChart.singlePointMessage), findsOneWidget);
      expect(find.byType(CustomPaint), findsWidgets);
    });

    testWidgets('several points draw the chart with no caveat line',
        (WidgetTester tester) async {
      await tester.pumpWidget(
        _host(const TrendChart(scores: <double?>[61, null, 64, 70, 68])),
      );

      expect(find.byType(CustomPaint), findsWidgets);
      expect(find.text(TrendChart.singlePointMessage), findsNothing);
      expect(find.textContaining('No scored sessions yet'), findsNothing);
    });
  });

  group('CategoryBars', () {
    testWidgets('an unmeasured category says so and gets no bar',
        (WidgetTester tester) async {
      await tester.pumpWidget(_host(CategoryBars(
        categories: <CategoryScore>[
          _category(name: 'swing_path', score: 74),
          _category(name: 'balance', score: null, available: 0),
        ],
      )));

      // Humanised straight off the model.
      expect(find.text('Swing path'), findsOneWidget);
      expect(find.text('74'), findsOneWidget);

      expect(find.text('Balance'), findsOneWidget);
      expect(find.text('Not measured'), findsOneWidget);
      expect(find.text('0'), findsNothing);

      // One bar for one measured category: the null one draws nothing.
      expect(find.byType(FractionallySizedBox), findsOneWidget);
      expect(find.text('0 of 1 metrics measured'), findsOneWidget);
    });

    testWidgets('no categories renders nothing at all',
        (WidgetTester tester) async {
      await tester.pumpWidget(
        _host(const CategoryBars(categories: <CategoryScore>[])),
      );

      expect(find.byType(FractionallySizedBox), findsNothing);
      expect(find.text('Not measured'), findsNothing);
    });
  });

  group('StatTile', () {
    testWidgets('an absent value is an em dash and drops its unit',
        (WidgetTester tester) async {
      await tester.pumpWidget(
        _host(const StatTile(label: 'Top speed', value: null, unit: 'mph')),
      );

      expect(find.text('—'), findsOneWidget);
      expect(find.text('0'), findsNothing);
      // No "— mph": a missing speed is not a speed of nothing.
      expect(find.text('mph'), findsNothing);
    });

    testWidgets('a present value keeps its unit and label',
        (WidgetTester tester) async {
      await tester.pumpWidget(
        _host(const StatTile(label: 'Top speed', value: '68', unit: 'mph')),
      );

      expect(find.text('68'), findsOneWidget);
      expect(find.text('mph'), findsOneWidget);
      expect(find.text('TOP SPEED'), findsOneWidget);
    });
  });
}
