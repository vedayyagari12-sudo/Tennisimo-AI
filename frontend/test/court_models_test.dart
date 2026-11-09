import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/models/court.dart';

import 'support/court_fixtures.dart';

void main() {
  group('section 7.3 sample, verbatim', () {
    final CourtAnalysis court =
        CourtAnalysis.fromJson(jsonDecode(kSection73CourtJson) as Map<String, dynamic>);

    test('top level', () {
      expect(court.status, CourtSessionStatus.partial);
      expect(court.refusalReason, isNull);
      expect(court.isEstimate, isTrue);
      expect(court.cameraVerdict, StabilityVerdict.shaky);
      expect(court.fps, 59.9);
    });

    test('summary', () {
      expect(court.summary.count, 212);
      expect(court.summary.verdictCounts[LineVerdict.tooCloseToCall], 43);
      expect(court.summary.depthExpectedCounts[DepthZone.beyond], 12.3);
      expect(court.summary.widthCounts[WidthZone.centre], 90);
      expect(court.summary.medianSigmaAlongM, 0.21);
    });

    test('landing', () {
      final ShotLanding l = court.landings.single;
      expect(l.bounceId, 'b_000123');
      expect(l.courtXy.x, 1.82);
      expect(l.courtXy.y, 10.44);
      expect(l.sigma.major, 0.31);
      expect(l.half, CourtEnd.far);
      expect(l.hitterEnd, CourtEnd.near);
      expect(l.hitter, isNull);
      expect(l.shotType, isNull);
      expect(l.target, LineCallTarget.singlesHalf);
      expect(l.lineCall!.verdict, LineVerdict.tooCloseToCall);
      expect(l.lineCall!.decidingLine, CourtLine.farBaseline);
      expect(l.lineCall!.uncertaintyRadiusM, 0.59);
      expect(l.depthProbabilities[DepthZone.deep], 0.62);
      expect(l.shotIndexInRally, 3);
    });

    test('rally', () {
      final Rally r = court.rallies.single;
      expect(r.lengthHits, 5);
      expect(r.isRange, isTrue);
      expect(r.endReason, RallyEndReason.lostTrack);
      expect(r.lengthConfidence, LengthConfidence.medium);
      expect(r.bounceIds, <String>['b_000120', 'b_000123']);
    });

    test('score', () {
      final ScoreSummary s =
          ScoreSummary.fromJson(jsonDecode(kSection73ScoreJson) as Map<String, dynamic>);
      expect(s.isConfirmed, isFalse);
      expect(s.needsReviewCount, 3);
      expect(s.current.sets, <(int, int)>[(6, 4)]);
      expect(s.current.games, (2, 1));
      expect(s.current.pointsDisplay, '30-15');
      expect(s.current.pointLabels, isNull);
      expect(s.current.server, PlayerId.playerB);
      expect(s.current.matchWinner, isNull);
      final ScoreTimelineEntry e = s.timeline.single;
      expect(e.kind, ScoreEventKind.pointUnresolved);
      expect(e.winner, isNull);
      expect(e.basis, InferenceBasis.lostTrack);
      expect(e.needsReview, isTrue);
    });
  });

  group('defensive parsing', () {
    test('unknown enum strings become unrecognized, never throw', () {
      final Map<String, dynamic> json = landingJson(id: 'b', x: 0, y: 1)
        ..['half'] = 'centre_court'
        ..['hitter_end'] = 'sky'
        ..['depth_zone'] = 'galaxy';
      final ShotLanding l = ShotLanding.fromJson(json);
      expect(l.half, CourtEnd.unrecognized);
      expect(l.hitterEnd, CourtEnd.unrecognized);
      expect(l.depthZone, DepthZone.unrecognized);
    });

    test('null stays null (brief G20)', () {
      final ShotLanding l = ShotLanding.fromJson(landingJson(id: 'b', x: 0, y: 1, hitterEnd: null, verdict: null));
      expect(l.hitterEnd, isNull);
      expect(l.lineCall, isNull);
      expect(l.verdict, isNull);
      expect(CourtRefusalReason.fromJsonOrNull(null), isNull);
      expect(CourtRefusalReason.fromJsonOrNull('solar_flare'), CourtRefusalReason.unrecognized);
      expect(InferenceBasis.fromJsonOrNull(null), isNull);
    });

    test('a body without is_estimate is still an estimate', () {
      final Map<String, dynamic> json = courtSessionJson()..remove('is_estimate');
      expect(CourtAnalysis.fromJson(json).isEstimate, isTrue);
    });

    test('an unknown score status is an estimate', () {
      final Map<String, dynamic> json =
          jsonDecode(kSection73ScoreJson) as Map<String, dynamic>..['status'] = 'final?';
      expect(ScoreSummary.fromJson(json).isConfirmed, isFalse);
    });

    test('a refused body parses with no landings', () {
      final CourtAnalysis court = CourtAnalysis.fromJson(<String, dynamic>{
        'status': 'refused',
        'refusal_reason': 'camera_moving',
        'summary': <String, dynamic>{'count': 0},
      });
      expect(court.status, CourtSessionStatus.refused);
      expect(court.refusalReason, CourtRefusalReason.cameraMoving);
      expect(court.landings, isEmpty);
      expect(court.summary.medianSigmaAlongM, isNull);
    });
  });
}
