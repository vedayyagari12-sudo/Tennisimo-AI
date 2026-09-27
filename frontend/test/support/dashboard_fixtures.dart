/// Fixture dashboards for rendering the assembled [DashboardScreen] without a
/// Supabase session.
///
/// TEST-ONLY. Nothing here is imported from `lib/`, so none of it can reach
/// the shipped app. The numbers are shaped like real pipeline output — the
/// category and metric names, the metric counts per category, the bands and
/// the null patterns all mirror `backend/app/analysis/rubric.py` — but they
/// are invented for the fixtures and describe no real player.
library;

import 'package:tennisimo_ai/models/analysis_response.dart';
import 'package:tennisimo_ai/models/coaching_feedback.dart';
import 'package:tennisimo_ai/models/enums.dart';
import 'package:tennisimo_ai/models/metric_score.dart';
import 'package:tennisimo_ai/screens/dashboard_screen.dart';
import 'package:tennisimo_ai/services/api_client.dart';

/// A [DashboardDataSource] that answers from memory.
class FakeDashboardDataSource extends DashboardDataSource {
  const FakeDashboardDataSource({
    required this.history,
    this.details = const <String, AnalysisResponse>{},
    this.failingDetailIds = const <String>{},
    this.email = 'player@example.com',
  });

  /// Newest first, as the API returns it.
  final List<AnalysisSummary> history;
  final Map<String, AnalysisResponse> details;

  /// Detail requests for these ids fail with a server error.
  final Set<String> failingDetailIds;
  final String? email;

  @override
  Future<ApiResult<List<AnalysisSummary>>> fetchHistory() async =>
      ApiResult<List<AnalysisSummary>>.ok(history);

  @override
  Future<ApiResult<AnalysisResponse>> fetchAnalysisDetail(String id) async {
    final AnalysisResponse? detail = details[id];
    if (failingDetailIds.contains(id) || detail == null) {
      return const ApiResult<AnalysisResponse>.err(
        ApiFailure(
          kind: ApiFailureKind.server,
          message: 'The server had a problem.',
          statusCode: 500,
        ),
      );
    }
    return ApiResult<AnalysisResponse>.ok(detail);
  }

  @override
  String? get accountEmail => email;

  @override
  Future<void> signOut() async {}
}

/// A fixture state: a name for the screenshot and the data behind it.
class DashboardFixture {
  const DashboardFixture(this.name, this.source);
  final String name;
  final FakeDashboardDataSource source;
}

// ---------------------------------------------------------------------------
// Building blocks
// ---------------------------------------------------------------------------

/// Metric counts per category, from `METRIC_SPECS` minus `EXCLUDED_METRICS`.
const Map<String, int> _metricsPerCategory = <String, int>{
  'preparation': 3,
  'contact': 5,
  'swing_path': 3,
  'balance': 2,
  'follow_through': 1,
};

/// The two banded shot types; every other shot scores only the base subset.
bool _banded(ShotType shot) =>
    shot == ShotType.forehandTopspin || shot == ShotType.forehandSlice;

List<CategoryScore> _categories(ShotType shot, Map<String, double?> scores) {
  return <CategoryScore>[
    for (final MapEntry<String, int> entry in _metricsPerCategory.entries)
      _category(shot, entry.key, entry.value, scores[entry.key]),
  ];
}

CategoryScore _category(ShotType shot, String name, int banded, double? score) {
  // Unbanded shots carry no contact or follow-through bands at all, and only
  // two of the three swing-path metrics.
  final int total = _banded(shot)
      ? banded
      : switch (name) {
          'contact' || 'follow_through' => 0,
          'swing_path' => 2,
          _ => banded,
        };
  return CategoryScore(
    category: name,
    score: score,
    weight: 0.2,
    metricNames: const <String>[],
    metricsAvailable: score == null ? 0 : (total > 1 ? total - 1 : total),
    metricsTotal: total,
  );
}

MetricScore _m(
  String name,
  double? value,
  MetricUnit unit,
  MetricVerdict verdict, {
  double? score,
  double? lo,
  double? hi,
  bool viewSensitive = false,
}) => MetricScore(
  name: name,
  value: value,
  unit: unit,
  verdict: value == null ? MetricVerdict.unavailable : verdict,
  score: value == null ? null : score,
  idealMin: lo,
  idealMax: hi,
  viewSensitive: viewSensitive,
);

