/// The pure numbers behind the dashboard's charts.
///
/// Every decision that could make a chart lie is made in
/// `lib/models/chart_data.dart`, so it is tested here without a widget: what
/// is folded into "Other", which swing counts as "previous", what "your
/// average" averages, and that a null is carried as a null.
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/models/analysis_response.dart';
import 'package:tennisimo_ai/models/chart_data.dart';
import 'package:tennisimo_ai/models/dashboard_insights.dart';
import 'package:tennisimo_ai/models/enums.dart';

import 'support/dashboard_fixtures.dart';

ShotTypeInsight _type(ShotType shot, int sessions) => ShotTypeInsight(
  shotType: shot,
  scorePoints: List<double?>.filled(sessions, 60),
  speedPoints: List<double?>.filled(sessions, null),
  categoryTrends: const <CategoryTrend>[],
  detailsInspected: 0,
  latestCoverage: null,
);

AnalysisResponse _swing(
  String id, {
  ShotType shot = ShotType.forehandTopspin,
  double? prep = 70,
  double? contact = 72,
  double? path = 50,
  double? balance = 68,
  double? follow = 80,
}) => fixtureAnalysis(
  id: id,
  createdAt: DateTime(2026, 9, 20),
  shot: shot,
  overall: 65,
  categories: <String, double?>{
    'preparation': prep,
    'contact': contact,
    'swing_path': path,
    'balance': balance,
    'follow_through': follow,
  },
);

AnalysisSummary _row(String id, ShotType shot) => AnalysisSummary(
  analysisId: id,
  createdAt: null,
  shotType: shot,
  overallScore: 60,
  ballSpeedMph: null,
);

CategoryTrend _trend(String name, List<double?> points) =>
    CategoryTrend(category: name, displayName: name, points: points);

