/// The pipeline-version rule: history stays visible, comparisons never cross
/// a scoring change.
///
/// Pure functions only — `version_segments.dart`, and the places in
/// `dashboard_insights.dart` and `chart_data.dart` that apply it — so every
/// case runs without a widget or a server: mixed versions, all-null (an old
/// backend), all-same, a boundary that leaves fewer than two comparable
/// clips, and a boundary in the middle of one shot type's history.
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/models/analysis_response.dart';
import 'package:tennisimo_ai/models/chart_data.dart';
import 'package:tennisimo_ai/models/dashboard_insights.dart';
import 'package:tennisimo_ai/models/enums.dart';
import 'package:tennisimo_ai/models/version_segments.dart';

import 'support/dashboard_fixtures.dart';

AnalysisSummary _row(
  String id, {
  String? version,
  double? score,
  ShotType shot = ShotType.forehandTopspin,
  int? mph,
}) => AnalysisSummary(
  analysisId: id,
  createdAt: null,
  shotType: shot,
  overallScore: score,
  ballSpeedMph: mph,
  pipelineVersion: version,
);

/// A full analysis whose own `pipelineVersion` is [detailVersion] — which may
/// deliberately disagree with the list, to prove the list wins.
AnalysisResponse _detail(
  String id, {
  double? path,
  ShotType shot = ShotType.forehandTopspin,
  String detailVersion = 'v1',
}) => fixtureAnalysis(
  id: id,
  createdAt: DateTime(2026, 9, 20),
  shot: shot,
  overall: 60,
  categories: <String, double?>{
    'preparation': 70,
    'contact': 72,
    'swing_path': path,
    'balance': 68,
    'follow_through': 80,
  },
  pipelineVersion: detailVersion,
);

CategoryTrend _pathTrend(ShotTypeInsight insight) => insight.categoryTrends
    .firstWhere((CategoryTrend t) => t.category == 'swing_path');

