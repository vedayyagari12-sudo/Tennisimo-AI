import 'package:flutter/material.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import '../models/analysis_response.dart';
import '../models/chart_data.dart';
import '../models/dashboard_insights.dart';
import '../models/enums.dart';
import '../models/swing_advice.dart';
import '../models/swing_coaching.dart';
import '../services/api_client.dart';
import '../services/api_client.dart' as api
    show deleteAccount, fetchAnalysisDetail, fetchHistory;
import '../theme/app_theme.dart';
import '../widgets/app_card.dart';
import '../widgets/category_bars.dart';
import '../widgets/coaching_list.dart';
import '../widgets/content_width.dart';
import '../widgets/score_line_chart.dart';
import '../widgets/empty_state.dart';
import '../widgets/inline_stats.dart';
import '../widgets/score_ring.dart';
import '../widgets/section_header.dart';
import '../widgets/shot_mix_chart.dart';
import '../widgets/skeleton_block.dart';
import '../widgets/skill_radar.dart';
import 'home_shell.dart';
import 'record_screen.dart';
import 'results_screen.dart';

/// The dashboard's coaching section title.
const String kDashboardImproveTitle = 'Focus next session';

/// The title of the full fixes list under the breakdown.
const String kDashboardImproveListTitle = 'How to improve';

/// Where the dashboard gets its data: the history list, the per-analysis
/// details, and the signed-in account.
///
/// The default is the real thing — the API client and the live Supabase
/// session — and production never passes anything else. It exists as a seam
/// only so the assembled screen can be rendered in a test with fixture data,
/// which it otherwise cannot be: every member below reaches `Supabase.instance`.
class DashboardDataSource {
  const DashboardDataSource();

  Future<ApiResult<List<AnalysisSummary>>> fetchHistory() =>
      api.fetchHistory();

  Future<ApiResult<AnalysisResponse>> fetchAnalysisDetail(String id) =>
      api.fetchAnalysisDetail(id);

  /// The signed-in account's email, or null.
  String? get accountEmail =>
      Supabase.instance.client.auth.currentUser?.email;

  Future<void> signOut() => Supabase.instance.client.auth.signOut();

  /// `DELETE /v1/account`: the account and all of its data, permanently.
  Future<ApiResult<void>> deleteAccount() => api.deleteAccount();
}

/// The landing tab.
///
/// The screen is organised around ONE decision: a player wants to know what to
/// practise. So it opens with the latest session, then the weakest category of
/// that shot and whether it is moving, then a block per shot type with that
/// shot's own trend, breakdown, speed and records.
///
/// Nothing is averaged across shot types — see `dashboard_insights.dart`. Every
/// number comes from the server; anything the server left null is rendered as
/// an em dash or the words "not measured", never as a zero.
///
/// ## What a dashboard load costs
///
/// The history endpoint returns only `{id, created_at, shot_type,
/// overall_score, ball_speed_mph}`. Categories and metric coverage exist only
/// on the per-analysis detail, so every per-category number here costs a
/// request. The budget is fixed by [detailIdsToFetch] and enforced here:
/// **1 history request + at most [_detailBudget] detail requests**, issued
/// [_detailConcurrency] at a time. It never scales with history length.
class DashboardScreen extends StatefulWidget {
  const DashboardScreen({
    super.key,
    this.onSeeAllHistory,
    this.onOpenProfile,
    this.dataSource = const DashboardDataSource(),
  });

  /// Switches the shell to the History tab.
  final VoidCallback? onSeeAllHistory;

  /// Switches the shell to the Profile tab. Tapping the avatar does this.
  final VoidCallback? onOpenProfile;

  /// Always the default in the app; see [DashboardDataSource].
  final DashboardDataSource dataSource;

  @override
  State<DashboardScreen> createState() => DashboardScreenState();
}

/// Public so the shell can call [refresh] after a recording.
class DashboardScreenState extends State<DashboardScreen> {
  /// The hard ceiling on detail requests per load.
  static const int _detailBudget = 12;

  /// How many of those are in flight at once. Small enough not to fan out a
  /// cold Cloud Run instance, large enough that the screen fills promptly.
  static const int _detailConcurrency = 4;

