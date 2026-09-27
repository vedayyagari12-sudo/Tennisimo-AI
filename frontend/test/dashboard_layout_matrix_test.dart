/// Every dense dashboard block, mounted at real phone dimensions and at
/// accessibility text sizes.
///
/// The dashboard cannot be mounted whole in a test: [DashboardScreen] reads
/// `Supabase.instance` for the account header, and there is no session here.
/// So the blocks it composes are mounted individually, inside the same
/// scaffold padding the real screen gives them, across the size and text-scale
/// matrix. A `RenderFlex` overflow is an exception, and an exception is a
/// failure, which makes "does this layout hold at 320dp and 2.0x text" a
/// deterministic question rather than an eyeballed one.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/models/analysis_response.dart';
import 'package:tennisimo_ai/models/dashboard_insights.dart';
import 'package:tennisimo_ai/models/enums.dart';
import 'package:tennisimo_ai/screens/home_shell.dart';
import 'package:tennisimo_ai/theme/app_theme.dart';
import 'package:tennisimo_ai/widgets/app_card.dart';
import 'package:tennisimo_ai/widgets/category_bars.dart';
import 'package:tennisimo_ai/widgets/content_width.dart';
import 'package:tennisimo_ai/widgets/focus_card.dart';
import 'package:tennisimo_ai/widgets/inline_stats.dart';
import 'package:tennisimo_ai/widgets/mini_trend.dart';
import 'package:tennisimo_ai/widgets/score_ring.dart';
import 'package:tennisimo_ai/widgets/section_header.dart';
import 'package:tennisimo_ai/widgets/shot_type_section.dart';

/// Logical sizes, named. The physical size is derived with a DPR of 1 so the
/// numbers below ARE logical pixels.
const Map<String, Size> kViewports = <String, Size>{
  '320x640 small phone': Size(320, 640),
  '360x800 common Android': Size(360, 800),
  '412x915 Pixel 7': Size(412, 915),
  '834x1112 tablet': Size(834, 1112),
};

/// 1.0 is the default; 1.5 and 2.0 are the two accessibility steps Android's
/// font-size slider actually reaches.
const List<double> kTextScales = <double>[1.0, 1.5, 2.0];

/// Mounts [child] exactly as the dashboard's `ListView` does: inside the
/// screen's content-width clamp and its tab padding.
Future<void> pumpBlock(
  WidgetTester tester,
  Widget child, {
  required Size size,
  required double textScale,
}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);

  await tester.pumpWidget(MaterialApp(
    theme: buildAppTheme(),
    home: Builder(
      builder: (BuildContext context) => MediaQuery(
        data: MediaQuery.of(context)
            .copyWith(textScaler: TextScaler.linear(textScale)),
        child: Scaffold(
          body: ContentWidth(
            child: ListView(
              padding: kTabContentPadding,
              children: <Widget>[child],
            ),
          ),
        ),
      ),
    ),
  ));
  await tester.pump();
}

CategoryTrend _trend(String name, String display, List<double?> points) =>
    CategoryTrend(category: name, displayName: display, points: points);

/// The worst case the screen can produce: the longest shot label, every stat
/// populated to its widest formatting, all five category panels.
ShotTypeInsight _densest() => ShotTypeInsight(
      shotType: ShotType.backhandTwoHanded,
      scorePoints: const <double?>[61, 70, 88, 74],
      speedPoints: const <double?>[70, 74, 81, 104],
      detailsInspected: 4,
      latestCoverage: const CoverageSummary(available: 11, total: 15),
      categoryTrends: <CategoryTrend>[
        _trend('preparation', 'Preparation', const <double?>[55, 60, 72, 68]),
        _trend('swing_path', 'Swing path', const <double?>[40, 44, 51, 49]),
        _trend('contact', 'Contact', const <double?>[80, 78, 84, 88]),
        _trend(
            'follow_through', 'Follow through', const <double?>[62, 66, 61, 70]),
        _trend('balance', 'Balance', const <double?>[71, 69, 75, 77]),
      ],
    );

List<CategoryScore> _categories() => <CategoryScore>[
      const CategoryScore(
        category: 'preparation',
        score: 74,
        weight: 0.25,
        metricNames: <String>['shoulder_turn_deg'],
        metricsAvailable: 3,
        metricsTotal: 3,
      ),
      const CategoryScore(
        category: 'follow_through',
        score: null,
        weight: 0.15,
        metricNames: <String>['finish_height'],
        metricsAvailable: 0,
        metricsTotal: 4,
      ),
    ];

