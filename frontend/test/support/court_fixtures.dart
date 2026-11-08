/// Court mode fixtures. TEST-ONLY: nothing in `lib/` imports this file.
///
/// [kSection73CourtJson] and [kSection73ScoreJson] are the SESSION_MODE_PHASE2.md
/// section 7.3 samples verbatim. [courtSessionJson] builds a small session with
/// one landing of every kind, in invented positions that describe no player.
library;

const String kSection73CourtJson = '''
{
  "status": "partial",
  "refusal_reason": null,
  "court_engine_version": "court_engine_v1",
  "is_estimate": true,
  "camera": {
    "verdict": "shaky",
    "p95_shift_fraction": 0.0014,
    "rms_shift_px": 1.2,
    "bumped_at_s": null
  },
  "capture": {"fps": 59.9, "frame_width_px": 1920, "frame_height_px": 1080},
  "summary": {
    "count": 212,
    "verdict_counts": {"in": 151, "out": 18, "too_close_to_call": 43},
    "depth_expected_counts": {"short": 40.2, "mid": 88.1, "deep": 71.4, "beyond": 12.3},
    "width_counts": {"left": 61, "centre": 90, "right": 61},
    "median_sigma_along_m": 0.21
  },
  "landings": [
    {
      "bounce_id": "b_000123",
      "time_s": 212.437,
      "time_sigma_s": 0.004,
      "court_xy_m": [1.82, 10.44],
      "sigma_m": {"major": 0.31, "minor": 0.05, "angle_deg": 88.0},
      "half": "far",
      "hitter_end": "near",
      "shot_type": null,
      "is_serve": false,
      "target": "singles_half",
      "line_call": {
        "verdict": "too_close_to_call",
        "deciding_line": "far_baseline",
        "signed_margin_m": 0.21,
        "normal_sigma_m": 0.30,
        "uncertainty_radius_m": 0.59,
        "flags": []
      },
      "depth_zone": "deep",
      "depth_zone_confident": false,
      "depth_probabilities": {"short": 0.0, "mid": 0.14, "deep": 0.62, "beyond": 0.24},
      "width_zone": "right",
      "confidence": 0.72,
      "rally_id": "r_0017",
      "shot_index_in_rally": 3,
      "flags": []
    }
  ],
  "rallies": [
    {
      "rally_id": "r_0017",
      "start_s": 208.1,
      "end_s": 216.9,
      "length_hits": 5,
      "length_min": 5,
      "length_max": 6,
      "length_confidence": "medium",
      "hits_observed": 4,
      "hits_inferred": 1,
      "end_reason": "lost_track",
      "bounce_ids": ["b_000120", "b_000123"],
      "flags": []
    }
  ]
}
''';

const String kSection73ScoreJson = '''
{
  "status": "estimate",
  "needs_review_count": 3,
  "score_rules_version": "score_rules_v1",
  "current": {
    "sets": [[6, 4]], "games": [2, 1], "points_display": "30-15",
    "in_tiebreak": false, "server": "player_b", "serve_side": "ad",
    "player_a_end": "far", "match_winner": null, "unresolved_before": 2
  },
  "timeline": [
    {"event_id": "se_0091", "rally_id": "r_0017", "kind": "point_unresolved",
     "winner": null, "source": "inferred", "basis": "lost_track",
     "needs_review": true, "points_display": "30-15"}
  ]
}
''';

Map<String, dynamic> landingJson({
  required String id,
  required double x,
  required double y,
  String? verdict = 'in',
  String? hitterEnd = 'near',
  bool isServe = false,
  double major = 0.2,
  double minor = 0.05,
  String? rallyId = 'r_0000',
  int? index = 1,
}) =>
    <String, dynamic>{
      'bounce_id': id,
      'time_s': 10.0,
      'time_sigma_s': 0.004,
      'court_xy_m': <double>[x, y],
      'sigma_m': <String, dynamic>{'major': major, 'minor': minor, 'angle_deg': 90.0},
      'half': y >= 0 ? 'far' : 'near',
      'hitter_end': hitterEnd,
      'shot_type': isServe ? 'serve' : null,
      'is_serve': isServe,
      'target': hitterEnd == null ? 'half_only' : 'singles_half',
      'line_call': verdict == null
          ? null
          : <String, dynamic>{
              'verdict': verdict,
              'deciding_line': 'far_baseline',
              'signed_margin_m': verdict == 'out' ? -0.4 : 0.3,
              'normal_sigma_m': 0.1,
              'uncertainty_radius_m': 0.2,
              'flags': <String>[],
            },
      'depth_zone': 'deep',
      'depth_zone_confident': true,
      'depth_probabilities': <String, double>{'short': 0, 'mid': 0.25, 'deep': 0.75, 'beyond': 0},
      'width_zone': 'centre',
      'confidence': 0.9,
      'rally_id': rallyId,
      'shot_index_in_rally': index,
      'flags': <String>[],
    };

/// One landing of every kind on known spots, plus two rallies.
Map<String, dynamic> courtSessionJson() => <String, dynamic>{
      'status': 'complete',
      'refusal_reason': null,
      'court_engine_version': 'court_engine_v1',
      'is_estimate': true,
      'camera': <String, dynamic>{
        'verdict': 'stable',
        'p95_shift_fraction': 0.0004,
        'rms_shift_px': 0.3,
        'bumped_at_s': null,
      },
      'capture': <String, dynamic>{'fps': 60.0, 'frame_width_px': 1920, 'frame_height_px': 1080},
      'summary': <String, dynamic>{
        'count': 5,
        'verdict_counts': <String, int>{'in': 2, 'out': 1, 'too_close_to_call': 1},
        'depth_expected_counts': <String, double>{'short': 0, 'mid': 1.25, 'deep': 3.75, 'beyond': 0},
        'width_counts': <String, int>{'left': 0, 'centre': 5, 'right': 0},
        'median_sigma_along_m': 0.2,
      },
      'landings': <Map<String, dynamic>>[
        landingJson(id: 'b_000001', x: 0.5, y: 9.0, isServe: true),
        landingJson(id: 'b_000002', x: -2.0, y: -8.0, hitterEnd: 'far', index: 2),
        landingJson(id: 'b_000003', x: 4.6, y: 10.0, verdict: 'out', index: 3),
        landingJson(id: 'b_000004', x: 1.0, y: 11.6, verdict: 'too_close_to_call', major: 0.5),
        landingJson(id: 'b_000005', x: -1.0, y: 5.0, verdict: null, hitterEnd: null, rallyId: null, index: null),
      ],
      'rallies': <Map<String, dynamic>>[
        <String, dynamic>{
          'rally_id': 'r_0000', 'start_s': 3.0, 'end_s': 12.0, 'length_hits': 5,
          'length_min': 5, 'length_max': 6, 'length_confidence': 'medium',
          'hits_observed': 4, 'hits_inferred': 1, 'end_reason': 'lost_track',
          'bounce_ids': <String>['b_000001', 'b_000002'], 'flags': <String>[],
        },
        <String, dynamic>{
          'rally_id': 'r_0001', 'start_s': 208.0, 'end_s': 212.0, 'length_hits': 2,
          'length_min': 2, 'length_max': 2, 'length_confidence': 'high',
          'hits_observed': 2, 'hits_inferred': 0, 'end_reason': 'ball_out',
          'bounce_ids': <String>['b_000003'], 'flags': <String>[],
        },
      ],
    };
