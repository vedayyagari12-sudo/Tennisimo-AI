// Match mode (2026-11-09): the Matches and Rallies tabs, the upload flow and
// the result screen. Both are a video from one still phone; the server tracks
// the ball, finds bounces, rallies and line calls. A rally also gets an
// overall coaching summary that AI words from those measured numbers.

import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../models/coaching_feedback.dart';
import '../models/court.dart';
import '../models/enums.dart';
import '../models/match.dart';
import '../models/video_clip.dart';
import '../services/api_client.dart';
import '../services/supabase_storage.dart';
import '../services/video_file_picker.dart';
import '../services/video_intake.dart';
import '../theme/app_theme.dart';
import '../widgets/app_card.dart';
import '../widgets/court_map.dart';
import '../widgets/empty_state.dart';
import '../widgets/line_call_card.dart';
import '../widgets/rally_timeline.dart';

/// How often a running match is polled.
const Duration kMatchPollInterval = Duration(seconds: 3);

/// Landing flag set when the camera could not be confirmed still.
const String kCameraUnverifiedFlag = 'camera_unverified';

String _percent(double? value) => value == null ? '—' : '${value.round()}%';

/// The words that differ between the two tabs.
extension SessionKindCopy on SessionKind {
  String get noun => this == SessionKind.rally ? 'Rally' : 'Match';
  String get tabTitle => this == SessionKind.rally ? 'Rallies' : 'Matches';
  String get newLabel => this == SessionKind.rally ? 'New rally' : 'New match';
  String get emptyHeadline => this == SessionKind.rally ? 'No rallies yet' : 'No matches yet';
  String get listTitle => this == SessionKind.rally ? 'Your rallies' : 'Your matches';
  String get intro => this == SessionKind.rally
      ? 'Film yourself rallying and get overall coaching on consistency, depth and '
          'placement. AI writes it from what we measured.'
      : 'Rally or play a set, then see where every ball landed, your rallies '
          'and the line calls.';
  String get emptySupporting => this == SessionKind.rally
      ? 'Film a rally from behind the baseline to get your first coaching summary.'
      : 'Film a rally from behind the baseline and the court map fills in.';

  /// Title of the "how it works" card at the top of the tab.
  String get guideTitle => this == SessionKind.rally ? 'How rallies work' : 'How matches work';

  /// The three steps, in order, in plain words.
  List<(String, String)> get guideSteps => this == SessionKind.rally
      ? const <(String, String)>[
          ('Film', 'Prop your phone still behind the baseline so the whole court is in view, then rally.'),
          ('Upload', 'Tap “New rally” and choose the video. Analysis takes about twice the video length, '
              'and you can leave and come back.'),
          ('Read the coaching', 'Tap the finished rally for a short coaching summary and the numbers behind it.'),
        ]
      : const <(String, String)>[
          ('Film', 'Prop your phone still behind the baseline so the whole court is in view, then play.'),
          ('Upload', 'Tap “New match” and choose the video. Analysis takes about twice the video length, '
              'and you can leave and come back.'),
          ('Explore', 'Tap the finished match to see the shot map, your rallies and the closest line calls.'),
        ];

  /// Points at the other tab, because the two are easy to mix up.
  String get otherTabHint => this == SessionKind.rally
      ? 'Want the shot map and line calls instead? Tap Matches above.'
      : 'Want coaching on one rally instead? Tap Rallies above.';
}

String _matchTitle(MatchRecord match) {
  if (match.title != null) return match.title!;
  final DateTime? at = match.createdAt;
  if (at == null) return match.kind.noun;
  const List<String> months = <String>[
    'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
  ];
  return '${match.kind.noun}, ${at.day} ${months[at.month - 1]}';
}

/// The headline for the step a running job is on.
String stageTitle(MatchStage? stage) {
  switch (stage) {
    case MatchStage.preparing:
      return 'Getting your video ready';
    case MatchStage.court:
      return 'Finding the court lines';
    case MatchStage.ball:
      return 'Tracking the ball';
    case MatchStage.players:
      return 'Following the players';
    case MatchStage.finishing:
      return 'Writing your result';
    case null:
      return 'Tracking the ball';
  }
}

/// The headline over the court-lines picture.
String courtCheckHeadline(CourtCheck check) {
  switch (check.confidence) {
    case CourtConfidence.check:
      return 'Check the court lines';
    case CourtConfidence.poor:
      return 'We’re not sure about the court lines';
    case CourtConfidence.good:
    case CourtConfidence.unknown:
      return 'Court lines found';
  }
}

/// One reason from the server, as a sentence a player can act on.
String courtReasonText(String code) {
  switch (code) {
    case 'court_cut_off':
      return 'Part of the court is outside the picture, so shots near that edge may be placed wrongly.';
    case 'few_lines':
      return 'Only a few court lines were clear in the picture.';
    case 'weak_match':
      return 'The lines we drew fit the picture only partly.';
    case 'calls_off':
      return 'In and out calls are switched off for this video.';
    case 'tap_error':
      return 'The court points you tapped do not agree with each other.';
    default:
      return code;
  }
}

/// What to do about it. Worded so that nobody wastes a rally on a wrong court.
String courtCheckAdvice(CourtCheck check) => check.needsAttention
    ? 'Look closely at the yellow lines. If they are not on the real court lines, '
        'stop now and film again with the whole court in view.'
    : 'If the yellow lines sit on the real court lines, you’re set. '
        'If they do not, stop now and film again.';

/// "About 3 min left", or null when there is no estimate yet. The estimate is
/// the server's rate so far, so it is worded as rough.
String? etaText(double? seconds) {
  if (seconds == null || !seconds.isFinite) return null;
  if (seconds < 45) return 'Less than a minute left';
  final int minutes = (seconds / 60).ceil();
  return 'About $minutes min left';
}

String _statusLine(MatchRecord match) {
  switch (match.status) {
    case MatchStatus.queued:
      return 'Waiting to start…';
    case MatchStatus.running:
      final double? p = match.progress;
      final String title = stageTitle(match.stage);
      return p == null ? '$title…' : '$title… ${(p * 100).round()}%';
    case MatchStatus.failed:
      return match.errorMessage ?? 'This video could not be analysed.';
    case MatchStatus.complete:
    case MatchStatus.unrecognized:
      final MatchStats? s = match.stats;
      if (s == null) return 'Done';
      return '${s.rallies} ${s.rallies == 1 ? 'rally' : 'rallies'} · '
          '${s.shotsLanded} shots landed · In ${_percent(s.inPercent)}';
  }
}

