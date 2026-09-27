import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/models/analysis_response.dart';
import 'package:tennisimo_ai/screens/results_screen.dart';

/// Fixture in the shape the server really sends: five weighted categories,
/// one of them unmeasurable.
Map<String, dynamic> _payload() => <String, dynamic>{
      'analysis_id': 'a1',
      'status': 'complete',
      'shot_type': 'forehand',
      'scorecard': <String, dynamic>{
        'overall_score': 68.0,
        'categories': <Map<String, dynamic>>[
          <String, dynamic>{
            'category': 'preparation',
            'score_0_100': 74.0,
            'weight': 0.25,
            'metric_names': <String>['shoulder_turn_deg'],
            'metrics_available': 1,
            'metrics_total': 1,
          },
          <String, dynamic>{
            'category': 'balance',
            'score_0_100': null,
            'weight': 0.15,
            'metric_names': <String>['torso_lean_deg'],
            'metrics_available': 0,
            'metrics_total': 1,
          },
        ],
        'metrics': <Map<String, dynamic>>[
          <String, dynamic>{
            'name': 'shoulder_turn_deg',
            'value': 88.0,
            'unit': 'deg',
            'verdict': 'ideal',
          },
        ],
      },
    };

void main() {
  testWidgets('the score breakdown renders alongside the metric list',
      (WidgetTester tester) async {
    await tester.pumpWidget(MaterialApp(
      home: ResultsScreen(analysis: AnalysisResponse.fromJson(_payload())),
    ));

    expect(find.text('Score breakdown'), findsOneWidget);
    expect(find.text('Preparation'), findsOneWidget);
    expect(find.text('74'), findsOneWidget);
    expect(find.text('25% of the score'), findsNothing);
    expect(find.text('1 of 1 metric \u00b7 25% of the score'), findsOneWidget);

    // The metric-level section is still there, behind its expander: this is
    // an addition, not a replacement.
    expect(find.text('See all measurements'), findsOneWidget);
    await tester.tap(find.text('See all measurements'));
    await tester.pumpAndSettle();
    expect(find.text('Shoulder turn'), findsOneWidget);
  });

  testWidgets('an unmeasured category is never rendered as 0',
      (WidgetTester tester) async {
    await tester.pumpWidget(MaterialApp(
      home: ResultsScreen(analysis: AnalysisResponse.fromJson(_payload())),
    ));

    expect(find.text('Balance'), findsOneWidget);
    expect(find.text('not measured'), findsOneWidget);
    expect(find.text('0'), findsNothing);
  });

  testWidgets('a payload with no categories omits the section entirely',
      (WidgetTester tester) async {
    final Map<String, dynamic> json = _payload();
    (json['scorecard'] as Map<String, dynamic>).remove('categories');
    await tester.pumpWidget(MaterialApp(
      home: ResultsScreen(analysis: AnalysisResponse.fromJson(json)),
    ));

    expect(find.text('Score breakdown'), findsNothing);
    expect(find.text('See all measurements'), findsOneWidget);
  });

  group('measurements expander', () {
    Map<String, dynamic> withNullMetric() {
      final Map<String, dynamic> payload = _payload();
      ((payload['scorecard'] as Map<String, dynamic>)['metrics']
              as List<Map<String, dynamic>>)
          .add(<String, dynamic>{
        'name': 'weight_transfer_tu',
        'value': null,
        'unit': 'TU',
        'verdict': 'unavailable',
        'view_sensitive': true,
      });
      return payload;
    }

    testWidgets('is closed by default and opens to the full list',
        (WidgetTester tester) async {
      await tester.pumpWidget(MaterialApp(
        home: ResultsScreen(
            analysis: AnalysisResponse.fromJson(withNullMetric())),
      ));

      expect(find.text('See all measurements'), findsOneWidget);
      expect(find.text('Shoulder turn'), findsNothing);
      expect(find.text('Weight transfer'), findsNothing);

      await tester.tap(find.text('See all measurements'));
      await tester.pumpAndSettle();

      expect(find.text('Shoulder turn'), findsOneWidget);
      expect(find.text('Weight transfer'), findsOneWidget);
    });

    testWidgets('a null metric reads as not measured, never as 0 or the clip',
        (WidgetTester tester) async {
      await tester.pumpWidget(MaterialApp(
        home: ResultsScreen(
            analysis: AnalysisResponse.fromJson(withNullMetric())),
      ));
      await tester.tap(find.text('See all measurements'));
      await tester.pumpAndSettle();

      // The category row's own 'not measured' plus the metric's.
      expect(find.text('not measured'), findsNWidgets(2));
      expect(find.textContaining('in this clip'), findsNothing);
      expect(find.textContaining('Not measurable'), findsNothing);
      expect(find.text('camera-angle sensitive'), findsOneWidget);
      expect(find.text('0'), findsNothing);
    });
  });

  group('section order and strengths', () {
    Map<String, dynamic> withFeedback(List<Map<String, dynamic>> metrics) {
      final Map<String, dynamic> payload = _payload();
      (payload['scorecard'] as Map<String, dynamic>)['metrics'] = metrics;
      payload['feedback'] = <String, dynamic>{
        'summary': 'Your racket path is holding this forehand back.',
        'strengths': <String>['Good separation between hips and shoulders.'],
        'improvements': <Map<String, dynamic>>[],
        'source': 'gemini',
      };
      return payload;
    }

    Map<String, dynamic> hip(double value, String verdict) =>
        <String, dynamic>{
          'name': 'hip_rotation_deg',
          'value': value,
          'unit': 'deg',
          'verdict': verdict,
          'ideal_min': 35.0,
          'ideal_max': 60.0,
        };

    testWidgets('swing notes sit directly under the score header',
        (WidgetTester tester) async {
      // Tall enough that the lazy list builds every section.
      tester.view.physicalSize = const Size(800, 3000);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(MaterialApp(
        home: ResultsScreen(
          analysis: AnalysisResponse.fromJson(
              withFeedback(<Map<String, dynamic>>[hip(25, 'low')])),
        ),
      ));

      final double header = tester.getTopLeft(find.text('overall')).dy;
      final double notes = tester.getTopLeft(find.text('Swing notes')).dy;
      final double summary = tester.getTopLeft(find.text('Coach summary')).dy;
      final double breakdown =
          tester.getTopLeft(find.text('Score breakdown')).dy;
      expect(header, lessThan(notes));
      expect(notes, lessThan(summary));
      expect(summary, lessThan(breakdown));
    });

    testWidgets('praise in the swing notes replaces the AI strengths',
        (WidgetTester tester) async {
      await tester.pumpWidget(MaterialApp(
        home: ResultsScreen(
          analysis: AnalysisResponse.fromJson(
              withFeedback(<Map<String, dynamic>>[hip(45, 'ideal')])),
        ),
      ));

      expect(find.text('Nice hip turn — keep it up.'), findsOneWidget);
      expect(find.text('What is working'), findsNothing);
      expect(find.text('Good separation between hips and shoulders.'),
          findsNothing);
    });

    testWidgets('the AI strengths show when the notes have no praise',
        (WidgetTester tester) async {
      await tester.pumpWidget(MaterialApp(
        home: ResultsScreen(
          analysis: AnalysisResponse.fromJson(
              withFeedback(<Map<String, dynamic>>[hip(25, 'low')])),
        ),
      ));

      expect(find.text('What is working'), findsOneWidget);
      expect(find.text('Good separation between hips and shoulders.'),
          findsOneWidget);
    });
  });

  group('swing notes', () {
    Map<String, dynamic> withMetrics(
      List<Map<String, dynamic>> metrics, {
      String source = 'gemini',
    }) {
      final Map<String, dynamic> payload = _payload();
      (payload['scorecard'] as Map<String, dynamic>)['metrics'] = metrics;
      payload['feedback'] = <String, dynamic>{
        'summary': '',
        'strengths': <String>[],
        'improvements': <Map<String, dynamic>>[
          <String, dynamic>{
            'priority': 1,
            'title': 'Turn your hips earlier',
            'why': '',
            'cue': 'Point your belt buckle at the side fence.',
            'drill': '',
            'metric_refs': <String>['hip_rotation_deg'],
          },
        ],
        'source': source,
      };
      return payload;
    }

    Map<String, dynamic> metric(String name, double? value, String verdict) =>
        <String, dynamic>{
          'name': name,
          'value': value,
          'unit': 'deg',
          'verdict': verdict,
          'ideal_min': 35.0,
          'ideal_max': 60.0,
        };

    testWidgets('come from the rule table even when the AI fell back',
        (WidgetTester tester) async {
      await tester.pumpWidget(MaterialApp(
        home: ResultsScreen(
          analysis: AnalysisResponse.fromJson(withMetrics(
            <Map<String, dynamic>>[metric('hip_rotation_deg', 25, 'low')],
            source: 'template',
          )),
        ),
      ));

      expect(find.text('Swing notes'), findsOneWidget);
      expect(
        find.text('Looks like your hips could turn a bit more — that’s where '
            'your power comes from.'),
        findsOneWidget,
      );
      expect(find.text('Hip turn 25° · ideal 35–60°'), findsOneWidget);
      // The AI card is still there underneath, unchanged.
      expect(find.text('Turn your hips earlier'), findsOneWidget);
    });

    testWidgets('say nothing about a metric that was not measured',
        (WidgetTester tester) async {
      await tester.pumpWidget(MaterialApp(
        home: ResultsScreen(
          analysis: AnalysisResponse.fromJson(withMetrics(
            <Map<String, dynamic>>[
              metric('hip_rotation_deg', null, 'unavailable'),
            ],
          )),
        ),
      ));

      expect(find.text('Swing notes'), findsNothing);
      expect(find.textContaining('Hip turn'), findsNothing);
    });
  });
}
