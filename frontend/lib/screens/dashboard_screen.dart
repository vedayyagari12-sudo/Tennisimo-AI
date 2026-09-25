import 'package:flutter/material.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import '../models/analysis_response.dart';
import '../services/api_client.dart';
import '../theme/app_theme.dart';
import '../widgets/app_card.dart';
import '../widgets/app_logo.dart';
import '../widgets/category_bars.dart';
import '../widgets/content_width.dart';
import '../widgets/empty_state.dart';
import '../widgets/score_ring.dart';
import '../widgets/section_header.dart';
import '../widgets/stat_tile.dart';
import '../widgets/trend_chart.dart';
import 'home_shell.dart';
import 'record_screen.dart';
import 'results_screen.dart';

/// The landing tab: latest session, headline stats, score trend, the newest
/// analysis's category breakdown, and the most recent sessions.
///
/// Every number here comes from the server. Anything the server left null is
/// rendered as "not measured" — never as a zero.
class DashboardScreen extends StatefulWidget {
  const DashboardScreen({super.key, this.onSeeAllHistory});

  /// Switches the shell to the History tab.
  final VoidCallback? onSeeAllHistory;

  @override
  State<DashboardScreen> createState() => DashboardScreenState();
}

/// Public so the shell can call [refresh] after a recording.
class DashboardScreenState extends State<DashboardScreen> {
  /// How many sessions the trend chart plots.
  static const int _trendWindow = 10;

  /// How many sessions the rolling average covers.
  static const int _averageWindow = 5;

  bool _loading = true;
  ApiFailure? _failure;
  List<AnalysisSummary> _items = const <AnalysisSummary>[];