// ------------------------------------------------------------------ the tab

/// The Matches tab: matches and rallies behind one switch at the top, so the
/// bottom bar keeps room for Profile. Both lists stay alive when switching.
class MatchesHubScreen extends StatefulWidget {
  const MatchesHubScreen({super.key});

  @override
  State<MatchesHubScreen> createState() => _MatchesHubScreenState();
}

class _MatchesHubScreenState extends State<MatchesHubScreen> {
  SessionKind _kind = SessionKind.match;

  Widget _switcher() => _KindSwitch(
        value: _kind,
        onChanged: (SessionKind k) => setState(() => _kind = k),
      );

  @override
  Widget build(BuildContext context) {
    return IndexedStack(
      index: _kind == SessionKind.rally ? 1 : 0,
      children: <Widget>[
        MatchesScreen(switcher: _switcher()),
        MatchesScreen(kind: SessionKind.rally, switcher: _switcher()),
      ],
    );
  }
}

/// The redesign's Matches | Rallies switch: a grey track with the chosen
/// side as a white raised pill.
class _KindSwitch extends StatelessWidget {
  const _KindSwitch({required this.value, required this.onChanged});

  final SessionKind value;
  final ValueChanged<SessionKind> onChanged;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    Widget side(SessionKind kind, String label) {
      final bool on = kind == value;
      return Expanded(
        child: Semantics(
          button: true,
          selected: on,
          child: Material(
            color: on ? theme.colorScheme.surfaceContainerLowest : Colors.transparent,
            elevation: on ? 1 : 0,
            borderRadius: BorderRadius.circular(10),
            child: InkWell(
              borderRadius: BorderRadius.circular(10),
              onTap: () => onChanged(kind),
              child: SizedBox(
                height: 44,
                child: Center(
                  child: Text(
                    label,
                    style: theme.textTheme.labelLarge?.copyWith(
                      fontWeight: on ? FontWeight.w700 : FontWeight.w600,
                      color: on ? theme.colorScheme.onSurface : theme.colorScheme.onSurfaceVariant,
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      );
    }

    return Container(
      padding: const EdgeInsets.all(4),
      decoration: BoxDecoration(
        color: theme.colorScheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(14),
      ),
      child: Row(
        children: <Widget>[
          side(SessionKind.match, 'Matches'),
          side(SessionKind.rally, 'Rallies'),
        ],
      ),
    );
  }
}

class MatchesScreen extends StatefulWidget {
  const MatchesScreen({super.key, this.kind = SessionKind.match, this.loader, this.switcher});

  final SessionKind kind;

  /// The Matches / Rallies switch, shown under the title when given.
  final Widget? switcher;

  /// Defaults to this tab's kind from the server.
  final Future<ApiResult<List<MatchRecord>>> Function()? loader;

  @override
  State<MatchesScreen> createState() => MatchesScreenState();
}

class MatchesScreenState extends State<MatchesScreen> {
  List<MatchRecord>? _matches;
  ApiFailure? _failure;

  /// null = automatic: open until the person has at least one session.
  bool? _guideOpen;

  @override
  void initState() {
    super.initState();
    unawaited(refresh());
  }

  Future<void> refresh() async {
    final ApiResult<List<MatchRecord>> result =
        await (widget.loader ?? () => fetchMatches(kind: widget.kind))();
    if (!mounted) return;
    setState(() {
      _matches = result.data ?? _matches;
      _failure = result.failure;
    });
  }

  Future<void> _newMatch() async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(builder: (BuildContext context) => NewMatchScreen(kind: widget.kind)),
    );
    if (mounted) await refresh();
  }

  Future<void> _open(MatchRecord match) async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (BuildContext context) => MatchResultScreen(matchId: match.matchId),
      ),
    );
    if (mounted) await refresh();
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final List<MatchRecord>? matches = _matches;
    final SessionKind kind = widget.kind;
    return SafeArea(
      child: RefreshIndicator(
        onRefresh: refresh,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(AppSpacing.lg, AppSpacing.sm, AppSpacing.lg, AppSpacing.xxl),
          children: <Widget>[
            Row(
              children: <Widget>[
                Expanded(
                  child: Text(
                    kind.tabTitle,
                    style: theme.textTheme.headlineSmall
                        ?.copyWith(fontWeight: FontWeight.w800, letterSpacing: -0.5),
                  ),
                ),
                FilledButton.icon(
                  style: FilledButton.styleFrom(minimumSize: const Size(0, 44)),
                  onPressed: _newMatch,
                  icon: const Icon(Icons.add, size: 18),
                  label: Text(kind.newLabel),
                ),
              ],
            ),
            if (widget.switcher != null) ...<Widget>[
              const SizedBox(height: AppSpacing.lg),
              widget.switcher!,
            ],
            const SizedBox(height: AppSpacing.md),
            Text.rich(
              TextSpan(children: <InlineSpan>[
                TextSpan(text: '${kind.intro} '),
                TextSpan(
                  text: kind.otherTabHint,
                  style: TextStyle(fontWeight: FontWeight.w700, color: theme.colorScheme.onSurface),
                ),
              ]),
              style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant),
            ),
            const SizedBox(height: AppSpacing.lg),
            if (_failure != null && (matches == null || matches.isEmpty)) ...<Widget>[
              _LoadErrorCard(kind: kind, failure: _failure!, onRetry: refresh),
              const SizedBox(height: AppSpacing.lg),
              _HowItWorksCard(kind: kind, open: true, compact: true, onToggle: () {}),
            ] else ...<Widget>[
              _HowItWorksCard(
                kind: kind,
                open: _guideOpen ?? (matches == null || matches.isEmpty),
                onToggle: () => setState(
                  () => _guideOpen = !(_guideOpen ?? (matches == null || matches.isEmpty)),
                ),
              ),
              const SizedBox(height: AppSpacing.lg),
              if (matches == null)
                const Center(child: Padding(padding: EdgeInsets.all(AppSpacing.xl), child: CircularProgressIndicator()))
              else if (matches.isEmpty)
                EmptyState(
                  icon: Icons.sports_tennis,
                  headline: kind.emptyHeadline,
                  supporting: kind.emptySupporting,
                  actionLabel: kind.newLabel,
                  onAction: _newMatch,
                )
              else ...<Widget>[
                Padding(
                  padding: const EdgeInsets.fromLTRB(4, 0, 4, 10),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.baseline,
                    textBaseline: TextBaseline.alphabetic,
                    children: <Widget>[
                      Expanded(child: Text(kind.listTitle, style: theme.textTheme.titleMedium)),
                      Text('Pull down to refresh',
                          style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
                    ],
                  ),
                ),
                AppCard(
                  padding: EdgeInsets.zero,
                  child: Column(
                    children: <Widget>[
                      for (int i = 0; i < matches.length; i++) ...<Widget>[
                        if (i > 0) const Divider(height: 1),
                        _MatchRow(match: matches[i], onTap: () => _open(matches[i])),
                      ],
                    ],
                  ),
                ),
              ],
            ],
          ],
        ),
      ),
    );
  }
}

