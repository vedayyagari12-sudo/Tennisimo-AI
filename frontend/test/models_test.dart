import 'package:flutter_test/flutter_test.dart';
import 'package:tennisform_ai/models/analysis_response.dart';
import 'package:tennisform_ai/models/ball_speed.dart';
import 'package:tennisform_ai/models/ball_speed_calibration.dart';
import 'package:tennisform_ai/models/coaching_feedback.dart';
import 'package:tennisform_ai/models/enums.dart';
import 'package:tennisform_ai/models/metric_score.dart';

void main() {
  group('enum parsing degrades safely', () {
    test('unknown strings fall back instead of throwing', () {
      expect(MetricVerdict.fromJson('sideways'), MetricVerdict.unavailable);
      expect(MetricVerdict.fromJson(null), MetricVerdict.unavailable);
      expect(MetricUnit.fromJson('mph'), MetricUnit.ratio);
      expect(ShotType.fromJson('drop_shot'), ShotType.unknown);
      expect(ShotType.fromJson(42), ShotType.unknown);
      expect(BallSpeedConfidence.fromJson('excellent'),
          BallSpeedConfidence.unavailable);
      expect(FeedbackSource.fromJson('gpt'), FeedbackSource.template);
      expect(Handedness.fromJson('ambidextrous'), Handedness.unknown);
      expect(JobStatus.fromJson('cancelled'), JobStatus.unrecognized);
    });

    test('known strings parse', () {
      expect(MetricVerdict.fromJson('ideal'), MetricVerdict.ideal);
      expect(MetricUnit.fromJson('TU/s'), MetricUnit.torsoUnitsPerSec);
      expect(ShotType.fromJson('backhand_two_handed'),
          ShotType.backhandTwoHanded);
      expect(BallSpeedConfidence.fromJson('medium'), BallSpeedConfidence.medium);
    });

    test('unknown analysis status is neither complete nor partial', () {
      // Regression: the fallback used to be `complete`, which silently
      // presented an analysis of unknown standing as a fully successful one.
      // `partial` would be wrong the other way. It must be the sentinel.
      expect(
        AnalysisStatus.fromJson('some_future_value_this_client_does_not_know'),
        isNot(AnalysisStatus.complete),
      );
      expect(
        AnalysisStatus.fromJson('some_future_value_this_client_does_not_know'),
        isNot(AnalysisStatus.partial),
      );
      expect(
        AnalysisStatus.fromJson('some_future_value_this_client_does_not_know'),
        AnalysisStatus.unrecognized,
      );
      expect(AnalysisStatus.fromJson(null), AnalysisStatus.unrecognized);
      expect(AnalysisStatus.fromJson(7), AnalysisStatus.unrecognized);
      // The real contract still parses exactly as before.
      expect(AnalysisStatus.fromJson('complete'), AnalysisStatus.complete);
      expect(AnalysisStatus.fromJson('partial'), AnalysisStatus.partial);
    });

    test('unknown job status is not read as queued', () {
      // Regression: the fallback used to be `queued`, which the poll loop does
      // not treat as terminal — so an unrecognised status was polled as work
      // still in progress until the 120 s cap instead of surfacing at once.
      expect(
        JobStatus.fromJson('some_future_value_this_client_does_not_know'),
        isNot(JobStatus.queued),
      );
      expect(
        JobStatus.fromJson('some_future_value_this_client_does_not_know'),
        JobStatus.unrecognized,
      );
      expect(JobStatus.fromJson(null), JobStatus.unrecognized);
      expect(JobStatus.fromJson(9), JobStatus.unrecognized);
      // The real contract still parses exactly as before.
      expect(JobStatus.fromJson('queued'), JobStatus.queued);
      expect(JobStatus.fromJson('running'), JobStatus.running);
      expect(JobStatus.fromJson('succeeded'), JobStatus.succeeded);
      expect(JobStatus.fromJson('failed'), JobStatus.failed);
    });

    test('unavailable reason: unknown yields null rather than a guess', () {
      expect(BallSpeedUnavailableReason.fromJson('cosmic_rays'), isNull);
      expect(BallSpeedUnavailableReason.fromJson(null), isNull);
      expect(BallSpeedUnavailableReason.fromJson('too_few_detections'),
          BallSpeedUnavailableReason.tooFewDetections);
    });

    test('error codes map to plain language, unknown falls through', () {
      expect(errorCodeToPlainLanguage('contact_not_found', fallback: 'x'),
          contains('ball-strike'));
      expect(errorCodeToPlainLanguage('brand_new_code', fallback: 'fallback'),
          'fallback');
      expect(errorCodeToPlainLanguage(null, fallback: 'fallback'), 'fallback');
    });
  });

  group('MetricScore', () {
    test('null value renders as not measurable, never zero', () {
      final MetricScore metric = MetricScore.fromJson(<String, dynamic>{
        'name': 'wrist_lag_deg',
        'value': null,
        'unit': 'deg',
        'verdict': 'unavailable',
      });
      expect(metric.value, isNull);
      expect(metric.isMeasurable, isFalse);
      expect(metric.displayValue, 'not measurable in this clip');
      expect(metric.displayValue, isNot(contains('0')));
    });

    test('missing optional fields do not throw', () {
      final MetricScore metric =
          MetricScore.fromJson(<String, dynamic>{'name': 'shoulder_turn_deg'});
      expect(metric.unit, MetricUnit.ratio);
      expect(metric.verdict, MetricVerdict.unavailable);
      expect(metric.score, isNull);
      expect(metric.hasIdealBand, isFalse);
      expect(metric.viewSensitive, isFalse);
    });

    test('accepts both score and score_0_100 spellings', () {
      expect(
        MetricScore.fromJson(<String, dynamic>{'name': 'a', 'score': 71.0})
            .score,
        71.0,
      );
      expect(
        MetricScore.fromJson(<String, dynamic>{'name': 'a', 'score_0_100': 82})
            .score,
        82.0,
      );
    });

    test('formats value and band with the unit, never converting TU', () {
      final MetricScore metric = MetricScore.fromJson(<String, dynamic>{
        'name': 'peak_hand_speed_tu_s',
        'value': 8.42,
        'unit': 'TU/s',
        'verdict': 'ideal',
        'ideal_min': 7.0,
        'ideal_max': 11.0,
      });
      expect(metric.displayValue, '8.4 TU/s');
      expect(metric.displayIdealBand, '7.0–11.0 TU/s');
      expect(metric.displayName, 'Peak hand speed');
    });
  });

  group('BallSpeedResult', () {
    test('parses the implemented (narrow) backend model', () {
      final BallSpeedResult result = BallSpeedResult.fromJson(<String, dynamic>{
        'ball_speed_mph': 68,
        'confidence': 'medium',
        'detections_used': 6,
        'unavailable_reason': null,
      });
      expect(result.ballSpeedMph, 68);
      expect(result.hasSpeed, isTrue);
      expect(result.displaySpeed, '68 mph');
      expect(result.confidence, BallSpeedConfidence.medium);
      // Plan-only fields absent -> safe fallbacks.
      expect(result.confidenceCapsApplied, isEmpty);
      expect(result.measurementDefinition, kBallSpeedMeasurementDefinition);
      expect(result.hasCalibrationEcho, isFalse);
    });

    test('prefers a server-sent measurement definition', () {
      final BallSpeedResult result = BallSpeedResult.fromJson(<String, dynamic>{
        'ball_speed_mph': 70,
        'measurement_definition': 'Server text.',
      });
      expect(result.measurementDefinition, 'Server text.');
    });

    test('skipped calibration is not treated as a calibrated failure', () {
      final BallSpeedResult result = BallSpeedResult.fromJson(<String, dynamic>{
        'ball_speed_mph': null,
        'confidence': 'unavailable',
        'detections_used': 0,
        'unavailable_reason': 'not_calibrated',
      });
      expect(result.hasSpeed, isFalse);
      expect(result.userCalibrated, isFalse);
    });

    test('a calibrated failure is explainable', () {
      final BallSpeedResult result = BallSpeedResult.fromJson(<String, dynamic>{
        'ball_speed_mph': null,
        'confidence': 'unavailable',
        'detections_used': 2,
        'unavailable_reason': 'too_few_detections',
      });
      expect(result.userCalibrated, isTrue);
      expect(result.unavailableReason!.explanation, isNotEmpty);
    });

    test('a decimal mph is rejected rather than rounded', () {
      final BallSpeedResult result = BallSpeedResult.fromJson(
          <String, dynamic>{'ball_speed_mph': 68.4});
      expect(result.ballSpeedMph, isNull);
      expect(result.hasSpeed, isFalse);
    });

    test('empty json parses to a fully unavailable result', () {
      final BallSpeedResult result =
          BallSpeedResult.fromJson(<String, dynamic>{});
      expect(result.ballSpeedMph, isNull);
      expect(result.confidence, BallSpeedConfidence.unavailable);
      expect(result.detectionsUsed, 0);
      expect(result.userCalibrated, isFalse);
    });
  });

  group('CoachingFeedback', () {
    test('parses and orders improvements by priority', () {
      final CoachingFeedback feedback =
          CoachingFeedback.fromJson(<String, dynamic>{
        'summary': 'Solid base.',
        'strengths': <dynamic>['Good balance', 7, null],
        'improvements': <dynamic>[
          <String, dynamic>{
            'priority': 3,
            'title': 'C',
            'why': 'w',
            'cue': 'c',
            'drill': 'd',
          },
          <String, dynamic>{
            'priority': 1,
            'title': 'A',
            'why': 'w',
            'cue': 'c',
            'drill': 'd',
            'metric_refs': <dynamic>['shoulder_turn_deg'],
          },
        ],
        'source': 'gemini',
        'guard': <String, dynamic>{'passed': true, 'mph_rule': 'banned'},
      });
      expect(feedback.summary, 'Solid base.');
      // Non-string strengths are dropped, not stringified.
      expect(feedback.strengths, <String>['Good balance']);
      expect(feedback.improvements.first.title, 'A');
      expect(feedback.improvements.first.metricRefs,
          <String>['shoulder_turn_deg']);
      expect(feedback.source, FeedbackSource.gemini);
      expect(feedback.guard!.mphRule, 'banned');
    });

    test('a missing guard block does not throw', () {
      final CoachingFeedback feedback =
          CoachingFeedback.fromJson(<String, dynamic>{'summary': 'Hi'});
      expect(feedback.guard, isNull);
      expect(feedback.improvements, isEmpty);
      expect(feedback.strengths, isEmpty);
    });
  });

  group('AnalysisResponse', () {
    test('parses a v2-shaped payload with nested blocks', () {
      final AnalysisResponse analysis =
          AnalysisResponse.fromJson(<String, dynamic>{
        'analysis_id': 'abc-123',
        'created_at': '2026-08-28T10:30:00Z',
        'status': 'complete',
        'pipeline_version': 'v2',
        'shot_type': <String, dynamic>{
          'shot_type': 'forehand_topspin',
          'confidence': 0.82,
        },
        'scorecard': <String, dynamic>{
          'overall_score': 74.0,
          'metrics': <dynamic>[
            <String, dynamic>{
              'name': 'shoulder_turn_deg',
              'value': 88.0,
              'unit': 'deg',
              'verdict': 'ideal',
              'ideal_min': 80.0,
              'ideal_max': 100.0,
            },
          ],
        },
        'ball_speed': <String, dynamic>{
          'ball_speed_mph': 61,
          'confidence': 'medium',
          'detections_used': 6,
        },
        'feedback': <String, dynamic>{'summary': 'Nice turn.'},
        'warnings': <dynamic>['ankles_not_visible'],
      });

      expect(analysis.analysisId, 'abc-123');
      expect(analysis.shotType, ShotType.forehandTopspin);
      expect(analysis.shotTypeConfidence, 0.82);
      expect(analysis.status, AnalysisStatus.complete);
      expect(analysis.overallScore, 74.0);
      expect(analysis.metrics.single.name, 'shoulder_turn_deg');
      expect(analysis.ballSpeed!.ballSpeedMph, 61);
      expect(analysis.feedback!.summary, 'Nice turn.');
      expect(analysis.warnings, <String>['ankles_not_visible']);
      expect(analysis.createdAt, isNotNull);
    });

    test('a v1-era row with no ball_speed block yields a null ball speed', () {
      final AnalysisResponse analysis =
          AnalysisResponse.fromJson(<String, dynamic>{
        'analysis_id': 'v1-row',
        'pipeline_version': 'v1',
        'shot_type': 'serve',
        'overall_score': 55,
      });
      expect(analysis.ballSpeed, isNull);
      expect(analysis.shotType, ShotType.serve);
      expect(analysis.overallScore, 55.0);
      expect(analysis.metrics, isEmpty);
      expect(analysis.feedback, isNull);
    });

    test('a flat top-level SwingMetrics object is not read as a metric list',
        () {
      final AnalysisResponse analysis =
          AnalysisResponse.fromJson(<String, dynamic>{
        'analysis_id': 'x',
        'metrics': <String, dynamic>{'shoulder_turn_deg': 90.0},
      });
      expect(analysis.metrics, isEmpty);
    });

    test('a top-level metric list is used when there is no scorecard', () {
      final AnalysisResponse analysis =
          AnalysisResponse.fromJson(<String, dynamic>{
        'analysis_id': 'x',
        'metrics': <dynamic>[
          <String, dynamic>{'name': 'wrist_lag_deg', 'unit': 'deg'},
        ],
      });
      expect(analysis.metrics.single.name, 'wrist_lag_deg');
    });

    test('a partial payload parses as partial', () {
      final AnalysisResponse analysis =
          AnalysisResponse.fromJson(<String, dynamic>{
        'analysis_id': 'partial-row',
        'status': 'partial',
      });
      expect(analysis.status, AnalysisStatus.partial);
    });

    test('a complete payload parses as complete', () {
      final AnalysisResponse analysis =
          AnalysisResponse.fromJson(<String, dynamic>{
        'analysis_id': 'complete-row',
        'status': 'complete',
      });
      expect(analysis.status, AnalysisStatus.complete);
    });

    test('unknown or missing status is flagged, not read as complete', () {
      // An unrecognised string must neither fabricate a degraded-analysis
      // banner nor pass itself off as a fully successful analysis.
      expect(
        AnalysisResponse.fromJson(<String, dynamic>{
          'analysis_id': 'x',
          'status': 'half_done',
        }).status,
        AnalysisStatus.unrecognized,
      );
      expect(
        AnalysisResponse.fromJson(<String, dynamic>{'analysis_id': 'x'}).status,
        AnalysisStatus.unrecognized,
      );
      expect(
        AnalysisResponse.fromJson(<String, dynamic>{
          'analysis_id': 'x',
          'status': null,
        }).status,
        AnalysisStatus.unrecognized,
      );
      expect(
        AnalysisResponse.fromJson(<String, dynamic>{
          'analysis_id': 'x',
          'status': 7,
        }).status,
        AnalysisStatus.unrecognized,
      );
    });

    test('a partial analysis still reports null metrics as not measurable', () {
      final AnalysisResponse analysis =
          AnalysisResponse.fromJson(<String, dynamic>{
        'analysis_id': 'partial-row',
        'status': 'partial',
        'scorecard': <String, dynamic>{
          'metrics': <dynamic>[
            <String, dynamic>{
              'name': 'wrist_lag_deg',
              'value': null,
              'unit': 'deg',
              'verdict': 'unavailable',
            },
          ],
        },
      });
      expect(analysis.status, AnalysisStatus.partial);
      expect(analysis.metrics.single.displayValue,
          'not measurable in this clip');
    });

    test('an empty payload parses without throwing', () {
      final AnalysisResponse analysis =
          AnalysisResponse.fromJson(<String, dynamic>{});
      expect(analysis.analysisId, '');
      expect(analysis.shotType, ShotType.unknown);
      expect(analysis.overallScore, isNull);
      expect(analysis.ballSpeed, isNull);
      expect(analysis.createdAt, isNull);
    });
  });

  group('AnalysisSummary', () {
    test('reads the indexed-column row shape', () {
      final AnalysisSummary summary = AnalysisSummary.fromJson(<String, dynamic>{
        'id': 'row-1',
        'created_at': '2026-08-27T09:00:00Z',
        'shot_type': 'volley',
        'overall_score': 63.5,
        'ball_speed_mph': 44,
      });
      expect(summary.analysisId, 'row-1');
      expect(summary.shotType, ShotType.volley);
      expect(summary.overallScore, 63.5);
      expect(summary.ballSpeedMph, 44);
    });

    test('reads a full payload shape, and a missing speed stays null', () {
      final AnalysisSummary summary = AnalysisSummary.fromJson(<String, dynamic>{
        'analysis_id': 'row-2',
        'shot_type': <String, dynamic>{'shot_type': 'serve'},
        'scorecard': <String, dynamic>{'overall_score': 70.0},
        'ball_speed': <String, dynamic>{'ball_speed_mph': null},
      });
      expect(summary.analysisId, 'row-2');
      expect(summary.shotType, ShotType.serve);
      expect(summary.overallScore, 70.0);
      expect(summary.ballSpeedMph, isNull);
    });
  });

  group('BallSpeedCalibration', () {
    test('serializes the canonical distance and the preview surface', () {
      final BallSpeedCalibration calibration = BallSpeedCalibration(
        pointA: const NormalizedPoint(x: 0.1, y: 0.8),
        pointB: const NormalizedPoint(x: 0.9, y: 0.5),
        reference: CourtReference.sidelineBaselineToNet,
        captureWidthPx: 1080,
        captureHeightPx: 1920,
        tappedAt: DateTime.utc(2026, 8, 28, 12),
      );
      final Map<String, dynamic> json = calibration.toJson();

      expect(json['reference'], 'sideline_baseline_to_net');
      expect(json['distance_m'], 11.885);
      expect(json['capture_width_px'], 1080);
      expect(json['capture_height_px'], 1920);
      expect(json['capture_rotation_deg'], 0);
      expect(json['point_a'], <String, dynamic>{'x': 0.1, 'y': 0.8});
      expect(json['tapped_at'], '2026-08-28T12:00:00.000Z');
      // The JWT is the identity: no user id or email is ever in the payload.
      expect(json.keys, isNot(contains('user_id')));
      expect(json.keys, isNot(contains('email')));
    });

    test('service-line reference carries its own canonical distance', () {
      const BallSpeedCalibration calibration = BallSpeedCalibration(
        pointA: NormalizedPoint(x: 0.2, y: 0.7),
        pointB: NormalizedPoint(x: 0.6, y: 0.6),
        reference: CourtReference.sidelineBaselineToServiceLine,
        captureWidthPx: 720,
        captureHeightPx: 1280,
      );
      expect(calibration.toJson()['distance_m'], 5.485);
    });

    test('taps closer than the server minimum are rejected locally', () {
      const BallSpeedCalibration tooClose = BallSpeedCalibration(
        pointA: NormalizedPoint(x: 0.50, y: 0.50),
        pointB: NormalizedPoint(x: 0.52, y: 0.51),
        reference: CourtReference.sidelineBaselineToNet,
        captureWidthPx: 1080,
        captureHeightPx: 1920,
      );
      expect(tooClose.isFarEnoughApart, isFalse);

      const BallSpeedCalibration farEnough = BallSpeedCalibration(
        pointA: NormalizedPoint(x: 0.1, y: 0.5),
        pointB: NormalizedPoint(x: 0.9, y: 0.5),
        reference: CourtReference.sidelineBaselineToNet,
        captureWidthPx: 1080,
        captureHeightPx: 1920,
      );
      expect(farEnough.isFarEnoughApart, isTrue);
      expect(farEnough.separation, closeTo(0.8, 1e-9));
    });
  });
}
