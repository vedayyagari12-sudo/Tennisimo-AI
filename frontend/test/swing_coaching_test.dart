import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/models/analysis_response.dart';
import 'package:tennisimo_ai/models/metric_score.dart';
import 'package:tennisimo_ai/models/swing_coaching.dart';

MetricScore _m(
  String name,
  double? value, {
  String unit = 'deg',
  String verdict = 'ideal',
  double? lo,
  double? hi,
  double? score,
}) =>
    MetricScore.fromJson(<String, dynamic>{
      'name': name,
      'value': value,
      'unit': unit,
      'verdict': verdict,
      'ideal_min': lo,
      'ideal_max': hi,
      'score_0_100': score,
    });

void main() {
  group('readable units', () {
    test('torso-unit metrics read as torso lengths, never centimetres', () {
      final MetricScore m = _m('contact_point_forward_tu', 0.34,
          unit: 'TU', lo: 0.25, hi: 0.6);
      expect(coachingValueText(m), '0.34 torso lengths');
      expect(coachingTargetText(m), '0.25–0.60 torso lengths');
      expect(coachingValueText(m), isNot(contains('cm')));
    });

    test('hand speed reads per second', () {
      expect(
        coachingValueText(_m('peak_hand_speed_tu_s', 7.25,
            unit: 'TU/s', lo: 6, hi: 12)),
        '7.3 torso lengths/s',
      );
    });

    test('contact height reads as a percentage from hip to shoulder', () {
      final MetricScore m = _m('contact_height_ratio', 0.9,
          unit: 'ratio', lo: 0.75, hi: 1.05);
      expect(coachingValueText(m), '90%');
      expect(coachingTargetText(m), '75–105%');
    });

    test('degrees and tempo', () {
      expect(
          coachingValueText(_m('elbow_angle_at_contact_deg', 128,
              lo: 140, hi: 165)),
          '128°');
      expect(
          coachingValueText(_m('tempo_ratio', 2.14,
              unit: 'ratio', lo: 1.5, hi: 3)),
          '2.1 : 1');
    });

    test('a lower-is-better band reads as a limit', () {
      expect(
        coachingTargetText(_m('head_stillness_tu', 0.03,
            unit: 'TU', lo: 0, hi: 0.06)),
        'under 0.06 torso lengths',
      );
    });

    test('never prints a value that contradicts its verdict', () {
      // 34.6 under a 35 band must not round to "35°".
      expect(
        coachingValueText(_m('swing_path_angle_deg', 34.6,
            verdict: 'low', lo: 35, hi: 60)),
        '34.6°',
      );
    });

    test('unmeasured is words, never 0', () {
      expect(coachingValueText(_m('elbow_angle_at_contact_deg', null,
          verdict: 'unavailable')), 'not measured');
    });
  });

  group('how far off', () {
    test('below and above, in the metric’s own unit', () {
      expect(
          coachingGapText(_m('elbow_angle_at_contact_deg', 112,
              verdict: 'low', lo: 120, hi: 160)),
          '8° below target');
      expect(
          coachingGapText(_m('contact_height_ratio', 1.2,
              unit: 'ratio', verdict: 'high', lo: 0.75, hi: 1.05)),
          '15% above target');
    });

    test('lower-is-better says over the limit', () {
      expect(
          coachingGapText(_m('swing_plane_deviation_tu', 0.11,
              unit: 'TU', verdict: 'high', lo: 0, hi: 0.08)),
          '0.03 torso lengths over the limit');
    });

    test('a tiny miss is never rounded to "0° below"', () {
      expect(
          coachingGapText(_m('elbow_angle_at_contact_deg', 119.8,
              verdict: 'low', lo: 120, hi: 160)),
          '0.2° below target');
    });

    test('in range, unmeasured, and no band', () {
      expect(
          coachingGapText(
              _m('elbow_angle_at_contact_deg', 140, lo: 120, hi: 160)),
          'Within target');
      expect(coachingGapText(_m('elbow_angle_at_contact_deg', null)), isNull);
      expect(coachingGapText(_m('elbow_angle_at_contact_deg', 140)), isNull);
    });
  });

  group('what to change, and the drill', () {
    test('hitting late says meet it in front, with the cone drill', () {
      final MetricScore late = _m('contact_point_forward_tu', 0.1,
          unit: 'TU', verdict: 'low', lo: 0.25, hi: 0.6);
      expect(coachingAdviceFor(late), contains('late'));
      expect(coachingDrillFor(late), contains('Cone drill'));
    });

    test('a fast swing above the band is not a fault', () {
      final MetricScore fast = _m('peak_hand_speed_tu_s', 14,
          unit: 'TU/s', verdict: 'high', lo: 6, hi: 12);
      expect(coachingAdviceFor(fast), isNull);
      expect(coachingDrillFor(fast), isNull);
    });

    test('a slice path gets the slice drill, not the topspin one', () {
      final MetricScore chop = _m('swing_path_angle_deg', -50,
          verdict: 'low', lo: -30, hi: -8);
      expect(coachingDrillFor(chop), contains('Tray drill'));
      final MetricScore flat = _m('swing_path_angle_deg', 2,
          verdict: 'low', lo: 15, hi: 35);
      expect(coachingDrillFor(flat), contains('Low-to-high'));
    });

    test('praise has no drill; unscored metrics say nothing at all', () {
      final MetricScore ok =
          _m('elbow_angle_at_contact_deg', 140, lo: 120, hi: 160);
      expect(coachingAdviceFor(ok), isNotNull);
      expect(coachingDrillFor(ok), isNull);
      final MetricScore lag =
          _m('wrist_lag_deg', 5, verdict: 'low', lo: 20, hi: 40);
      expect(coachingAdviceFor(lag), isNull);
      expect(coachingDrillFor(lag), isNull);
    });

    test('every drill-bearing direction also has a sentence', () {
      // A drill without a sentence would train in an unstated direction.
      for (final String name in kDrills.keys) {
        final String metric = name.split('/').first;
        expect(
          kBodyLengthAdviceRules.containsKey(metric) ||
              <String>{
                'elbow_angle_at_contact_deg',
                'knee_flexion_min_deg',
                'swing_path_angle_deg',
                'tempo_ratio',
              }.contains(metric),
          isTrue,
          reason: name,
        );
      }
    });
  });

  group('buildCoaching', () {
    AnalysisResponse analysis(List<Map<String, dynamic>> metrics) =>
        AnalysisResponse.fromJson(<String, dynamic>{
          'analysis_id': 'a1',
          'status': 'complete',
          'shot_type': 'forehand',
          'scorecard': <String, dynamic>{
            'overall_score': 60.0,
            'categories': <Map<String, dynamic>>[],
            'metrics': metrics,
          },
        });

    test('off target worst first, then in range, then unmeasured; '
        'unscored dropped', () {
      final List<CoachingItem> items = buildCoaching(analysis(
        <Map<String, dynamic>>[
          <String, dynamic>{
            'name': 'elbow_angle_at_contact_deg', 'value': 140.0,
            'unit': 'deg', 'verdict': 'ideal', 'score_0_100': 95.0,
            'ideal_min': 120.0, 'ideal_max': 160.0,
          },
          <String, dynamic>{
            'name': 'head_stillness_tu', 'value': null,
            'unit': 'TU', 'verdict': 'unavailable',
          },
          <String, dynamic>{
            'name': 'contact_point_forward_tu', 'value': 0.1,
            'unit': 'TU', 'verdict': 'low', 'score_0_100': 40.0,
            'ideal_min': 0.25, 'ideal_max': 0.6,
          },
          <String, dynamic>{
            'name': 'knee_flexion_min_deg', 'value': 175.0,
            'unit': 'deg', 'verdict': 'high', 'score_0_100': 20.0,
            'ideal_min': 120.0, 'ideal_max': 155.0,
          },
          <String, dynamic>{
            'name': 'shoulder_turn_deg', 'value': 10.0,
            'unit': 'deg', 'verdict': 'low', 'score_0_100': 5.0,
            'ideal_min': 60.0, 'ideal_max': 90.0,
          },
        ],
      ));
      expect(items.map((CoachingItem c) => c.label), <String>[
        'Front-knee angle',
        'Contact in front',
        'Elbow angle at contact',
        'Head stillness',
      ]);
      expect(items.first.gapText, '20° above target');
      expect(items.first.drill, contains('Wide-base drill'));
      expect(items.last.valueText, 'not measured');
    });
  });
}