/// One session in the list: an icon tile, its name and status, a chevron.
class _MatchRow extends StatelessWidget {
  const _MatchRow({required this.match, required this.onTap});

  final MatchRecord match;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final bool done = match.status.isFinished && match.status != MatchStatus.failed;
    final bool failed = match.status == MatchStatus.failed;
    final Color tileInk = failed
        ? theme.colorScheme.error
        : done
            ? theme.colorScheme.primary
            : theme.colorScheme.onSurfaceVariant;
    final Color tile = failed
        ? theme.colorScheme.errorContainer
        : done
            ? theme.colorScheme.primaryContainer
            : theme.colorScheme.surfaceContainerHigh;
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 14),
        child: Row(
          children: <Widget>[
            Container(
              width: 44,
              height: 44,
              decoration: BoxDecoration(color: tile, borderRadius: BorderRadius.circular(12)),
              child: Icon(
                failed ? Icons.error_outline : (done ? Icons.grid_view : Icons.hourglass_top),
                color: tileInk,
                size: 22,
              ),
            ),
            const SizedBox(width: AppSpacing.md),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(_matchTitle(match), style: theme.textTheme.bodyLarge?.copyWith(fontWeight: FontWeight.w600)),
                  Text(
                    done || failed ? _statusLine(match) : '${_statusLine(match)} You can leave this screen.',
                    style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                  ),
                ],
              ),
            ),
            Icon(Icons.chevron_right, color: theme.colorScheme.onSurfaceVariant),
          ],
        ),
      ),
    );
  }
}

/// The redesign's load-error card: what probably happened, that nothing is
/// lost, a way to retry, and the technical detail kept small.
class _LoadErrorCard extends StatelessWidget {
  const _LoadErrorCard({required this.kind, required this.failure, required this.onRetry});

  final SessionKind kind;
  final ApiFailure failure;
  final Future<void> Function() onRetry;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final String what = kind.tabTitle.toLowerCase();
    return AppCard(
      padding: const EdgeInsets.fromLTRB(22, 32, 22, 24),
      child: Column(
        children: <Widget>[
          Container(
            width: 72,
            height: 72,
            decoration: const BoxDecoration(color: Color(0xFFFFF1E6), shape: BoxShape.circle),
            child: const Icon(Icons.wifi_off, size: 32, color: Color(0xFFB4500B)),
          ),
          const SizedBox(height: AppSpacing.md),
          Text(
            '${kind.tabTitle} could not be loaded',
            textAlign: TextAlign.center,
            style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: AppSpacing.sm),
          Text(
            'This is usually a weak connection, and your $what are safe. '
            'Check your connection and try again.',
            textAlign: TextAlign.center,
            style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant),
          ),
          const SizedBox(height: AppSpacing.md),
          FilledButton(
            style: FilledButton.styleFrom(minimumSize: const Size(0, 48), padding: const EdgeInsets.symmetric(horizontal: 28)),
            onPressed: () => unawaited(onRetry()),
            child: const Text('Try again'),
          ),
          const SizedBox(height: AppSpacing.md),
          Text(
            'Still stuck? Sign out and back in from Profile. Details: ${failure.message}',
            textAlign: TextAlign.center,
            style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
          ),
        ],
      ),
    );
  }
}

/// The three-step guide at the top of each tab, as in the redesign. Open
/// for a first-time visitor, hidden (one tap to bring back) once there is
/// something to look at. [compact] is the one-paragraph version shown under
/// a load error.
class _HowItWorksCard extends StatelessWidget {
  const _HowItWorksCard({
    required this.kind,
    required this.open,
    required this.onToggle,
    this.compact = false,
  });

  final SessionKind kind;
  final bool open;
  final VoidCallback onToggle;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final List<(String, String)> steps = kind.guideSteps;
    final Widget icon = Icon(Icons.help_outline, size: 20, color: theme.colorScheme.primary);
    if (compact) {
      return AppCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(children: <Widget>[
              icon,
              const SizedBox(width: AppSpacing.sm),
              Expanded(
                child: Text('While you wait: ${kind.guideTitle.toLowerCase()}',
                    style: theme.textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w700)),
              ),
            ]),
            const SizedBox(height: AppSpacing.sm),
            for (int i = 0; i < steps.length; i++)
              Text.rich(
                TextSpan(children: <InlineSpan>[
                  TextSpan(text: '${i + 1} ${steps[i].$1} ', style: const TextStyle(fontWeight: FontWeight.w700)),
                  TextSpan(text: steps[i].$2),
                ]),
                style: theme.textTheme.bodyMedium,
              ),
          ],
        ),
      );
    }
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              icon,
              const SizedBox(width: AppSpacing.sm),
              Expanded(
                child: Text(kind.guideTitle,
                    style: theme.textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w700, fontSize: 16)),
              ),
              TextButton(
                style: TextButton.styleFrom(minimumSize: const Size(44, 44)),
                onPressed: onToggle,
                child: Text(open ? 'Hide' : 'Show'),
              ),
            ],
          ),
          if (open) ...<Widget>[
            const SizedBox(height: AppSpacing.xs),
            for (int i = 0; i < steps.length; i++)
              Padding(
                padding: const EdgeInsets.only(bottom: AppSpacing.md),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Container(
                      width: 28,
                      height: 28,
                      alignment: Alignment.center,
                      decoration: BoxDecoration(color: theme.colorScheme.primaryContainer, shape: BoxShape.circle),
                      child: Text(
                        '${i + 1}',
                        style: theme.textTheme.labelLarge?.copyWith(
                          color: theme.colorScheme.primary,
                          fontWeight: FontWeight.w800,
                        ),
                      ),
                    ),
                    const SizedBox(width: AppSpacing.md),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: <Widget>[
                          Text(steps[i].$1, style: theme.textTheme.bodyMedium?.copyWith(fontWeight: FontWeight.w700)),
                          Text(steps[i].$2,
                              style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
          ],
        ],
      ),
    );
  }
}