/// A forehand topspin's full metric list, bands from `_FOREHAND_TOPSPIN`.
List<MetricScore> forehandMetrics() => <MetricScore>[
  _m(
    'shoulder_hip_separation_deg',
    44,
    MetricUnit.degrees,
    MetricVerdict.ideal,
    score: 88,
    lo: 40,
    hi: 65,
  ),
  _m(
    'shoulder_turn_deg',
    86,
    MetricUnit.degrees,
    MetricVerdict.ideal,
    score: 84,
    lo: 80,
    hi: 110,
    viewSensitive: true,
  ),
  _m(
    'hip_rotation_deg',
    25,
    MetricUnit.degrees,
    MetricVerdict.low,
    score: 38,
    lo: 35,
    hi: 60,
    viewSensitive: true,
  ),
  _m(
    'elbow_angle_at_contact_deg',
    112,
    MetricUnit.degrees,
    MetricVerdict.low,
    score: 55,
    lo: 120,
    hi: 160,
    viewSensitive: true,
  ),
  _m(
    'wrist_lag_deg',
    31,
    MetricUnit.degrees,
    MetricVerdict.ideal,
    score: 92,
    lo: 15,
    hi: 45,
    viewSensitive: true,
  ),
  _m(
    'contact_height_ratio',
    0.92,
    MetricUnit.ratio,
    MetricVerdict.ideal,
    score: 90,
    lo: 0.75,
    hi: 1.05,
  ),
  _m(
    'contact_point_forward_tu',
    0.18,
    MetricUnit.torsoUnits,
    MetricVerdict.low,
    score: 60,
    lo: 0.25,
    hi: 0.60,
    viewSensitive: true,
  ),
  _m(
    'peak_hand_speed_tu_s',
    7.2,
    MetricUnit.torsoUnitsPerSec,
    MetricVerdict.ideal,
    score: 80,
    lo: 6,
    hi: 12,
    viewSensitive: true,
  ),
  _m(
    'swing_path_angle_deg',
    9,
    MetricUnit.degrees,
    MetricVerdict.low,
    score: 31,
    lo: 15,
    hi: 35,
  ),
  _m(
    'swing_plane_deviation_tu',
    0.11,
    MetricUnit.torsoUnits,
    MetricVerdict.high,
    score: 58,
    lo: 0,
    hi: 0.08,
    viewSensitive: true,
  ),
  _m(
    'knee_flexion_min_deg',
    158,
    MetricUnit.degrees,
    MetricVerdict.ideal,
    score: 86,
    lo: 135,
    hi: 165,
    viewSensitive: true,
  ),
  _m(
    'follow_through_height_tu',
    0.55,
    MetricUnit.torsoUnits,
    MetricVerdict.ideal,
    score: 82,
    lo: 0.30,
    hi: 0.90,
  ),
  _m(
    'head_stillness_tu',
    0.04,
    MetricUnit.torsoUnits,
    MetricVerdict.ideal,
    score: 90,
    lo: 0,
    hi: 0.06,
  ),
  _m(
    'tempo_ratio',
    3.4,
    MetricUnit.ratio,
    MetricVerdict.high,
    score: 62,
    lo: 1.5,
    hi: 3.0,
  ),
  // Structurally unmeasurable in this pipeline: always null.
  _m(
    'weight_transfer_tu',
    null,
    MetricUnit.torsoUnits,
    MetricVerdict.unavailable,
    viewSensitive: true,
  ),
  _m('balance_sway_tu', null, MetricUnit.torsoUnits, MetricVerdict.unavailable),
];