  bool _loading = true;
  ApiFailure? _failure;
  List<AnalysisSummary> _items = const <AnalysisSummary>[];

  /// The full analyses fetched for the head of [_items], newest first.
  bool _detailLoading = false;
  List<AnalysisResponse> _details = const <AnalysisResponse>[];

  String? _openingId;

  @override
  void initState() {
    super.initState();
    _load();
  }

  /// Re-fetches everything. Called by the shell when a recording finishes.
  Future<void> refresh() => _load();

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _failure = null;
    });

    final ApiResult<List<AnalysisSummary>> result =
        await widget.dataSource.fetchHistory();
    if (!mounted) return;

    if (!result.isOk) {
      setState(() {
        _loading = false;
        _failure = result.failure;
        _items = const <AnalysisSummary>[];
        _details = const <AnalysisResponse>[];
      });
      return;
    }

    setState(() {
      _loading = false;
      _items = result.data!;
      _details = const <AnalysisResponse>[];
    });
    await _loadDetails();
  }

  /// Fetches the budgeted set of full analyses.
  ///
  /// Individual failures are dropped rather than failing the screen: a missing
  /// detail makes a trend sparser, and the section captions say how many clips
  /// they are built from, so a partial fetch understates rather than lies.
  Future<void> _loadDetails() async {
    final List<String> ids = detailIdsToFetch(_items, budget: _detailBudget);
    if (ids.isEmpty) return;

    setState(() => _detailLoading = true);

    final Map<String, AnalysisResponse> fetched = <String, AnalysisResponse>{};
    for (int i = 0; i < ids.length; i += _detailConcurrency) {
      final List<String> chunk = ids.skip(i).take(_detailConcurrency).toList();
      final List<ApiResult<AnalysisResponse>> results =
          await Future.wait(chunk.map(widget.dataSource.fetchAnalysisDetail));
      if (!mounted) return;
      for (int j = 0; j < chunk.length; j++) {
        if (results[j].isOk) fetched[chunk[j]] = results[j].data!;
      }
    }
    if (!mounted) return;

    // Re-ordered into history order — newest first — so every series below
    // reads chronologically however the requests happened to land.
    setState(() {
      _detailLoading = false;
      _details = <AnalysisResponse>[
        for (final AnalysisSummary item in _items)
          if (fetched.containsKey(item.analysisId)) fetched[item.analysisId]!,
      ];
    });
  }

  /// The full analysis of the NEWEST session, or null when that one request
  /// failed or has not landed.
  ///
  /// Not simply `_details.first`: when the newest detail fails, the first
  /// detail is an OLDER clip, and showing its breakdown under the latest
  /// session's score would pass one swing's numbers off as another's.
  AnalysisResponse? get _latestDetail {
    if (_details.isEmpty || _items.isEmpty) return null;
    final AnalysisResponse first = _details.first;
    return first.analysisId == _items.first.analysisId ? first : null;
  }

  Future<void> _open(AnalysisSummary summary) async {
    setState(() => _openingId = summary.analysisId);
    final ApiResult<AnalysisResponse> result =
        await widget.dataSource.fetchAnalysisDetail(summary.analysisId);
    if (!mounted) return;
    setState(() => _openingId = null);

    if (!result.isOk) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(result.failure!.plainLanguageWithDebugDetail)),
      );
      return;
    }
    if (!mounted) return;
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (BuildContext context) =>
            ResultsScreen(analysis: result.data!),
      ),
    );
  }

  Future<void> _record() async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (BuildContext context) => const RecordScreen(),
      ),
    );
    if (!mounted) return;
    await _load();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        bottom: false,
        child: ContentWidth(
          child: RefreshIndicator(
            onRefresh: _load,
            child: _buildBody(),
          ),
        ),
      ),
    );
  }

  Widget _header() => _DashboardHeader(
        email: widget.dataSource.accountEmail,
        onOpenProfile: widget.onOpenProfile,
      );

  Widget _buildBody() {
    if (_loading) {
      return ListView(
        padding: kTabContentPadding,
        children: <Widget>[
          _header(),
          const SizedBox(height: AppSpacing.lg),
          const SkeletonBlock(height: 180),
          const SizedBox(height: AppSpacing.lg),
          const SkeletonBlock(height: 92),
          const SizedBox(height: AppSpacing.lg),
          const SkeletonBlock(height: 190),
        ],
      );
    }

    final ApiFailure? failure = _failure;
    if (failure != null) {
      return ListView(
        padding: kTabContentPadding,
        children: <Widget>[
          _header(),
          const SizedBox(height: AppSpacing.xxl),
          EmptyState(
            icon: Icons.cloud_off,
            headline: 'Could not load your sessions',
            supporting: failure.plainLanguageWithDebugDetail,
            actionLabel: 'Try again',
            onAction: _load,
          ),
        ],
      );
    }

    if (_items.isEmpty) {
      return ListView(
        padding: kTabContentPadding,
        children: <Widget>[
          _header(),
          const SizedBox(height: AppSpacing.xxl),
          EmptyState(
            icon: Icons.sports_tennis,
            headline: 'No swings analysed yet',
            supporting:
                'Record a swing side-on and your score, trend and breakdown '
                'will appear here.',
            actionLabel: 'Record a swing',
            onAction: _record,
          ),
        ],
      );
    }

    final DashboardInsights insights = buildDashboardInsights(
      history: _items,
      details: _details,
    );

    return ListView(
      padding: kTabContentPadding,
      children: <Widget>[
        _header(),
        const SizedBox(height: AppSpacing.lg),
        _HeroCard(
          latest: _items.first,
          // Latest against the previous clip OF THE SAME SHOT, which is the
          // only comparison that means anything.
          delta: insights.latestShotType?.latestDelta,
          coverage: _latestDetail == null ? null : coverageOf(_latestDetail!),
          onTap: _openingId == null ? () => _open(_items.first) : null,
        ),
        const SizedBox(height: AppSpacing.lg),
        _StatTiles(stats: _overallStats(insights)),
        const SizedBox(height: AppSpacing.lg),
        ..._buildFocus(),
        ..._buildProgress(insights),
        ..._buildLatestBreakdown(),
        ..._buildSwingNotes(),
        ..._buildShotMix(insights),
        SectionHeader(
          title: 'Recent sessions',
          actionLabel: widget.onSeeAllHistory == null ? null : 'See all',
          onAction: widget.onSeeAllHistory,
        ),
        _buildRecent(),
      ],
    );
  }

  /// "Focus next session": the newest clip's biggest fix, with its drill.
  /// From the fixed rule table in `swing_coaching.dart`, not from AI text.
  ///
  /// Silent when the newest clip's detail is not loaded, or nothing in it is
  /// off target: a fix borrowed from an older clip would be the wrong swing's.
  List<Widget> _buildFocus() {
    if (_detailLoading && _details.isEmpty) {
      return const <Widget>[
        SkeletonBlock(height: 150),
        SizedBox(height: AppSpacing.lg),
      ];
    }
    final AnalysisResponse? latest = _latestDetail;
    if (latest == null) return const <Widget>[];
    final List<CoachingItem> fixes = buildCoaching(latest)
        .where((CoachingItem c) => c.isOffTarget && c.advice != null)
        .toList();
    if (fixes.isEmpty) return const <Widget>[];
    return <Widget>[
      _FocusNextCard(item: fixes.first),
      const SizedBox(height: AppSpacing.lg),
    ];
  }

  /// "Progress": one card, a chip per recorded shot type, that shot's
  /// overall score over its recent clips, then best / average / change.
  List<Widget> _buildProgress(DashboardInsights insights) {
    if (insights.shotTypes.isEmpty) return const <Widget>[];
    return <Widget>[
      _ProgressCard(
        insights: insights.shotTypes,
        initial: insights.latestShotType?.shotType,
      ),
      const SizedBox(height: AppSpacing.lg),
    ];
  }

  /// The clip count per shot type, as its own section. A COUNT per shot type
  /// — a part-to-whole of clips — is the one cross-shot-type view that is
  /// legitimate: nothing is averaged.
  List<Widget> _buildShotMix(DashboardInsights insights) {
    final List<ShotMixSlice> mix = buildShotMix(insights.shotTypes);
    if (mix.isEmpty) return const <Widget>[];
    return <Widget>[
      _SectionTitle(title: 'Shot mix', trailing: '${insights.totalSessions} ${plural(insights.totalSessions, 'clip')}'),
      AppCard(child: ShotMixChart(slices: mix)),
      const SizedBox(height: AppSpacing.lg),
    ];
  }

  /// The only cross-shot-type numbers allowed: counts and one record.
  ///
  /// There is deliberately no "average score" here. A mean over forehands,
  /// serves and volleys is arithmetic over three different measurements and
  /// would move when the player simply changed which shot they filmed.
  ///
  /// The latest clip's coverage used to sit here too; it describes one
  /// session, so it now sits on that session's card, and this row fits on
  /// one line at 360dp instead of wrapping one label onto a second.
  List<InlineStat> _overallStats(DashboardInsights insights) {
    return <InlineStat>[
      InlineStat(label: 'Clips', value: insights.totalSessions.toString()),
      InlineStat(
        label: 'Top speed',
        value: insights.topSpeedMph?.toString(),
        unit: 'mph',
      ),
      InlineStat(
        label: 'Shot types',
        value: insights.shotTypesRecorded.toString(),
      ),
    ];
  }

  /// The newest analysis's own scorecard: score AND per-category coverage in
  /// one place, which no sparkline shows.
  List<Widget> _buildLatestBreakdown() {
    if (_detailLoading && _details.isEmpty) {
      return const <Widget>[
        SectionHeader(title: 'Latest breakdown'),
        AppCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              SkeletonBlock(height: 14),
              SizedBox(height: AppSpacing.md),
              SkeletonBlock(height: 14),
              SizedBox(height: AppSpacing.md),
              SkeletonBlock(height: 14),
            ],
          ),
        ),
        SizedBox(height: AppSpacing.lg),
      ];
    }
    final AnalysisResponse? latest = _latestDetail;
    if (latest == null) {
      if (_detailLoading) return const <Widget>[];
      // The newest clip's own request failed. Say so, rather than showing an
      // older clip's breakdown under this one's score.
      final ThemeData theme = Theme.of(context);
      return <Widget>[
        const SectionHeader(title: 'Latest breakdown'),
        AppCard(
          child: Text(
            'The breakdown for your latest clip could not be loaded. Pull '
            'down to try again.',
            style: theme.textTheme.bodySmall
                ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
          ),
        ),
        const SizedBox(height: AppSpacing.lg),
      ];
    }
    if (latest.categories.isEmpty) return const <Widget>[];
    final ThemeData theme = Theme.of(context);
    // The radar shows the SHAPE, against the previous swing of the same shot
    // when that one loaded; the bars under it are its exact numbers.
    final SkillProfile? profile = buildSkillProfile(
      latest,
      previous: previousOfSameShot(_items, _details),
    );
    return <Widget>[
      SectionHeader(
        title: 'Latest breakdown',
        actionLabel: 'Details',
        onAction: _openingId == null ? () => _open(_items.first) : null,
      ),
      AppCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            if (profile != null) ...<Widget>[
              SkillRadar(profile: profile),
              const SizedBox(height: AppSpacing.md),
              const Divider(height: 1),
              const SizedBox(height: AppSpacing.md),
            ],
            CategoryBars(categories: latest.categories),
            const SizedBox(height: AppSpacing.md),
            Text(
              'Orange = your biggest gain. Tap Details for every measurement.',
              style: theme.textTheme.bodySmall
                  ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
            ),
          ],
        ),
      ),
      const SizedBox(height: AppSpacing.lg),
    ];
  }

  /// "How to improve": the newest clip's top fixes — each with the measured
  /// gap and a drill — and one thing that is working. From the fixed rule
  /// table in `swing_coaching.dart`, not from AI text.
  ///
  /// Silent when the newest clip's detail is not loaded (the breakdown block
  /// above already says so) or produced nothing to say.
  List<Widget> _buildSwingNotes() {
    final AnalysisResponse? latest = _latestDetail;
    if (latest == null) return const <Widget>[];
    final List<CoachingItem> coaching = buildCoaching(latest);
    final List<CoachingItem> items = <CoachingItem>[
      ...coaching
          .where((CoachingItem c) => c.isOffTarget && c.advice != null)
          .take(kDashboardFixBullets),
      ...coaching
          .where((CoachingItem c) => c.isInRange && c.advice != null)
          .take(kDashboardPraiseBullets),
    ];
    if (items.isEmpty) return const <Widget>[];
    return <Widget>[
      const SectionHeader(title: kDashboardImproveListTitle),
      AppCard(child: CoachingList(items: items)),
      const SizedBox(height: AppSpacing.lg),
    ];
  }

  Widget _buildRecent() {
    final List<AnalysisSummary> recent = _items.take(5).toList();
    return AppCard(
      padding: EdgeInsets.zero,
      child: Column(
        children: <Widget>[
          for (int i = 0; i < recent.length; i++) ...<Widget>[
            if (i > 0) const Divider(height: 1, indent: AppSpacing.lg),
            _RecentRow(
              summary: recent[i],
              busy: _openingId == recent[i].analysisId,
              onTap: _openingId == null ? () => _open(recent[i]) : null,
            ),
          ],
        ],
      ),
    );
  }
}