// ------------------------------------------------------------ new match

class NewMatchScreen extends StatefulWidget {
  const NewMatchScreen({super.key, this.kind = SessionKind.match});

  final SessionKind kind;

  @override
  State<NewMatchScreen> createState() => _NewMatchScreenState();
}

class _NewMatchScreenState extends State<NewMatchScreen> {
  String _format = 'singles';
  final TextEditingController _title = TextEditingController();
  String? _busy;
  String? _error;

  @override
  void dispose() {
    _title.dispose();
    super.dispose();
  }

  Future<void> _chooseAndSend() async {
    setState(() {
      _error = null;
      _busy = 'Opening your videos…';
    });
    final VideoPickOutcome outcome =
        await pickVideoClip(maxSeconds: kMaxMatchSeconds, maxBytes: kMaxMatchUploadBytes);
    if (!mounted) return;
    switch (outcome) {
      case VideoPickCancelled():
        setState(() => _busy = null);
        return;
      case VideoPickRejected(:final String message):
        setState(() {
          _busy = null;
          _error = message;
        });
        return;
      case VideoPickSucceeded(:final VideoClip clip):
        await _send(clip);
    }
  }

  Future<void> _send(VideoClip clip) async {
    setState(() => _busy = 'Uploading…');
    final ApiResult<UploadTicket> ticket = await requestUploadTicket(
      contentType: clip.contentType,
      sizeBytes: clip.sizeBytes,
      durationS: clip.durationSeconds,
      purpose: 'match',
    );
    if (!mounted) return;
    if (!ticket.isOk) return _fail(ticket.failure!.message);
    final UploadTicket t = ticket.data!;
    final int count = t.parts.length;
    if (count == 0 || t.partBytes == null) {
      return _fail('The server did not accept a match upload. Update the app and try again.');
    }
    final List<(int, int)> ranges = uploadPartRanges(clip.sizeBytes, t.partBytes!, count);
    for (int i = 0; i < count; i++) {
      setState(() => _busy = count == 1 ? 'Uploading…' : 'Uploading part ${i + 1} of $count…');
      final (int start, int end) = ranges[i];
      final ApiFailure? uploaded = await uploadVideoToSignedUrl(
        storagePath: t.parts[i].storagePath,
        uploadToken: t.parts[i].uploadToken,
        bytes: Uint8List.sublistView(clip.bytes, start, end),
        contentType: clip.contentType,
      );
      if (!mounted) return;
      if (uploaded != null) return _fail(uploaded.message);
    }
    setState(() => _busy = 'Starting the analysis…');
    final ApiResult<MatchRecord> created = await createMatch(
      storagePath: ticket.data!.storagePath,
      courtFormat: _format,
      title: _title.text,
      partCount: count,
      kind: widget.kind,
    );
    if (!mounted) return;
    if (!created.isOk) return _fail(created.failure!.message);
    await Navigator.of(context).pushReplacement(
      MaterialPageRoute<void>(
        builder: (BuildContext context) => MatchResultScreen(matchId: created.data!.matchId),
      ),
    );
  }

  void _fail(String message) => setState(() {
        _busy = null;
        _error = message;
      });

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final bool busy = _busy != null;
    return Scaffold(
      appBar: AppBar(title: Text(widget.kind.newLabel)),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(AppSpacing.lg),
          children: <Widget>[
            AppCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text('Filming tips', style: theme.textTheme.titleMedium),
                  const SizedBox(height: AppSpacing.sm),
                  for (final String tip in <String>[
                    'Stand the phone behind a baseline with the whole court in view. Higher is better, but anywhere works.',
                    'No tripod needed: lean it on the fence, a bag or a bottle. It just has to stay still.',
                    'Record with your normal camera app at 720p, then choose the video here.',
                    'Up to 5 minutes (450 MB). The video is deleted as soon as it has been analysed.',
                    'About half a minute after you upload, we show the court lines we found. '
                        'If they are not on the real lines you can stop right away and film again.',
                    if (widget.kind == SessionKind.rally)
                      'Keep the ball going: the more bounces we see, the more the coaching can say.',
                  ])
                    Padding(
                      padding: const EdgeInsets.only(bottom: AppSpacing.xs),
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: <Widget>[
                          const Text('•  '),
                          Expanded(child: Text(tip, style: theme.textTheme.bodyMedium)),
                        ],
                      ),
                    ),
                ],
              ),
            ),
            const SizedBox(height: AppSpacing.lg),
            Text('Court', style: theme.textTheme.titleSmall),
            const SizedBox(height: AppSpacing.sm),
            SegmentedButton<String>(
              segments: const <ButtonSegment<String>>[
                ButtonSegment<String>(value: 'singles', label: Text('Singles')),
                ButtonSegment<String>(value: 'doubles', label: Text('Doubles')),
              ],
              selected: <String>{_format},
              onSelectionChanged: busy ? null : (Set<String> s) => setState(() => _format = s.first),
            ),
            const SizedBox(height: AppSpacing.lg),
            TextField(
              controller: _title,
              enabled: !busy,
              maxLength: 80,
              decoration: const InputDecoration(labelText: 'Name (optional)', hintText: 'e.g. Sunday hit with Sam'),
            ),
            const SizedBox(height: AppSpacing.md),
            if (_error != null)
              Padding(
                padding: const EdgeInsets.only(bottom: AppSpacing.md),
                child: Text(_error!, style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.error)),
              ),
            FilledButton.icon(
              onPressed: busy ? null : _chooseAndSend,
              icon: busy
                  ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                  : const Icon(Icons.video_library_outlined),
              label: Text(_busy ?? 'Choose a video'),
            ),
          ],
        ),
      ),
    );
  }
}

