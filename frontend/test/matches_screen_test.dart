// Match mode screens and models (2026-11-09).

import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/models/court.dart';
import 'package:tennisimo_ai/models/enums.dart';
import 'package:tennisimo_ai/models/match.dart';
import 'package:tennisimo_ai/screens/matches_screen.dart';
import 'package:tennisimo_ai/services/api_client.dart';
import 'package:tennisimo_ai/services/video_intake.dart';
import 'package:tennisimo_ai/theme/app_theme.dart';
import 'package:tennisimo_ai/widgets/court_map.dart';
import 'package:tennisimo_ai/widgets/line_call_card.dart';

import 'support/court_fixtures.dart';

Map<String, dynamic> matchJson({
  String status = 'complete',
  double? progress = 1.0,
  Map<String, dynamic>? result,
  bool picture = false,
  String? errorMessage,
  String? kind,
  Map<String, dynamic>? feedback,
  String? stage,
  double? etaS,
  Map<String, dynamic>? courtCheck,
}) =>
    <String, dynamic>{
      'match_id': 'm-1',
      'status': status,
      'created_at': '2026-11-09T10:00:00Z',
      'updated_at': '2026-11-09T10:01:00Z',
      'title': 'Sunday hit',
      'court_format': 'singles',
      'progress': progress,
      'error_code': errorMessage == null ? null : 'video_too_short',
      'error_message': errorMessage,
      'court_status': status == 'complete' ? 'complete' : null,
      'stats': status == 'complete'
          ? <String, dynamic>{
              'shots_landed': 5,
              'rallies': 1,
              'longest_rally_hits': 5,
              'average_rally_hits': 5.0,
              'in_count': 2,
              'out_count': 1,
              'in_percent': 66.7,
              'deep_percent': 60.0,
            }
          : null,
      'calibration_source': status == 'complete' ? 'auto' : null,
      'has_court_picture': picture,
      'duration_s': 42.0,
      'result': result,
      'kind': ?kind,
      'stage': ?stage,
      'eta_s': ?etaS,
      'court_check': ?courtCheck,
      'feedback': ?feedback,
    };

Map<String, dynamic> feedbackJson({String source = 'gemini'}) => <String, dynamic>{
      'summary': 'An estimated 2 of your 5 landings were in.',
      'strengths': <String>['Your longest rally was 5 hits.'],
      'improvements': <Map<String, dynamic>>[
        <String, dynamic>{
          'priority': 1,
          'title': 'Aim deeper',
          'why': 'Most balls landed short.',
          'cue': 'Hit up through the ball.',
          'drill': 'Rally past the service line.',
          'metric_refs': <String>[],
        },
      ],
      'source': source,
      'model': source == 'template' ? null : 'gemini-2.5-flash',
      'guard': <String, dynamic>{
        'passed': true,
        'rejected_tokens': <String>[],
        'fields_discarded': <String>[],
        'fell_back_to_template': source == 'template',
        'mph_rule': 'banned',
      },
      'latency_ms': 900,
    };

Widget _host(Widget child) => MaterialApp(theme: buildAppTheme(), home: child);

Future<ApiResult<MatchRecord>> Function(String) _fixed(Map<String, dynamic> json) =>
    (String _) async => ApiResult<MatchRecord>.ok(MatchRecord.fromJson(json));

Future<ApiResult<Uint8List>> _noPicture(String _) async =>
    const ApiResult<Uint8List>.err(ApiFailure(kind: ApiFailureKind.server, message: 'none'));

