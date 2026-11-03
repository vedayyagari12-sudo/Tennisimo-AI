/// The declared shot type, end to end on the client: the optional chip on the
/// record screen, the request body, the new `shot_type` keys, and what the
/// results screen and dashboard say about them.
library;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/models/analysis_response.dart';
import 'package:tennisimo_ai/models/dashboard_insights.dart';
import 'package:tennisimo_ai/models/enums.dart';
import 'package:tennisimo_ai/screens/dashboard_screen.dart';
import 'package:tennisimo_ai/screens/record_screen.dart';
import 'package:tennisimo_ai/screens/results_screen.dart';
import 'package:tennisimo_ai/services/api_client.dart';
import 'package:tennisimo_ai/theme/app_theme.dart';
import 'package:tennisimo_ai/widgets/shot_type_section.dart';

import 'support/dashboard_fixtures.dart';

/// A full analysis payload whose `shot_type` object is [shotType].
Map<String, dynamic> _payload(Object? shotType, {double? overall = 66}) =>
    <String, dynamic>{
      'analysis_id': 'a1',
      'status': 'complete',
      'shot_type': shotType,
      'scorecard': <String, dynamic>{
        'overall_score': overall,
        'categories': <Map<String, dynamic>>[],
        'metrics': <Map<String, dynamic>>[],
      },
    };

AnalysisResponse _parse(Object? shotType, {double? overall = 66}) =>
    AnalysisResponse.fromJson(_payload(shotType, overall: overall));

Future<void> _pumpResults(WidgetTester tester, AnalysisResponse a) async {
  tester.view.physicalSize = const Size(412, 2400);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
    MaterialApp(
      theme: buildAppTheme(),
      home: ResultsScreen(analysis: a),
    ),
  );
  await tester.pump();
}

const String _mismatchServeVolley =
    'You chose a serve, but the swing looked more like a volley. It was '
    'scored as a serve, as you asked.';