// --------------------------------------------------------------- result

class MatchResultScreen extends StatefulWidget {
  const MatchResultScreen({
    super.key,
    required this.matchId,
    this.loader = fetchMatch,
    this.pictureLoader = fetchMatchCourtPicture,
    this.deleter = deleteMatch,
  });

  final String matchId;
  final Future<ApiResult<MatchRecord>> Function(String matchId) loader;
  final Future<ApiResult<Uint8List>> Function(String matchId) pictureLoader;

  /// Deletes a finished match, or STOPS a running one.
  final Future<ApiResult<void>> Function(String matchId) deleter;

  @override
  State<MatchResultScreen> createState() => _MatchResultScreenState();
}

class _MatchResultScreenState extends State<MatchResultScreen> {
  MatchRecord? _match;
  ApiFailure? _failure;
  Timer? _timer;
  Future<ApiResult<Uint8List>>? _picture;

  /// The player said the court lines look right.
  bool _linesConfirmed = false;

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    final ApiResult<MatchRecord> result = await widget.loader(widget.matchId);
    if (!mounted) return;
    setState(() {
      _match = result.data ?? _match;
      _failure = result.failure;
      if (_match != null && _match!.hasCourtPicture && _picture == null) {
        _picture = widget.pictureLoader(widget.matchId);
      }
    });
    final MatchRecord? match = _match;
    if (match == null || !match.status.isFinished) {
      _timer = Timer(kMatchPollInterval, () => unawaited(_load()));
    }
  }

  Future<void> _delete() async {
    final bool? sure = await showDialog<bool>(
      context: context,
      builder: (BuildContext context) => AlertDialog(
        title: Text('Delete this ${(_match?.kind ?? SessionKind.match).noun.toLowerCase()}?'),
        content: const Text('Its shot map, stats and coaching will be gone for good.'),
        actions: <Widget>[
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Delete')),
        ],
      ),
    );
    if (sure != true || !mounted) return;
    final ApiResult<void> deleted = await widget.deleter(widget.matchId);
    if (!mounted) return;
    if (deleted.isOk) {
      Navigator.of(context).pop();
    } else {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(deleted.failure!.message)));
    }
  }

  /// Stop a running analysis: the video is deleted and nothing is kept.
  Future<void> _stop() async {
    final bool? sure = await showDialog<bool>(
      context: context,
      builder: (BuildContext context) => AlertDialog(
        title: const Text('Stop this analysis?'),
        content: const Text('The video is deleted and nothing from it is kept. You can film and upload again.'),
        actions: <Widget>[
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Keep going')),
          FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Stop')),
        ],
      ),
    );
    if (sure != true || !mounted) return;
    final ApiResult<void> stopped = await widget.deleter(widget.matchId);
    if (!mounted) return;
    if (stopped.isOk) {
      Navigator.of(context).pop();
    } else {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(stopped.failure!.message)));
    }
  }

  @override
  Widget build(BuildContext context) {
    final MatchRecord? match = _match;
    return Scaffold(
      appBar: AppBar(
        title: Text(match == null ? '' : _matchTitle(match)),
        actions: <Widget>[
          if (match != null && match.status.isFinished)
            IconButton(tooltip: 'Delete', onPressed: _delete, icon: const Icon(Icons.delete_outline)),
          if (match != null && !match.status.isFinished)
            TextButton(onPressed: _stop, child: const Text('Stop')),
        ],
      ),
      body: SafeArea(child: _body(context, match)),
    );
  }

  Widget _body(BuildContext context, MatchRecord? match) {
    if (match == null) {
      if (_failure != null) {
        return EmptyState(
          icon: Icons.cloud_off,
          headline: 'This could not be loaded',
          supporting: _failure!.message,
          actionLabel: 'Try again',
          onAction: () => unawaited(_load()),
        );
      }
      return const Center(child: CircularProgressIndicator());
    }
    if (!match.status.isFinished) {
      final CourtCheck? check = match.courtCheck;
      return ListView(
        padding: const EdgeInsets.all(AppSpacing.xl),
        children: <Widget>[
          if (check != null && !_linesConfirmed) ...<Widget>[
            CourtCheckCard(
              check: check,
              picture: _picture,
              onConfirm: () => setState(() => _linesConfirmed = true),
              onStop: _stop,
            ),
            const SizedBox(height: AppSpacing.xl),
          ] else if (check != null)
            Padding(
              padding: const EdgeInsets.only(bottom: AppSpacing.lg),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: <Widget>[
                  Icon(Icons.check_circle, size: 18, color: Theme.of(context).colorScheme.primary),
                  const SizedBox(width: AppSpacing.xs),
                  const Text('You confirmed the court lines'),
                ],
              ),
            ),
          MatchProgressPanel(match: match),
        ],
      );
    }
    if (match.status == MatchStatus.failed) {
      return EmptyState(
        icon: Icons.error_outline,
        headline: 'This video could not be analysed',
        supporting: match.errorMessage ?? 'Something went wrong. Try another video.',
      );
    }
    final CourtAnalysis? result = match.result;
    if (result == null) return const Center(child: Text('No result.'));
    return _MatchResultBody(match: match, result: result, picture: _picture);
  }
}

/// The court lines found in the video, drawn on the first frame, with how far
/// they can be trusted: shown within seconds of the court being found, so a
/// rally with a wrong court is stopped before it is wasted.
class CourtCheckCard extends StatelessWidget {
  const CourtCheckCard({super.key, required this.check, required this.picture, this.onConfirm, this.onStop});

  final CourtCheck check;
  final Future<ApiResult<Uint8List>>? picture;