/// Greeting and "Your tennis", then the avatar, which opens Profile. Sign
/// out and Delete account live in Profile.
class _DashboardHeader extends StatelessWidget {
  const _DashboardHeader({
    required this.email,
    this.onOpenProfile,
  });

  final String? email;
  final VoidCallback? onOpenProfile;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final String? email = this.email;
    final String initial =
        (email == null || email.isEmpty) ? '?' : email[0].toUpperCase();

    final Widget avatar = CircleAvatar(
      radius: 22,
      backgroundColor: theme.colorScheme.primaryContainer,
      child: Text(
        initial,
        style: theme.textTheme.titleMedium?.copyWith(
          color: theme.colorScheme.primary,
          fontWeight: FontWeight.w700,
        ),
      ),
    );

    return Row(
      children: <Widget>[
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Text(
                _greeting(),
                style: theme.textTheme.bodyMedium
                    ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
              ),
              Text(
                'Your tennis',
                style: theme.textTheme.headlineSmall?.copyWith(
                  fontWeight: FontWeight.w800,
                  letterSpacing: -0.5,
                ),
              ),
            ],
          ),
        ),
        if (onOpenProfile == null)
          avatar
        else
          Semantics(
            button: true,
            label: 'Profile and settings',
            child: InkWell(
              onTap: onOpenProfile,
              customBorder: const CircleBorder(),
              child: SizedBox(
                width: 44,
                height: 44,
                child: Center(child: ExcludeSemantics(child: avatar)),
              ),
            ),
          ),
      ],
    );
  }

  String _greeting() {
    final int hour = DateTime.now().hour;
    if (hour < 12) return 'Good morning';
    if (hour < 18) return 'Good afternoon';
    return 'Good evening';
  }
}