  /// The newest analysis's category breakdown, from a second request.
  bool _detailLoading = false;
  List<CategoryScore> _categories = const <CategoryScore>[];

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
        _categories = const <CategoryScore>[];
      });
      return;
    }

    setState(() {
      _loading = false;
      _items = result.data!;
      _categories = const <CategoryScore>[];
    });
    await _loadLatestBreakdown();
  }

  /// Fetches the newest analysis in full, purely for its category breakdown.
  ///
  /// The history endpoint carries no per-category data, and fetching a detail
  /// per session to build a category time series would be N requests for a
  /// chart — so this shows the LATEST analysis only. A failure here hides the
  /// section; it never fails the whole screen.
  Future<void> _loadLatestBreakdown() async {
    if (_items.isEmpty) return;
    final String id = _items.first.analysisId;

    setState(() => _detailLoading = true);
    final ApiResult<AnalysisResponse> result = await fetchAnalysisDetail(id);
    if (!mounted) return;
    setState(() {
      _detailLoading = false;
      _categories =
          result.isOk ? result.data!.categories : const <CategoryScore>[];
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
          _SkeletonBlock(height: 180),
          SizedBox(height: AppSpacing.lg),
          _SkeletonBlock(height: 92),
          SizedBox(height: AppSpacing.lg),
          _SkeletonBlock(height: 190),
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

    return ListView(
      padding: kTabContentPadding,
      children: <Widget>[
        const _DashboardHeader(),
        const SizedBox(height: AppSpacing.lg),
        _HeroCard(
          latest: _items.first,
          delta: _latestDelta(),
        ),
        const SizedBox(height: AppSpacing.lg),
        ..._buildStatRows(),
        const SizedBox(height: AppSpacing.lg),
        const SectionHeader(title: 'Progress'),
        AppCard(child: _buildTrend()),
        const SizedBox(height: AppSpacing.lg),
        ..._buildBreakdown(),
        SectionHeader(
          title: 'Recent sessions',
          actionLabel: widget.onSeeAllHistory == null ? null : 'See all',
          onAction: widget.onSeeAllHistory,
        ),
        _buildRecent(),
      ],
    );
  }

  /// Latest minus previous, only when BOTH sessions were actually scored.
  double? _latestDelta() {
    if (_items.length < 2) return null;
    final double? latest = _items[0].overallScore;
    final double? previous = _items[1].overallScore;
    if (latest == null || previous == null) return null;
    return latest - previous;
  }

  List<Widget> _buildStatRows() {
    final List<double> scored = _items
        .map((AnalysisSummary e) => e.overallScore)
        .whereType<double>()
        .toList();
    final List<double> recentScored = _items
        .take(_averageWindow)
        .map((AnalysisSummary e) => e.overallScore)
        .whereType<double>()
        .toList();
    final List<int> speeds = _items
        .map((AnalysisSummary e) => e.ballSpeedMph)
        .whereType<int>()
        .toList();

    final double? best =
        scored.isEmpty ? null : scored.reduce((double a, double b) => a > b ? a : b);
    final double? average = recentScored.isEmpty
        ? null
        : recentScored.reduce((double a, double b) => a + b) /
            recentScored.length;
    final int? topSpeed =
        speeds.isEmpty ? null : speeds.reduce((int a, int b) => a > b ? a : b);

    final ColorScheme scheme = Theme.of(context).colorScheme;

    final List<Widget> tiles = <Widget>[
      StatTile(label: 'Sessions', value: _items.length.toString()),
      StatTile(
        label: 'Best score',
        // Null when nothing has ever been scored — not 0.
        value: best?.round().toString(),
        valueColor: scoreColor(best),
      ),
      StatTile(
        label: 'Avg last $_averageWindow',
        value: average?.toStringAsFixed(1),
        valueColor: scoreColor(average),
      ),
      // The speed tile is omitted outright when no session ever carried a
      // speed: that means nobody calibrated, not that the ball was slow.
      if (topSpeed != null)
        StatTile(
          label: 'Top speed',
          value: topSpeed.toString(),
          unit: 'mph',
          valueColor: scheme.secondary,
          accent: scheme.secondary,
        ),
    ];

    return _inRowsOfTwo(tiles);
  }

  /// Lays tiles out two per row, so a missing speed tile leaves no hole.
  List<Widget> _inRowsOfTwo(List<Widget> tiles) {
    final List<Widget> rows = <Widget>[];
    for (int i = 0; i < tiles.length; i += 2) {
      final bool hasSecond = i + 1 < tiles.length;
      if (i > 0) rows.add(const SizedBox(height: AppSpacing.md));
      rows.add(Row(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Expanded(child: tiles[i]),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: hasSecond ? tiles[i + 1] : const SizedBox.shrink(),
          ),
        ],
      ));
    }
    return rows;
  }

  Widget _buildTrend() {
    // History is newest first; the chart reads oldest -> newest.
    final List<AnalysisSummary> window =
        _items.take(_trendWindow).toList().reversed.toList();
    final List<double?> scores =
        window.map((AnalysisSummary e) => e.overallScore).toList();
    final int plotted = scores.whereType<double>().length;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        TrendChart(scores: scores),
        const SizedBox(height: AppSpacing.sm),
        Text(
          plotted == 0
              ? 'Overall score · no scored sessions in the last '
                  '${window.length}'
              : 'Overall score · last $plotted scored '
                  '${plotted == 1 ? 'session' : 'sessions'}',
          style: Theme.of(context).textTheme.bodySmall?.copyWith(
                color: Theme.of(context).colorScheme.onSurfaceVariant,
              ),
        ),
      ],
    );
  }

  /// The latest analysis's categories, or nothing at all.
  List<Widget> _buildBreakdown() {
    if (_detailLoading) {
      return const <Widget>[
        SectionHeader(title: 'Latest breakdown'),
        AppCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              _SkeletonBlock(height: 14),
              SizedBox(height: AppSpacing.md),
              _SkeletonBlock(height: 14),
              SizedBox(height: AppSpacing.md),
              _SkeletonBlock(height: 14),
            ],
          ),
        ),
        SizedBox(height: AppSpacing.lg),
      ];
    }
    // Detail request failed, or the analysis carried no categories: the
    // section disappears rather than showing an error on a working screen.
    if (_categories.isEmpty) return const <Widget>[];

    return <Widget>[
      const SectionHeader(title: 'Latest breakdown'),
      AppCard(child: CategoryBars(categories: _categories)),
      const SizedBox(height: AppSpacing.lg),
    ];
  }

  Widget _buildRecent() {
    final List<AnalysisSummary> recent = _items.take(3).toList();
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
        PopupMenuButton<String>(
          icon: const Icon(Icons.more_vert),
          onSelected: (String value) {
            if (value == 'sign_out') {
              Supabase.instance.client.auth.signOut();
            }
          },
          itemBuilder: (BuildContext context) => const <PopupMenuEntry<String>>[
            PopupMenuItem<String>(
              value: 'sign_out',
              child: Text('Sign out'),
            ),
          ],
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

/// The latest session, as a score ring with its context.
class _HeroCard extends StatelessWidget {
  const _HeroCard({required this.latest, required this.delta});

  final AnalysisSummary latest;

  /// Only non-null when this session and the one before it were both scored.
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
      child: Text(shotType, style: theme.textTheme.labelMedium),
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
      title: Text(summary.shotType.label, style: theme.textTheme.bodyLarge),
      subtitle: Text(
        <String>[
          _relativeTime(summary.createdAt),
          // No speed at all when the clip was not calibrated.
          if (mph != null) '$mph mph',
        ].join(' · '),
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
    final Color colour = scoreColor(score);

    return Container(
      width: 44,
      height: 44,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: colour.withValues(alpha: score == null ? 0.08 : 0.16),
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

/// A shimmer-free loading block. Quieter than a spinner on a dark surface.
class _SkeletonBlock extends StatelessWidget {
  const _SkeletonBlock({required this.height});

  final double height;

  @override
  Widget build(BuildContext context) {
    return Container(
      height: height,
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surfaceContainer,
        borderRadius: BorderRadius.circular(AppSpacing.cardRadius),
        border: Border.all(color: Theme.of(context).colorScheme.outline),
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