  /// "Lines look right" and "Stop and film again"; both null for a read-only card.
  final VoidCallback? onConfirm;
  final VoidCallback? onStop;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final IconData icon = check.needsAttention ? Icons.visibility : Icons.check_circle_outline;
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              Icon(
                check.confidence == CourtConfidence.poor ? Icons.warning_amber_rounded : icon,
                color: check.confidence == CourtConfidence.poor
                    ? theme.colorScheme.error
                    : theme.colorScheme.primary,
              ),
              const SizedBox(width: AppSpacing.sm),
              Expanded(child: Text(courtCheckHeadline(check), style: theme.textTheme.titleMedium)),
            ],
          ),
          const SizedBox(height: AppSpacing.md),
          if (picture != null)
            FutureBuilder<ApiResult<Uint8List>>(
              future: picture,
              builder: (BuildContext context, AsyncSnapshot<ApiResult<Uint8List>> snapshot) {
                final Uint8List? bytes = snapshot.data?.data;
                if (bytes == null) {
                  return snapshot.connectionState == ConnectionState.done
                      ? const Text('The court picture could not be loaded.')
                      : const SizedBox(height: 120, child: Center(child: CircularProgressIndicator()));
                }
                return ClipRRect(
                  borderRadius: BorderRadius.circular(AppSpacing.innerRadius),
                  child: Image.memory(bytes, fit: BoxFit.contain),
                );
              },
            ),
          for (final String reason in check.reasons)
            Padding(
              padding: const EdgeInsets.only(top: AppSpacing.sm),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  const Text('•  '),
                  Expanded(child: Text(courtReasonText(reason), style: theme.textTheme.bodyMedium)),
                ],
              ),
            ),
          const SizedBox(height: AppSpacing.md),
          Text(courtCheckAdvice(check), style: theme.textTheme.bodyMedium),
          if (onConfirm != null || onStop != null) ...<Widget>[
            const SizedBox(height: AppSpacing.md),
            Wrap(
              spacing: AppSpacing.sm,
              runSpacing: AppSpacing.sm,
              children: <Widget>[
                if (onConfirm != null) FilledButton(onPressed: onConfirm, child: const Text('Lines look right')),
                if (onStop != null) OutlinedButton(onPressed: onStop, child: const Text('Stop and film again')),
              ],
            ),
          ],
        ],
      ),
    );
  }
}

/// A running job: the step it is on, a bar that moves with the real frames
/// processed, the percentage and a rough time left.
class MatchProgressPanel extends StatelessWidget {
  const MatchProgressPanel({super.key, required this.match});

  final MatchRecord match;

  static const List<MatchStage> _steps = <MatchStage>[
    MatchStage.preparing,
    MatchStage.court,
    MatchStage.ball,
    MatchStage.players,
    MatchStage.finishing,
  ];

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final bool running = match.status == MatchStatus.running;
    final double? progress = running ? match.progress : null;
    final MatchStage? current = running ? match.stage : null;
    final int currentIndex = current == null ? -1 : _steps.indexOf(current);
    final String? left = running ? etaText(match.etaS) : null;
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        const Icon(Icons.sports_tennis, size: 48),
        const SizedBox(height: AppSpacing.lg),
        Text(
          match.status == MatchStatus.queued ? 'Waiting to start' : stageTitle(current),
          style: theme.textTheme.titleLarge,
          textAlign: TextAlign.center,
        ),
        const SizedBox(height: AppSpacing.lg),
        ClipRRect(
          borderRadius: BorderRadius.circular(999),
          child: LinearProgressIndicator(
            minHeight: 10,
            value: progress,
            semanticsLabel: 'Analysis progress',
          ),
        ),
        const SizedBox(height: AppSpacing.sm),
        Row(
          children: <Widget>[
            Text(progress == null ? '' : '${(progress * 100).round()}%', style: theme.textTheme.titleMedium),
            const Spacer(),
            if (left != null) Text(left, style: theme.textTheme.bodyMedium),
          ],
        ),
        const SizedBox(height: AppSpacing.lg),
        for (int i = 0; i < _steps.length; i++)
          Padding(
            padding: const EdgeInsets.only(bottom: AppSpacing.xs),
            child: Row(
              children: <Widget>[
                Icon(
                  i < currentIndex
                      ? Icons.check_circle
                      : i == currentIndex
                          ? Icons.radio_button_checked
                          : Icons.radio_button_unchecked,
                  size: 20,
                  color: i <= currentIndex ? theme.colorScheme.primary : theme.colorScheme.outline,
                ),
                const SizedBox(width: AppSpacing.sm),
                Expanded(
                  child: Text(
                    stageTitle(_steps[i]),
                    style: theme.textTheme.bodyMedium?.copyWith(
                      fontWeight: i == currentIndex ? FontWeight.w600 : FontWeight.w400,
                      color: i > currentIndex ? theme.colorScheme.onSurfaceVariant : null,
                    ),
                  ),
                ),
              ],
            ),
          ),
        const SizedBox(height: AppSpacing.lg),
        Text(
          'You can leave this screen: it keeps going and shows up in ${match.kind.tabTitle}.',
          style: theme.textTheme.bodySmall,
          textAlign: TextAlign.center,
        ),
      ],
    );
  }
}

class _MatchResultBody extends StatefulWidget {
  const _MatchResultBody({required this.match, required this.result, required this.picture});

  final MatchRecord match;
  final CourtAnalysis result;
  final Future<ApiResult<Uint8List>>? picture;

  @override
  State<_MatchResultBody> createState() => _MatchResultBodyState();
}

class _MatchResultBodyState extends State<_MatchResultBody> {
  // The page is a plain Column in a scroll view (not a lazy list) so every
  // section is built and these keys can always be scrolled to.
  final GlobalKey _shotMapKey = GlobalKey();
  final GlobalKey _ralliesKey = GlobalKey();
  final GlobalKey _lineCallsKey = GlobalKey();
  final GlobalKey _courtLinesKey = GlobalKey();
  final GlobalKey _coachingKey = GlobalKey();

  /// The "How to read this page" card, until "Got it".
  bool _guideOpen = true;

  void _jumpTo(GlobalKey key) {
    final BuildContext? target = key.currentContext;
    if (target == null) return;
    Scrollable.ensureVisible(
      target,
      duration: const Duration(milliseconds: 350),
      curve: Curves.easeOutCubic,
      alignment: 0.02,
    );
  }

