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
import '../widgets/section_header.dart';

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
  String get intro => this == SessionKind.rally
      ? 'Film yourself rallying and get overall coaching on consistency, depth and '
          'placement. AI writes it from what we measured.'
      : 'Rally or play a set, then see where every ball landed, your rallies '
          'and the line calls.';
  String get emptySupporting => this == SessionKind.rally
      ? 'Film a rally from behind the baseline to get your first coaching summary.'
      : 'Film a rally from behind the baseline and the court map fills in.';
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

String _statusLine(MatchRecord match) {
  switch (match.status) {
    case MatchStatus.queued:
      return 'Waiting to start…';
    case MatchStatus.running:
      final double? p = match.progress;
      return p == null ? 'Tracking the ball…' : 'Tracking the ball… ${(p * 100).round()}%';
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

class MatchesScreen extends StatefulWidget {
  const MatchesScreen({super.key, this.kind = SessionKind.match, this.loader});

  final SessionKind kind;

  /// Defaults to this tab's kind from the server.
  final Future<ApiResult<List<MatchRecord>>> Function()? loader;

  @override
  State<MatchesScreen> createState() => MatchesScreenState();
}

class MatchesScreenState extends State<MatchesScreen> {
  List<MatchRecord>? _matches;
  ApiFailure? _failure;

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
                Expanded(child: Text(kind.tabTitle, style: theme.textTheme.headlineSmall)),
                FilledButton.icon(
                  onPressed: _newMatch,
                  icon: const Icon(Icons.add),
                  label: Text(kind.newLabel),
                ),
              ],
            ),
            const SizedBox(height: AppSpacing.sm),
            Text(kind.intro, style: theme.textTheme.bodyMedium),
            const SizedBox(height: AppSpacing.lg),
            if (matches == null && _failure == null)
              const Center(child: Padding(padding: EdgeInsets.all(AppSpacing.xl), child: CircularProgressIndicator()))
            else if (matches == null || matches.isEmpty)
              EmptyState(
                icon: Icons.sports_tennis,
                headline: _failure != null ? '${kind.tabTitle} could not be loaded' : kind.emptyHeadline,
                supporting: _failure?.message ?? kind.emptySupporting,
                actionLabel: _failure != null ? 'Try again' : kind.newLabel,
                onAction: _failure != null ? refresh : _newMatch,
              )
            else
              for (final MatchRecord match in matches)
                Padding(
                  padding: const EdgeInsets.only(bottom: AppSpacing.md),
                  child: AppCard(
                    onTap: () => _open(match),
                    child: Row(
                      children: <Widget>[
                        Icon(
                          match.status == MatchStatus.failed
                              ? Icons.error_outline
                              : match.status.isFinished
                                  ? Icons.sports_tennis
                                  : Icons.hourglass_top,
                        ),
                        const SizedBox(width: AppSpacing.md),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: <Widget>[
                              Text(_matchTitle(match), style: theme.textTheme.titleMedium),
                              const SizedBox(height: AppSpacing.xs),
                              Text(_statusLine(match), style: theme.textTheme.bodySmall),
                            ],
                          ),
                        ),
                        const Icon(Icons.chevron_right),
                      ],
                    ),
                  ),
                ),
          ],
        ),
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
                    'We find the court lines ourselves and show you the ones we used.',
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
  });

  final String matchId;
  final Future<ApiResult<MatchRecord>> Function(String matchId) loader;
  final Future<ApiResult<Uint8List>> Function(String matchId) pictureLoader;

  @override
  State<MatchResultScreen> createState() => _MatchResultScreenState();
}

