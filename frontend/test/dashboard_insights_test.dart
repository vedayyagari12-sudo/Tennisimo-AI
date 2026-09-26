/// The dashboard's aggregation layer.
///
/// These are pure functions over already-parsed payloads, so every case is
/// reachable without a server. The cases that matter most are the ones the
/// screen would otherwise fake: a null score, a single data point, a shot type
/// with sessions and no score at all, and an empty history.
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/models/analysis_response.dart';
import 'package:tennisimo_ai/models/dashboard_insights.dart';
import 'package:tennisimo_ai/models/enums.dart';
import 'package:tennisimo_ai/models/metric_score.dart';

AnalysisSummary _summary({
  required String id,
  ShotType shotType = ShotType.forehandTopspin,
  double? score,
  int? mph,
}) =>
    AnalysisSummary(
      analysisId: id,
      createdAt: null,
      shotType: shotType,
      overallScore: score,
      ballSpeedMph: mph,
    );

CategoryScore _category(String name, double? score,
        {int available = 3, int total = 3}) =>
    CategoryScore(
      category: name,
      score: score,
      weight: 0.2,
      metricNames: const <String>['a'],
      metricsAvailable: available,
      metricsTotal: total,
    );

AnalysisResponse _analysis({
  required String id,
  ShotType shotType = ShotType.forehandTopspin,
  List<CategoryScore> categories = const <CategoryScore>[],
}) =>
    AnalysisResponse(
      analysisId: id,
      createdAt: null,
      status: AnalysisStatus.complete,
      pipelineVersion: 'v1',
      shotType: shotType,
      shotTypeConfidence: null,
      overallScore: null,
      categories: categories,
      metrics: const <MetricScore>[],
      ballSpeed: null,
      feedback: null,
      warnings: const <String>[],
    );