void main() {
  group('wording', () {
    test('plural agrees with the count, including one and zero', () {
      expect(plural(1, 'metric'), 'metric');
      expect(plural(0, 'metric'), 'metrics');
      expect(plural(2, 'metric'), 'metrics');
      expect(plural(1, 'category', 'categories'), 'category');
      expect(plural(3, 'category', 'categories'), 'categories');
    });

    test('shortDate is day and month, no year', () {
      expect(shortDate(DateTime(2026, 9, 7)), '7 Sep');
      expect(shortDate(DateTime(2026, 1, 31)), '31 Jan');
      expect(shortDate(DateTime(2026, 12, 1)), '1 Dec');
    });
  });

  group('wholePercents', () {
    test('sums to exactly 100 where plain rounding would not', () {
      // 45.45 / 36.36 / 18.18: plain rounding gives 45 + 36 + 18 = 99.
      expect(wholePercents(<int>[5, 4, 2]), <int>[46, 36, 18]);
      expect(wholePercents(<int>[5, 3, 1]), <int>[56, 33, 11]);
      expect(wholePercents(<int>[7, 2, 1]), <int>[70, 20, 10]);
    });

    test('equal counts always get equal percents', () {
      // 3 and 3 of 8 are both 37.5%: never shown as 38 and 37.
      final List<int> p = wholePercents(<int>[3, 3, 2]);
      expect(p[0], p[1]);
      expect(p, <int>[37, 37, 25]);
      final List<int> q = wholePercents(<int>[1, 1, 1]);
      expect(q.toSet(), hasLength(1));
    });

    test('no counts, or only zeros, are all zeros', () {
      expect(wholePercents(<int>[]), isEmpty);
      expect(wholePercents(<int>[0, 0]), <int>[0, 0]);
    });
  });

  group('buildShotMix', () {
    test('no clips is no slices', () {
      expect(buildShotMix(const <ShotTypeInsight>[]), isEmpty);
    });

    test('one shot type is one slice, which the widget turns into words', () {
      final List<ShotMixSlice> mix = buildShotMix(<ShotTypeInsight>[
        _type(ShotType.serve, 4),
      ]);
      expect(mix, hasLength(1));
      expect(mix.single.percent, 100);
      expect(mix.single.isOther, isFalse);
    });

    test('up to four shot types are all named, largest first', () {
      final List<ShotMixSlice> mix = buildShotMix(<ShotTypeInsight>[
        _type(ShotType.serve, 1),
        _type(ShotType.forehandTopspin, 5),
        _type(ShotType.volley, 3),
      ]);
      expect(mix.map((ShotMixSlice s) => s.label), <String>[
        'Forehand topspin',
        'Volley',
        'Serve',
      ]);
      expect(mix.map((ShotMixSlice s) => s.count), <int>[5, 3, 1]);
      expect(mix.any((ShotMixSlice s) => s.isOther), isFalse);
    });

    test('six shot types fold the two smallest into one Other slice', () {
      final List<ShotMixSlice> mix = buildShotMix(<ShotTypeInsight>[
        _type(ShotType.forehandTopspin, 5),
        _type(ShotType.backhandTwoHanded, 3),
        _type(ShotType.serve, 2),
        _type(ShotType.volley, 2),
        _type(ShotType.forehandSlice, 1),
        _type(ShotType.backhandOneHanded, 1),
      ]);
      expect(mix, hasLength(kMaxMixSlices));
      expect(mix.last.isOther, isTrue);
      expect(mix.last.label, 'Other');
      expect(mix.last.count, 2);
      expect(mix.last.shotTypes, hasLength(2));
      // Nothing is lost in the fold.
      expect(mix.fold<int>(0, (int a, ShotMixSlice s) => a + s.count), 14);
    });

    test('a fold of exactly one shot keeps that shot\'s name', () {
      final List<ShotMixSlice> mix = buildShotMix(<ShotTypeInsight>[
        _type(ShotType.forehandTopspin, 5),
        _type(ShotType.backhandTwoHanded, 4),
        _type(ShotType.serve, 3),
        _type(ShotType.volley, 2),
        _type(ShotType.forehandSlice, 1),
      ]);
      expect(mix, hasLength(5));
      expect(mix.last.isOther, isTrue);
      expect(mix.last.label, 'Forehand slice');
    });
  });

  group('buildSkillProfile', () {
    test('a null category stays null on its axis, never zero', () {
      final SkillProfile p = buildSkillProfile(
        _swing('a', contact: null, follow: null),
      )!;
      expect(p.axes, hasLength(5));
      expect(p.current.values[1], isNull);
      expect(p.current.values[4], isNull);
      expect(p.current.values, isNot(contains(0)));
      expect(p.current.measuredCount, 3);
      expect(p.current.plottable, isTrue);
      expect(p.unmeasured, <String>['Contact', 'Follow through']);
    });

    test('fewer than three measured axes is not plottable', () {
      final SkillProfile p = buildSkillProfile(
        _swing('a', contact: null, follow: null, path: null),
      )!;
      expect(p.current.measuredCount, 2);
      expect(p.current.plottable, isFalse);
    });

    test('no categories at all is no profile', () {
      final AnalysisResponse bare = fixtureAnalysis(
        id: 'x',
        createdAt: DateTime(2026),
        shot: ShotType.serve,
        overall: null,
        categories: const <String, double?>{},
      );
      // The fixture always emits five categories; strip them.
      final AnalysisResponse none = AnalysisResponse(
        analysisId: bare.analysisId,
        createdAt: bare.createdAt,
        status: bare.status,
        pipelineVersion: bare.pipelineVersion,
        shotType: bare.shotType,
        shotTypeConfidence: bare.shotTypeConfidence,
        overallScore: null,
        categories: const <CategoryScore>[],
        metrics: bare.metrics,
        ballSpeed: null,
        feedback: null,
        warnings: const <String>[],
      );
      expect(buildSkillProfile(none), isNull);
    });

    test(
      'the previous swing of the same shot is overlaid, aligned by name',
      () {
        final SkillProfile p = buildSkillProfile(
          _swing('new'),
          previous: _swing('old', path: 44, contact: null),
        )!;
        expect(p.previous, isNotNull);
        expect(p.previous!.values[2], 44);
        expect(p.previous!.values[1], isNull);
      },
    );

    test('a previous swing of ANOTHER shot type is refused', () {
      final SkillProfile p = buildSkillProfile(
        _swing('new'),
        previous: _swing('old', shot: ShotType.serve),
      )!;
      expect(p.previous, isNull);
    });

    test('a previous swing too sparse to draw is dropped, not half-drawn', () {
      final SkillProfile p = buildSkillProfile(
        _swing('new'),
        previous: _swing('old', prep: null, contact: null, follow: null),
      )!;
      expect(p.previous, isNull);
    });

    test('a swing is never its own previous', () {
      final SkillProfile p = buildSkillProfile(
        _swing('same'),
        previous: _swing('same'),
      )!;
      expect(p.previous, isNull);
    });
  });

  group('previousOfSameShot', () {
    final List<AnalysisSummary> history = <AnalysisSummary>[
      _row('f2', ShotType.forehandTopspin),
      _row('s1', ShotType.serve),
      _row('f1', ShotType.forehandTopspin),
      _row('f0', ShotType.forehandTopspin),
    ];

    test('skips other shot types to the immediately previous one', () {
      final AnalysisResponse? prev = previousOfSameShot(
        history,
        <AnalysisResponse>[
          _swing('f2'),
          _swing('s1', shot: ShotType.serve),
          _swing('f1'),
          _swing('f0'),
        ],
      );
      expect(prev?.analysisId, 'f1');
    });

    test('never reaches further back when that one did not load', () {
      final AnalysisResponse? prev = previousOfSameShot(
        history,
        <AnalysisResponse>[_swing('f2'), _swing('f0')],
      );
      expect(prev, isNull);
    });

    test('a first swing of its type has no previous', () {
      expect(
        previousOfSameShot(
          <AnalysisSummary>[
            _row('s1', ShotType.serve),
            _row('f1', ShotType.forehandTopspin),
          ],
          <AnalysisResponse>[_swing('f1')],
        ),
        isNull,
      );
      expect(
        previousOfSameShot(
          const <AnalysisSummary>[],
          const <AnalysisResponse>[],
        ),
        isNull,
      );
    });
  });

  group('buildCategoryComparison', () {
    test('the average is over the EARLIER clips only, nulls skipped', () {
      final List<CategoryComparison> rows = buildCategoryComparison(
        <CategoryTrend>[
          _trend('prep', <double?>[60, null, 70, 90]),
        ],
      );
      expect(rows.single.current, 90);
      expect(rows.single.average, 65); // (60 + 70) / 2, not including 90
      expect(rows.single.averageOf, 2);
      expect(rows.single.delta, 25);
    });

    test('a null newest score is a gap, never a zero', () {
      final List<CategoryComparison> rows = buildCategoryComparison(
        <CategoryTrend>[
          _trend('prep', <double?>[60, 70]),
          _trend('contact', <double?>[80, null]),
          _trend('follow', <double?>[null, 75]),
        ],
      );
      expect(rows[1].current, isNull);
      expect(rows[1].delta, isNull);
      expect(rows[2].average, isNull);
      expect(rows[2].averageOf, 0);
      expect(rows[2].delta, isNull);
    });

    test('no earlier clip, or nothing comparable, is no chart', () {
      expect(
        buildCategoryComparison(<CategoryTrend>[
          _trend('prep', <double?>[70]),
        ]),
        isEmpty,
      );
      expect(
        buildCategoryComparison(<CategoryTrend>[
          _trend('prep', <double?>[null, 70]),
          _trend('contact', <double?>[80, null]),
        ]),
        isEmpty,
      );
    });
  });

  group('scoreAxis', () {
    test('brackets the data with round tens, at least 20 tall', () {
      final ({double min, double max, double step}) a = scoreAxis(<double>[
        64,
        66,
        70,
        68,
        71,
      ]);
      expect(a.min, 60);
      expect(a.max, 80);
      expect(a.step, 10);
    });

    test('a flat series still gets a readable span', () {
      final ({double min, double max, double step}) a = scoreAxis(<double>[
        70,
        70,
      ]);
      expect(a.max - a.min, greaterThanOrEqualTo(20));
      expect(a.min, lessThanOrEqualTo(70));
      expect(a.max, greaterThanOrEqualTo(70));
    });

    test('never leaves 0-100, and a wide span takes a coarser step', () {
      final ({double min, double max, double step}) a = scoreAxis(<double>[
        3,
        98,
      ]);
      expect(a.min, 0);
      expect(a.max, 100);
      expect(a.step, 20);
      final ({double min, double max, double step}) b = scoreAxis(<double>[
        100,
        99,
      ]);
      expect(b.max, 100);
      expect(b.max - b.min, greaterThanOrEqualTo(20));
    });
  });
}
