import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/models/analysis_response.dart';
import 'package:tennisimo_ai/models/coaching_feedback.dart';
import 'package:tennisimo_ai/models/enums.dart';
import 'package:tennisimo_ai/screens/results_screen.dart';

import 'support/dashboard_fixtures.dart';

Improvement _card(String title, List<String> refs, {int priority = 1}) =>
    Improvement(
      priority: priority,
      title: title,
      why: 'Why $title.',
      cue: 'Cue for $title.',
      drill: 'Drill for $title.',
      metricRefs: refs,
    );

CoachingFeedback _feedback(List<Improvement> improvements) => CoachingFeedback(
  summary: '',
  strengths: const <String>[],
  improvements: improvements,
  source: FeedbackSource.gemini,
  model: 'gemini-2.5-flash',
  guard: null,
  latencyMs: null,
);

/// An older-backend forehand (the unscored metrics still carry verdicts)
/// with the given AI improvement cards.
AnalysisResponse _analysis(List<Improvement> improvements) => fixtureAnalysis(
  id: 'cards',
  createdAt: DateTime(2026, 11, 1),
  shot: ShotType.forehandTopspin,
  overall: 71,
  categories: const <String, double?>{},
  metrics: forehandMetrics(),
  feedback: _feedback(improvements),
);

Future<void> _pump(WidgetTester tester, AnalysisResponse analysis) async {
  // Tall enough that the lazy list builds every section.
  tester.view.physicalSize = const Size(800, 6000);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(home: ResultsScreen(analysis: analysis)));
  await tester.pump();
  expect(tester.takeException(), isNull);
}

void main() {
  group('refersOnlyToUnscoredMetrics', () {
    test('true when every ref is unscored', () {
      expect(refersOnlyToUnscoredMetrics(<String>['hip_rotation_deg']), isTrue);
      expect(
        refersOnlyToUnscoredMetrics(<String>[
          'wrist_lag_deg',
          'shoulder_turn_deg',
          'hip_rotation_deg',
          'shoulder_hip_separation_deg',
        ]),
        isTrue,
      );
    });

    test('false for no refs at all', () {
      expect(refersOnlyToUnscoredMetrics(const <String>[]), isFalse);
    });

    test('false when at least one ref is scored or unknown', () {
      expect(
        refersOnlyToUnscoredMetrics(
          <String>['hip_rotation_deg', 'elbow_angle_at_contact_deg'],
        ),
        isFalse,
      );
      expect(
        refersOnlyToUnscoredMetrics(<String>['swing_path_angle_deg']),
        isFalse,
      );
      expect(
        refersOnlyToUnscoredMetrics(<String>['some_future_metric']),
        isFalse,
      );
    });
  });

  group('CoachingFeedback.shownImprovements', () {
    test('drops only all-unscored cards, keeping order; raw list intact', () {
      final CoachingFeedback f = _feedback(<Improvement>[
        _card('Swing up', <String>['swing_path_angle_deg'], priority: 1),
        _card('Turn hips', <String>['hip_rotation_deg'], priority: 2),
        _card('Mixed', <String>['shoulder_turn_deg', 'tempo_ratio'],
            priority: 3),
        _card('Coil', <String>['shoulder_hip_separation_deg'], priority: 4),
        _card('No refs', const <String>[], priority: 5),
      ]);
      expect(
        f.shownImprovements.map((Improvement i) => i.title),
        <String>['Swing up', 'Mixed', 'No refs'],
      );
      expect(f.improvements, hasLength(5));
    });
  });

  group('results screen AI cards', () {
    testWidgets('a card whose refs are all unscored is hidden',
        (WidgetTester tester) async {
      await _pump(
        tester,
        _analysis(<Improvement>[
          _card('Turn your hips earlier', <String>['hip_rotation_deg']),
          _card('Load the coil',
              <String>['shoulder_hip_separation_deg', 'shoulder_turn_deg'],
              priority: 2),
        ]),
      );
      expect(find.text('Turn your hips earlier'), findsNothing);
      expect(find.text('Cue for Turn your hips earlier.'), findsNothing);
      expect(find.text('Load the coil'), findsNothing);
    });

    testWidgets('mixed-ref and no-ref cards stay',
        (WidgetTester tester) async {
      await _pump(
        tester,
        _analysis(<Improvement>[
          _card('Mixed card',
              <String>['hip_rotation_deg', 'elbow_angle_at_contact_deg']),
          _card('No-ref card', const <String>[], priority: 2),
          _card('Hidden card', <String>['wrist_lag_deg'], priority: 3),
        ]),
      );
      expect(find.text('Mixed card'), findsOneWidget);
      expect(find.text('Cue for Mixed card.'), findsOneWidget);
      expect(find.text('No-ref card'), findsOneWidget);
      expect(find.text('Hidden card'), findsNothing);
    });

    testWidgets('hiding every card leaves no empty box behind',
        (WidgetTester tester) async {
      await _pump(tester, _analysis(const <Improvement>[]));
      final int cardsWithoutAny = tester.widgetList(find.byType(Card)).length;
      final int textsWithoutAny = tester.widgetList(find.byType(Text)).length;

      await _pump(
        tester,
        _analysis(<Improvement>[
          _card('Turn your hips earlier', <String>['hip_rotation_deg']),
          _card('Coil', <String>['shoulder_hip_separation_deg'], priority: 2),
        ]),
      );
      expect(find.text('Cue'), findsNothing);
      expect(find.text('Drill'), findsNothing);
      // Exactly the screen with no AI cards at all: no leftover card, label
      // or placeholder.
      expect(tester.widgetList(find.byType(Card)).length, cardsWithoutAny);
      expect(tester.widgetList(find.byType(Text)).length, textsWithoutAny);
    });

    testWidgets('hip-shoulder separation gives no note and no measurement row',
        (WidgetTester tester) async {
      // forehandMetrics() sends separation in range (44 in 40-65) with an
      // ideal verdict, as an older backend still would.
      await _pump(tester, _analysis(const <Improvement>[]));
      expect(find.textContaining('stretch between your hips'), findsNothing);
      await tester.tap(find.text('See all measurements'));
      await tester.pumpAndSettle();
      // Neither label form the list has used for it.
      expect(find.textContaining('Hip-shoulder separation'), findsNothing);
      expect(find.textContaining('Shoulder hip separation'), findsNothing);
      // The list itself is still there for the scored metrics.
      expect(find.textContaining('Elbow angle at contact'), findsWidgets);
    });
  });
}