void main() {
  group('versionBoundariesOf', () {
    test('mixed versions break where the version changes', () {
      expect(versionBoundariesOf(<String?>['v2', 'v2', 'v3', 'v3'], 4), <int>[
        2,
      ]);
      expect(versionBoundariesOf(<String?>['v1', 'v2', 'v2', 'v3'], 4), <int>[
        1,
        3,
      ]);
    });

    test('all-null is one unknown version: no boundary is invented', () {
      expect(versionBoundariesOf(<String?>[null, null, null], 3), isEmpty);
      // A series built without versions at all reads the same way.
      expect(versionBoundariesOf(const <String?>[], 5), isEmpty);
    });

    test('all-same has no boundary', () {
      expect(versionBoundariesOf(<String?>['v3', 'v3', 'v3'], 3), isEmpty);
    });

    test('a null beside a known version is a boundary', () {
      expect(versionBoundariesOf(<String?>[null, 'v3'], 2), <int>[1]);
    });

    test('empty and single-clip series have nothing to break', () {
      expect(versionBoundariesOf(const <String?>[], 0), isEmpty);
      expect(versionBoundariesOf(<String?>['v3'], 1), isEmpty);
    });
  });

  group('comparableIndices / comparableOnly', () {
    test('keeps only clips on the newest clip\'s version, in order', () {
      final List<String?> v = <String?>['v2', 'v3', 'v2', 'v3'];
      expect(comparableIndices(v, 4), <int>[1, 3]);
      expect(comparableOnly(<double?>[10, 20, 30, null], v), <double?>[
        20,
        null,
      ]);
    });

    test('all-null keeps everything, exactly as before versions existed', () {
      expect(
        comparableOnly(<double?>[1, 2, 3], <String?>[null, null, null]),
        <double?>[1, 2, 3],
      );
      expect(comparableOnly(<double?>[1, 2, 3], const <String?>[]), <double?>[
        1,
        2,
        3,
      ]);
    });

    test('nothing to keep from an empty series', () {
      expect(comparableIndices(const <String?>[], 0), isEmpty);
    });
  });

  group('AnalysisSummary.pipelineVersion', () {
    test('is read from the list item', () {
      final AnalysisSummary s = AnalysisSummary.fromJson(<String, dynamic>{
        'id': 'a',
        'shot_type': 'serve',
        'pipeline_version': 'v3',
      });
      expect(s.pipelineVersion, 'v3');
    });

    test('is null, not defaulted, when an older backend omits it', () {
      final AnalysisSummary s = AnalysisSummary.fromJson(<String, dynamic>{
        'id': 'a',
        'shot_type': 'serve',
      });
      expect(s.pipelineVersion, isNull);
    });
  });

  group('buildDashboardInsights across a scoring change', () {
    test('a boundary in the middle of a shot type\'s history', () {
      // Oldest first: 40 (v2), 45 (v2), 70 (v3), 73 (v3). Across the line
      // that would read "Improving +28" off 45 -> 73.
      final DashboardInsights insights = buildDashboardInsights(
        history: <AnalysisSummary>[
          _row('d', version: 'v3', score: 73, mph: 60),
          _row('c', version: 'v3', score: 70),
          _row('b', version: 'v2', score: 45, mph: 70),
          _row('a', version: 'v2', score: 40),
        ],
        details: <AnalysisResponse>[
          _detail('d', path: 50),
          _detail('c', path: 44),
          _detail('b', path: 90),
          _detail('a', path: 88),
        ],
      );
      final ShotTypeInsight fh = insights.shotTypes.single;

      // History stays visible: every clip, with the break marked.
      expect(fh.sessions, 4);
      expect(fh.scorePoints, <double?>[40, 45, 70, 73]);
      expect(fh.versionBoundaries, <int>[2]);
      expect(fh.hasVersionBoundary, isTrue);

      // Every comparison stays on v3.
      expect(fh.comparableScores, <double>[70, 73]);
      expect(fh.bestScore, 73);
      expect(fh.averageScore, closeTo(71.5, 1e-9));
      expect(fh.consistency.spread, 3);
      expect(fh.latestDelta, closeTo(3, 1e-9));
      expect(fh.detailsInspected, 2);
      expect(_pathTrend(fh).points, <double?>[44, 50]);
      expect(_pathTrend(fh).direction, TrendDirection.improving);

      // Ball speed is a physical measurement, not a score: not gated.
      expect(fh.topSpeed, 70);
      expect(insights.topSpeedMph, 70);
    });

    test('a boundary leaving fewer than two clips falls back honestly', () {
      final DashboardInsights insights = buildDashboardInsights(
        history: <AnalysisSummary>[
          _row('c', version: 'v3', score: 80),
          _row('b', version: 'v2', score: 40),
          _row('a', version: 'v2', score: 38),
        ],
        details: <AnalysisResponse>[
          _detail('c', path: 70),
          _detail('b', path: 20),
        ],
      );
      final ShotTypeInsight fh = insights.shotTypes.single;

      expect(fh.scorePoints, <double?>[38, 40, 80]);
      expect(fh.versionBoundaries, <int>[2]);
      // One comparable clip: no delta, no direction, no spread. Not +40.
      expect(fh.latestDelta, isNull);
      expect(directionOf(fh.latestDelta), TrendDirection.unknown);
      expect(fh.consistency.spread, isNull);
      expect(fh.bestScore, 80);
      // The hero reads the same number.
      expect(insights.latestShotType?.latestDelta, isNull);
      // One category point: the "need 2 for a trend" case, not a direction.
      expect(fh.detailsInspected, 1);
      expect(_pathTrend(fh).points, <double?>[70]);
      expect(_pathTrend(fh).direction, TrendDirection.unknown);
      expect(fh.focus?.direction, TrendDirection.unknown);
      expect(buildCategoryComparison(fh.categoryTrends), isEmpty);
    });

    test('all-null versions behave exactly as before', () {
      final DashboardInsights insights = buildDashboardInsights(
        history: <AnalysisSummary>[
          _row('c', score: 80),
          _row('b', score: 40),
          _row('a', score: 38),
        ],
        // The details carry their own (different) versions; the list has
        // none, so the list's "unknown" is what counts.
        details: <AnalysisResponse>[
          _detail('c', path: 70, detailVersion: 'v3'),
          _detail('b', path: 20, detailVersion: 'v2'),
        ],
      );
      final ShotTypeInsight fh = insights.shotTypes.single;

      expect(fh.versionBoundaries, isEmpty);
      expect(fh.comparableScores, <double>[38, 40, 80]);
      expect(fh.latestDelta, closeTo(40, 1e-9));
      expect(fh.bestScore, 80);
      expect(fh.consistency.spread, 42);
      expect(fh.detailsInspected, 2);
      expect(_pathTrend(fh).points, <double?>[20, 70]);
    });

    test('all-same versions behave exactly as all-null', () {
      final DashboardInsights insights = buildDashboardInsights(
        history: <AnalysisSummary>[
          _row('b', version: 'v3', score: 64),
          _row('a', version: 'v3', score: 60),
        ],
        details: <AnalysisResponse>[
          _detail('b', path: 50),
          _detail('a', path: 46),
        ],
      );
      final ShotTypeInsight fh = insights.shotTypes.single;

      expect(fh.versionBoundaries, isEmpty);
      expect(fh.latestDelta, closeTo(4, 1e-9));
      expect(fh.averageScore, 62);
      expect(fh.detailsInspected, 2);
    });

    test('each shot type compares within its OWN newest version', () {
      // The backhand predates the change entirely: it compares within v2,
      // and the forehand's boundary does not touch it.
      final DashboardInsights insights = buildDashboardInsights(
        history: <AnalysisSummary>[
          _row('f2', version: 'v3', score: 70),
          _row(
            'b2',
            version: 'v2',
            score: 66,
            shot: ShotType.backhandTwoHanded,
          ),
          _row('f1', version: 'v2', score: 50),
          _row(
            'b1',
            version: 'v2',
            score: 60,
            shot: ShotType.backhandTwoHanded,
          ),
        ],
        details: const <AnalysisResponse>[],
      );
      final ShotTypeInsight fh = insights.shotTypes.firstWhere(
        (ShotTypeInsight i) => i.shotType == ShotType.forehandTopspin,
      );
      final ShotTypeInsight bh = insights.shotTypes.firstWhere(
        (ShotTypeInsight i) => i.shotType == ShotType.backhandTwoHanded,
      );

      expect(fh.versionBoundaries, <int>[1]);
      expect(fh.latestDelta, isNull);
      expect(bh.versionBoundaries, isEmpty);
      expect(bh.latestDelta, closeTo(6, 1e-9));
    });
  });

  group('previousOfSameShot across a scoring change', () {
    test('the radar overlays no swing from another version', () {
      final List<AnalysisSummary> history = <AnalysisSummary>[
        _row('f2', version: 'v3'),
        _row('f1', version: 'v2'),
      ];
      expect(
        previousOfSameShot(history, <AnalysisResponse>[
          _detail('f2', path: 50),
          _detail('f1', path: 90),
        ]),
        isNull,
      );
    });

    test('same version, or no versions at all, still overlays', () {
      for (final String? v in <String?>['v3', null]) {
        final AnalysisResponse? prev = previousOfSameShot(
          <AnalysisSummary>[_row('f2', version: v), _row('f1', version: v)],
          <AnalysisResponse>[_detail('f2', path: 50), _detail('f1', path: 44)],
        );
        expect(prev?.analysisId, 'f1');
      }
    });
  });

  group('buildCategoryComparison keeps the earlier scores', () {
    test('each earlier clip is carried, oldest first, nulls kept', () {
      final List<CategoryComparison> rows = buildCategoryComparison(
        <CategoryTrend>[
          const CategoryTrend(
            category: 'prep',
            displayName: 'Prep',
            points: <double?>[60, null, 70, 90],
          ),
        ],
      );
      expect(rows.single.earlier, <double?>[60, null, 70]);
      expect(rows.single.average, 65);
    });
  });
}
