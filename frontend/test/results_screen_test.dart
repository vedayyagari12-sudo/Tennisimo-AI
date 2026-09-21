import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tennisform_ai/models/analysis_response.dart';
import 'package:tennisform_ai/screens/results_screen.dart';

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
    expect(find.text('1 of 1 metrics \u00b7 25% of the score'), findsOneWidget);

    // The metric-level section is still there: this is an addition, not a
    // replacement.
    expect(find.text('Metrics'), findsOneWidget);
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
    expect(find.text('Metrics'), findsOneWidget);
  });
}