void main() {
  group('models', () {
    test('a complete record parses its stats and result; null stays null', () {
      final MatchRecord match = MatchRecord.fromJson(matchJson(result: courtSessionJson()));
      expect(match.status, MatchStatus.complete);
      expect(match.status.isFinished, isTrue);
      expect(match.stats!.inPercent, 66.7);
      expect(match.result!.landings, hasLength(5));
      expect(match.courtFoundAutomatically, isTrue);

      final MatchRecord running = MatchRecord.fromJson(matchJson(status: 'running', progress: 0.4));
      expect(running.status.isFinished, isFalse);
      expect(running.stats, isNull);
      expect(running.result, isNull);
    });

    test('an unknown status is finished, so polling stops', () {
      expect(MatchStatus.fromJson('exploded'), MatchStatus.unrecognized);
      expect(MatchStatus.unrecognized.isFinished, isTrue);
    });

    test('the create body omits an empty name', () {
      expect(
        createMatchBody(storagePath: 'b/u/v.mp4', courtFormat: 'doubles', title: '  '),
        <String, dynamic>{'storage_path': 'b/u/v.mp4', 'court_format': 'doubles'},
      );
      expect(createMatchBody(storagePath: 'p', courtFormat: 'singles', title: 'Sun')['title'], 'Sun');
    });

    test('a match may be five minutes and 450 MB; a swing still sixty seconds and 50 MB', () {
      String? match(int bytes, double seconds) => videoRejectionReason(
          contentType: 'video/mp4', sizeBytes: bytes, durationSeconds: seconds,
          maxSeconds: kMaxMatchSeconds, maxBytes: kMaxMatchUploadBytes);
      expect(match(300 * 1024 * 1024, 300), isNull);
      expect(match(10, 301), contains('minute'));
      expect(match(kMaxMatchUploadBytes + 1, 200), contains('720p'));
      expect(videoRejectionReason(contentType: 'video/mp4', sizeBytes: 10, durationSeconds: 300), isNotNull);
      expect(
        videoRejectionReason(contentType: 'video/mp4', sizeBytes: 60 * 1024 * 1024, durationSeconds: 20),
        isNotNull,
      );
    });

    test('pieces cover every byte once, in order, the last one short', () {
      expect(uploadPartRanges(100, 40, 3), <(int, int)>[(0, 40), (40, 80), (80, 100)]);
      expect(uploadPartRanges(80, 40, 2), <(int, int)>[(0, 40), (40, 80)]);
      expect(uploadPartRanges(5, 40, 1), <(int, int)>[(0, 5)]);
    });

    test('the create body carries the piece count only when there are pieces', () {
      expect(createMatchBody(storagePath: 'p', courtFormat: 'singles', partCount: 3)['part_count'], 3);
      expect(createMatchBody(storagePath: 'p', courtFormat: 'singles').containsKey('part_count'), isFalse);
    });
  });


  group('court check', () {
    Map<String, dynamic> check(String confidence, {List<String> reasons = const <String>[]}) => <String, dynamic>{
          'confidence': confidence,
          'source': 'auto',
          'support': 0.91,
          'lines_visible': 9,
          'corners_in_frame': confidence == 'good' ? 4 : 3,
          'reasons': reasons,
          'calls_allowed': true,
        };

    final Uint8List png = Uint8List.fromList(<int>[
      0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,
      0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x06, 0x00, 0x00, 0x00, 0x1F, 0x15, 0xC4,
      0x89, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x44, 0x41, 0x54, 0x78, 0x9C, 0x63, 0x00, 0x01, 0x00, 0x00,
      0x05, 0x00, 0x01, 0x0D, 0x0A, 0x2D, 0xB4, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE,
      0x42, 0x60, 0x82,
    ]);

    test('the check parses; an unknown rating is not trusted as good', () {
      final MatchRecord m = MatchRecord.fromJson(matchJson(
        status: 'running',
        progress: 0.3,
        courtCheck: check('check', reasons: <String>['court_cut_off', 'weak_match']),
      ));
      expect(m.courtCheck!.confidence, CourtConfidence.check);
      expect(m.courtCheck!.needsAttention, isTrue);
      expect(m.courtCheck!.reasons, <String>['court_cut_off', 'weak_match']);
      expect(m.courtCheck!.support, 0.91);
      expect(m.courtCheck!.cornersInFrame, 3);
      expect(MatchRecord.fromJson(matchJson()).courtCheck, isNull);
      final CourtCheck? odd = CourtCheck.fromJsonOrNull(<String, dynamic>{'confidence': 'splendid', 'corners_in_frame': 4});
      expect(odd!.confidence, CourtConfidence.unknown);
      expect(odd.needsAttention, isTrue);
    });

    test('every reason and rating has words a player can act on', () {
      for (final String code in <String>['court_cut_off', 'few_lines', 'weak_match', 'calls_off', 'tap_error']) {
        expect(courtReasonText(code), isNot(code));
        expect(courtReasonText(code).length, greaterThan(20));
      }
      final CourtCheck good = CourtCheck.fromJsonOrNull(check('good'))!;
      final CourtCheck poor = CourtCheck.fromJsonOrNull(check('poor'))!;
      expect(courtCheckHeadline(good), 'Court lines found');
      expect(courtCheckHeadline(poor), 'We’re not sure about the court lines');
      expect(courtCheckAdvice(good), contains('stop now and film again'));
      expect(courtCheckAdvice(poor), contains('Look closely'));
    });

    testWidgets('a running job shows the court lines early, with advice and two choices',
        (WidgetTester tester) async {
      tester.view.physicalSize = const Size(900, 2600);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(_host(MatchResultScreen(
        matchId: 'm-1',
        loader: _fixed(matchJson(
          status: 'running',
          progress: 0.2,
          stage: 'ball',
          picture: true,
          courtCheck: check('check', reasons: <String>['court_cut_off']),
        )),
        pictureLoader: (String _) async => ApiResult<Uint8List>.ok(png),
        deleter: (String _) async => const ApiResult<void>.ok(null),
      )));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));
      expect(find.text('Check the court lines'), findsOneWidget);
      expect(find.text(courtReasonText('court_cut_off')), findsOneWidget);
      expect(find.textContaining('Look closely at the yellow lines'), findsOneWidget);
      expect(find.text('Lines look right'), findsOneWidget);
      expect(find.text('Stop and film again'), findsOneWidget);
      // The progress is still shown under it.
      expect(find.text('Tracking the ball'), findsWidgets);
      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('no card until the court is found', (WidgetTester tester) async {
      await tester.pumpWidget(_host(MatchResultScreen(
        matchId: 'm-1',
        loader: _fixed(matchJson(status: 'running', progress: 0.05, stage: 'court')),
        pictureLoader: _noPicture,
      )));
      await tester.pump();
      expect(find.byType(CourtCheckCard), findsNothing);
      expect(find.text('Finding the court lines'), findsWidgets);
      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('confirming the lines tucks the card away', (WidgetTester tester) async {
      tester.view.physicalSize = const Size(900, 2600);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(_host(MatchResultScreen(
        matchId: 'm-1',
        loader: _fixed(matchJson(status: 'running', progress: 0.2, stage: 'ball', courtCheck: check('good'))),
        pictureLoader: _noPicture,
      )));
      await tester.pump();
      expect(find.text('Court lines found'), findsOneWidget);
      await tester.tap(find.text('Lines look right'));
      await tester.pump();
      expect(find.byType(CourtCheckCard), findsNothing);
      expect(find.text('You confirmed the court lines'), findsOneWidget);
      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('stopping asks first, then deletes and leaves the screen', (WidgetTester tester) async {
      tester.view.physicalSize = const Size(900, 2600);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);
      final List<String> stopped = <String>[];
      await tester.pumpWidget(_host(Builder(
        builder: (BuildContext context) => Scaffold(
          body: Center(
            child: FilledButton(
              onPressed: () => Navigator.of(context).push(MaterialPageRoute<void>(
                builder: (BuildContext context) => MatchResultScreen(
                  matchId: 'm-1',
                  loader: _fixed(matchJson(status: 'running', progress: 0.2, stage: 'ball', courtCheck: check('poor'))),
                  pictureLoader: _noPicture,
                  deleter: (String id) async {
                    stopped.add(id);
                    return const ApiResult<void>.ok(null);
                  },
                ),
              )),
              child: const Text('open'),
            ),
          ),
        ),
      )));
      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Stop and film again'));
      await tester.pumpAndSettle();
      expect(find.text('Stop this analysis?'), findsOneWidget);
      expect(stopped, isEmpty); // nothing is stopped by the question alone
      await tester.tap(find.text('Keep going'));
      await tester.pumpAndSettle();
      expect(stopped, isEmpty);
      await tester.tap(find.text('Stop and film again'));
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(FilledButton, 'Stop'));
      await tester.pumpAndSettle();
      expect(stopped, <String>['m-1']);
      expect(find.byType(MatchResultScreen), findsNothing); // back on the first screen
      expect(find.text('open'), findsOneWidget);
    });

    testWidgets('the app bar can stop a running job at any time', (WidgetTester tester) async {
      await tester.pumpWidget(_host(MatchResultScreen(
        matchId: 'm-1',
        loader: _fixed(matchJson(status: 'running', progress: 0.05, stage: 'court')),
        pictureLoader: _noPicture,
      )));
      await tester.pump();
      expect(find.widgetWithText(TextButton, 'Stop'), findsOneWidget);
      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('a finished result warns when the lines were shaky, and not when they were good',
        (WidgetTester tester) async {
      tester.view.physicalSize = const Size(800, 3600);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(_host(MatchResultScreen(
        matchId: 'm-1',
        loader: _fixed(matchJson(
          result: courtSessionJson(),
          courtCheck: check('poor', reasons: <String>['weak_match', 'few_lines']),
        )),
        pictureLoader: _noPicture,
      )));
      await tester.pumpAndSettle();
      expect(find.text('We’re not sure about the court lines'), findsOneWidget);
      expect(find.text(courtReasonText('few_lines')), findsOneWidget);
      // A finished result has no stop or confirm buttons.
      expect(find.text('Lines look right'), findsNothing);
      expect(find.text('Stop and film again'), findsNothing);
      await tester.pumpWidget(const SizedBox());
      await tester.pumpWidget(_host(MatchResultScreen(
        matchId: 'm-1',
        loader: _fixed(matchJson(result: courtSessionJson(), courtCheck: check('good'))),
        pictureLoader: _noPicture,
      )));
      await tester.pumpAndSettle();
      expect(find.byType(CourtCheckCard), findsNothing);
    });
  });

  group('progress', () {
    test('the time left reads as rough and is absent without an estimate', () {
      expect(etaText(null), isNull);
      expect(etaText(double.nan), isNull);
      expect(etaText(20), 'Less than a minute left');
      expect(etaText(44), 'Less than a minute left');
      expect(etaText(60), 'About 1 min left');
      expect(etaText(61), 'About 2 min left');
      expect(etaText(400), 'About 7 min left');
    });

    test('every stage has its own headline; an unknown one still reads', () {
      expect(stageTitle(MatchStage.preparing), 'Getting your video ready');
      expect(stageTitle(MatchStage.court), 'Finding the court lines');
      expect(stageTitle(MatchStage.players), 'Following the players');
      expect(stageTitle(MatchStage.finishing), 'Writing your result');
      expect(stageTitle(null), 'Tracking the ball');
      expect(MatchStage.fromJson('players'), MatchStage.players);
      expect(MatchStage.fromJson('mystery'), isNull);
    });

    testWidgets('a running job shows the step, the percentage and the time left',
        (WidgetTester tester) async {
      await tester.pumpWidget(_host(MatchResultScreen(
        matchId: 'm-1',
        loader: _fixed(matchJson(status: 'running', progress: 0.62, stage: 'players', etaS: 95)),
        pictureLoader: _noPicture,
      )));
      await tester.pump();
      expect(find.text('Following the players'), findsWidgets);
      expect(find.text('62%'), findsOneWidget);
      expect(find.text('About 2 min left'), findsOneWidget);
      final LinearProgressIndicator bar = tester.widget(find.byType(LinearProgressIndicator));
      expect(bar.value, 0.62);
      expect(find.byIcon(Icons.check_circle), findsNWidgets(3)); // preparing, court and ball are done
      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('with no estimate yet it says nothing about time', (WidgetTester tester) async {
      await tester.pumpWidget(_host(MatchResultScreen(
        matchId: 'm-1',
        loader: _fixed(matchJson(status: 'running', progress: 0.1, stage: 'ball')),
        pictureLoader: _noPicture,
      )));
      await tester.pump();
      expect(find.textContaining('left'), findsNothing);
      expect(find.text('10%'), findsOneWidget);
      await tester.pumpWidget(const SizedBox());
    });
  });

  group('rallies', () {
    test('kind and feedback parse; an old record is a match without feedback', () {
      final MatchRecord rally = MatchRecord.fromJson(matchJson(kind: 'rally', feedback: feedbackJson()));
      expect(rally.kind, SessionKind.rally);
      expect(rally.feedback!.source, FeedbackSource.gemini);
      expect(rally.feedback!.improvements.single.title, 'Aim deeper');
      final MatchRecord old = MatchRecord.fromJson(matchJson());
      expect(old.kind, SessionKind.match);
      expect(old.feedback, isNull);
    });

    test('the create body names a rally and leaves a match as the default', () {
      expect(createMatchBody(storagePath: 'p', courtFormat: 'singles', kind: SessionKind.rally)['kind'], 'rally');
      expect(createMatchBody(storagePath: 'p', courtFormat: 'singles').containsKey('kind'), isFalse);
    });

    testWidgets('the rallies tab has its own words', (WidgetTester tester) async {
      await tester.pumpWidget(_host(Scaffold(
        body: MatchesScreen(
          kind: SessionKind.rally,
          loader: () async => const ApiResult<List<MatchRecord>>.ok(<MatchRecord>[]),
        ),
      )));
      await tester.pumpAndSettle();
      expect(find.text('Rallies'), findsOneWidget);
      expect(find.text('No rallies yet'), findsOneWidget);
      expect(find.text('New rally'), findsWidgets);
    });

    testWidgets('a rally shows its AI coaching above the numbers', (WidgetTester tester) async {
      tester.view.physicalSize = const Size(800, 4000);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(_host(MatchResultScreen(
        matchId: 'm-1',
        loader: _fixed(matchJson(kind: 'rally', result: courtSessionJson(), feedback: feedbackJson())),
        pictureLoader: _noPicture,
      )));
      await tester.pumpAndSettle();
      expect(find.byType(CoachingSummaryCard), findsOneWidget);
      expect(find.text('Coaching'), findsOneWidget);
      expect(find.text('An estimated 2 of your 5 landings were in.'), findsOneWidget);
      expect(find.text('Aim deeper'), findsOneWidget);
      expect(find.text('Cue: Hit up through the ball.'), findsOneWidget);
      expect(find.textContaining('Written by AI'), findsOneWidget);
      expect(find.byType(CourtMap), findsOneWidget);
    });

    testWidgets('a template summary says to film a longer rally', (WidgetTester tester) async {
      tester.view.physicalSize = const Size(800, 4000);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);
      final Map<String, dynamic> template = feedbackJson(source: 'template')
        ..['strengths'] = <String>[]
        ..['improvements'] = <Map<String, dynamic>>[];
      await tester.pumpWidget(_host(MatchResultScreen(
        matchId: 'm-1',
        loader: _fixed(matchJson(kind: 'rally', result: courtSessionJson(), feedback: template)),
        pictureLoader: _noPicture,
      )));
      await tester.pumpAndSettle();
      expect(find.textContaining('film a longer rally'), findsOneWidget);
      expect(find.text('What to work on'), findsNothing);
    });

    testWidgets('a match has no coaching card', (WidgetTester tester) async {
      await tester.pumpWidget(_host(MatchResultScreen(
        matchId: 'm-1',
        loader: _fixed(matchJson(result: courtSessionJson())),
        pictureLoader: _noPicture,
      )));
      await tester.pumpAndSettle();
      expect(find.byType(CoachingSummaryCard), findsNothing);
    });
  });

  group('matches tab', () {
    testWidgets('no matches shows the way in', (WidgetTester tester) async {
      await tester.pumpWidget(_host(Scaffold(
        body: MatchesScreen(loader: () async => const ApiResult<List<MatchRecord>>.ok(<MatchRecord>[])),
      )));
      await tester.pumpAndSettle();
      expect(find.text('No matches yet'), findsOneWidget);
      expect(find.text('New match'), findsWidgets);
    });

    testWidgets('a match lists its headline numbers', (WidgetTester tester) async {
      await tester.pumpWidget(_host(Scaffold(
        body: MatchesScreen(
          loader: () async => ApiResult<List<MatchRecord>>.ok(<MatchRecord>[MatchRecord.fromJson(matchJson())]),
        ),
      )));
      await tester.pumpAndSettle();
      expect(find.text('Sunday hit'), findsOneWidget);
      expect(find.text('1 rally · 5 shots landed · In 67%'), findsOneWidget);
    });
  });

  group('match result', () {
    testWidgets('a running match shows its progress', (WidgetTester tester) async {
      await tester.pumpWidget(_host(MatchResultScreen(
        matchId: 'm-1',
        loader: _fixed(matchJson(status: 'running', progress: 0.45)),
        pictureLoader: _noPicture,
      )));
      await tester.pump();
      expect(find.text('Tracking the ball'), findsWidgets);
      expect(find.text('45%'), findsOneWidget);
      expect(find.byType(LinearProgressIndicator), findsOneWidget);
      // Leave no poll timer behind.
      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('a complete match shows stats, the shot map, rallies and calls',
        (WidgetTester tester) async {
      tester.view.physicalSize = const Size(800, 3000);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(_host(MatchResultScreen(
        matchId: 'm-1',
        loader: _fixed(matchJson(result: courtSessionJson())),
        pictureLoader: _noPicture,
      )));
      await tester.pumpAndSettle();
      expect(find.text('Rallies'), findsWidgets);
      expect(find.text('5 shots'), findsWidgets);
      expect(find.text('67%'), findsOneWidget);
      expect(find.byType(CourtMap), findsOneWidget);
      expect(find.byType(LineCallCard), findsWidgets);
      expect(find.text('Everything here is an estimate from one camera.'), findsOneWidget);
    });

    testWidgets('an unverified camera explains why calls are off', (WidgetTester tester) async {
      final Map<String, dynamic> session = courtSessionJson();
      session['status'] = 'partial';
      for (final Map<String, dynamic> landing in (session['landings'] as List<Map<String, dynamic>>)) {
        landing['flags'] = <String>['camera_unverified'];
        landing['line_call'] = null;
      }
      await tester.pumpWidget(_host(MatchResultScreen(
        matchId: 'm-1',
        loader: _fixed(matchJson(result: session)),
        pictureLoader: _noPicture,
      )));
      await tester.pumpAndSettle();
      expect(find.textContaining('in/out calls are switched off'), findsOneWidget);
      expect(find.byType(LineCallCard), findsNothing);
    });

    testWidgets('a refused match says why and how to film it', (WidgetTester tester) async {
      final Map<String, dynamic> session = courtSessionJson()
        ..['status'] = 'refused'
        ..['refusal_reason'] = 'camera_moving'
        ..['landings'] = <Map<String, dynamic>>[]
        ..['rallies'] = <Map<String, dynamic>>[];
      await tester.pumpWidget(_host(MatchResultScreen(
        matchId: 'm-1',
        loader: _fixed(matchJson(result: session)),
        pictureLoader: _noPicture,
      )));
      await tester.pumpAndSettle();
      expect(find.text('We couldn’t track this one'), findsOneWidget);
      expect(find.text(CourtRefusalReason.cameraMoving.message), findsOneWidget);
    });

    testWidgets('a failed match shows the server message', (WidgetTester tester) async {
      await tester.pumpWidget(_host(MatchResultScreen(
        matchId: 'm-1',
        loader: _fixed(matchJson(status: 'failed', progress: null, errorMessage: 'That clip is too short to analyze.')),
        pictureLoader: _noPicture,
      )));
      await tester.pumpAndSettle();
      expect(find.text('This video could not be analysed'), findsOneWidget);
      expect(find.text('That clip is too short to analyze.'), findsOneWidget);
    });

    testWidgets('the court picture is shown with what it means', (WidgetTester tester) async {
      tester.view.physicalSize = const Size(800, 4000);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);
      // A 1x1 transparent PNG.
      final Uint8List png = Uint8List.fromList(<int>[
        0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,
        0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x06, 0x00, 0x00, 0x00, 0x1F, 0x15, 0xC4,
        0x89, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x44, 0x41, 0x54, 0x78, 0x9C, 0x63, 0x00, 0x01, 0x00, 0x00,
        0x05, 0x00, 0x01, 0x0D, 0x0A, 0x2D, 0xB4, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE,
        0x42, 0x60, 0x82,
      ]);
      await tester.pumpWidget(_host(MatchResultScreen(
        matchId: 'm-1',
        loader: _fixed(matchJson(result: courtSessionJson(), picture: true)),
        pictureLoader: (String _) async => ApiResult<Uint8List>.ok(png),
      )));
      await tester.pumpAndSettle();
      expect(find.text('Court lines we used'), findsOneWidget);
      expect(find.byType(Image), findsOneWidget);
      expect(find.textContaining('Found automatically'), findsOneWidget);
    });
  });
}