class _MatchResultScreenState extends State<MatchResultScreen> {
  MatchRecord? _match;
  ApiFailure? _failure;
  Timer? _timer;
  Future<ApiResult<Uint8List>>? _picture;

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
    final ApiResult<void> deleted = await deleteMatch(widget.matchId);
    if (!mounted) return;
    if (deleted.isOk) {
      Navigator.of(context).pop();
    } else {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(deleted.failure!.message)));
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
        ],
      ),
      body: SafeArea(child: _body(context, match)),
    );
  }

  Widget _body(BuildContext context, MatchRecord? match) {
    final ThemeData theme = Theme.of(context);
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
      return Padding(
        padding: const EdgeInsets.all(AppSpacing.xl),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: <Widget>[
            const Icon(Icons.sports_tennis, size: 48),
            const SizedBox(height: AppSpacing.lg),
            Text(_statusLine(match), style: theme.textTheme.titleMedium, textAlign: TextAlign.center),
            const SizedBox(height: AppSpacing.md),
            LinearProgressIndicator(value: match.status == MatchStatus.running ? match.progress : null),
            const SizedBox(height: AppSpacing.md),
            Text(
              'This takes about twice the length of the video. You can leave this screen: '
              'it keeps going and shows up in ${match.kind.tabTitle}.',
              style: theme.textTheme.bodySmall,
              textAlign: TextAlign.center,
            ),
          ],
        ),
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

class _MatchResultBody extends StatelessWidget {
  const _MatchResultBody({required this.match, required this.result, required this.picture});

  final MatchRecord match;
  final CourtAnalysis result;
  final Future<ApiResult<Uint8List>>? picture;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final MatchStats? stats = match.stats;
    final bool cameraUnverified =
        result.landings.any((ShotLanding l) => l.flags.contains(kCameraUnverifiedFlag));
    final List<ShotLanding> called = result.landings.where((ShotLanding l) => l.lineCall != null).toList()
      ..sort((ShotLanding a, ShotLanding b) =>
          a.lineCall!.signedMarginM.abs().compareTo(b.lineCall!.signedMarginM.abs()));

    return ListView(
      padding: const EdgeInsets.fromLTRB(AppSpacing.lg, AppSpacing.sm, AppSpacing.lg, AppSpacing.xxl),
      children: <Widget>[
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
          if (match.feedback != null) ...<Widget>[
            CoachingSummaryCard(feedback: match.feedback!),
            const SizedBox(height: AppSpacing.xl),
          ],
          if (stats != null) _StatsGrid(stats: stats),
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
          const SectionHeader(title: 'Shot map'),
          const SizedBox(height: AppSpacing.sm),
          if (result.landings.isEmpty)
            Text('No bounces were found in this video.', style: theme.textTheme.bodyMedium)
          else
            CourtMap(landings: result.landings),
          const SizedBox(height: AppSpacing.xl),
          const SectionHeader(title: 'Rallies'),
          const SizedBox(height: AppSpacing.sm),
          RallyTimeline(rallies: result.rallies),
          if (called.isNotEmpty) ...<Widget>[
            const SizedBox(height: AppSpacing.xl),
            const SectionHeader(title: 'Closest line calls'),
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
          const SectionHeader(title: 'Court lines we used'),
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

class _StatsGrid extends StatelessWidget {
  const _StatsGrid({required this.stats});

  final MatchStats stats;

  @override
  Widget build(BuildContext context) {
    final List<(String, String)> tiles = <(String, String)>[
      ('Rallies', '${stats.rallies}'),
      ('Longest rally', stats.longestRallyHits == null ? '—' : '${stats.longestRallyHits} shots'),
      ('Average rally', stats.averageRallyHits == null ? '—' : '${stats.averageRallyHits!.toStringAsFixed(1)} shots'),
      ('Shots landed', '${stats.shotsLanded}'),
      ('In', _percent(stats.inPercent)),
      ('Deep', _percent(stats.deepPercent)),
    ];
    final ThemeData theme = Theme.of(context);
    return LayoutBuilder(
      builder: (BuildContext context, BoxConstraints constraints) {
        final int columns = constraints.maxWidth >= 520 ? 3 : 2;
        final double width = (constraints.maxWidth - AppSpacing.md * (columns - 1)) / columns;
        return Wrap(
          spacing: AppSpacing.md,
          runSpacing: AppSpacing.md,
          children: <Widget>[
            for (final (String label, String value) in tiles)
              SizedBox(
                width: width,
                child: AppCard(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(label, style: theme.textTheme.bodySmall),
                      const SizedBox(height: AppSpacing.xs),
                      Text(value, style: theme.textTheme.titleLarge),
                    ],
                  ),
                ),
              ),
          ],
        );
      },
    );
  }
}
