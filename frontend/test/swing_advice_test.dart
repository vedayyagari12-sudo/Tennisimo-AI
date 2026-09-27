import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/models/enums.dart';
import 'package:tennisimo_ai/models/key_numbers.dart';
import 'package:tennisimo_ai/models/metric_score.dart';
import 'package:tennisimo_ai/models/swing_advice.dart';

/// Topspin bands from `backend/app/analysis/rubric.py::_FOREHAND_TOPSPIN`.
const Map<String, (double, double)> _bands = <String, (double, double)>{
  'hip_rotation_deg': (35, 60),
  'shoulder_turn_deg': (80, 110),
  'shoulder_hip_separation_deg': (40, 65),
  'elbow_angle_at_contact_deg': (120, 160),
  'knee_flexion_min_deg': (135, 165),
  'wrist_lag_deg': (15, 45),
  'swing_path_angle_deg': (15, 35),
  'tempo_ratio': (1.5, 3.0),
};

/// `METRIC_VIEW_SENSITIVE` in backend/app/analysis/metrics.py, for the
/// metrics in the simple view.
const Set<String> _viewSensitive = <String>{
  'hip_rotation_deg',
  'shoulder_turn_deg',
  'elbow_angle_at_contact_deg',
  'wrist_lag_deg',
  'knee_flexion_min_deg',
};

MetricScore _m(
  String name,
  double? value,
  MetricVerdict verdict, {
  double? lo,
  double? hi,
  double? score,
}) {
  final (double, double)? band = _bands[name];
  return MetricScore(
    name: name,
    value: value,
    unit: name == 'tempo_ratio' ? MetricUnit.ratio : MetricUnit.degrees,
    verdict: verdict,
    score: score,
    idealMin: lo ?? band?.$1,
    idealMax: hi ?? band?.$2,
    viewSensitive: _viewSensitive.contains(name),
  );
}

/// A value just outside (near) or far outside the band on the given side.
double _valueFor(String name, MetricVerdict verdict, {required bool far}) {
  final (double lo, double hi) = _bands[name]!;
  final double width = hi - lo;
  final double step = far ? width * 1.5 : width * 0.2;
  return verdict == MetricVerdict.low ? lo - step : hi + step;
}

String? _say(String name, MetricVerdict verdict, {bool far = false}) =>
    adviceFor(_m(name, _valueFor(name, verdict, far: far), verdict));

/// What each direction of advice must say, and must never say.
///
/// This is the table the rule table is checked against: a below-range hip
/// turn must say "more", never "steadier".
class _Direction {
  const _Direction(this.must, this.never);
  final List<String> must;
  final List<String> never;
}

const Map<String, Map<MetricVerdict, _Direction>>
_directions = <String, Map<MetricVerdict, _Direction>>{
  // Below = hips turned LESS than ideal -> turn more.
  'hip_rotation_deg': <MetricVerdict, _Direction>{
    MetricVerdict.low: _Direction(
      <String>['more'],
      <String>['steadier', 'too much'],
    ),
    MetricVerdict.high: _Direction(
      <String>['too much', 'steadier'],
      <String>['more power', 'turn a bit more'],
    ),
  },
  // Below = shoulders turned LESS than ideal -> turn further.
  'shoulder_turn_deg': <MetricVerdict, _Direction>{
    MetricVerdict.low: _Direction(
      <String>['turn them'],
      <String>['shorter', 'compact', 'too much'],
    ),
    MetricVerdict.high: _Direction(
      <String>['too much'],
      <String>['further', 'sideways'],
    ),
  },
  // 180 is straight. Below = MORE bent -> reach out; above = straighter ->
  // keep a bend.
  'elbow_angle_at_contact_deg': <MetricVerdict, _Direction>{
    MetricVerdict.low: _Direction(
      <String>['bent', 'reach'],
      <String>['bend in your elbow', 'straight'],
    ),
    MetricVerdict.high: _Direction(
      <String>['straight', 'bend in your elbow'],
      <String>['reach'],
    ),
  },
  // 180 is straight. Below = MORE bend than ideal -> less bend; above =
  // too straight -> bend more. The counter-intuitive one.
  'knee_flexion_min_deg': <MetricVerdict, _Direction>{
    MetricVerdict.low: _Direction(
      <String>['bend your front knee'],
      <String>['bend your knees', 'straight', 'get lower'],
    ),
    MetricVerdict.high: _Direction(
      <String>['bend your knees', 'straight'],
      <String>['taller', 'crouching', 'that much'],
    ),
  },
  // Topspin band, positive = low to high. Below = too flat -> swing up.
  'swing_path_angle_deg': <MetricVerdict, _Direction>{
    MetricVerdict.low: _Direction(
      <String>['low to high'],
      <String>['steep', 'forward through'],
    ),
    MetricVerdict.high: _Direction(
      <String>['steep', 'forward'],
      <String>['low to high'],
    ),
  },
  // take-back / forward swing. Below = rushed take-back -> slower take-back.
  'tempo_ratio': <MetricVerdict, _Direction>{
    MetricVerdict.low: _Direction(<String>['slow'], <String>['quicker']),
    MetricVerdict.high: _Direction(
      <String>['quicker'],
      <String>['slower', 'slowly', 'rush'],
    ),
  },
};