class _HeroCard extends StatelessWidget {
  const _HeroCard({
    required this.latest,
    required this.delta,
    required this.coverage,
    this.onTap,
  });

  final AnalysisSummary latest;

  /// This session's own metric coverage, or null when its detail is not
  /// loaded.
  final CoverageSummary? coverage;

  /// Only non-null when this session and the previous one of the SAME shot
  /// type were both scored.
  final double? delta;

  /// Opens this session's results.
  final VoidCallback? onTap;

  static const Color _ground = Color(0xFF0F3D24);
  static const Color _lime = Color(0xFFD4F04C);
  static const Color _onLime = Color(0xFF142000);

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final BorderRadius radius = BorderRadius.circular(AppSpacing.cardRadius);
    final double? d = delta;
    final String shot = latest.shotType.label;
    return Theme(
      data: buildHeroTheme(),
      child: Material(
        color: _ground,
        shape: RoundedRectangleBorder(borderRadius: radius),
        clipBehavior: Clip.antiAlias,
        child: InkWell(
          onTap: onTap,
          borderRadius: radius,
          child: Padding(
            padding: const EdgeInsets.all(18),
            child: Row(
              children: <Widget>[
                // The ring shows the number alone; "Not scored" only when
                // there is no number, never a 0.
                ScoreRing(
                  score: latest.overallScore,
                  label: latest.overallScore == null ? 'Not scored' : null,
                  size: 104,
                  strokeWidth: 10,
                  arcColor: _lime,
                ),
                const SizedBox(width: 18),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(
                        'LATEST SWING · ${_relativeTime(latest.createdAt).toUpperCase()}',
                        style: theme.textTheme.labelSmall?.copyWith(
                          color: const Color(0xB3FFFFFF),
                          fontWeight: FontWeight.w600,
                          letterSpacing: 0.5,
                        ),
                      ),
                      const SizedBox(height: 6),
                      Text(
                        shot,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: theme.textTheme.titleLarge?.copyWith(
                          color: Colors.white,
                          fontWeight: FontWeight.w800,
                        ),
                      ),
                      if (d != null) ...<Widget>[
                        const SizedBox(height: 6),
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                          decoration: BoxDecoration(
                            color: _lime,
                            borderRadius: BorderRadius.circular(999),
                          ),
                          child: Text(
                            '${d >= 0 ? '▲' : '▼'} ${d.abs().round()} vs last ${shot.toLowerCase()}',
                            style: theme.textTheme.labelMedium?.copyWith(
                              color: _onLime,
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                        ),
                      ],
                      if (latest.ballSpeedMph != null) ...<Widget>[
                        const SizedBox(height: 6),
                        Text('${latest.ballSpeedMph} mph',
                            style: theme.textTheme.bodySmall
                                ?.copyWith(color: const Color(0xBFFFFFFF))),
                      ],
                      if (coverage case final CoverageSummary c
                          when c.total > 0) ...<Widget>[
                        const SizedBox(height: 6),
                        Text(
                          '${c.available} of ${c.total} '
                          '${plural(c.total, 'check')} measured',
                          style: theme.textTheme.bodySmall
                              ?.copyWith(color: const Color(0xBFFFFFFF)),
                        ),
                      ],
                    ],
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// The newest clip's biggest fix, as the redesign's "Focus next session"
/// card: an icon tile, the fix, what was measured, and a drill.
class _FocusNextCard extends StatelessWidget {
  const _FocusNextCard({required this.item});

  final CoachingItem item;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final String? measured = item.gapText ??
        (item.targetText == null
            ? null
            : '${item.label}: ${item.valueText}, target ${item.targetText}.');
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              Container(
                width: 36,
                height: 36,
                decoration: BoxDecoration(
                  color: const Color(0xFFFFF1E6),
                  borderRadius: BorderRadius.circular(12),
                ),
                child: const Icon(Icons.track_changes, size: 20, color: Color(0xFFB4500B)),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(
                      kDashboardImproveTitle.toUpperCase(),
                      style: theme.textTheme.labelSmall?.copyWith(
                        color: theme.colorScheme.onSurfaceVariant,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    Text(
                      item.advice!,
                      style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
                    ),
                  ],
                ),
              ),
            ],
          ),
          if (measured != null) ...<Widget>[
            const SizedBox(height: 10),
            Text(measured, style: theme.textTheme.bodyMedium),
          ],
          if (item.drill != null) ...<Widget>[
            const SizedBox(height: 10),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
              decoration: BoxDecoration(
                color: theme.colorScheme.surface,
                borderRadius: BorderRadius.circular(AppSpacing.innerRadius),
              ),
              child: Text.rich(
                TextSpan(
                  children: <InlineSpan>[
                    const TextSpan(text: 'Drill: ', style: TextStyle(fontWeight: FontWeight.w700)),
                    TextSpan(text: item.drill),
                  ],
                ),
                style: theme.textTheme.bodySmall,
              ),
            ),
          ],
        ],
      ),
    );
  }
}

