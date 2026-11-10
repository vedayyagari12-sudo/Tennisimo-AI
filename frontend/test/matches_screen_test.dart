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
      expect(find.text('Tracking the ball… 45%'), findsOneWidget);
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
