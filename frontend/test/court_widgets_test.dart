import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/models/court.dart';
import 'package:tennisimo_ai/theme/app_theme.dart';
import 'package:tennisimo_ai/widgets/court_map.dart';
import 'package:tennisimo_ai/widgets/line_call_card.dart';
import 'package:tennisimo_ai/widgets/rally_timeline.dart';
import 'package:tennisimo_ai/widgets/score_panel.dart';

import 'support/court_fixtures.dart';

final CourtAnalysis _session = CourtAnalysis.fromJson(courtSessionJson());

ShotLanding _landing(String id) => _session.landings.firstWhere((ShotLanding l) => l.bounceId == id);

Widget _host(Widget child, {AppPalette? palette, double textScale = 1.0, double width = 400}) {
  final ThemeData base = buildAppTheme();
  return MaterialApp(
    theme: palette == null ? base : base.copyWith(extensions: <ThemeExtension<dynamic>>[palette]),
    home: MediaQuery(
      data: MediaQueryData(size: Size(width, 2000), textScaler: TextScaler.linear(textScale)),
      child: Scaffold(
        body: SingleChildScrollView(
          child: Center(child: SizedBox(width: width, child: child)),
        ),
      ),
    ),
  );
}

void main() {
  group('court map', () {
    testWidgets('counts every verdict, too close never as in', (WidgetTester tester) async {
      await tester.pumpWidget(_host(CourtMap(landings: _session.landings)));
      expect(find.text('5 landings, 2 in, 1 out, 1 too close to call, 1 not called'), findsOneWidget);
      expect(find.text(kCourtMapFootnote), findsOneWidget);
    });

    testWidgets('filters narrow the landings', (WidgetTester tester) async {
      await tester.pumpWidget(_host(CourtMap(landings: _session.landings)));
      await tester.tap(find.text('Opponent'));
      await tester.pump();
      expect(find.text('1 landing, 1 in, 0 out, 0 too close to call'), findsOneWidget);
      await tester.tap(find.text('All'));
      await tester.tap(find.text('Serves'));
      await tester.pump();
      expect(find.text('1 landing, 1 in, 0 out, 0 too close to call'), findsOneWidget);
      await tester.tap(find.text('Serves'));
      await tester.tap(find.text('Out'));
      await tester.pump();
      expect(find.text('1 landing, 0 in, 1 out, 0 too close to call'), findsOneWidget);
    });

    test('your shots are the near player\'s; an unknown hitter is neither', () {
      const CourtMapFilter yours = CourtMapFilter(side: CourtMapSide.yours);
      const CourtMapFilter theirs = CourtMapFilter(side: CourtMapSide.opponent);
      final ShotLanding unknown = _landing('b_000005');
      expect(yours.accepts(unknown) || theirs.accepts(unknown), isFalse);
      expect(yours.apply(_session.landings).length, 3);
    });

    testWidgets('tapping a mark selects that landing', (WidgetTester tester) async {
      ShotLanding? picked;
      await tester.pumpWidget(_host(CourtMap(landings: _session.landings, onSelect: (ShotLanding l) => picked = l)));
      final Finder canvas = find.byKey(const Key('court-map-canvas'));
      final Size size = tester.getSize(canvas);
      final Offset origin = tester.getTopLeft(canvas);
      final ShotLanding target = _landing('b_000003');
      await tester.tapAt(origin + CourtMapPainter.toCanvas(target.courtXy, size));
      expect(picked?.bounceId, 'b_000003');
    });

    test('hit test misses empty court', () {
      const Size size = Size(300, 300 / CourtMapPainter.aspectRatio);
      expect(CourtMapPainter.landingAt(Offset.zero, size, _session.landings), isNull);
      final ShotLanding l = _landing('b_000001');
      expect(CourtMapPainter.landingAt(CourtMapPainter.toCanvas(l.courtXy, size), size, _session.landings)?.bounceId,
          'b_000001');
    });

    test('far baseline is at the top, net in the middle', () {
      const Size size = Size(130, 290);
      expect(CourtMapPainter.toCanvas(const CourtPoint(0, 11.885), size).dy, lessThan(size.height / 4));
      expect(CourtMapPainter.toCanvas(const CourtPoint(0, 0), size).dy, closeTo(size.height / 2, 1e-9));
    });

    test('depth line uses each landing\'s probabilities', () {
      expect(depthLine(_session.landings), 'Depth: Short 0%  Mid 25%  Deep 75%  Beyond 0%');
      expect(depthLine(const <ShotLanding>[]), 'Depth: no landings yet');
    });

    for (final AppPalette palette in <AppPalette>[...kAllPalettes, ...kAllDarkPalettes]) {
      testWidgets('renders on every palette', (WidgetTester tester) async {
        await tester.pumpWidget(_host(CourtMap(landings: _session.landings), palette: palette));
        expect(tester.takeException(), isNull);
      });
    }

    testWidgets('large text on a narrow phone does not overflow', (WidgetTester tester) async {
      await tester.pumpWidget(_host(CourtMap(landings: _session.landings), textScale: 2.0, width: 320));
      expect(tester.takeException(), isNull);
    });

    testWidgets('has a semantic summary', (WidgetTester tester) async {
      final SemanticsHandle handle = tester.ensureSemantics();
      await tester.pumpWidget(_host(CourtMap(landings: _session.landings)));
      expect(find.bySemanticsLabel(RegExp('^Court map\\. 5 landings')), findsOneWidget);
      handle.dispose();
    });
  });

  group('line call card', () {
    testWidgets('too close reads calmly, with distance and uncertainty', (WidgetTester tester) async {
      final ShotLanding sample = CourtAnalysis.fromJson(
              jsonDecode(kSection73CourtJson) as Map<String, dynamic>)
          .landings
          .single;
      await tester.pumpWidget(_host(LineCallCard(landing: sample)));
      expect(find.text('Too close to call'), findsOneWidget);
      expect(find.text('Far baseline'), findsOneWidget);
      expect(find.text('About 21 cm inside the line, uncertainty about 59 cm.'), findsOneWidget);
      expect(find.text('View in video'), findsNothing);
    });

    testWidgets('no call explains how to get one', (WidgetTester tester) async {
      await tester.pumpWidget(_host(LineCallCard(landing: _landing('b_000005'), onViewVideo: () {})));
      expect(find.text('Not called'), findsOneWidget);
      expect(find.text(kNoCallText), findsOneWidget);
      expect(find.text('View in video'), findsOneWidget);
    });

    test('centimetres never round a real distance to 0', () {
      expect(centimetres(0.004), 'under 1 cm');
      expect(centimetres(-0.4), '40 cm');
      expect(lineCallSentence(_landing('b_000003').lineCall), startsWith('About 40 cm beyond'));
    });
  });

  group('rally timeline', () {
    testWidgets('shows ranges, end reasons and the header', (WidgetTester tester) async {
      Rally? picked;
      await tester.pumpWidget(_host(RallyTimeline(rallies: _session.rallies, onSelect: (Rally r) => picked = r)));
      expect(find.text('Rallies   Longest 5   Median 2'), findsOneWidget);
      expect(find.text('5 hits (could be 6)'), findsOneWidget);
      expect(find.text('2 hits'), findsOneWidget);
      expect(find.text('Ended: lost track'), findsOneWidget);
      expect(find.text('3:28'), findsOneWidget);
      await tester.tap(find.text('2 hits'));
      expect(picked?.rallyId, 'r_0001');
    });

    test('length text for a wide range', () {
      final Rally r = Rally.fromJson(<String, dynamic>{
        'rally_id': 'r', 'length_hits': 5, 'length_min': 4, 'length_max': 7,
        'end_reason': 'timeout',
      });
      expect(rallyLengthText(r), '5 hits (could be 4 to 7)');
      expect(clockText(-3), '0:00');
    });

    testWidgets('empty', (WidgetTester tester) async {
      await tester.pumpWidget(_host(const RallyTimeline(rallies: <Rally>[])));
      expect(find.text('No rallies found yet.'), findsOneWidget);
    });
  });

  group('score panel', () {
    final ScoreSummary score =
        ScoreSummary.fromJson(jsonDecode(kSection73ScoreJson) as Map<String, dynamic>);

    testWidgets('an estimate says so, and corrections go out', (WidgetTester tester) async {
      final List<(String, PlayerId?)> calls = <(String, PlayerId?)>[];
      await tester.pumpWidget(_host(ScorePanel(
        score: score,
        onCorrect: (String id, PlayerId? w) => calls.add((id, w)),
      )));
      expect(find.text('Score (estimate)'), findsOneWidget);
      expect(find.text('3 points need your review'), findsOneWidget);
      expect(find.text('Points: 30-15'), findsOneWidget);
      expect(find.text('Lost track'), findsOneWidget);
      await tester.tap(find.widgetWithText(OutlinedButton, 'You'));
      await tester.tap(find.text('Replay'));
      expect(calls, <(String, PlayerId?)>[('se_0091', PlayerId.playerA), ('se_0091', null)]);
    });

    test('confirmed drops the estimate label', () {
      final Map<String, dynamic> json = jsonDecode(kSection73ScoreJson) as Map<String, dynamic>
        ..['status'] = 'confirmed';
      expect(scoreTitle(ScoreSummary.fromJson(json)), 'Score');
      expect(reviewLine(1), '1 point needs your review');
    });
  });

  test('no screen imports the court widgets yet (blocked on Phase 1)', () {
    for (final FileSystemEntity f in Directory('lib/screens').listSync(recursive: true)) {
      if (f is! File || !f.path.endsWith('.dart')) continue;
      final String text = f.readAsStringSync();
      for (final String name in <String>['court_map.dart', 'line_call_card.dart', 'rally_timeline.dart', 'score_panel.dart']) {
        expect(text.contains(name), isFalse, reason: '${f.path} imports $name');
      }
    }
  });
}
