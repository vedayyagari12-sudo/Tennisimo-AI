import 'dart:async';

import 'package:flutter/material.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import '../models/analysis_response.dart';
import '../models/chart_data.dart';
import '../models/dashboard_insights.dart';
import '../models/enums.dart';
import '../models/key_numbers.dart';
import '../models/swing_advice.dart';
import '../services/api_client.dart';
import '../services/account_deletion.dart';
import '../services/api_client.dart' as api
    show deleteAccount, fetchAnalysisDetail, fetchHistory;
import '../services/haptics.dart';
import '../theme/app_theme.dart';
import '../theme/theme_controller.dart';
import '../widgets/app_card.dart';
import '../widgets/app_logo.dart';
import '../widgets/category_bars.dart';
import '../widgets/content_width.dart';
import '../widgets/empty_state.dart';
import '../widgets/focus_card.dart';
import '../widgets/inline_stats.dart';
import '../widgets/score_ring.dart';
import '../widgets/section_header.dart';
import '../widgets/shot_mix_chart.dart';
import '../widgets/shot_type_section.dart';
import '../widgets/skeleton_block.dart';
import '../widgets/skill_radar.dart';
import '../widgets/swing_advice_list.dart';
import 'home_shell.dart';
import 'login_screen.dart';
import 'record_screen.dart';
import 'results_screen.dart';

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
    this.dataSource = const DashboardDataSource(),
  });

  /// Switches the shell to the History tab.
  final VoidCallback? onSeeAllHistory;

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

  /// The "Delete account" menu entry: confirm, delete, then leave.
  ///
  /// The dialog does the request itself and only closes with `true` once the
  /// server has answered success, so nothing below runs on a failed or
  /// unfinished deletion.
  Future<void> _deleteAccount() async {
    final bool? deleted = await showDialog<bool>(
      context: context,
      // Only an explicit Cancel closes it: a stray tap outside must not look
      // like an answer to a question this serious.
      barrierDismissible: false,
      builder: (BuildContext context) =>
          _DeleteAccountDialog(onDelete: widget.dataSource.deleteAccount),
    );
    if (deleted != true || !mounted) return;

    // Queued BEFORE signing out: the sign-out is what makes the AuthGate build
    // the login screen that shows it.
    LoginNotice.post(kAccountDeletedNotice);
    Navigator.of(context).popUntil((Route<dynamic> route) => route.isFirst);
    try {
      // Local scope (gotrue's default): the session is removed from memory and
      // storage FIRST, then the server is told. The server has already
      // deleted the user, so that second step may fail — harmlessly, since
      // the local session, and with it the cached token, is already gone.
      await widget.dataSource.signOut();
    } catch (_) {
      // See above: nothing is left signed in on this device.
    }
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
        onSignOut: widget.dataSource.signOut,
        onDeleteAccount: _deleteAccount,
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
        ),
        const SizedBox(height: AppSpacing.lg),
        ..._buildFocus(insights),
        _buildOverview(insights),
        const SizedBox(height: AppSpacing.lg),
        ..._buildLatestBreakdown(),
        ..._buildSwingNotes(),
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

  /// The overall counts, and the shot mix they break down into, in one card.
  ///
  /// The mix is a COUNT per shot type — a part-to-whole of clips — which is
  /// the one cross-shot-type view that is legitimate: nothing is averaged.
  Widget _buildOverview(DashboardInsights insights) {
    final ThemeData theme = Theme.of(context);
    final List<ShotMixSlice> mix = buildShotMix(insights.shotTypes);
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          InlineStats(stats: _overallStats(insights)),
          if (mix.isNotEmpty) ...<Widget>[
            const SizedBox(height: AppSpacing.md),
            const Divider(height: 1),
            const SizedBox(height: AppSpacing.md),
            Text(
              'SHOT MIX',
              style: theme.textTheme.labelSmall?.copyWith(
                color: theme.colorScheme.onSurfaceVariant,
                fontWeight: FontWeight.w600,
              ),
            ),
            const SizedBox(height: AppSpacing.sm),
            ShotMixChart(slices: mix),
          ],
        ],
      ),
    );
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
        label: 'Shot types',
        value: insights.shotTypesRecorded.toString(),
      ),
      InlineStat(
        label: 'Top speed',
        value: insights.topSpeedMph?.toString(),
        unit: 'mph',
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
    // The radar shows the SHAPE, against the previous swing of the same shot
    // when that one loaded; the bars under it are its exact numbers and
    // coverage, so no value on the radar is reachable only by tapping.
    final SkillProfile? profile = buildSkillProfile(
      latest,
      previous: previousOfSameShot(_items, _details),
    );
    return <Widget>[
      const SectionHeader(title: 'Latest breakdown'),
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
          ],
        ),
      ),
      const SizedBox(height: AppSpacing.lg),
    ];
  }

  /// The newest clip's top few plain coaching bullets, from the fixed rule
  /// table in `swing_advice.dart` — not from AI text.
  ///
  /// Silent when the newest clip's detail is not loaded (the breakdown block
  /// above already says so) or produced nothing to say.
  List<Widget> _buildSwingNotes() {
    final AnalysisResponse? latest = _latestDetail;
    if (latest == null) return const <Widget>[];
    final List<AdviceBullet> bullets = buildSwingAdvice(
      buildKeyNumbers(latest),
      maxFixes: kDashboardFixBullets,
      maxPraise: kDashboardPraiseBullets,
    );
    if (bullets.isEmpty) return const <Widget>[];
    return <Widget>[
      const SectionHeader(title: 'Swing notes'),
      AppCard(child: SwingAdviceList(bullets: bullets)),
      const SizedBox(height: AppSpacing.lg),
    ];
  }

  List<Widget> _buildShotTypes(DashboardInsights insights) {
    return <Widget>[
      const SectionHeader(title: 'By shot type'),
      for (final ShotTypeInsight insight in insights.shotTypes) ...<Widget>[
        // Only the shot the player last hit starts open; the rest are one
        // header row each. Keyed by shot so an open section stays open while
        // the detail requests land and the list rebuilds.
        ShotTypeSection(
          key: ValueKey<ShotType>(insight.shotType),
          insight: insight,
          initiallyExpanded:
              insight.shotType == insights.latestShotType?.shotType,
        ),
        const SizedBox(height: AppSpacing.md),
      ],
      // Never-recorded shots are listed rather than hidden, so the player can
      // see what the app would analyse if they filmed it — and they carry no
      // chart and no number at all.
      if (insights.notRecorded.isNotEmpty) ...<Widget>[
        ShotTypesNotRecordedCard(shotTypes: insights.notRecorded),
        const SizedBox(height: AppSpacing.md),
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
  const _DashboardHeader({
    required this.email,
    required this.onSignOut,
    required this.onDeleteAccount,
  });

  final String? email;
  final Future<void> Function() onSignOut;
  final Future<void> Function() onDeleteAccount;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final String? email = this.email;
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
        _OverflowMenu(
          onSignOut: onSignOut,
          onDeleteAccount: onDeleteAccount,
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

/// The overflow menu: the light / dark choice, then sign out, then — set apart
/// and in the error colour — delete account.
///
/// The theme entries render only under a [ThemeScope], which `main.dart`
/// installs on the web and nowhere else. On mobile, and in a widget test that
/// mounts the dashboard on its own, the theme entries are absent.
class _OverflowMenu extends StatelessWidget {
  const _OverflowMenu({required this.onSignOut, required this.onDeleteAccount});

  final Future<void> Function() onSignOut;

  /// Opens the confirmation dialog. Choosing the menu entry deletes nothing.
  final Future<void> Function() onDeleteAccount;

  static const Map<ThemeMode, String> _themeLabels = <ThemeMode, String>{
    ThemeMode.system: 'Follow system',
    ThemeMode.light: 'Light',
    ThemeMode.dark: 'Dark',
  };

  @override
  Widget build(BuildContext context) {
    final ThemeController? controller = ThemeScope.maybeOf(context);
    final Color destructive = Theme.of(context).colorScheme.error;

    return PopupMenuButton<String>(
      icon: const Icon(Icons.more_vert),
      onSelected: (String value) {
        if (value == 'sign_out') {
          onSignOut();
          return;
        }
        if (value == 'delete_account') {
          onDeleteAccount();
          return;
        }
        for (final ThemeMode mode in _themeLabels.keys) {
          if (value == 'theme_${mode.name}') controller?.setMode(mode);
        }
      },
      itemBuilder: (BuildContext context) => <PopupMenuEntry<String>>[
        if (controller != null) ...<PopupMenuEntry<String>>[
          for (final MapEntry<ThemeMode, String> entry in _themeLabels.entries)
            CheckedPopupMenuItem<String>(
              value: 'theme_${entry.key.name}',
              checked: controller.mode == entry.key,
              child: Text(entry.value),
            ),
          const PopupMenuDivider(),
        ],
        const PopupMenuItem<String>(
          value: 'sign_out',
          child: Text('Sign out'),
        ),
        const PopupMenuDivider(),
        PopupMenuItem<String>(
          value: 'delete_account',
          child: Text(
            'Delete account',
            style: TextStyle(color: destructive),
          ),
        ),
      ],
    );
  }
}

/// Confirms, then performs, the permanent deletion of the account.
///
/// Three deliberate steps stand between the menu and the request: the menu
/// entry only opens this dialog; the delete button stays disabled until
/// [kDeleteConfirmationPhrase] is typed (see its doc for why a typed word);
/// and only then does a tap send anything.
///
/// The request runs HERE rather than after the dialog closes, so a failure is
/// shown in the same place the user asked, with the typed word still in the
/// field and the button relabelled "Try again". The dialog closes with `true`
/// only on a server-confirmed success; nothing ever pretends otherwise.
class _DeleteAccountDialog extends StatefulWidget {
  const _DeleteAccountDialog({required this.onDelete});

  final Future<ApiResult<void>> Function() onDelete;

  @override
  State<_DeleteAccountDialog> createState() => _DeleteAccountDialogState();
}

class _DeleteAccountDialogState extends State<_DeleteAccountDialog> {
  final TextEditingController _typed = TextEditingController();

  /// True from the confirming tap until the server answers.
  ///
  /// Same idea as the record screen's intake guard: set synchronously, before
  /// the first await, so a second tap in the same frame is refused by the
  /// check in [_confirm] even before the rebuild disables the button. While it
  /// is true the dialog also cannot be popped — the user must not be left
  /// wondering whether a request they walked away from went through.
  bool _inFlight = false;

  /// The last failure, already worded for this action. See
  /// [accountDeletionFailure].
  ApiFailure? _failure;

  @override
  void dispose() {
    _typed.dispose();
    super.dispose();
  }

  bool get _canConfirm => !_inFlight && deleteConfirmationMatches(_typed.text);

  Future<void> _confirm() async {
    if (!_canConfirm) return;
    setState(() {
      _inFlight = true;
      _failure = null;
    });
    unawaited(haptics.accountDeletionConfirmed());

    final ApiResult<void> result = await widget.onDelete();
    if (!mounted) return;

    if (result.isOk) {
      Navigator.of(context).pop(true);
      return;
    }
    setState(() {
      _inFlight = false;
      _failure = accountDeletionFailure(result.failure!);
    });
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final ColorScheme colors = theme.colorScheme;
    final ApiFailure? failure = _failure;

    return PopScope<bool>(
      canPop: !_inFlight,
      child: AlertDialog(
        scrollable: true,
        title: const Text('Delete your account?'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            const Text(
              'This permanently deletes your account and every swing you have '
              'recorded: the videos, the scores and the coaching notes. It '
              'cannot be undone.',
            ),
            const SizedBox(height: AppSpacing.lg),
            const Text('Type $kDeleteConfirmationPhrase to confirm.'),
            const SizedBox(height: AppSpacing.sm),
            TextField(
              controller: _typed,
              enabled: !_inFlight,
              autocorrect: false,
              enableSuggestions: false,
              textCapitalization: TextCapitalization.characters,
              decoration: const InputDecoration(
                labelText: kDeleteConfirmationPhrase,
                border: OutlineInputBorder(),
              ),
              onChanged: (_) => setState(() {}),
              onSubmitted: (_) => _confirm(),
            ),
            if (failure != null) ...<Widget>[
              const SizedBox(height: AppSpacing.lg),
              Container(
                padding: const EdgeInsets.all(AppSpacing.md),
                decoration: BoxDecoration(
                  color: colors.errorContainer,
                  borderRadius: BorderRadius.circular(AppSpacing.innerRadius),
                ),
                child: Text(
                  failure.plainLanguageWithDebugDetail,
                  style: theme.textTheme.bodyMedium
                      ?.copyWith(color: colors.onErrorContainer),
                ),
              ),
            ],
          ],
        ),
        actions: <Widget>[
          TextButton(
            onPressed: _inFlight ? null : () => Navigator.of(context).pop(false),
            child: const Text('Cancel'),
          ),
          TextButton(
            style: TextButton.styleFrom(foregroundColor: colors.error),
            onPressed: _canConfirm ? _confirm : null,
            child: Text(
              _inFlight
                  ? 'Deleting…'
                  : (failure == null ? 'Delete forever' : 'Try again'),
            ),
          ),
        ],
      ),
    );
  }
}

/// The latest session, as a score ring with its context.
class _HeroCard extends StatelessWidget {
  const _HeroCard({
    required this.latest,
    required this.delta,
    required this.coverage,
  });

  final AnalysisSummary latest;

  /// This session's own metric coverage, or null when its detail is not
  /// loaded.
  final CoverageSummary? coverage;

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
                  <String>[
                    _relativeTime(latest.createdAt),
                    // No speed at all when the clip was not calibrated.
                    if (latest.ballSpeedMph != null)
                      '${latest.ballSpeedMph} mph',
                  ].join(' · '),
                  style: theme.textTheme.bodySmall
                      ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                ),
                if (coverage case final CoverageSummary c
                    when c.total > 0) ...<Widget>[
                  const SizedBox(height: AppSpacing.xs),
                  Text(
                    '${c.available} of ${c.total} '
                    '${plural(c.total, 'metric')} measured',
                    style: theme.textTheme.bodySmall
                        ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                  ),
                ],
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