void main() {
  group('parsing the new shot_type keys', () {
    test('every new key present', () {
      final AnalysisResponse a = _parse(<String, dynamic>{
        'shot_type': 'serve',
        'confidence': null,
        'source': 'user_declared',
        'inferred_shot_type': 'volley',
        'inferred_confidence': 0.82,
        'mismatch': true,
        'shot_specific_scoring': false,
      });
      expect(a.shotType, ShotType.serve);
      expect(a.shotTypeConfidence, isNull);
      expect(a.shotTypeSource, ShotTypeSource.userDeclared);
      expect(a.inferredShotType, ShotType.volley);
      expect(a.inferredShotTypeConfidence, 0.82);
      expect(a.shotTypeMismatch, isTrue);
      expect(a.shotSpecificScoring, isFalse);
    });

    test('an old nested payload with none of the new keys', () {
      final AnalysisResponse a = _parse(<String, dynamic>{
        'shot_type': 'forehand_topspin',
        'confidence': 0.8,
      });
      expect(a.shotType, ShotType.forehandTopspin);
      expect(a.shotTypeConfidence, 0.8);
      expect(a.shotTypeSource, ShotTypeSource.inferred);
      expect(a.inferredShotType, isNull);
      expect(a.inferredShotTypeConfidence, isNull);
      expect(a.shotTypeMismatch, isFalse);
      expect(a.shotSpecificScoring, isNull);
    });

    test('a bare-string shot_type (an indexed row) takes every default', () {
      final AnalysisResponse a = _parse('serve');
      expect(a.shotType, ShotType.serve);
      expect(a.shotTypeSource, ShotTypeSource.inferred);
      expect(a.shotTypeMismatch, isFalse);
      expect(a.shotSpecificScoring, isNull);
      expect(a.inferredShotType, isNull);
    });

    test('an unknown source degrades to the sentinel, not to a real one', () {
      final AnalysisResponse a = _parse(<String, dynamic>{
        'shot_type': 'serve',
        'source': 'coach_declared',
        'inferred_shot_type': 'drop_shot',
      });
      expect(a.shotTypeSource, ShotTypeSource.unrecognized);
      expect(a.inferredShotType, ShotType.unknown);
    });

    test('explicit nulls and wrong types read as the safe defaults', () {
      final AnalysisResponse a = _parse(<String, dynamic>{
        'shot_type': 'volley',
        'source': null,
        'inferred_shot_type': null,
        'inferred_confidence': null,
        'mismatch': 'yes',
        'shot_specific_scoring': 'no',
      });
      expect(a.shotTypeSource, ShotTypeSource.inferred);
      expect(a.inferredShotType, isNull);
      expect(a.inferredShotTypeConfidence, isNull);
      expect(a.shotTypeMismatch, isFalse);
      expect(a.shotSpecificScoring, isNull);
    });

    test('the source enum reads its two real wire strings', () {
      expect(
        ShotTypeSource.fromJson('user_declared'),
        ShotTypeSource.userDeclared,
      );
      expect(ShotTypeSource.fromJson('inferred'), ShotTypeSource.inferred);
      expect(ShotTypeSource.fromJson(null), ShotTypeSource.inferred);
      expect(ShotTypeSource.fromJson(3), ShotTypeSource.unrecognized);
    });
  });

  group('the create-analysis body', () {
    test('omits label_hint when no shot was chosen', () {
      final Map<String, dynamic> body = createAnalysisBody(
        storagePath: 'u/clip.mp4',
        handednessHint: Handedness.right,
        labelHint: null,
      );
      expect(body.containsKey('label_hint'), isFalse);
      expect(body['handedness_hint'], 'right');
      expect(body['storage_path'], 'u/clip.mp4');
    });

    test('sends the chosen shot as label_hint', () {
      final Map<String, dynamic> body = createAnalysisBody(
        storagePath: 'u/clip.mp4',
        handednessHint: Handedness.left,
        labelHint: ShotType.serve,
      );
      expect(body['label_hint'], 'serve');
    });
  });

  group('the shot-type chip on the record screen', () {
    setUp(() {
      // No cameras: the screen shows its camera-error state and every hint
      // control, which is all this group needs.
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(
            const MethodChannel('plugins.flutter.io/camera'),
            (MethodCall call) async =>
                call.method == 'availableCameras' ? <Object?>[] : null,
          );
    });

    tearDown(() {
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(
            const MethodChannel('plugins.flutter.io/camera'),
            null,
          );
    });

    Future<void> pumpRecord(WidgetTester tester) async {
      tester.view.physicalSize = const Size(1000, 3000);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(
        MaterialApp(theme: buildAppTheme(), home: const RecordScreen()),
      );
      await tester.pumpAndSettle();
    }

    List<ChoiceChip> chips(WidgetTester tester) =>
        tester.widgetList<ChoiceChip>(find.byType(ChoiceChip)).toList();

    ChoiceChip chip(WidgetTester tester, String label) =>
        tester.widget<ChoiceChip>(find.widgetWithText(ChoiceChip, label));

    VoidCallback? fileButton(WidgetTester tester) => tester
        .widget<OutlinedButton>(
          find.widgetWithText(OutlinedButton, 'Choose a video file'),
        )
        .onPressed;

    testWidgets('starts with no shot selected, and says it is optional', (
      WidgetTester tester,
    ) async {
      await pumpRecord(tester);
      expect(chips(tester), hasLength(ShotType.selectable.length));
      expect(chips(tester).where((ChoiceChip c) => c.selected), isEmpty);
      expect(find.text(kShotTypeChoiceHelp), findsOneWidget);
      expect(find.textContaining('A hint for the analysis'), findsNothing);
    });

    testWidgets('can be set, switched and cleared', (
      WidgetTester tester,
    ) async {
      await pumpRecord(tester);
      await tester.tap(find.widgetWithText(ChoiceChip, 'Serve'));
      await tester.pumpAndSettle();
      expect(chip(tester, 'Serve').selected, isTrue);

      await tester.tap(find.widgetWithText(ChoiceChip, 'Volley'));
      await tester.pumpAndSettle();
      expect(chip(tester, 'Serve').selected, isFalse);
      expect(chip(tester, 'Volley').selected, isTrue);

      // Tapping the selected chip again clears it.
      await tester.tap(find.widgetWithText(ChoiceChip, 'Volley'));
      await tester.pumpAndSettle();
      expect(chips(tester).where((ChoiceChip c) => c.selected), isEmpty);
    });

    testWidgets('only handedness gates choosing a file, never the shot', (
      WidgetTester tester,
    ) async {
      await pumpRecord(tester);
      // No hand, no shot: blocked.
      expect(fileButton(tester), isNull);

      // A shot alone does not unlock it.
      await tester.tap(find.widgetWithText(ChoiceChip, 'Serve'));
      await tester.pumpAndSettle();
      expect(fileButton(tester), isNull);

      // A hand does, with the shot cleared again.
      await tester.tap(find.widgetWithText(ChoiceChip, 'Serve'));
      await tester.tap(find.text('Right'));
      await tester.pumpAndSettle();
      expect(chips(tester).where((ChoiceChip c) => c.selected), isEmpty);
      expect(fileButton(tester), isNotNull);
      expect(
        find.text('Choose your racket hand to record or choose a file.'),
        findsNothing,
      );
      // The analysis gate itself never looks at the shot.
      expect(analysisBlockedReason(Handedness.right), isNull);
    });
  });

  group('the results screen', () {
    testWidgets('declared serve, mismatch, no score', (
      WidgetTester tester,
    ) async {
      await _pumpResults(tester, declaredServeMismatchNoScore());
      expect(find.text('Serve'), findsOneWidget);
      expect(find.text(kShotDeclaredLine), findsOneWidget);
      expect(find.textContaining('Detected from your technique'), findsNothing);
      expect(find.textContaining('confidence'), findsNothing);
      expect(find.text(_mismatchServeVolley), findsOneWidget);
      expect(find.text(kNoShotSpecificScoringMessage), findsOneWidget);
      // No overall score: no generic-score line, no number, no "overall".
      expect(find.text(kGenericScoreMessage), findsNothing);
      expect(find.text('overall'), findsNothing);
    });

    testWidgets('declared serve with a score on the general bands', (
      WidgetTester tester,
    ) async {
      await _pumpResults(tester, declaredServeMismatchGenericScore());
      expect(find.text(kShotDeclaredLine), findsOneWidget);
      expect(find.text(_mismatchServeVolley), findsOneWidget);
      // The score is still shown, never hidden...
      expect(find.text('66'), findsOneWidget);
      expect(find.text('overall'), findsOneWidget);
      // ...with the notice and what it was measured against.
      expect(find.text(kNoShotSpecificScoringMessage), findsOneWidget);
      expect(find.text(kGenericScoreMessage), findsOneWidget);
    });

    testWidgets('declared forehand: no notice, no mismatch', (
      WidgetTester tester,
    ) async {
      await _pumpResults(
        tester,
        _parse(<String, dynamic>{
          'shot_type': 'forehand_topspin',
          'confidence': null,
          'source': 'user_declared',
          'inferred_shot_type': 'forehand_topspin',
          'inferred_confidence': 0.9,
          'mismatch': false,
          'shot_specific_scoring': true,
        }),
      );
      expect(find.text('Forehand topspin'), findsOneWidget);
      expect(find.text(kShotDeclaredLine), findsOneWidget);
      expect(find.textContaining('You chose'), findsNothing);
      expect(find.text(kNoShotSpecificScoringMessage), findsNothing);
      expect(find.text('66'), findsOneWidget);
    });

    testWidgets('inferred with a null confidence prints no null and no 0%', (
      WidgetTester tester,
    ) async {
      await _pumpResults(
        tester,
        _parse(<String, dynamic>{
          'shot_type': 'volley',
          'confidence': null,
          'source': 'inferred',
          'mismatch': false,
          'shot_specific_scoring': false,
        }, overall: null),
      );
      expect(find.text('Detected from your technique.'), findsOneWidget);
      expect(find.textContaining('null'), findsNothing);
      expect(find.textContaining('0%'), findsNothing);
      expect(find.text(kShotDeclaredLine), findsNothing);
      // Inferred shots get the same notice under the same condition.
      expect(find.text(kNoShotSpecificScoringMessage), findsOneWidget);
    });

    testWidgets('inferred with a confidence keeps today\'s line', (
      WidgetTester tester,
    ) async {
      await _pumpResults(
        tester,
        _parse(<String, dynamic>{'shot_type': 'serve', 'confidence': 0.8}),
      );
      expect(
        find.text('Detected from your technique · 80% confidence'),
        findsOneWidget,
      );
    });

    testWidgets('an old payload with none of the new keys looks as before', (
      WidgetTester tester,
    ) async {
      await _pumpResults(
        tester,
        _parse(<String, dynamic>{'shot_type': 'serve', 'confidence': 0.71}),
      );
      expect(
        find.text('Detected from your technique · 71% confidence'),
        findsOneWidget,
      );
      expect(find.textContaining('You chose'), findsNothing);
      expect(find.text(kShotDeclaredLine), findsNothing);
      // shot_specific_scoring missing is unknown: no notice.
      expect(find.text(kNoShotSpecificScoringMessage), findsNothing);
      expect(find.text(kGenericScoreMessage), findsNothing);
    });

    testWidgets('an unknown shot type never gets the notice', (
      WidgetTester tester,
    ) async {
      await _pumpResults(
        tester,
        _parse(<String, dynamic>{
          'shot_type': 'unknown',
          'source': 'inferred',
          'shot_specific_scoring': false,
        }),
      );
      expect(find.text(kNoShotSpecificScoringMessage), findsNothing);
    });

    test('the mismatch sentence without a usable inferred type', () {
      final AnalysisResponse a = _parse(<String, dynamic>{
        'shot_type': 'backhand_two_handed',
        'source': 'user_declared',
        'mismatch': true,
      });
      expect(
        mismatchMessage(a),
        'You chose a backhand (two-handed), but the swing looked like a '
        'different shot. It was scored as a backhand (two-handed), as you '
        'asked.',
      );
    });

    test('an unrecognised source claims neither selected nor detected', () {
      final AnalysisResponse a = _parse(<String, dynamic>{
        'shot_type': 'serve',
        'source': 'something_new',
        'confidence': 0.9,
      });
      expect(shotProvenanceLine(a), isNull);
    });
  });

  group('the dashboard', () {
    test('the newest detail carries shot_specific_scoring to the insight', () {
      final DashboardFixture f = declaredUnbandedServe();
      final DashboardInsights insights = buildDashboardInsights(
        history: f.source.history,
        details: f.source.details.values.toList()
          ..sort(
            (AnalysisResponse a, AnalysisResponse b) =>
                b.createdAt!.compareTo(a.createdAt!),
          ),
      );
      final ShotTypeInsight serve = insights.shotTypes.firstWhere(
        (ShotTypeInsight i) => i.shotType == ShotType.serve,
      );
      expect(serve.latestShotSpecificScoring, isFalse);
      final ShotTypeInsight forehand = insights.shotTypes.firstWhere(
        (ShotTypeInsight i) => i.shotType == ShotType.forehandTopspin,
      );
      expect(forehand.latestShotSpecificScoring, isNull);
    });

    test('a failed newest detail says nothing about the shot\'s scoring', () {
      final DashboardFixture f = declaredUnbandedServe();
      final DashboardInsights insights = buildDashboardInsights(
        history: f.source.history,
        // Only the OLDER serve's detail loaded.
        details: <AnalysisResponse>[f.source.details['dsv1']!],
      );
      final ShotTypeInsight serve = insights.shotTypes.firstWhere(
        (ShotTypeInsight i) => i.shotType == ShotType.serve,
      );
      expect(serve.latestShotSpecificScoring, isNull);
    });

    Widget host(Widget child) => MaterialApp(
      theme: buildAppTheme(),
      home: Scaffold(body: SingleChildScrollView(child: child)),
    );

    ShotTypeInsight unscored({bool? shotSpecific}) => ShotTypeInsight(
      shotType: ShotType.serve,
      scorePoints: const <double?>[null, null],
      speedPoints: const <double?>[null, null],
      categoryTrends: const <CategoryTrend>[],
      detailsInspected: 1,
      latestCoverage: null,
      newestDetailLoaded: true,
      latestShotSpecificScoring: shotSpecific,
    );

    testWidgets('an unbanded shot with no score uses the new sentence', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(
        host(
          ShotTypeSection(
            insight: unscored(shotSpecific: false),
            initiallyExpanded: true,
          ),
        ),
      );
      expect(find.text(kNoShotSpecificScoringMessage), findsOneWidget);
      expect(find.text(ShotTypeSection.noOverallScoreMessage), findsNothing);
    });

    testWidgets('an unknown scoring flag keeps the old explanation', (
      WidgetTester tester,
    ) async {
      for (final bool? flag in <bool?>[null, true]) {
        await tester.pumpWidget(
          host(
            ShotTypeSection(
              key: ValueKey<bool?>(flag),
              insight: unscored(shotSpecific: flag),
              initiallyExpanded: true,
            ),
          ),
        );
        expect(
          find.text(ShotTypeSection.noOverallScoreMessage),
          findsOneWidget,
        );
        expect(find.text(kNoShotSpecificScoringMessage), findsNothing);
      }
    });

    testWidgets('the assembled dashboard says it for a declared serve', (
      WidgetTester tester,
    ) async {
      tester.view.physicalSize = const Size(412, 4000);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(
        MaterialApp(
          theme: buildAppTheme(),
          home: DashboardScreen(dataSource: declaredUnbandedServe().source),
        ),
      );
      for (int i = 0; i < 20; i++) {
        await tester.pump(const Duration(milliseconds: 100));
      }
      final Finder list = find.byType(Scrollable).first;
      await tester.scrollUntilVisible(
        find.text(kNoShotSpecificScoringMessage),
        300,
        scrollable: list,
      );
      expect(find.text(kNoShotSpecificScoringMessage), findsOneWidget);
      expect(find.text(ShotTypeSection.noOverallScoreMessage), findsNothing);
    });
  });
}