/// A serve's metric list: only the base bands exist, so the contact metrics
/// are measured but carry no reference range, and hip turn was not measurable.
List<MetricScore> serveMetrics() => <MetricScore>[
  _m(
    'shoulder_hip_separation_deg',
    52,
    MetricUnit.degrees,
    MetricVerdict.ideal,
    score: 90,
    lo: 30,
    hi: 65,
  ),
  _m(
    'shoulder_turn_deg',
    64,
    MetricUnit.degrees,
    MetricVerdict.low,
    score: 44,
    lo: 70,
    hi: 110,
    viewSensitive: true,
  ),
  _m(
    'hip_rotation_deg',
    null,
    MetricUnit.degrees,
    MetricVerdict.unavailable,
    lo: 25,
    hi: 60,
    viewSensitive: true,
  ),
  _m(
    'elbow_angle_at_contact_deg',
    168,
    MetricUnit.degrees,
    MetricVerdict.unavailable,
    viewSensitive: true,
  ),
  _m(
    'knee_flexion_min_deg',
    121,
    MetricUnit.degrees,
    MetricVerdict.low,
    score: 52,
    lo: 130,
    hi: 165,
    viewSensitive: true,
  ),
  _m(
    'tempo_ratio',
    2.2,
    MetricUnit.ratio,
    MetricVerdict.ideal,
    score: 94,
    lo: 1.5,
    hi: 3.0,
  ),
  _m(
    'swing_plane_deviation_tu',
    0.05,
    MetricUnit.torsoUnits,
    MetricVerdict.ideal,
    score: 88,
    lo: 0,
    hi: 0.08,
    viewSensitive: true,
  ),
];

/// Gemini-sourced coaching tied to the forehand metrics above. No numbers in
/// any of it: the guard only ever lets through numbers the pipeline produced.
CoachingFeedback forehandFeedback({bool template = false}) => CoachingFeedback(
  summary: 'Your racket path is the thing holding this forehand back.',
  strengths: const <String>['Good separation between hips and shoulders.'],
  improvements: const <Improvement>[
    Improvement(
      priority: 1,
      title: 'Swing up through the ball',
      why: 'A flat path gives the ball no topspin to bring it down.',
      cue: 'Start the racket below the ball, finish over your shoulder.',
      drill:
          'Twenty shadow swings from knee height to above your '
          'opposite shoulder.',
      metricRefs: <String>['swing_path_angle_deg', 'swing_plane_deviation_tu'],
    ),
    Improvement(
      priority: 2,
      title: 'Turn your hips earlier',
      why:
          'Power starts in the hips; a short turn leaves it all to the '
          'arm.',
      cue: 'Point your belt buckle at the side fence on the take-back.',
      drill:
          'Hold a racket across your hips and turn until it points at '
          'the net post.',
      metricRefs: <String>['hip_rotation_deg'],
    ),
    Improvement(
      priority: 3,
      title: 'Meet the ball further out',
      why: 'A cramped arm at contact costs reach and control.',
      cue: 'Hit with a long arm, out in front of your front hip.',
      drill:
          'Drop-feed ten balls and catch each one on the strings in '
          'front of you.',
      metricRefs: <String>[
        'elbow_angle_at_contact_deg',
        'contact_point_forward_tu',
      ],
    ),
  ],
  source: template ? FeedbackSource.template : FeedbackSource.gemini,
  model: template ? null : 'gemini-2.5-flash',
  guard: NumericGuardReport(
    passed: !template,
    rejectedTokens: const <String>[],
    fieldsDiscarded: const <String>[],
    fellBackToTemplate: template,
    mphRule: 'banned',
    modelFinishReason: null,
  ),
  latencyMs: 1800,
);

AnalysisResponse fixtureAnalysis({
  required String id,
  required DateTime createdAt,
  required ShotType shot,
  required double? overall,
  required Map<String, double?> categories,
  List<MetricScore> metrics = const <MetricScore>[],
  CoachingFeedback? feedback,
}) => AnalysisResponse(
  analysisId: id,
  createdAt: createdAt,
  status: AnalysisStatus.complete,
  pipelineVersion: '1.0.0',
  shotType: shot,
  shotTypeConfidence: 0.8,
  overallScore: overall,
  categories: _categories(shot, categories),
  metrics: metrics,
  ballSpeed: null,
  feedback: feedback,
  warnings: const <String>[],
);

/// One session: its history row and its full analysis, kept in step.
class _Session {
  _Session(this.summary, this.detail);
  final AnalysisSummary summary;
  final AnalysisResponse detail;
}

