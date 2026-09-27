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
import 'package:tennisimo_ai/models/chart_data.dart';
import 'package:tennisimo_ai/models/dashboard_insights.dart';
import 'package:tennisimo_ai/models/enums.dart';
import 'package:tennisimo_ai/screens/home_shell.dart';
import 'package:tennisimo_ai/screens/results_screen.dart';
import 'package:tennisimo_ai/theme/app_theme.dart';
import 'package:tennisimo_ai/widgets/app_card.dart';
import 'package:tennisimo_ai/widgets/category_bars.dart';
import 'package:tennisimo_ai/widgets/comparison_bars.dart';
import 'package:tennisimo_ai/widgets/content_width.dart';
import 'package:tennisimo_ai/widgets/focus_card.dart';
import 'package:tennisimo_ai/widgets/inline_stats.dart';
import 'package:tennisimo_ai/widgets/mini_trend.dart';
import 'package:tennisimo_ai/widgets/score_line_chart.dart';
import 'package:tennisimo_ai/widgets/score_ring.dart';
import 'package:tennisimo_ai/widgets/section_header.dart';
import 'package:tennisimo_ai/widgets/shot_mix_chart.dart';
import 'package:tennisimo_ai/widgets/shot_type_section.dart';
import 'package:tennisimo_ai/widgets/skill_radar.dart';