void main() {
  group('CategoryTrend', () {
    test('nulls are kept as gaps and never counted as zeros', () {
      const CategoryTrend trend = CategoryTrend(
        category: 'balance',
        displayName: 'Balance',
        points: <double?>[null, 60, null, 70],
      );

      expect(trend.points, hasLength(4));
      expect(trend.measured, <double>[60, 70]);
      expect(trend.earliest, 60);
      expect(trend.latest, 70);
      expect(trend.delta, 10);
      expect(trend.direction, TrendDirection.improving);
    });

    test('one measured point is not a trend', () {
      const CategoryTrend trend = CategoryTrend(
        category: 'contact',
        displayName: 'Contact',
        points: <double?>[null, 55, null],
      );

      expect(trend.latest, 55);
      expect(trend.delta, isNull);
      expect(trend.direction, TrendDirection.unknown);
    });

    test('no measured point at all reports nothing rather than zero', () {
      const CategoryTrend trend = CategoryTrend(
        category: 'follow_through',
        displayName: 'Follow through',
        points: <double?>[null, null, null],
      );

      expect(trend.measured, isEmpty);
      expect(trend.latest, isNull);
      expect(trend.earliest, isNull);
      expect(trend.delta, isNull);
      expect(trend.direction, TrendDirection.unknown);
    });

    test('a move smaller than the steady band is not called a move', () {
      CategoryTrend at(double from, double to) => CategoryTrend(
            category: 'swing_path',
            displayName: 'Swing path',
            points: <double?>[from, to],
          );

      expect(at(60, 61.9).direction, TrendDirection.steady);
      expect(at(60, 58.1).direction, TrendDirection.steady);
      expect(at(60, 62.0).direction, TrendDirection.improving);
      expect(at(60, 58.0).direction, TrendDirection.declining);
    });

    test('a real measured zero is a point, not an absence', () {
      // 0.0 is a measurement. It plots, it counts, and it is the latest value.
      const CategoryTrend trend = CategoryTrend(
        category: 'balance',
        displayName: 'Balance',
        points: <double?>[40, 0],
      );

      expect(trend.measured, <double>[40, 0]);
      expect(trend.latest, 0);
      expect(trend.direction, TrendDirection.declining);
    });
  });

  group('ConsistencySummary', () {
    test('one score has an unknown spread, not a spread of zero', () {
      const ConsistencySummary c = ConsistencySummary(scores: <double>[70]);
      expect(c.sampleSize, 1);
      expect(c.spread, isNull);
      expect(c.standardDeviation, isNull);
      expect(c.band, isNull);
    });

    test('no scores at all is also unknown', () {
      const ConsistencySummary c = ConsistencySummary(scores: <double>[]);
      expect(c.spread, isNull);
      expect(c.band, isNull);
    });

    test('spread, deviation and band on a real run', () {
      const ConsistencySummary c =
          ConsistencySummary(scores: <double>[60, 70, 80]);
      expect(c.spread, 20);
      expect(c.standardDeviation, closeTo(8.165, 0.001));
      expect(c.band, ConsistencyBand.moderate);
    });

    test('the band cuts fall on the documented side', () {
      ConsistencyBand? bandFor(double lo, double hi) =>
          ConsistencySummary(scores: <double>[lo, hi]).band;

      expect(bandFor(60, 67.9), ConsistencyBand.tight);
      expect(bandFor(60, 68), ConsistencyBand.moderate);
      expect(bandFor(60, 80), ConsistencyBand.moderate);
      expect(bandFor(60, 80.1), ConsistencyBand.wide);
    });
  });

  group('CoverageSummary', () {
    test('an analysis declaring no metrics has no ratio', () {
      const CoverageSummary c = CoverageSummary(available: 0, total: 0);
      expect(c.ratio, isNull);
      expect(c.hasGaps, isFalse);
    });

    test('gaps are reported as a fraction, never as a percentage of zero', () {
      const CoverageSummary c = CoverageSummary(available: 9, total: 15);
      expect(c.ratio, closeTo(0.6, 1e-9));
      expect(c.hasGaps, isTrue);
    });

    test('coverageOf sums the categories of one analysis', () {
      final AnalysisResponse analysis = _analysis(
        id: 'a',
        categories: <CategoryScore>[
          _category('preparation', 70, available: 3, total: 3),
          _category('contact', null, available: 0, total: 4),
        ],
      );
      final CoverageSummary c = coverageOf(analysis);
      expect(c.available, 3);
      expect(c.total, 7);
      expect(c.hasGaps, isTrue);
    });
  });

  group('buildCategoryTrends', () {
    test('no analyses gives no series at all', () {
      expect(buildCategoryTrends(const <AnalysisResponse>[]), isEmpty);
    });

    test('a category missing from one clip contributes a gap, not a zero', () {
      final List<CategoryTrend> trends = buildCategoryTrends(<AnalysisResponse>[
        _analysis(id: 'old', categories: <CategoryScore>[
          _category('preparation', 60),
        ]),
        _analysis(id: 'new', categories: <CategoryScore>[
          _category('preparation', 68),
          _category('contact', null),
        ]),
      ]);

      final CategoryTrend preparation =
          trends.firstWhere((CategoryTrend t) => t.category == 'preparation');
      final CategoryTrend contact =
          trends.firstWhere((CategoryTrend t) => t.category == 'contact');

      expect(preparation.points, <double?>[60, 68]);
      // Absent from the older clip, unmeasured in the newer one: two gaps.
      expect(contact.points, <double?>[null, null]);
      expect(contact.latest, isNull);
    });

    test('the newest clip decides the order the categories appear in', () {
      final List<CategoryTrend> trends = buildCategoryTrends(<AnalysisResponse>[
        _analysis(id: 'old', categories: <CategoryScore>[
          _category('balance', 50),
        ]),
        _analysis(id: 'new', categories: <CategoryScore>[
          _category('preparation', 60),
          _category('balance', 55),
        ]),
      ]);

      expect(
        trends.map((CategoryTrend t) => t.category),
        <String>['preparation', 'balance'],
      );
    });

    test('displayName is humanised straight off the model', () {
      final List<CategoryTrend> trends = buildCategoryTrends(<AnalysisResponse>[
        _analysis(id: 'a', categories: <CategoryScore>[
          _category('follow_through', 62),
        ]),
      ]);
      expect(trends.single.displayName, 'Follow through');
    });
  });

  group('detailIdsToFetch', () {
    test('an empty history asks for nothing', () {
      expect(detailIdsToFetch(const <AnalysisSummary>[]), isEmpty);
    });

    test('the budget is a hard ceiling however long the history is', () {
      // Every shot type, several times over: the unbudgeted policy would want
      // 6 recent + 3 per shot type across 7 types = far more than 12.
      final List<AnalysisSummary> history = <AnalysisSummary>[
        for (int i = 0; i < 10; i++)
          for (final ShotType type in ShotType.values)
            _summary(id: '$type-$i', shotType: type),
      ];
      expect(detailIdsToFetch(history, budget: 12), hasLength(12));
    });

    test('it asks for no more than the policy wants, budget or no budget', () {
      // One shot type, 50 clips: 6 recent plus rounds 2 and 3 of that type is
      // 6 requests, not 12. The budget is a ceiling, never a quota to fill.
      final List<AnalysisSummary> history = <AnalysisSummary>[
        for (int i = 0; i < 50; i++) _summary(id: 'a$i'),
      ];
      expect(detailIdsToFetch(history, budget: 12), hasLength(6));
    });

    test('every shot type gets at least its newest clip, however old', () {
      // A single ancient volley sits below 40 newer forehands. A plain
      // newest-first union truncated to the budget would drop it and leave the
      // volley section empty for no reason.
      final List<AnalysisSummary> history = <AnalysisSummary>[
        for (int i = 0; i < 40; i++) _summary(id: 'fh$i'),
        _summary(id: 'volley', shotType: ShotType.volley),
      ];

      final List<String> ids = detailIdsToFetch(history, budget: 12);
      expect(ids, contains('volley'));
      expect(ids, contains('fh0'));
      // 1 volley + the 6 newest forehands + rounds 2 and 3 of the forehand.
      expect(ids, hasLength(7));
    });

    test('ids are unique and the newest clip is always in', () {
      final List<AnalysisSummary> history = <AnalysisSummary>[
        _summary(id: 'newest'),
        _summary(id: 'b', shotType: ShotType.serve),
        _summary(id: 'c'),
      ];
      final List<String> ids = detailIdsToFetch(history);
      expect(ids.toSet().length, ids.length);
      expect(ids.first, 'newest');
      expect(ids, containsAll(<String>['newest', 'b', 'c']));
    });
  });

  group('buildDashboardInsights', () {
    test('an empty history yields nothing to show and no fake zeros', () {
      final DashboardInsights insights = buildDashboardInsights(
        history: const <AnalysisSummary>[],
        details: const <AnalysisResponse>[],
      );

      expect(insights.shotTypes, isEmpty);
      expect(insights.totalSessions, 0);
      expect(insights.topSpeedMph, isNull);
      expect(insights.latestShotType, isNull);
      expect(insights.latestCoverage, isNull);
      expect(insights.analysesInspected, 0);
      // Every real shot type is offered as "not recorded"; `unknown` is not a
      // shot anybody can go and play.
      expect(insights.notRecorded, isNot(contains(ShotType.unknown)));
      expect(insights.notRecorded, contains(ShotType.forehandTopspin));
      expect(insights.notRecorded, contains(ShotType.serve));
    });

    test('a recorded shot type leaves the not-recorded list', () {
      final DashboardInsights insights = buildDashboardInsights(
        history: <AnalysisSummary>[_summary(id: 'a', score: 70)],
        details: const <AnalysisResponse>[],
      );

      expect(insights.notRecorded, isNot(contains(ShotType.forehandTopspin)));
      expect(insights.notRecorded, contains(ShotType.volley));
      expect(insights.shotTypesRecorded, 1);
    });

    test('shot types are never averaged together', () {
      // A 90 forehand and a 30 serve must not produce a 60 of anything.
      final DashboardInsights insights = buildDashboardInsights(
        history: <AnalysisSummary>[
          _summary(id: 'fh', score: 90),
          _summary(id: 'sv', shotType: ShotType.serve, score: 30),
        ],
        details: const <AnalysisResponse>[],
      );

      final ShotTypeInsight forehand = insights.shotTypes
          .firstWhere((ShotTypeInsight i) => i.shotType == ShotType.forehandTopspin);
      final ShotTypeInsight serve = insights.shotTypes
          .firstWhere((ShotTypeInsight i) => i.shotType == ShotType.serve);

      expect(forehand.averageScore, 90);
      expect(serve.averageScore, 30);
      // The overall figures are counts and one record — no cross-shot mean is
      // exposed at all.
      expect(insights.totalSessions, 2);
      expect(insights.shotTypesRecorded, 2);
    });

    test('a shot type with sessions and no score says so rather than zeroing',
        () {
      // The documented serve/volley case: clips exist, an overall score does
      // not. Best, average and spread are all "unknown", never 0.
      final DashboardInsights insights = buildDashboardInsights(
        history: <AnalysisSummary>[
          _summary(id: 's1', shotType: ShotType.serve, mph: 88),
          _summary(id: 's2', shotType: ShotType.serve),
        ],
        details: const <AnalysisResponse>[],
      );

      final ShotTypeInsight serve = insights.shotTypes.single;
      expect(serve.sessions, 2);
      expect(serve.hasNoScoreAtAll, isTrue);
      expect(serve.bestScore, isNull);
      expect(serve.averageScore, isNull);
      expect(serve.consistency.spread, isNull);
      expect(serve.latestDelta, isNull);
      // And what WAS measured is still there.
      expect(serve.topSpeed, 88);
      expect(insights.topSpeedMph, 88);
    });

    test('score points are chronological with their gaps intact', () {
      // History arrives newest first; the series must read oldest first.
      final DashboardInsights insights = buildDashboardInsights(
        history: <AnalysisSummary>[
          _summary(id: 'c', score: 72),
          _summary(id: 'b'),
          _summary(id: 'a', score: 64),
        ],
        details: const <AnalysisResponse>[],
      );

      final ShotTypeInsight forehand = insights.shotTypes.single;
      expect(forehand.scorePoints, <double?>[64, null, 72]);
      expect(forehand.scores, <double>[64, 72]);
      // Previous clip was unscored, so there is no delta to claim.
      expect(forehand.latestDelta, isNull);
    });

    test('the latest delta compares like with like', () {
      final DashboardInsights insights = buildDashboardInsights(
        history: <AnalysisSummary>[
          _summary(id: 'fh2', score: 75),
          _summary(id: 'sv', shotType: ShotType.serve, score: 10),
          _summary(id: 'fh1', score: 70),
        ],
        details: const <AnalysisResponse>[],
      );

      // 75 against the previous FOREHAND (70), not against the serve between
      // them.
      expect(insights.latestShotType?.shotType, ShotType.forehandTopspin);
      expect(insights.latestShotType?.latestDelta, closeTo(5, 1e-9));
    });

    test('details are grouped by their own shot type', () {
      final DashboardInsights insights = buildDashboardInsights(
        history: <AnalysisSummary>[
          _summary(id: 'fh', score: 70),
          _summary(id: 'sv', shotType: ShotType.serve),
        ],
        details: <AnalysisResponse>[
          _analysis(id: 'fh', categories: <CategoryScore>[
            _category('preparation', 70),
          ]),
          _analysis(
            id: 'sv',
            shotType: ShotType.serve,
            categories: <CategoryScore>[
              _category('preparation', 55),
              _category('contact', null, available: 0, total: 4),
            ],
          ),
        ],
      );

      final ShotTypeInsight serve = insights.shotTypes
          .firstWhere((ShotTypeInsight i) => i.shotType == ShotType.serve);
      expect(serve.detailsInspected, 1);
      expect(serve.categoryTrends, hasLength(2));
      expect(serve.latestCoverage?.available, 3);
      expect(serve.latestCoverage?.total, 7);
      // Contact is unmeasured for this shot type: it is not the focus, because
      // there is no number to be weakest.
      expect(serve.focus?.category, 'preparation');
    });

    test('the focus is the lowest MEASURED category', () {
      final DashboardInsights insights = buildDashboardInsights(
        history: <AnalysisSummary>[_summary(id: 'a', score: 60)],
        details: <AnalysisResponse>[
          _analysis(id: 'a', categories: <CategoryScore>[
            _category('preparation', 80),
            _category('balance', 41),
            _category('contact', null),
          ]),
        ],
      );

      expect(insights.latestShotType?.focus?.category, 'balance');
      expect(insights.latestShotType?.focus?.latest, 41);
    });

    test('nothing measurable means no focus at all, not an arbitrary one', () {
      final DashboardInsights insights = buildDashboardInsights(
        history: <AnalysisSummary>[_summary(id: 'a')],
        details: <AnalysisResponse>[
          _analysis(id: 'a', categories: <CategoryScore>[
            _category('preparation', null),
            _category('balance', null),
          ]),
        ],
      );

      expect(insights.latestShotType?.focus, isNull);
    });

    test('no ball speed anywhere is null, never 0 mph', () {
      final DashboardInsights insights = buildDashboardInsights(
        history: <AnalysisSummary>[_summary(id: 'a', score: 70)],
        details: const <AnalysisResponse>[],
      );

      expect(insights.topSpeedMph, isNull);
      expect(insights.shotTypes.single.topSpeed, isNull);
      expect(insights.shotTypes.single.speeds, isEmpty);
    });

    test('shot types are ordered by how much data each has', () {
      final DashboardInsights insights = buildDashboardInsights(
        history: <AnalysisSummary>[
          _summary(id: 'v', shotType: ShotType.volley),
          _summary(id: 'f1'),
          _summary(id: 'f2'),
        ],
        details: const <AnalysisResponse>[],
      );

      expect(
        insights.shotTypes.map((ShotTypeInsight i) => i.shotType),
        <ShotType>[ShotType.forehandTopspin, ShotType.volley],
      );
    });
  });
}