  @override
  Widget build(BuildContext context) {
    final MatchRecord match = widget.match;
    final CourtAnalysis result = widget.result;
    final Future<ApiResult<Uint8List>>? picture = widget.picture;
    final ThemeData theme = Theme.of(context);
    final MatchStats? stats = match.stats;
    final bool cameraUnverified =
        result.landings.any((ShotLanding l) => l.flags.contains(kCameraUnverifiedFlag));
    final List<ShotLanding> called = result.landings.where((ShotLanding l) => l.lineCall != null).toList()
      ..sort((ShotLanding a, ShotLanding b) =>
          a.lineCall!.signedMarginM.abs().compareTo(b.lineCall!.signedMarginM.abs()));

    final bool tracked = result.status != CourtSessionStatus.refused;
    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(AppSpacing.lg, AppSpacing.sm, AppSpacing.lg, AppSpacing.xxl),
      child: Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        if (tracked && _guideOpen) ...<Widget>[
          _ReadThisPageCard(
            onDismiss: () => setState(() => _guideOpen = false),
            jumps: <(String, VoidCallback)>[
              ('Shot map', () => _jumpTo(_shotMapKey)),
              ('Rallies', () => _jumpTo(_ralliesKey)),
              if (match.feedback != null) ('Coaching', () => _jumpTo(_coachingKey)),
              if (called.isNotEmpty) ('Line calls', () => _jumpTo(_lineCallsKey)),
              if (picture != null) ('Court lines', () => _jumpTo(_courtLinesKey)),
            ],
          ),
          const SizedBox(height: AppSpacing.lg),
        ],
        if (result.status == CourtSessionStatus.refused)
          AppCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text('We couldn’t track this one', style: theme.textTheme.titleMedium),
                const SizedBox(height: AppSpacing.sm),
                Text(
                  (result.refusalReason ?? CourtRefusalReason.unrecognized).message,
                  style: theme.textTheme.bodyMedium,
                ),
              ],
            ),
          )
        else ...<Widget>[
          if (match.courtCheck != null && match.courtCheck!.needsAttention) ...<Widget>[
            CourtCheckCard(check: match.courtCheck!, picture: null),
            const SizedBox(height: AppSpacing.xl),
          ],
          if (stats != null) ...<Widget>[
            _TwoTiles(stats: stats),
            const SizedBox(height: AppSpacing.sm),
            const _Caption('Shots in: of the shots we could call, how many landed inside the lines.'),
          ],
          if (cameraUnverified || result.status == CourtSessionStatus.partial) ...<Widget>[
            const SizedBox(height: AppSpacing.md),
            AppCard(
              child: Text(
                cameraUnverified
                    ? 'We couldn’t confirm the phone stayed still, so in/out calls are '
                        'switched off for this video. Where balls landed is still shown.'
                    : 'Only part of this video could be used: the phone was knocked part-way through.',
                style: theme.textTheme.bodyMedium,
              ),
            ),
          ],
          const SizedBox(height: AppSpacing.xl),
          _Title(key: _shotMapKey, text: 'Shot map'),
          const _Caption('Each dot is a place a ball bounced, seen from above. Use the filter chips to '
              'look at your shots, your opponent’s, serves or a type of line call.'),
          const SizedBox(height: AppSpacing.sm),
          if (result.landings.isEmpty)
            Text('No bounces were found in this video.', style: theme.textTheme.bodyMedium)
          else
            AppCard(child: CourtMap(landings: result.landings)),
          const SizedBox(height: AppSpacing.xl),
          _Title(key: _ralliesKey, text: 'Rallies'),
          const _Caption('A rally is a run of shots until the point ends. Each bar is one rally, and '
              'taller means more shots.'),
          const SizedBox(height: AppSpacing.sm),
          if (stats != null) ...<Widget>[
            _ThreeTiles(stats: stats),
            const SizedBox(height: AppSpacing.md),
          ],
          AppCard(child: RallyTimeline(rallies: result.rallies)),
          if (match.feedback != null) ...<Widget>[
            const SizedBox(height: AppSpacing.xl),
            _Title(key: _coachingKey, text: 'Coaching'),
            CoachingSummaryCard(feedback: match.feedback!),
          ],
          if (called.isNotEmpty) ...<Widget>[
            const SizedBox(height: AppSpacing.xl),
            _Title(key: _lineCallsKey, text: 'Closest line calls'),
            const _Caption('The balls that landed nearest a line, closest first. The distance is how '
                'far inside or beyond the line the ball came down.'),
            const SizedBox(height: AppSpacing.sm),
            for (final ShotLanding landing in called.take(10))
              Padding(
                padding: const EdgeInsets.only(bottom: AppSpacing.md),
                child: LineCallCard(landing: landing),
              ),
          ],
        ],
        if (picture != null) ...<Widget>[
          const SizedBox(height: AppSpacing.xl),
          _Title(key: _courtLinesKey, text: 'Court lines we used'),
          const SizedBox(height: AppSpacing.sm),
          FutureBuilder<ApiResult<Uint8List>>(
            future: picture,
            builder: (BuildContext context, AsyncSnapshot<ApiResult<Uint8List>> snapshot) {
              final Uint8List? bytes = snapshot.data?.data;
              if (bytes == null) {
                return snapshot.connectionState == ConnectionState.done
                    ? const Text('The court picture could not be loaded.')
                    : const SizedBox(height: 120, child: Center(child: CircularProgressIndicator()));
              }
              return ClipRRect(
                borderRadius: BorderRadius.circular(AppSpacing.innerRadius),
                child: Image.memory(bytes, fit: BoxFit.contain),
              );
            },
          ),
          const SizedBox(height: AppSpacing.sm),
          Text(
            match.courtFoundAutomatically
                ? 'Found automatically. If these lines are not on the real court lines, '
                    'the positions and calls are off too: film with more of the court in view.'
                : 'From your court taps.',
            style: theme.textTheme.bodySmall,
          ),
        ],
        const SizedBox(height: AppSpacing.lg),
        Text(
          'Everything here is an estimate from one camera.',
          style: theme.textTheme.bodySmall,
        ),
      ],
      ),
    );
  }
}

/// A rally's overall coaching: the summary, what is working, what to work on.
/// AI only words the measured numbers; it never measures anything.
class CoachingSummaryCard extends StatelessWidget {
  const CoachingSummaryCard({super.key, required this.feedback});

