import 'package:flutter/material.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import '../models/analysis_response.dart';
import '../models/dashboard_insights.dart';
import '../models/enums.dart';
import '../services/api_client.dart';
import '../theme/app_theme.dart';
import '../widgets/app_card.dart';
import '../widgets/app_logo.dart';
import '../widgets/category_bars.dart';
import '../widgets/content_width.dart';
import '../widgets/empty_state.dart';
import '../widgets/focus_card.dart';
import '../widgets/inline_stats.dart';
import '../widgets/score_ring.dart';
import '../widgets/section_header.dart';
import '../widgets/shot_type_section.dart';
import '../widgets/skeleton_block.dart';
import 'home_shell.dart';
import 'record_screen.dart';
import 'results_screen.dart';

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
  const DashboardScreen({super.key, this.onSeeAllHistory});

  /// Switches the shell to the History tab.
  final VoidCallback? onSeeAllHistory;

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

    final ApiResult<List<AnalysisSummary>> result = await fetchHistory();
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
          await Future.wait(chunk.map(fetchAnalysisDetail));
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

  Future<void> _open(AnalysisSummary summary) async {
    setState(() => _openingId = summary.analysisId);
    final ApiResult<AnalysisResponse> result =
        await fetchAnalysisDetail(summary.analysisId);
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

  Widget _buildBody() {
    if (_loading) {
      return ListView(
        padding: kTabContentPadding,
        children: const <Widget>[
          _DashboardHeader(),
          SizedBox(height: AppSpacing.lg),
          SkeletonBlock(height: 180),
          SizedBox(height: AppSpacing.lg),
          SkeletonBlock(height: 92),
          SizedBox(height: AppSpacing.lg),
          SkeletonBlock(height: 190),
        ],
      );
    }

    final ApiFailure? failure = _failure;
    if (failure != null) {
      return ListView(
        padding: kTabContentPadding,
        children: <Widget>[
          const _DashboardHeader(),
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
          const _DashboardHeader(),
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
        const _DashboardHeader(),
        const SizedBox(height: AppSpacing.lg),
        _HeroCard(
          latest: _items.first,
          // Latest against the previous clip OF THE SAME SHOT, which is the
          // only comparison that means anything.
          delta: insights.latestShotType?.latestDelta,
        ),
        const SizedBox(height: AppSpacing.lg),
        ..._buildFocus(insights),
        AppCard(child: InlineStats(stats: _overallStats(insights))),
        const SizedBox(height: AppSpacing.lg),
        ..._buildLatestBreakdown(),
        ..._buildShotTypes(insights),
        SectionHeader(
          title: 'Recent sessions',
          actionLabel: widget.onSeeAllHistory == null ? null : 'See all',
          onAction: widget.onSeeAllHistory,
        ),
        _buildRecent(),
      ],
    );
  }

  List<Widget> _buildFocus(DashboardInsights insights) {
    if (_detailLoading && _details.isEmpty) {
      return const <Widget>[
        SkeletonBlock(height: 150),
        SizedBox(height: AppSpacing.lg),
      ];
    }
    return <Widget>[
      FocusCard(insight: insights.latestShotType),
      const SizedBox(height: AppSpacing.lg),
    ];
  }

  /// The only cross-shot-type numbers allowed: counts and one record.
  ///
  /// There is deliberately no "average score" here. A mean over forehands,
  /// serves and volleys is arithmetic over three different measurements and
  /// would move when the player simply changed which shot they filmed.
  List<InlineStat> _overallStats(DashboardInsights insights) {
    final CoverageSummary? coverage = insights.latestCoverage;
    return <InlineStat>[
      InlineStat(label: 'Clips', value: insights.totalSessions.toString()),
      InlineStat(
        label: 'Shot types',
        value: insights.shotTypesRecorded.toString(),
      ),
      InlineStat(
        label: 'Top speed',
        value: insights.topSpeedMph?.toString(),
        unit: 'mph',
      ),
      InlineStat(
        label: 'Latest coverage',
        value: coverage == null || coverage.total <= 0
            ? null
            : '${coverage.available}/${coverage.total}',
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
    if (_details.isEmpty || _details.first.categories.isEmpty) {
      return const <Widget>[];
    }
    return <Widget>[
      const SectionHeader(title: 'Latest breakdown'),
      AppCard(child: CategoryBars(categories: _details.first.categories)),
      const SizedBox(height: AppSpacing.lg),
    ];
  }

  List<Widget> _buildShotTypes(DashboardInsights insights) {
    return <Widget>[
      const SectionHeader(title: 'By shot type'),
      for (final ShotTypeInsight insight in insights.shotTypes) ...<Widget>[
        ShotTypeSection(insight: insight),
        const SizedBox(height: AppSpacing.md),
      ],
      // Never-recorded shots are listed rather than hidden, so the player can
      // see what the app would analyse if they filmed it — and they carry no
      // chart and no number at all.
      for (final ShotType shotType in insights.notRecorded) ...<Widget>[
        ShotTypeBlankCard(shotType: shotType),
        const SizedBox(height: AppSpacing.sm),
      ],
      const SizedBox(height: AppSpacing.md),
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

/// Greeting, account, sign-out.
class _DashboardHeader extends StatelessWidget {
  const _DashboardHeader();

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final String? email = Supabase.instance.client.auth.currentUser?.email;
    final String initial =
        (email == null || email.isEmpty) ? '?' : email[0].toUpperCase();

    return Row(
      children: <Widget>[
        CircleAvatar(
          radius: 20,
          backgroundColor: theme.colorScheme.surfaceContainerHigh,
          child: Text(
            initial,
            style: theme.textTheme.titleMedium
                ?.copyWith(color: theme.colorScheme.primary),
          ),
        ),
        const SizedBox(width: AppSpacing.md),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Text(_greeting(), style: theme.textTheme.titleLarge),
              if (email != null && email.isNotEmpty)
                Text(
                  email,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.bodySmall
                      ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                ),
            ],
          ),
        ),
        const AppLogo(size: 28),
        const SizedBox(width: AppSpacing.xs),
        const _OverflowMenu(),
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

/// The overflow menu: sign out. The app is light-only, so there is no theme
/// choice to offer here.
class _OverflowMenu extends StatelessWidget {
  const _OverflowMenu();

  @override
  Widget build(BuildContext context) {
    return PopupMenuButton<String>(
      icon: const Icon(Icons.more_vert),
      onSelected: (String value) {
        if (value == 'sign_out') Supabase.instance.client.auth.signOut();
      },
      itemBuilder: (BuildContext context) => const <PopupMenuEntry<String>>[
        PopupMenuItem<String>(
          value: 'sign_out',
          child: Text('Sign out'),
        ),
      ],
    );
  }
}

/// The latest session, as a score ring with its context.
class _HeroCard extends StatelessWidget {
  const _HeroCard({required this.latest, required this.delta});

  final AnalysisSummary latest;

  /// Only non-null when this session and the previous one of the SAME shot
  /// type were both scored.
  final double? delta;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);

    return AppCard(
      child: Row(
        children: <Widget>[
          ScoreRing(
            score: latest.overallScore,
            delta: delta,
            label: latest.overallScore == null ? 'Not scored' : 'Overall',
            size: 132,
          ),
          const SizedBox(width: AppSpacing.lg),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  'LATEST SESSION',
                  style: theme.textTheme.labelSmall?.copyWith(
                    color: theme.colorScheme.onSurfaceVariant,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                const SizedBox(height: AppSpacing.sm),
                _ShotChip(shotType: latest.shotType.label),
                const SizedBox(height: AppSpacing.sm),
                Text(
                  _relativeTime(latest.createdAt),
                  style: theme.textTheme.bodySmall
                      ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _ShotChip extends StatelessWidget {
  const _ShotChip({required this.shotType});

  final String shotType;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration: BoxDecoration(
        color: theme.colorScheme.surfaceContainerHigh,
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: theme.colorScheme.outline),
      ),
      child: Text(
        shotType,
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: theme.textTheme.labelMedium,
      ),
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

/// "2 hours ago". Local, and without pulling in an i18n package.
String _relativeTime(DateTime? value) {
  if (value == null) return 'Date unknown';
  final DateTime local = value.toLocal();
  final Duration age = DateTime.now().difference(local);

  if (age.inSeconds < 60) return 'Just now';
  if (age.inMinutes < 60) {
    return '${age.inMinutes} min ago';
  }
  if (age.inHours < 24) {
    return '${age.inHours} hour${age.inHours == 1 ? '' : 's'} ago';
  }
  if (age.inDays < 7) {
    return '${age.inDays} day${age.inDays == 1 ? '' : 's'} ago';
  }
  if (age.inDays < 30) {
    final int weeks = age.inDays ~/ 7;
    return '$weeks week${weeks == 1 ? '' : 's'} ago';
  }
  String two(int n) => n.toString().padLeft(2, '0');
  return '${local.year}-${two(local.month)}-${two(local.day)}';
}
