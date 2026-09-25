import 'package:flutter/material.dart';

import '../models/analysis_response.dart';
import '../services/api_client.dart';
import '../theme/app_theme.dart';
import '../widgets/app_card.dart';
import '../widgets/content_width.dart';
import '../widgets/empty_state.dart';
import '../widgets/section_header.dart';
import '../widgets/trend_chart.dart';
import 'home_shell.dart';
import 'record_screen.dart';
import 'results_screen.dart';

/// Past analyses: the score trend, personal bests, and every session grouped
/// by how recent it is.
///
/// Class name and constructor are unchanged from the previous version so
/// existing imports keep working.
class HistoryScreen extends StatefulWidget {
  const HistoryScreen({super.key});

  @override
  State<HistoryScreen> createState() => _HistoryScreenState();
}

/// The recency buckets sessions are grouped into, in display order.
enum _Bucket {
  today('Today'),
  yesterday('Yesterday'),
  thisWeek('This week'),
  earlier('Earlier');

  const _Bucket(this.label);

  final String label;
}

class _HistoryScreenState extends State<HistoryScreen> {
  bool _loading = true;
  ApiFailure? _failure;
  List<AnalysisSummary> _items = const <AnalysisSummary>[];
  String? _openingId;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _failure = null;
    });
    final ApiResult<List<AnalysisSummary>> result = await fetchHistory();
    if (!mounted) return;
    setState(() {
      _loading = false;
      if (result.isOk) {
        _items = result.data!;
        _failure = null;
      } else {
        _failure = result.failure;
      }
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
    final ThemeData theme = Theme.of(context);
    final Widget title = Padding(
      padding: const EdgeInsets.only(bottom: AppSpacing.lg),
      child: Text('History', style: theme.textTheme.headlineSmall),
    );

    if (_loading) {
      return ListView(
        padding: kTabContentPadding,
        children: <Widget>[
          title,
          const _SkeletonBlock(height: 190),
          const SizedBox(height: AppSpacing.lg),
          const _SkeletonBlock(height: 88),
          const SizedBox(height: AppSpacing.lg),
          const _SkeletonBlock(height: 220),
        ],
      );
    }

    final ApiFailure? failure = _failure;
    if (failure != null) {
      return ListView(
        padding: kTabContentPadding,
        children: <Widget>[
          title,
          const SizedBox(height: AppSpacing.xxl),
          EmptyState(
            icon: Icons.cloud_off,
            headline: 'Could not load your history',
            supporting: failure.plainLanguageWithDebugDetail,
            actionLabel: 'Try again',
            onAction: _load,
          ),
        ],
      );
    }

    if (_items.isEmpty) {
      // The first thing a brand-new user ever sees.
      return ListView(
        padding: kTabContentPadding,
        children: <Widget>[
          title,
          const SizedBox(height: AppSpacing.xxl),
          EmptyState(
            icon: Icons.timeline,
            headline: 'Your swing history starts here',
            supporting:
                'Every swing you analyse is saved with its score, so you can '
                'watch your technique improve session by session.',
            actionLabel: 'Record your first swing',
            onAction: _record,
          ),
        ],
      );
    }

    return ListView(
      padding: kTabContentPadding,
      children: <Widget>[
        title,
        AppCard(child: _buildTrend()),
        const SizedBox(height: AppSpacing.lg),
        _buildPersonalBests(),
        const SizedBox(height: AppSpacing.xl),
        ..._buildGroups(),
      ],
    );
  }

  Widget _buildTrend() {
    // Oldest -> newest, with unscored sessions left out by the chart itself.
    final List<double?> scores = _items.reversed
        .map((AnalysisSummary e) => e.overallScore)
        .toList(growable: false);
    final int plotted = scores.whereType<double>().length;
    final ThemeData theme = Theme.of(context);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        TrendChart(scores: scores),
        const SizedBox(height: AppSpacing.sm),
        Text(
          plotted == 0
              ? 'Overall score · none of your ${_items.length} sessions '
                  'could be scored'
              : 'Overall score · $plotted scored '
                  '${plotted == 1 ? 'session' : 'sessions'} '
                  'of ${_items.length}',
          style: theme.textTheme.bodySmall
              ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
        ),
      ],
    );
  }

  /// Best score, and best speed only when some session actually had one.
  Widget _buildPersonalBests() {
    final ThemeData theme = Theme.of(context);
    final List<double> scored = _items
        .map((AnalysisSummary e) => e.overallScore)
        .whereType<double>()
        .toList();
    final List<int> speeds = _items
        .map((AnalysisSummary e) => e.ballSpeedMph)
        .whereType<int>()
        .toList();
    final double? best = scored.isEmpty
        ? null
        : scored.reduce((double a, double b) => a > b ? a : b);
    final int? fastest =
        speeds.isEmpty ? null : speeds.reduce((int a, int b) => a > b ? a : b);

    return AppCard(
      child: Row(
        children: <Widget>[
          Icon(Icons.emoji_events_outlined, color: theme.colorScheme.primary),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  'PERSONAL BEST',
                  style: theme.textTheme.labelSmall?.copyWith(
                    color: theme.colorScheme.onSurfaceVariant,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                const SizedBox(height: AppSpacing.xs),
                Text(
                  best == null
                      // Nothing has been scored yet: say so, do not show 0.
                      ? 'No scored session yet'
                      : 'Best score ${best.round()}',
                  style: theme.textTheme.titleMedium?.copyWith(
                    color: scoreColor(best),
                    fontFeatures: kTabularFigures,
                  ),
                ),
                // Omitted entirely when no clip was ever calibrated.
                if (fastest != null) ...<Widget>[
                  const SizedBox(height: AppSpacing.xs),
                  Text(
                    'Fastest ball $fastest mph',
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: theme.colorScheme.secondary,
                      fontFeatures: kTabularFigures,
                    ),
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }

  List<Widget> _buildGroups() {
    final Map<_Bucket, List<AnalysisSummary>> grouped =
        <_Bucket, List<AnalysisSummary>>{};
    for (final AnalysisSummary item in _items) {
      grouped.putIfAbsent(_bucketFor(item.createdAt), () => <AnalysisSummary>[])
          .add(item);
    }

    final List<Widget> out = <Widget>[];
    for (final _Bucket bucket in _Bucket.values) {
      final List<AnalysisSummary>? rows = grouped[bucket];
      if (rows == null || rows.isEmpty) continue;
      out.add(SectionHeader(title: bucket.label));
      out.add(AppCard(
        padding: EdgeInsets.zero,
        child: Column(
          children: <Widget>[
            for (int i = 0; i < rows.length; i++) ...<Widget>[
              if (i > 0) const Divider(height: 1, indent: AppSpacing.lg),
              _SessionRow(
                summary: rows[i],
                timeLabel: _timeLabel(rows[i].createdAt, bucket),
                busy: _openingId == rows[i].analysisId,
                onTap: _openingId == null ? () => _open(rows[i]) : null,
              ),
            ],
          ],
        ),
      ));
      out.add(const SizedBox(height: AppSpacing.xl));
    }
    return out;
  }

  _Bucket _bucketFor(DateTime? value) {
    if (value == null) return _Bucket.earlier;
    final DateTime local = value.toLocal();
    final DateTime now = DateTime.now();
    final DateTime day = DateTime(local.year, local.month, local.day);
    final DateTime today = DateTime(now.year, now.month, now.day);
    final int days = today.difference(day).inDays;
    if (days <= 0) return _Bucket.today;
    if (days == 1) return _Bucket.yesterday;
    if (days < 7) return _Bucket.thisWeek;
    return _Bucket.earlier;
  }

  /// Clock time inside the last two days, full date beyond that.
  String _timeLabel(DateTime? value, _Bucket bucket) {
    if (value == null) return 'Date unknown';
    final DateTime local = value.toLocal();
    String two(int n) => n.toString().padLeft(2, '0');
    final String clock = '${two(local.hour)}:${two(local.minute)}';
    if (bucket == _Bucket.today || bucket == _Bucket.yesterday) return clock;
    return '${local.year}-${two(local.month)}-${two(local.day)} · $clock';
  }
}

class _SessionRow extends StatelessWidget {
  const _SessionRow({
    required this.summary,
    required this.timeLabel,
    required this.busy,
    required this.onTap,
  });

  final AnalysisSummary summary;
  final String timeLabel;
  final bool busy;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final int? mph = summary.ballSpeedMph;

    return ListTile(
      onTap: onTap,
      contentPadding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.lg,
        vertical: AppSpacing.xs,
      ),
      leading: _ScoreChip(score: summary.overallScore),
      title: Text(summary.shotType.label, style: theme.textTheme.bodyLarge),
      subtitle: Text(
        timeLabel,
        style: theme.textTheme.bodySmall
            ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
      ),
      trailing: busy
          ? const SizedBox(
              width: 18,
              height: 18,
              child: CircularProgressIndicator(strokeWidth: 2),
            )
          : Row(
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                // Speed is shown only when the server measured one.
                if (mph != null)
                  Text(
                    '$mph mph',
                    style: theme.textTheme.labelMedium?.copyWith(
                      color: theme.colorScheme.secondary,
                      fontFeatures: kTabularFigures,
                    ),
                  ),
                Icon(Icons.chevron_right,
                    color: theme.colorScheme.onSurfaceVariant),
              ],
            ),
    );
  }
}

/// A colour-ramped score pill. A null score is an em dash, never a 0.
class _ScoreChip extends StatelessWidget {
  const _ScoreChip({required this.score});

  final double? score;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final double? value = score;
    final Color colour = scoreColor(value);

    return Container(
      width: 44,
      height: 44,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: colour.withValues(alpha: value == null ? 0.08 : 0.16),
        borderRadius: BorderRadius.circular(AppSpacing.innerRadius),
      ),
      child: Text(
        value == null ? '—' : value.round().toString(),
        style: theme.textTheme.titleMedium?.copyWith(
          color: colour,
          fontFeatures: kTabularFigures,
        ),
      ),
    );
  }
}

/// Quiet loading placeholder, matching the dashboard's.
class _SkeletonBlock extends StatelessWidget {
  const _SkeletonBlock({required this.height});

  final double height;

  @override
  Widget build(BuildContext context) {
    final ColorScheme scheme = Theme.of(context).colorScheme;
    return Container(
      height: height,
      decoration: BoxDecoration(
        color: scheme.surfaceContainer,
        borderRadius: BorderRadius.circular(AppSpacing.cardRadius),
        border: Border.all(color: scheme.outline),
      ),
    );
  }
}