  final CoachingFeedback feedback;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final bool byAi = feedback.source != FeedbackSource.template;
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              Icon(Icons.auto_awesome, size: 20, color: theme.colorScheme.primary),
              const SizedBox(width: AppSpacing.sm),
              Text('Coaching', style: theme.textTheme.titleMedium),
            ],
          ),
          const SizedBox(height: AppSpacing.sm),
          Text(feedback.summary, style: theme.textTheme.bodyLarge),
          if (feedback.strengths.isNotEmpty) ...<Widget>[
            const SizedBox(height: AppSpacing.md),
            Text('What’s working', style: theme.textTheme.titleSmall),
            const SizedBox(height: AppSpacing.xs),
            for (final String strength in feedback.strengths)
              Padding(
                padding: const EdgeInsets.only(bottom: AppSpacing.xs),
                child: Text('•  $strength', style: theme.textTheme.bodyMedium),
              ),
          ],
          if (feedback.improvements.isNotEmpty) ...<Widget>[
            const SizedBox(height: AppSpacing.md),
            Text('What to work on', style: theme.textTheme.titleSmall),
            for (final Improvement item in feedback.improvements) ...<Widget>[
              const SizedBox(height: AppSpacing.sm),
              Text(item.title, style: theme.textTheme.bodyLarge?.copyWith(fontWeight: FontWeight.w600)),
              const SizedBox(height: AppSpacing.xs),
              Text(item.why, style: theme.textTheme.bodyMedium),
              if (item.cue.isNotEmpty) Text('Cue: ${item.cue}', style: theme.textTheme.bodyMedium),
              if (item.drill.isNotEmpty) Text('Drill: ${item.drill}', style: theme.textTheme.bodyMedium),
            ],
          ],
          const SizedBox(height: AppSpacing.md),
          Text(
            byAi
                ? 'Written by AI from the numbers below. The AI never measures anything itself.'
                : 'A short summary only: film a longer rally for fuller coaching.',
            style: theme.textTheme.bodySmall,
          ),
        ],
      ),
    );
  }
}

/// The redesign's "How to read this page" card: a green-tinted note with
/// jump buttons, dismissed with "Got it".
class _ReadThisPageCard extends StatelessWidget {
  const _ReadThisPageCard({required this.onDismiss, required this.jumps});

  final VoidCallback onDismiss;
  final List<(String, VoidCallback)> jumps;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Container(
      padding: const EdgeInsets.fromLTRB(18, 8, 8, 18),
      decoration: BoxDecoration(
        color: theme.colorScheme.primaryContainer,
        borderRadius: BorderRadius.circular(AppSpacing.cardRadius),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              Expanded(
                child: Text(
                  'How to read this page',
                  style: theme.textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w700, fontSize: 15),
                ),
              ),
              TextButton(
                style: TextButton.styleFrom(minimumSize: const Size(44, 44)),
                onPressed: onDismiss,
                child: const Text('Got it'),
              ),
            ],
          ),
          Padding(
            padding: const EdgeInsets.only(right: 10),
            child: Text(
              'Everything here comes from your video. Start with the numbers, look at the shot '
              'map, then check your rallies. Tap a section below to jump to it.',
              style: theme.textTheme.bodyMedium,
            ),
          ),
          const SizedBox(height: AppSpacing.md),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: <Widget>[
              for (final (String label, VoidCallback go) in jumps)
                Material(
                  color: theme.colorScheme.surfaceContainerLowest,
                  shape: const StadiumBorder(),
                  child: InkWell(
                    customBorder: const StadiumBorder(),
                    onTap: go,
                    child: ConstrainedBox(
                      constraints: const BoxConstraints(minHeight: 44),
                      child: Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                        child: Text(
                          label,
                          style: theme.textTheme.labelLarge?.copyWith(fontWeight: FontWeight.w600),
                        ),
                      ),
                    ),
                  ),
                ),
            ],
          ),
        ],
      ),
    );
  }
}

/// A section title, as the redesign sets them.
class _Title extends StatelessWidget {
  const _Title({super.key, required this.text});

  final String text;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(4, 0, 4, 4),
        child: Text(
          text,
          style: Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
        ),
      );
}

/// The small grey explanation under a section title.
class _Caption extends StatelessWidget {
  const _Caption(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 4),
      child: Text(
        text,
        style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
      ),
    );
  }
}

/// One stat tile: an upper-case label, a big number, an optional note.
class _Tile extends StatelessWidget {
  const _Tile({required this.label, required this.value, this.note});

  final String label;
  final String value;
  final String? note;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return AppCard(
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            label.toUpperCase(),
            style: theme.textTheme.labelSmall?.copyWith(
              color: theme.colorScheme.onSurfaceVariant,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            value,
            style: theme.textTheme.headlineSmall?.copyWith(
              fontWeight: FontWeight.w800,
              fontFeatures: kTabularFigures,
            ),
          ),
          if (note != null)
            Text(
              note!,
              style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
            ),
        ],
      ),
    );
  }
}

/// "Shots in" and "Deep shots", side by side.
class _TwoTiles extends StatelessWidget {
  const _TwoTiles({required this.stats});

  final MatchStats stats;

  @override
  Widget build(BuildContext context) {
    final int called = stats.inCount + stats.outCount;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Expanded(
          child: _Tile(
            label: 'Shots in',
            value: _percent(stats.inPercent),
            note: called == 0 ? 'none called' : '${stats.inCount} of $called',
          ),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: _Tile(
            label: 'Deep shots',
            value: _percent(stats.deepPercent),
            note: 'landed close to the baseline',
          ),
        ),
      ],
    );
  }
}

/// Rallies, longest and average, in a row of three.
class _ThreeTiles extends StatelessWidget {
  const _ThreeTiles({required this.stats});

  final MatchStats stats;

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Expanded(child: _Tile(label: 'Rallies', value: '${stats.rallies}')),
        const SizedBox(width: 10),
        Expanded(
          child: _Tile(
            label: 'Longest',
            value: stats.longestRallyHits == null ? '—' : '${stats.longestRallyHits}',
          ),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: _Tile(
            label: 'Average',
            value: stats.averageRallyHits == null ? '—' : stats.averageRallyHits!.toStringAsFixed(1),
          ),
        ),
      ],
    );
  }
}