_Session _session({
  required String id,
  required Duration age,
  required ShotType shot,
  required double? overall,
  required Map<String, double?> categories,
  int? mph,
  List<MetricScore> metrics = const <MetricScore>[],
  CoachingFeedback? feedback,
}) {
  final DateTime at = DateTime.now().subtract(age);
  return _Session(
    AnalysisSummary(
      analysisId: id,
      createdAt: at,
      shotType: shot,
      overallScore: overall,
      ballSpeedMph: mph,
    ),
    fixtureAnalysis(
      id: id,
      createdAt: at,
      shot: shot,
      overall: overall,
      categories: categories,
      metrics: metrics,
      feedback: feedback,
    ),
  );
}

FakeDashboardDataSource _source(
  List<_Session> newestFirst, {
  Set<String> failing = const <String>{},
}) => FakeDashboardDataSource(
  history: <AnalysisSummary>[for (final _Session s in newestFirst) s.summary],
  details: <String, AnalysisResponse>{
    for (final _Session s in newestFirst) s.detail.analysisId: s.detail,
  },
  failingDetailIds: failing,
);

/// Up to eight forehands, newest first. Swing path is clearly the weak
/// category and creeping up; everything else sits in the 60s-80s.
List<_Session> _forehands({int count = 8, Duration olderBy = Duration.zero}) {
  const List<double> overall = <double>[71, 68, 70, 66, 64, 65, 61, 58];
  const List<double> prep = <double>[76, 74, 75, 72, 70, 71, 68, 66];
  const List<double> contact = <double>[78, 77, 74, 73, 71, 70, 69, 65];
  const List<double> path = <double>[48, 45, 46, 42, 40, 41, 38, 36];
  const List<double?> follow = <double?>[82, 80, null, 78, 77, 74, null, 72];
  const List<double> balance = <double>[74, 70, 73, 69, 68, 70, 66, 64];
  const List<int?> mph = <int?>[64, null, 61, 59, null, 57, null, 52];
  const List<Duration> ages = <Duration>[
    Duration(hours: 2),
    Duration(days: 1, hours: 3),
    Duration(days: 3),
    Duration(days: 5),
    Duration(days: 9),
    Duration(days: 12),
    Duration(days: 16),
    Duration(days: 21),
  ];
  return <_Session>[
    for (int i = 0; i < count; i++)
      _session(
        id: 'fh$i',
        age: ages[i] + olderBy,
        shot: ShotType.forehandTopspin,
        overall: overall[i],
        mph: mph[i],
        categories: <String, double?>{
          'preparation': prep[i],
          'contact': contact[i],
          'swing_path': path[i],
          'follow_through': follow[i],
          'balance': balance[i],
        },
        metrics: i == 0 ? forehandMetrics() : const <MetricScore>[],
        feedback: i == 0 ? forehandFeedback() : null,
      ),
  ];
}

/// Serves and volleys: unbanded, so contact and follow-through are null by
/// construction and one more gap drops the overall score entirely.
_Session _unbanded(
  String id,
  Duration age,
  ShotType shot, {
  required double? prep,
  required double? path,
  required double? balance,
  int? mph,
  bool withMetrics = false,
}) {
  final List<double> scored = <double?>[
    prep,
    path,
    balance,
  ].whereType<double>().toList();
  return _session(
    id: id,
    age: age,
    shot: shot,
    // Fewer than three categories scored -> no overall score. Not a zero.
    overall: scored.length < 3
        ? null
        : scored.reduce((double a, double b) => a + b) / 3,
    mph: mph,
    categories: <String, double?>{
      'preparation': prep,
      'contact': null,
      'swing_path': path,
      'follow_through': null,
      'balance': balance,
    },
    metrics: withMetrics ? serveMetrics() : const <MetricScore>[],
  );
}

_Session _backhand(
  String id,
  Duration age,
  double overall, {
  required double prep,
  required double path,
  required double balance,
  int? mph,
}) => _session(
  id: id,
  age: age,
  shot: ShotType.backhandTwoHanded,
  overall: overall,
  mph: mph,
  categories: <String, double?>{
    'preparation': prep,
    'contact': null,
    'swing_path': path,
    'follow_through': null,
    'balance': balance,
  },
);

// ---------------------------------------------------------------------------
// The states
// ---------------------------------------------------------------------------

DashboardFixture newUser() => const DashboardFixture(
  'new_user',
  FakeDashboardDataSource(history: <AnalysisSummary>[]),
);

DashboardFixture forehandOnly() =>
    DashboardFixture('forehand_only', _source(_forehands()));

