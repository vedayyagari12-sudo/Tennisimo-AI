import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/models/analysis_response.dart';
import 'package:tennisimo_ai/models/enums.dart';
import 'package:tennisimo_ai/models/key_numbers.dart';
import 'package:tennisimo_ai/models/metric_score.dart';

MetricScore _m(
  String name,
  double? value, {
  MetricUnit unit = MetricUnit.degrees,
  MetricVerdict verdict = MetricVerdict.ideal,
  double? score,
  double? lo,
  double? hi,
}) => MetricScore(
  name: name,
  value: value,
  unit: unit,
  verdict: verdict,
  score: score,
  idealMin: lo,
  idealMax: hi,
  viewSensitive: false,
);

AnalysisResponse _analysis(List<MetricScore> metrics) => AnalysisResponse(
  analysisId: 'a',
  createdAt: null,
  status: AnalysisStatus.complete,
  pipelineVersion: '1',
  shotType: ShotType.forehandTopspin,
  shotTypeConfidence: null,
  overallScore: null,
  categories: const <CategoryScore>[],
  metrics: metrics,
  ballSpeed: null,
  feedback: null,
  warnings: const <String>[],
);

KeyNumber _one(MetricScore m) =>
    buildKeyNumbers(_analysis(<MetricScore>[m])).single;