import 'support/dashboard_fixtures.dart';

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
/// populated to its widest formatting, all five category panels, the full
/// score chart with dates, and the latest-vs-average bars with a gap in them.
ShotTypeInsight _densest() => ShotTypeInsight(
      shotType: ShotType.backhandTwoHanded,
      scorePoints: const <double?>[61, 70, 88, 74],
      speedPoints: const <double?>[70, 74, 81, 104],
      detailsInspected: 4,
      latestCoverage: const CoverageSummary(available: 11, total: 15),
      sessionDates: <DateTime?>[
        DateTime(2026, 8, 30),
        DateTime(2026, 9, 3),
        DateTime(2026, 9, 12),
        DateTime(2026, 9, 27),
      ],
      newestDetailLoaded: true,
      categoryTrends: <CategoryTrend>[
        _trend('preparation', 'Preparation', const <double?>[55, 60, 72, 68]),
        _trend('swing_path', 'Swing path', const <double?>[40, 44, 51, 49]),
        _trend('contact', 'Contact', const <double?>[80, 78, 84, 88]),
        _trend('follow_through', 'Follow through',
            const <double?>[62, 66, 61, null]),
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
    'the never-recorded shot types',
    () => const ShotTypesNotRecordedCard(shotTypes: <ShotType>[
      ShotType.forehandSlice,
      ShotType.backhandOneHanded,
      ShotType.backhandTwoHanded,
      ShotType.serve,
      ShotType.volley,
    ]),
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

  // The charts, each at its widest content: the longest shot names in a
  // five-slice donut, a radar with two series and two unmeasured axes, bars
  // with both kinds of gap, and a line broken by an unscored clip.
  forEveryViewport(
    'the shot-mix donut with five long-named slices',
    () => AppCard(
      child: ShotMixChart(
        slices: buildShotMix(<ShotTypeInsight>[
          for (final (ShotType, int) s in <(ShotType, int)>[
            (ShotType.backhandTwoHanded, 104),
            (ShotType.backhandOneHanded, 88),
            (ShotType.forehandTopspin, 57),
            (ShotType.forehandSlice, 23),
            (ShotType.serve, 9),
            (ShotType.volley, 2),
          ])
            ShotTypeInsight(
              shotType: s.$1,
              scorePoints: List<double?>.filled(s.$2, null),
              speedPoints: List<double?>.filled(s.$2, null),
              categoryTrends: const <CategoryTrend>[],
              detailsInspected: 0,
              latestCoverage: null,
            ),
        ]),
      ),
    ),
  );

  forEveryViewport(
    'the skill radar with a previous swing and unmeasured axes',
    () => const AppCard(
      child: SkillRadar(
        profile: SkillProfile(
          axes: <RadarAxis>[
            RadarAxis(category: 'preparation', displayName: 'Preparation'),
            RadarAxis(category: 'contact', displayName: 'Contact'),
            RadarAxis(category: 'swing_path', displayName: 'Swing path'),
            RadarAxis(category: 'balance', displayName: 'Balance'),
            RadarAxis(
                category: 'follow_through', displayName: 'Follow through'),
          ],
          current: RadarSeries(
            label: 'This swing',
            values: <double?>[100, null, 48, 74, null],
          ),
          previous: RadarSeries(
            label: 'Previous swing',
            values: <double?>[70, 60, 44, 72, 90],
          ),
        ),
      ),
    ),
  );

  forEveryViewport(
    'the latest-vs-average bars with gaps',
    () => const AppCard(
      child: ComparisonBars(
        earlierClips: 11,
        rows: <CategoryComparison>[
          CategoryComparison(
            category: 'follow_through',
            displayName: 'Follow through',
            current: 100,
            average: 99.5,
            averageOf: 11,
          ),
          CategoryComparison(
            category: 'contact',
            displayName: 'Contact',
            current: null,
            average: 71,
            averageOf: 3,
          ),
          CategoryComparison(
            category: 'swing_path',
            displayName: 'Swing path',
            current: 48,
            average: null,
            averageOf: 0,
          ),
        ],
      ),
    ),
  );

  forEveryViewport(
    'the score line chart broken by an unscored clip',
    () => AppCard(
      child: ScoreLineChart(
        title: 'Overall score',
        points: const <double?>[61, null, 88, 100, 74],
        dates: <DateTime?>[
          DateTime(2026, 8, 30),
          DateTime(2026, 9, 3),
          DateTime(2026, 9, 12),
          DateTime(2026, 9, 20),
          DateTime(2026, 9, 27),
        ],
      ),
    ),
  );

  _painterGeometryTests();
  _resultsScreenTests();

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

/// The whole results screen, scrolled end to end, with the measurements
/// expander both closed (the default) and open. The list is lazy, so every
/// screenful is built and checked in turn rather than just the first.
void _resultsScreenTests() {
  Future<void> scrollToEnd(WidgetTester tester) async {
    final Finder list = find.byType(Scrollable).first;
    for (int i = 0; i < 200; i++) {
      final ScrollableState state = tester.state(list);
      if (state.position.pixels >= state.position.maxScrollExtent) break;
      await tester.drag(list, const Offset(0, -300));
      await tester.pump();
      expect(tester.takeException(), isNull);
    }
  }

  for (final bool expanded in <bool>[false, true]) {
    final String state = expanded ? 'expanded' : 'collapsed';
    for (final MapEntry<String, Size> viewport in kViewports.entries) {
      for (final double scale in kTextScales) {
        testWidgets(
            'the results screen, measurements $state — '
            '${viewport.key} @ ${scale}x', (WidgetTester tester) async {
          tester.view.physicalSize = viewport.value;
          tester.view.devicePixelRatio = 1.0;
          addTearDown(tester.view.reset);
          // The densest fixture swing: feedback with strengths and three
          // improvements, ball speed, five categories, and null metrics.
          final AnalysisResponse analysis =
              forehandOnly().source.details['fh0']!;

          await tester.pumpWidget(MaterialApp(
            theme: buildAppTheme(),
            home: Builder(
              builder: (BuildContext context) => MediaQuery(
                data: MediaQuery.of(context)
                    .copyWith(textScaler: TextScaler.linear(scale)),
                child: ResultsScreen(analysis: analysis),
              ),
            ),
          ));
          await tester.pump();
          expect(tester.takeException(), isNull);

          if (expanded) {
            final Finder toggle = find.text('See all measurements');
            await tester.scrollUntilVisible(toggle, 300,
                scrollable: find.byType(Scrollable).first);
            await tester.ensureVisible(toggle);
            await tester.pumpAndSettle();
            await tester.tap(toggle);
            await tester.pumpAndSettle();
            expect(tester.takeException(), isNull);
            expect(find.text('Shoulder turn'), findsOneWidget);
          }

          await scrollToEnd(tester);
        });
      }
    }
  }
}