DashboardFixture severalShotTypes() {
  final List<_Session> fh = _forehands(count: 5);
  return DashboardFixture(
    'several_shot_types',
    _source(<_Session>[
      fh[0],
      _backhand(
        'bh0',
        const Duration(hours: 20),
        63,
        prep: 66,
        path: 58,
        balance: 71,
        mph: 55,
      ),
      fh[1],
      _backhand(
        'bh1',
        const Duration(days: 2),
        57,
        prep: 60,
        path: 50,
        balance: 68,
      ),
      _unbanded(
        'sv0',
        const Duration(days: 2, hours: 5),
        ShotType.serve,
        prep: 70,
        path: 62,
        balance: 66,
        mph: 88,
      ),
      fh[2],
      _backhand(
        'bh2',
        const Duration(days: 4),
        60,
        prep: 62,
        path: 55,
        balance: 64,
      ),
      fh[3],
      fh[4],
    ]),
  );
}

/// The newest session is a serve with no overall score; the volleys never
/// produced one either.
DashboardFixture unscoredServesAndVolleys() {
  final List<_Session> fh = _forehands(
    count: 3,
    olderBy: const Duration(days: 1),
  );
  return DashboardFixture(
    'unscored_serves_volleys',
    _source(<_Session>[
      _unbanded(
        'sv0',
        const Duration(minutes: 40),
        ShotType.serve,
        prep: 72,
        path: null,
        balance: 58,
        mph: 91,
        withMetrics: true,
      ),
      _unbanded(
        'vo0',
        const Duration(hours: 5),
        ShotType.volley,
        prep: 64,
        path: null,
        balance: 70,
      ),
      _unbanded(
        'sv1',
        const Duration(days: 1),
        ShotType.serve,
        prep: 68,
        path: 60,
        balance: 55,
        mph: 86,
      ),
      fh[0],
      _unbanded(
        'vo1',
        const Duration(days: 2),
        ShotType.volley,
        prep: null,
        path: 52,
        balance: 66,
      ),
      _unbanded(
        'sv2',
        const Duration(days: 2, hours: 1),
        ShotType.serve,
        prep: 66,
        path: null,
        balance: null,
      ),
      fh[1],
      fh[2],
    ]),
  );
}

/// Forehands where three detail requests fail, including the NEWEST one.
DashboardFixture partialDetailFailure() => DashboardFixture(
  'partial_detail_failure',
  _source(_forehands(), failing: <String>{'fh0', 'fh3', 'fh5'}),
);

/// Six shot types, so the shot mix has to fold its tail: four named slices
/// and one "Other" holding the two smallest.
DashboardFixture wideShotMix() {
  final List<_Session> fh = _forehands(count: 5);
  return DashboardFixture(
    'wide_shot_mix',
    _source(<_Session>[
      fh[0],
      _backhand(
        'bh0',
        const Duration(hours: 20),
        63,
        prep: 66,
        path: 58,
        balance: 71,
      ),
      _unbanded(
        'sv0',
        const Duration(days: 1),
        ShotType.serve,
        prep: 70,
        path: 62,
        balance: 66,
        mph: 88,
      ),
      fh[1],
      _unbanded(
        'vo0',
        const Duration(days: 2),
        ShotType.volley,
        prep: 64,
        path: 55,
        balance: 70,
      ),
      _backhand(
        'bh1',
        const Duration(days: 2, hours: 4),
        57,
        prep: 60,
        path: 50,
        balance: 68,
      ),
      _unbanded(
        'fs0',
        const Duration(days: 3),
        ShotType.forehandSlice,
        prep: 61,
        path: 57,
        balance: 63,
      ),
      fh[2],
      _unbanded(
        'sv1',
        const Duration(days: 4),
        ShotType.serve,
        prep: 66,
        path: 60,
        balance: 61,
      ),
      _unbanded(
        'b1h0',
        const Duration(days: 5),
        ShotType.backhandOneHanded,
        prep: 58,
        path: 49,
        balance: 60,
      ),
      fh[3],
      fh[4],
    ]),
  );
}

List<DashboardFixture> allFixtures() => <DashboardFixture>[
  newUser(),
  forehandOnly(),
  severalShotTypes(),
  unscoredServesAndVolleys(),
  partialDetailFailure(),
  wideShotMix(),
];