void main() {
  /// Runs [body] over every viewport and every text scale.
  void forEveryViewport(
    String description,
    Widget Function() build,
  ) {
    for (final MapEntry<String, Size> viewport in kViewports.entries) {
      for (final double scale in kTextScales) {
        testWidgets('$description — ${viewport.key} @ ${scale}x',
            (WidgetTester tester) async {
          await pumpBlock(
            tester,
            build(),
            size: viewport.value,
            textScale: scale,
          );
          expect(tester.takeException(), isNull);
        });
      }
    }
  }

  forEveryViewport(
    'the densest shot-type section',
    () => ShotTypeSection(insight: _densest()),
  );

  forEveryViewport(
    'the focus card',
    () => FocusCard(insight: _densest()),
  );

  forEveryViewport(
    'the five-stat inline row',
    () => const AppCard(
      child: InlineStats(stats: <InlineStat>[
        InlineStat(label: 'Clips', value: '12'),
        InlineStat(label: 'Shot types', value: '5'),
        InlineStat(label: 'Top speed', value: '104', unit: 'mph'),
        InlineStat(label: 'Latest coverage', value: '11/15'),
      ]),
    ),
  );

  forEveryViewport(
    'the latest breakdown bars',
    () => AppCard(child: CategoryBars(categories: _categories())),
  );

  forEveryViewport(
    'a never-recorded shot type',
    () => const ShotTypeBlankCard(shotType: ShotType.backhandOneHanded),
  );

  forEveryViewport(
    'the section header with its action',
    () => SectionHeader(
      title: 'Recent sessions',
      actionLabel: 'See all',
      onAction: () {},
    ),
  );

  forEveryViewport(
    'the hero score ring beside its caption',
    () => AppCard(
      child: Row(
        children: <Widget>[
          const ScoreRing(score: 88, delta: 4.2, label: 'Overall', size: 132),
          const SizedBox(width: AppSpacing.lg),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                const Text('LATEST SESSION'),
                const SizedBox(height: AppSpacing.sm),
                const Text('Backhand (two-handed)'),
                const SizedBox(height: AppSpacing.sm),
                const Text('2 hours ago'),
              ],
            ),
          ),
        ],
      ),
    ),
  );

  _painterGeometryTests();

  forEveryViewport(
    'a mini trend on its own',
    () => const AppCard(
      child: MiniTrend(
        title: 'Follow through over your last 4 clips',
        points: <double?>[62, null, 66, 70],
      ),
    ),
  );
}

/// The sparkline's own geometry, which no overflow check can see: a marker is
/// painted, not laid out, so clipping it costs nothing at layout time and is
/// only visible on a device.
class _RecordingCanvas implements Canvas {
  final List<({Offset centre, double radius})> circles =
      <({Offset centre, double radius})>[];

  @override
  void drawCircle(Offset c, double radius, Paint paint) =>
      circles.add((centre: c, radius: radius));

  @override
  void noSuchMethod(Invocation invocation) {}
}

void _painterGeometryTests() {
  test('the end marker sits wholly inside the panel on both axes', () {
    const Size panel = Size(180, 44);
    final _RecordingCanvas canvas = _RecordingCanvas();
    // A rising series, so the marked newest point is the series MAXIMUM and
    // sits as high in the panel as a point can.
    MiniTrendPainter(
      values: const <double>[10, 40, 90],
      selected: null,
      lineColor: const Color(0xFF000000),
      baselineColor: const Color(0xFF000000),
      dotFill: const Color(0xFF000000),
    ).paint(canvas, panel);

    expect(canvas.circles, isNotEmpty);
    for (final ({Offset centre, double radius}) c in canvas.circles) {
      // 5px of fill with a 2px stroke centred on that edge is 6px of ink.
      const double ink = 6;
      expect(c.centre.dy - ink, greaterThanOrEqualTo(0));
      expect(c.centre.dy + ink, lessThanOrEqualTo(panel.height));
      expect(c.centre.dx - ink, greaterThanOrEqualTo(0));
      expect(c.centre.dx + ink, lessThanOrEqualTo(panel.width));
    }
  });
}