/// The redesign's "Progress" card: a chip per recorded shot type, that
/// shot's overall score over its clips, then best, average and change.
class _ProgressCard extends StatefulWidget {
  const _ProgressCard({required this.insights, required this.initial});

  final List<ShotTypeInsight> insights;
  final ShotType? initial;

  @override
  State<_ProgressCard> createState() => _ProgressCardState();
}

class _ProgressCardState extends State<_ProgressCard> {
  ShotType? _picked;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final List<ShotTypeInsight> all = widget.insights;
    final ShotTypeInsight shot = all.firstWhere(
      (ShotTypeInsight i) => i.shotType == (_picked ?? widget.initial),
      orElse: () => all.first,
    );
    final double? best = shot.bestScore;
    final double? average = shot.averageScore;
    final List<double> comparable = shot.comparableScores;
    final double? change = comparable.length < 2 ? null : comparable.last - comparable.first;
    String num(double? v) => v == null ? '—' : v.round().toString();
    final TextStyle? figure = theme.textTheme.bodyMedium?.copyWith(
      fontWeight: FontWeight.w700,
      color: theme.colorScheme.onSurface,
    );

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        _SectionTitle(
          title: 'Progress',
          trailing: 'Last ${shot.sessions} ${shot.shotType.label.toLowerCase()}'
              '${shot.sessions == 1 ? '' : 's'}',
        ),
        AppCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              Wrap(
                spacing: 6,
                runSpacing: 6,
                children: <Widget>[
                  for (final ShotTypeInsight i in all)
                    _ShotPill(
                      label: i.shotType.label,
                      selected: i.shotType == shot.shotType,
                      onTap: () => setState(() => _picked = i.shotType),
                    ),
                ],
              ),
              const SizedBox(height: AppSpacing.md),
              ScoreLineChart(
                title: '${shot.shotType.label} overall score',
                points: shot.scorePoints,
                dates: shot.sessionDates,
                breaksBefore: shot.versionBoundaries,
                height: 150,
              ),
              const SizedBox(height: AppSpacing.md),
              DefaultTextStyle.merge(
                style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                child: Wrap(
                  alignment: WrapAlignment.spaceBetween,
                  spacing: 12,
                  runSpacing: 4,
                  children: <Widget>[
                    Text.rich(TextSpan(children: <InlineSpan>[
                      const TextSpan(text: 'Best '),
                      TextSpan(text: num(best), style: figure),
                    ])),
                    Text.rich(TextSpan(children: <InlineSpan>[
                      const TextSpan(text: 'Average '),
                      TextSpan(text: num(average), style: figure),
                    ])),
                    Text.rich(TextSpan(children: <InlineSpan>[
                      const TextSpan(text: 'Change '),
                      TextSpan(
                        text: change == null ? '—' : '${change >= 0 ? '+' : ''}${change.round()}',
                        style: figure?.copyWith(
                          color: change == null || change < 0 ? null : CategoryBars.barGreen,
                        ),
                      ),
                    ])),
                  ],
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

/// A rounded filter pill: dark when chosen, grey when not.
class _ShotPill extends StatelessWidget {
  const _ShotPill({required this.label, required this.selected, required this.onTap});

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Semantics(
      button: true,
      selected: selected,
      child: Material(
        color: selected ? theme.colorScheme.onSurface : theme.colorScheme.surfaceContainerHigh,
        shape: const StadiumBorder(),
        child: InkWell(
          onTap: onTap,
          customBorder: const StadiumBorder(),
          child: ConstrainedBox(
            constraints: const BoxConstraints(minHeight: 36),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
              child: Text(
                label,
                style: theme.textTheme.labelLarge?.copyWith(
                  color: selected ? theme.colorScheme.surfaceContainerLowest : theme.colorScheme.onSurfaceVariant,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// A section title with a quiet note on the right ("24 clips").
class _SectionTitle extends StatelessWidget {
  const _SectionTitle({required this.title, required this.trailing});

  final String title;
  final String trailing;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(4, 0, 4, 10),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        crossAxisAlignment: CrossAxisAlignment.baseline,
        textBaseline: TextBaseline.alphabetic,
        children: <Widget>[
          Flexible(
            child: Text(
              title,
              style: theme.textTheme.titleMedium,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
            ),
          ),
          const SizedBox(width: 12),
          Flexible(
            flex: 2,
            child: Text(
              trailing,
              textAlign: TextAlign.end,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
            ),
          ),
        ],
      ),
    );
  }
}

/// Clips, shot types and top speed as three separate tiles.
class _StatTiles extends StatelessWidget {
  const _StatTiles({required this.stats});

  final List<InlineStat> stats;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        for (int i = 0; i < stats.length; i++) ...<Widget>[
          if (i > 0) const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: AppCard(
              padding: const EdgeInsets.all(AppSpacing.md),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(
                    stats[i].label.toUpperCase(),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.labelSmall?.copyWith(
                      color: theme.colorScheme.onSurfaceVariant,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                  const SizedBox(height: AppSpacing.xs),
                  FittedBox(
                    fit: BoxFit.scaleDown,
                    alignment: Alignment.centerLeft,
                    child: Text.rich(
                      TextSpan(
                        // Null means not measured: an em dash, never a zero.
                        text: stats[i].value ?? '—',
                        style: theme.textTheme.titleLarge,
                        children: <InlineSpan>[
                          if (stats[i].value != null && stats[i].unit != null)
                            TextSpan(
                              text: ' ${stats[i].unit}',
                              style: theme.textTheme.bodySmall
                                  ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                            ),
                        ],
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      ],
    );
  }
}

class _RecentRow extends StatelessWidget {
  const _RecentRow({
    required this.summary,
    required this.busy,
    required this.onTap,
  });

  final AnalysisSummary summary;
  final bool busy;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final double? score = summary.overallScore;
    final int? mph = summary.ballSpeedMph;

    return ListTile(
      onTap: onTap,
      contentPadding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.lg,
        vertical: AppSpacing.xs,
      ),
      leading: _ScoreBadge(score: score),
      title: Text(
        summary.shotType.label,
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: theme.textTheme.bodyLarge,
      ),
      subtitle: Text(
        <String>[
          _relativeTime(summary.createdAt),
          // No speed at all when the clip was not calibrated.
          if (mph != null) '$mph mph',
        ].join(' · '),
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: theme.textTheme.bodySmall
            ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
      ),
      trailing: busy
          ? const SizedBox(
              width: 18,
              height: 18,
              child: CircularProgressIndicator(strokeWidth: 2),
            )
          : Icon(Icons.chevron_right, color: theme.colorScheme.onSurfaceVariant),
    );
  }
}

/// The colour-ramped score square. Null renders an em dash, never a 0.
class _ScoreBadge extends StatelessWidget {
  const _ScoreBadge({required this.score});

  final double? score;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final AppPalette palette = context.palette;
    // Text tier for the numeral, fill tier for the wash behind it.
    final Color colour = palette.scoreColor(score);
    final Color wash = palette.scoreFillColor(score);

    return Container(
      width: 44,
      height: 44,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: wash.withValues(alpha: score == null ? 0.08 : 0.16),
        borderRadius: BorderRadius.circular(AppSpacing.innerRadius),
      ),
      child: Text(
        score == null ? '—' : score!.round().toString(),
        style: theme.textTheme.titleMedium?.copyWith(
          color: colour,
          fontFeatures: kTabularFigures,
        ),
      ),
    );
  }
}

/// "Today", "Yesterday", else "Nov 18" (with the year when it is not this
/// year), as in the redesign. Local, and without an i18n package.
String _relativeTime(DateTime? value) {
  if (value == null) return 'Date unknown';
  final DateTime local = value.toLocal();
  final DateTime now = DateTime.now();
  final DateTime today = DateTime(now.year, now.month, now.day);
  final DateTime day = DateTime(local.year, local.month, local.day);
  final int days = today.difference(day).inDays;
  if (days <= 0) return 'Today';
  if (days == 1) return 'Yesterday';
  const List<String> months = <String>[
    'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
  ];
  final String md = '${months[local.month - 1]} ${local.day}';
  return local.year == now.year ? md : '$md, ${local.year}';
}