/// Words only allowed when the value is far outside its range.
const List<String> _strongWords = <String>[
  'way ',
  'very',
  'hardly',
  'almost',
  'locked',
];

void main() {
  group('the direction of every fix matches the verdict', () {
    for (final MapEntry<String, Map<MetricVerdict, _Direction>> metric
        in _directions.entries) {
      for (final MapEntry<MetricVerdict, _Direction> side
          in metric.value.entries) {
        for (final bool far in <bool>[false, true]) {
          test('${metric.key} ${side.key.wire} ${far ? 'far' : 'near'}', () {
            final String? text = _say(metric.key, side.key, far: far);
            expect(text, isNotNull);
            final String lower = text!.toLowerCase();
            expect(
              side.value.must.any(lower.contains),
              isTrue,
              reason: '"$text" must say one of ${side.value.must}',
            );
            for (final String banned in side.value.never) {
              expect(
                lower,
                isNot(contains(banned)),
                reason: '"$text" points the wrong way',
              );
            }
          });
        }
      }
    }
  });

  group('the slice swing path points the other way', () {
    // _FOREHAND_SLICE: -30..-8. Below = chopping down more steeply.
    MetricScore slice(double v, MetricVerdict verdict) =>
        _m('swing_path_angle_deg', v, verdict, lo: -30, hi: -8);

    test('below a slice band: less chopping, never "high to low"', () {
      for (final double v in <double>[-35, -60]) {
        final String text = adviceFor(slice(v, MetricVerdict.low))!;
        expect(text, contains('chopping'));
        expect(text, isNot(contains('high to low')));
      }
    });

    test('above a slice band: more high to low, never "chopping"', () {
      for (final double v in <double>[-4, 20]) {
        final String text = adviceFor(slice(v, MetricVerdict.high))!;
        expect(text, contains('high to low'));
        expect(text, isNot(contains('chopping')));
      }
    });

    test('a band straddling flat gets no fix advice', () {
      expect(
        adviceFor(
          _m('swing_path_angle_deg', -40, MetricVerdict.low, lo: -10, hi: 10),
        ),
        isNull,
      );
    });

    test('an angle past 90° is not trusted for fix advice', () {
      // A low-to-high swing travelling left reads ~160° in the backend's
      // convention; "too steep" would be the wrong advice.
      expect(
        adviceFor(_m('swing_path_angle_deg', 160, MetricVerdict.high)),
        isNull,
      );
      expect(
        adviceFor(_m('swing_path_angle_deg', -120, MetricVerdict.low)),
        isNull,
      );
    });
  });

  group('no advice where the direction is unknown', () {
    test('hip-shoulder separation: praise only, never a fix', () {
      expect(_say('shoulder_hip_separation_deg', MetricVerdict.low), isNull);
      expect(_say('shoulder_hip_separation_deg', MetricVerdict.high), isNull);
      expect(
        adviceFor(_m('shoulder_hip_separation_deg', 50, MetricVerdict.ideal)),
        isNotNull,
      );
    });

    test('wrist lag: nothing either way', () {
      for (final MetricVerdict v in MetricVerdict.values) {
        expect(adviceFor(_m('wrist_lag_deg', 30, v)), isNull);
      }
    });
  });

  group('never advice about something not seen', () {
    test('a null value says nothing, whatever the verdict', () {
      for (final String name in kAdviceRules.keys) {
        for (final MetricVerdict v in MetricVerdict.values) {
          expect(adviceFor(_m(name, null, v)), isNull);
        }
      }
    });

    test('a measured value with no reference range says nothing', () {
      expect(
        adviceFor(
          MetricScore(
            name: 'elbow_angle_at_contact_deg',
            value: 168,
            unit: MetricUnit.degrees,
            verdict: MetricVerdict.unavailable,
            score: null,
            idealMin: null,
            idealMax: null,
            viewSensitive: true,
          ),
        ),
        isNull,
      );
    });
  });

  group('no exaggeration', () {
    test('"far" is one full range-width outside, no less', () {
      // Hip turn 35-60: 25 wide. 10 is exactly one width below.
      expect(
        isFarOutsideRange(_m('hip_rotation_deg', 10, MetricVerdict.low)),
        isTrue,
      );
      expect(
        isFarOutsideRange(_m('hip_rotation_deg', 11, MetricVerdict.low)),
        isFalse,
      );
      expect(
        isFarOutsideRange(_m('hip_rotation_deg', 85, MetricVerdict.high)),
        isTrue,
      );
      expect(
        isFarOutsideRange(_m('hip_rotation_deg', 84, MetricVerdict.high)),
        isFalse,
      );
      expect(
        isFarOutsideRange(_m('hip_rotation_deg', 40, MetricVerdict.ideal)),
        isFalse,
      );
    });

    test('near sentences never use strong words; far ones say more', () {
      for (final MapEntry<String, AdviceRule> e
          in <MapEntry<String, AdviceRule>>[
            ...kAdviceRules.entries,
            const MapEntry<String, AdviceRule>('slice', kSlicePathRule),
          ]) {
        for (final String? near in <String?>[
          e.value.belowNear,
          e.value.aboveNear,
        ]) {
          if (near == null) continue;
          for (final String w in _strongWords) {
            expect(
              near.toLowerCase(),
              isNot(contains(w)),
              reason: '${e.key}: "$near"',
            );
          }
        }
        // A far sentence may be strong ("way too much") or plainly
        // descriptive ("flat or downward"), but never the near one again.
        if (e.value.belowNear != null) {
          expect(e.value.belowFar, isNot(e.value.belowNear));
        }
        if (e.value.aboveNear != null) {
          expect(e.value.aboveFar, isNot(e.value.aboveNear));
        }
      }
    });
  });

  group('plain words', () {
    List<String> all() => <String>[
      for (final AdviceRule r in <AdviceRule>[
        ...kAdviceRules.values,
        kSlicePathRule,
      ])
        for (final String? s in <String?>[
          r.belowNear,
          r.belowFar,
          r.aboveNear,
          r.aboveFar,
          r.inRange,
        ])
          ?s,
    ];

    test('one short sentence, no numbers, no jargon', () {
      for (final String s in all()) {
        expect(s.length, lessThanOrEqualTo(110), reason: s);
        expect(RegExp(r'\d').hasMatch(s), isFalse, reason: s);
        for (final String jargon in <String>[
          'kinetic',
          'pronation',
          'x-factor',
          'optimal',
          'flexion',
          'rotation',
        ]) {
          expect(s.toLowerCase(), isNot(contains(jargon)), reason: s);
        }
      }
    });

    test('view-sensitive fixes are offered, not asserted', () {
      for (final String name in _viewSensitive) {
        final AdviceRule rule = kAdviceRules[name]!;
        for (final String? s in <String?>[
          rule.belowNear,
          rule.belowFar,
          rule.aboveNear,
          rule.aboveFar,
        ]) {
          if (s == null) continue;
          expect(s, startsWith('Looks like'), reason: s);
        }
      }
    });

    test('every plain-labelled metric has a rule entry', () {
      expect(kAdviceRules.keys.toSet(), kPlainMetricLabels.keys.toSet());
    });
  });

  group('choosing the bullets', () {
    KeyNumber n(MetricScore m) =>
        KeyNumber(metric: m, label: kPlainMetricLabels[m.name]!);

    final List<KeyNumber> numbers = <KeyNumber>[
      n(_m('swing_path_angle_deg', 9, MetricVerdict.low, score: 30)),
      n(_m('hip_rotation_deg', 25, MetricVerdict.low, score: 40)),
      n(_m('shoulder_hip_separation_deg', 30, MetricVerdict.low, score: 45)),
      n(_m('elbow_angle_at_contact_deg', 112, MetricVerdict.low, score: 50)),
      n(_m('tempo_ratio', 3.4, MetricVerdict.high, score: 60)),
      n(_m('knee_flexion_min_deg', 150, MetricVerdict.ideal, score: 95)),
      n(_m('shoulder_turn_deg', 90, MetricVerdict.ideal, score: 90)),
      n(_m('wrist_lag_deg', null, MetricVerdict.unavailable)),
    ];

    test('fixes first, in the given order, then capped praise', () {
      final List<AdviceBullet> b = buildSwingAdvice(
        numbers,
        maxFixes: 3,
        maxPraise: 1,
      );
      expect(b.map((AdviceBullet x) => x.number.metric.name), <String>[
        'swing_path_angle_deg',
        'hip_rotation_deg',
        // separation has no fix advice, so it is skipped, not counted.
        'elbow_angle_at_contact_deg',
        'knee_flexion_min_deg',
      ]);
      expect(b.last.isPraise, isTrue);
      expect(b.where((AdviceBullet x) => x.isPraise), hasLength(1));
    });

    test('uncapped fixes include every off-target number with advice', () {
      final List<AdviceBullet> b = buildSwingAdvice(numbers, maxPraise: 2);
      expect(b.where((AdviceBullet x) => !x.isPraise), hasLength(4));
      expect(b.where((AdviceBullet x) => x.isPraise), hasLength(2));
    });

    test('nothing measured, nothing said', () {
      expect(
        buildSwingAdvice(<KeyNumber>[numbers.last], maxPraise: 2),
        isEmpty,
      );
    });
  });
}
