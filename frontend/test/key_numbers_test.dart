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
          _m('hip_rotation_deg', 40, lo: 35, hi: 60),
          _m('knee_flexion_min_deg', 150, lo: 135, hi: 165),
          _m('shoulder_hip_separation_deg', 50, lo: 40, hi: 65),
        ]),
      );
      expect(
        k.map((KeyNumber n) => n.label),
        containsAll(<String>[
          'Hip turn',
          'Front-knee angle',
          'Hip-shoulder separation',
        ]),
      );
    });

    test('torso-unit, ratio and always-null metrics are left out', () {
      final List<KeyNumber> k = buildKeyNumbers(
        _analysis(<MetricScore>[
          _m('hip_rotation_deg', 40, lo: 35, hi: 60),
          _m('contact_point_forward_tu', 0.3, unit: MetricUnit.torsoUnits),
          _m('peak_hand_speed_tu_s', 7, unit: MetricUnit.torsoUnitsPerSec),
          _m('contact_height_ratio', 0.9, unit: MetricUnit.ratio),
          _m('weight_transfer_tu', null, unit: MetricUnit.torsoUnits),
          _m('balance_sway_tu', null, unit: MetricUnit.torsoUnits),
          _m('takeback_displacement_tu', null, unit: MetricUnit.torsoUnits),
        ]),
      );
      expect(k.map((KeyNumber n) => n.metric.name), <String>[
        'hip_rotation_deg',
      ]);
    });

    test('an angle arriving in a non-degree unit is left out', () {
      final List<KeyNumber> k = buildKeyNumbers(
        _analysis(<MetricScore>[
          _m('hip_rotation_deg', 40, unit: MetricUnit.unrecognized),
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
        _m('wrist_lag_deg', null, verdict: MetricVerdict.unavailable),
        _m('shoulder_turn_deg', 90, score: 80, lo: 80, hi: 110),
        _m('knee_flexion_min_deg', 150, score: 95, lo: 135, hi: 165),
        _m(
          'elbow_angle_at_contact_deg',
          170,
          verdict: MetricVerdict.unavailable,
        ),
        _m(
          'hip_rotation_deg',
          30,
          verdict: MetricVerdict.low,
          score: 60,
          lo: 35,
          hi: 60,
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
    expect(k.map((KeyNumber n) => n.metric.name), <String>[
      'swing_path_angle_deg',
      'hip_rotation_deg',
      'knee_flexion_min_deg',
      'shoulder_turn_deg',
      'elbow_angle_at_contact_deg',
      'wrist_lag_deg',
    ]);
  });

  group('formatting', () {
    test('not measured is an em dash, never a zero', () {
      final KeyNumber n = _one(
        _m(
          'hip_rotation_deg',
          null,
          verdict: MetricVerdict.unavailable,
          lo: 35,
          hi: 60,
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
          'hip_rotation_deg',
          25.4,
          verdict: MetricVerdict.low,
          lo: 35,
          hi: 60,
        ),
      );
      expect(n.valueText, '25°');
      expect(n.idealText, '35–60°');
    });

    test('rounding never contradicts the verdict', () {
      // 34.6 rounds to 35, which would sit inside a 35-60 band.
      expect(
        _one(
          _m(
            'hip_rotation_deg',
            34.6,
            verdict: MetricVerdict.low,
            lo: 35,
            hi: 60,
          ),
        ).valueText,
        '34.6°',
      );
      expect(
        _one(
          _m(
            'hip_rotation_deg',
            60.3,
            verdict: MetricVerdict.high,
            lo: 35,
            hi: 60,
          ),
        ).valueText,
        '60.3°',
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
            'hip_rotation_deg',
            25,
            verdict: MetricVerdict.low,
            lo: 35,
            hi: 60,
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