void main() {
  group('which metrics are shown', () {
    test('plain labels only, never a raw wire name', () {
      final List<KeyNumber> k = buildKeyNumbers(
        _analysis(<MetricScore>[
          _m('elbow_angle_at_contact_deg', 140, lo: 120, hi: 160),
          _m('knee_flexion_min_deg', 150, lo: 135, hi: 165),
          _m('shoulder_hip_separation_deg', 50, lo: 40, hi: 65),
        ]),
      );
      expect(
        k.map((KeyNumber n) => n.label),
        containsAll(<String>[
          'Elbow angle at contact',
          'Front-knee angle',
          'Hip-shoulder separation',
        ]),
      );
    });

    test('unscored metrics are left out, even with a verdict', () {
      // An older backend: all three still scored, with bands and verdicts.
      final List<KeyNumber> k = buildKeyNumbers(
        _analysis(<MetricScore>[
          _m(
            'hip_rotation_deg',
            0,
            verdict: MetricVerdict.low,
            score: 10,
            lo: 35,
            hi: 60,
          ),
          _m('shoulder_turn_deg', 90, score: 90, lo: 80, hi: 110),
          _m(
            'wrist_lag_deg',
            90,
            verdict: MetricVerdict.high,
            score: 20,
            lo: 15,
            hi: 45,
          ),
          _m('knee_flexion_min_deg', 150, score: 95, lo: 135, hi: 165),
        ]),
      );
      expect(k.map((KeyNumber n) => n.metric.name), <String>[
        'knee_flexion_min_deg',
      ]);
    });

    test('a response without the unscored metrics still works', () {
      // A newer backend: the three are simply absent.
      final List<KeyNumber> k = buildKeyNumbers(
        _analysis(<MetricScore>[
          _m('knee_flexion_min_deg', 150, score: 95, lo: 135, hi: 165),
        ]),
      );
      expect(k.single.metric.name, 'knee_flexion_min_deg');
      expect(
        buildKeyNumbers(_analysis(const <MetricScore>[])),
        isEmpty,
      );
    });

    test('torso-unit, ratio and always-null metrics are left out', () {
      final List<KeyNumber> k = buildKeyNumbers(
        _analysis(<MetricScore>[
          _m('elbow_angle_at_contact_deg', 140, lo: 120, hi: 160),
          _m('contact_point_forward_tu', 0.3, unit: MetricUnit.torsoUnits),
          _m('peak_hand_speed_tu_s', 7, unit: MetricUnit.torsoUnitsPerSec),
          _m('contact_height_ratio', 0.9, unit: MetricUnit.ratio),
          _m('weight_transfer_tu', null, unit: MetricUnit.torsoUnits),
          _m('balance_sway_tu', null, unit: MetricUnit.torsoUnits),
          _m('takeback_displacement_tu', null, unit: MetricUnit.torsoUnits),
        ]),
      );
      expect(k.map((KeyNumber n) => n.metric.name), <String>[
        'elbow_angle_at_contact_deg',
      ]);
    });

    test('an angle arriving in a non-degree unit is left out', () {
      final List<KeyNumber> k = buildKeyNumbers(
        _analysis(<MetricScore>[
          _m('elbow_angle_at_contact_deg', 140, unit: MetricUnit.unrecognized),
          _m('tempo_ratio', 2.0, unit: MetricUnit.seconds),
        ]),
      );
      expect(k, isEmpty);
    });
  });

  test('order: off-target worst first, in range best first, then no range, '
      'then not measured', () {
    final List<KeyNumber> k = buildKeyNumbers(
      _analysis(<MetricScore>[
        _m(
          'tempo_ratio',
          null,
          unit: MetricUnit.ratio,
          verdict: MetricVerdict.unavailable,
        ),
        _m('shoulder_hip_separation_deg', 50, score: 80, lo: 40, hi: 65),
        _m('knee_flexion_min_deg', 150, score: 95, lo: 135, hi: 165),
        _m(
          'elbow_angle_at_contact_deg',
          170,
          verdict: MetricVerdict.unavailable,
        ),
        _m(
          'swing_path_angle_deg',
          10,
          verdict: MetricVerdict.low,
          score: 60,
          lo: 15,
          hi: 35,
        ),
        _m(
          'swing_path_angle_deg',
          5,
          verdict: MetricVerdict.low,
          score: 20,
          lo: 15,
          hi: 35,
        ),
      ]),
    );
    expect(
      k.map((KeyNumber n) => '${n.metric.name} ${n.metric.score}'),
      <String>[
        'swing_path_angle_deg 20.0',
        'swing_path_angle_deg 60.0',
        'knee_flexion_min_deg 95.0',
        'shoulder_hip_separation_deg 80.0',
        'elbow_angle_at_contact_deg null',
        'tempo_ratio null',
      ],
    );
  });

  group('formatting', () {
    test('not measured is an em dash, never a zero', () {
      final KeyNumber n = _one(
        _m(
          'elbow_angle_at_contact_deg',
          null,
          verdict: MetricVerdict.unavailable,
          lo: 120,
          hi: 160,
        ),
      );
      expect(n.valueText, '—');
      expect(n.isMeasured, isFalse);
      expect(n.isOffTarget, isFalse);
      expect(n.isInRange, isFalse);
    });

    test('degrees round to whole numbers with the degree sign', () {
      final KeyNumber n = _one(
        _m(
          'elbow_angle_at_contact_deg',
          112.4,
          verdict: MetricVerdict.low,
          lo: 120,
          hi: 160,
        ),
      );
      expect(n.valueText, '112°');
      expect(n.idealText, '120–160°');
    });

    test('rounding never contradicts the verdict', () {
      // 119.6 rounds to 120, which would sit inside a 120-160 band.
      expect(
        _one(
          _m(
            'elbow_angle_at_contact_deg',
            119.6,
            verdict: MetricVerdict.low,
            lo: 120,
            hi: 160,
          ),
        ).valueText,
        '119.6°',
      );
      expect(
        _one(
          _m(
            'elbow_angle_at_contact_deg',
            160.3,
            verdict: MetricVerdict.high,
            lo: 120,
            hi: 160,
          ),
        ).valueText,
        '160.3°',
      );
    });

    test('tempo reads as a ratio to one decimal, band included', () {
      final KeyNumber n = _one(
        _m(
          'tempo_ratio',
          3.42,
          unit: MetricUnit.ratio,
          verdict: MetricVerdict.high,
          lo: 1.5,
          hi: 3.0,
        ),
      );
      expect(n.valueText, '3.4 : 1');
      expect(n.idealText, '1.5–3.0 : 1');
    });

    test('no band means no range is invented', () {
      final KeyNumber n = _one(
        _m(
          'elbow_angle_at_contact_deg',
          168,
          verdict: MetricVerdict.unavailable,
        ),
      );
      expect(n.idealText, isNull);
      expect(n.valueText, '168°');
      expect(n.isOffTarget, isFalse);
    });

    test('no user-facing text carries a torso unit', () {
      final List<KeyNumber> k = buildKeyNumbers(
        _analysis(<MetricScore>[
          _m(
            'elbow_angle_at_contact_deg',
            112,
            verdict: MetricVerdict.low,
            lo: 120,
            hi: 160,
          ),
          _m('swing_plane_deviation_tu', 0.1, unit: MetricUnit.torsoUnits),
        ]),
      );
      for (final KeyNumber n in k) {
        expect(
          '${n.label} ${n.valueText} ${n.idealText}',
          isNot(contains('TU')),
        );
      }
    });
  });
}
