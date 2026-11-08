import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../models/analysis_response.dart';
import '../models/ball_speed.dart';
import '../models/chart_data.dart';
import '../models/coaching_feedback.dart';
import '../models/enums.dart';
import '../models/metric_score.dart';
import '../models/swing_coaching.dart';
import '../widgets/coaching_list.dart';
import '../widgets/content_width.dart';
import '../widgets/skill_radar.dart';

/// Renders one finished analysis.
class ResultsScreen extends StatelessWidget {
  const ResultsScreen({
    super.key,
    required this.analysis,
    this.offerRecordAnother = false,
  });

  final AnalysisResponse analysis;

  /// True when this screen follows a swing just recorded or chosen. A
  /// practising player's next move is the next swing, so a pinned button takes
  /// them straight back to the record screen with their settings intact. Off
  /// when opened from history, where "record another" would be a non sequitur.
  final bool offerRecordAnother;

  @override
  Widget build(BuildContext context) {
    final CoachingFeedback? feedback = analysis.feedback;
    // Plain one-line coaching from the fixed rule table: every off-target
    // number, then a couple of in-range ones. Deterministic, so it is there
    // even when the AI feedback below is missing or fell back.
    final List<CoachingItem> coaching = buildCoaching(analysis);
    final List<CoachingItem> allFixes = <CoachingItem>[
      for (final CoachingItem c in coaching)
        if (c.isOffTarget && c.advice != null) c,
    ];
    // A coach gives two or three things at a time, not every number at once.
    // The rest are in "See all measurements", each with its gap.
    final List<CoachingItem> fixes =
        allFixes.take(kResultsFixItems).toList();
    final int moreOffTarget = coaching
            .where((CoachingItem c) => c.isOffTarget)
            .length -
        fixes.length;
    final List<CoachingItem> praise = <CoachingItem>[
      for (final CoachingItem c in coaching)
        if (c.isInRange && c.advice != null) c,
    ].take(kResultsPraiseItems).toList();

    // Debug-only. Nothing in this app surfaced guard diagnostics anywhere, so a
    // truncated Gemini draft looked identical to a normal template answer from
    // the client side. This is the smallest thing that makes that event
    // debuggable: the assert body is stripped entirely from release builds, so
    // it costs a user nothing and adds no UI.
    assert(() {
      final String? note = feedback?.guard?.truncationDebugLine;
      if (note != null) debugPrint('[Tennisimo] $note');
      return true;
    }());

    return Scaffold(
      appBar: AppBar(title: const Text('Your swing')),
      bottomNavigationBar: offerRecordAnother
          ? SafeArea(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
                child: FilledButton.icon(
                  // The analysing screen replaced itself with this one, so a
                  // pop lands on the record screen it came from.
                  onPressed: () => Navigator.of(context).maybePop(),
                  icon: const Icon(Icons.videocam_outlined),
                  label: const Text('Record another swing'),
                ),
              ),
            )
          : null,
      body: ContentWidth(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
          children: <Widget>[
            // Sits above every number it qualifies, so a degraded analysis is
            // never read as a normal one.
            if (analysis.status == AnalysisStatus.partial) ...<Widget>[
              const _PartialBanner(),
              const SizedBox(height: 16),
            ],
            if (analysis.status == AnalysisStatus.unrecognized) ...<Widget>[
              const _UnrecognizedStatusBanner(),
              const SizedBox(height: 16),
            ],
            _ShotHeader(analysis: analysis),
            const SizedBox(height: 16),
            if (analysis.shotTypeMismatch) ...<Widget>[
              _MismatchNote(analysis: analysis),
              const SizedBox(height: 16),
            ],
            // What went wrong and how to fix it leads: each item is the
            // change, the measured gap, and a drill. All from the fixed rule
            // table, so it is here whether or not the AI feedback arrived.
            if (fixes.isNotEmpty) ...<Widget>[
              _SectionCard(
                title: kWorkOnTitle,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: <Widget>[
                    CoachingList(items: fixes),
                    if (moreOffTarget > 0) ...<Widget>[
                      const Divider(height: 24),
                      Text(
                        moreOffTargetLine(moreOffTarget),
                        style: Theme.of(context).textTheme.bodySmall,
                      ),
                    ],
                  ],
                ),
              ),
              const SizedBox(height: 16),
            ],
            if (praise.isNotEmpty) ...<Widget>[
              _SectionCard(
                title: kWorkingTitle,
                child: CoachingList(items: praise),
              ),
              const SizedBox(height: 16),
            ],
            if (feedback != null && feedback.summary.isNotEmpty) ...<Widget>[
              _SectionCard(
                title: 'Coach summary',
                child: Text(feedback.summary),
              ),
              const SizedBox(height: 16),
            ],
            ..._buildBallSpeed(context),
            // A strength is said once. When the swing notes already praise
            // something, they are the source: deterministic, tied to a
            // measured number, and never a template fallback. The AI's own
            // list only appears when the notes have nothing good to say, so
            // a swing is never left without a single positive.
            if (feedback != null &&
                feedback.strengths.isNotEmpty &&
                praise.isEmpty) ...<Widget>[
              _SectionCard(
                title: 'What is working',
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: feedback.strengths
                      .map((String s) => _Bullet(text: s))
                      .toList(),
                ),
              ),
              const SizedBox(height: 16),
            ],
            if (feedback != null && feedback.improvements.isNotEmpty) ...<Widget>[
              ...feedback.shownImprovements.map(
                (Improvement improvement) => Padding(
                  padding: const EdgeInsets.only(bottom: 16),
                  child: _ImprovementCard(improvement: improvement),
                ),
              ),
            ],
            // Category summary first, individual metrics after: the categories
            // are the breakdown of the overall score shown in the header above,
            // so they read as the "why" of that number, and the metric list
            // below is the detail behind them. It does not replace the metric
            // list. The metric list itself is closed by default: its raw
            // torso-unit figures are for advanced players, not a beginner.
            _CategoriesSection(analysis: analysis),
            if (analysis.categories.isNotEmpty) const SizedBox(height: 16),
            // Unscored metrics are left out by buildCoaching, so an older
            // backend's verdict and band for them are never shown.
            _MetricsSection(items: coaching),
          ],
        ),
      ),
    );
  }

  /// Ball speed, or nothing at all.
  ///
  /// The block is omitted outright when there is no speed and the user did not
  /// calibrate — no "0 mph", no em dash, no empty placeholder.
  List<Widget> _buildBallSpeed(BuildContext context) {
    final BallSpeedResult? ballSpeed = analysis.ballSpeed;
    if (ballSpeed == null) return const <Widget>[];
    if (!ballSpeed.hasSpeed && !ballSpeed.userCalibrated) {
      return const <Widget>[];
    }
    return <Widget>[
      _BallSpeedCard(ballSpeed: ballSpeed),
      const SizedBox(height: 16),
    ];
  }
}

/// Informational notice for a `partial` analysis.
///
/// A partial analysis is still a useful analysis, so this is styled as
/// information, not as an error, and it is deliberately given its own container
/// colour so it does not read like the neutral ball-speed confidence chip.
class _PartialBanner extends StatelessWidget {
  const _PartialBanner();

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Card(
      color: theme.colorScheme.tertiaryContainer,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Icon(Icons.info_outline,
                color: theme.colorScheme.onTertiaryContainer),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(
                    'Part of this swing could not be measured',
                    style: theme.textTheme.titleSmall?.copyWith(
                      color: theme.colorScheme.onTertiaryContainer,
                    ),
                  ),
                  const SizedBox(height: 6),
                  Text(
                    'Some measurements were not reliable enough in this clip, '
                    'so they have been left out rather than guessed. The rest '
                    'of the feedback below still stands.',
                    style: theme.textTheme.bodyMedium?.copyWith(
                      color: theme.colorScheme.onTertiaryContainer,
                    ),
                  ),
                  const SizedBox(height: 6),
                  Text(
                    'For a fuller reading next time, film side-on with the '
                    'phone level, 5-10 m to your side, with your whole body in '
                    'frame.',
                    style: theme.textTheme.bodyMedium?.copyWith(
                      color: theme.colorScheme.onTertiaryContainer,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Notice for a status string this build does not recognise.
///
/// Deliberately the same container/icon/title/body shape as [_PartialBanner],
/// so an unrecognised status is as visible as a degraded one instead of being
/// silently rendered as a normal, fully successful analysis.
class _UnrecognizedStatusBanner extends StatelessWidget {
  const _UnrecognizedStatusBanner();

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Card(
      color: theme.colorScheme.tertiaryContainer,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Icon(Icons.help_outline,
                color: theme.colorScheme.onTertiaryContainer),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(
                    'This app could not read the status of this analysis',
                    style: theme.textTheme.titleSmall?.copyWith(
                      color: theme.colorScheme.onTertiaryContainer,
                    ),
                  ),
                  const SizedBox(height: 6),
                  Text(
                    'The server reported a status this version of the app does '
                    'not recognise, so it cannot tell you whether everything '
                    'below was measured fully. Treat these numbers with care.',
                    style: theme.textTheme.bodyMedium?.copyWith(
                      color: theme.colorScheme.onTertiaryContainer,
                    ),
                  ),
                  const SizedBox(height: 6),
                  Text(
                    'Updating the app usually clears this.',
                    style: theme.textTheme.bodyMedium?.copyWith(
                      color: theme.colorScheme.onTertiaryContainer,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// The header's provenance line for a shot the user chose themselves. No
/// confidence figure: the server reports none for a user's choice.
const String kShotDeclaredLine = 'You selected this shot.';

/// Added under [kNoShotSpecificScoringMessage] when an overall score was
/// still produced — on the general bands every shot shares.
const String kGenericScoreMessage =
    'This score uses general measurements, not ones tuned for this shot.';

/// PURE. The line under the shot name, or null when nothing honest can be
/// said about where the shot type came from.
///
/// A declared shot says so and carries no confidence. An inferred one keeps
/// the "detected" wording, with a percentage only when one was reported — a
/// null is never printed as "null" or "0%". An unrecognised source claims
/// neither.
String? shotProvenanceLine(AnalysisResponse analysis) {
  switch (analysis.shotTypeSource) {
    case ShotTypeSource.userDeclared:
      return kShotDeclaredLine;
    case ShotTypeSource.unrecognized:
      return null;
    case ShotTypeSource.inferred:
      final double? confidence = analysis.shotTypeConfidence;
      return confidence == null
          ? 'Detected from your technique.'
          : 'Detected from your technique · '
              '${(confidence * 100).round()}% confidence';
  }
}

/// PURE. True when the "scoring isn't available yet" notice applies: the
/// server said this shot has no bands of its own, and the shot is a real one.
/// A missing key (null) is unknown, and shows nothing.
bool showsNoShotSpecificScoring(AnalysisResponse analysis) =>
    analysis.shotSpecificScoring == false &&
    analysis.shotType != ShotType.unknown;

/// PURE. The plain-language mismatch sentence. No numbers, no blame: the user
/// chose one shot, the swing looked like another, and their choice stood.
String mismatchMessage(AnalysisResponse analysis) {
  final String chosen = analysis.shotType.label.toLowerCase();
  final ShotType? seen = analysis.inferredShotType;
  final String looked = seen == null || seen == ShotType.unknown
      ? 'the swing looked like a different shot'
      : 'the swing looked more like a ${seen.label.toLowerCase()}';
  return 'You chose a $chosen, but $looked. It was scored as a $chosen, as '
      'you asked.';
}

class _ShotHeader extends StatelessWidget {
  const _ShotHeader({required this.analysis});

  final AnalysisResponse analysis;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final String? provenance = shotProvenanceLine(analysis);
    final double? score = analysis.overallScore;
    final bool noShotScoring = showsNoShotSpecificScoring(analysis);

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(analysis.shotType.label,
                          style: theme.textTheme.headlineSmall),
                      if (provenance != null) ...<Widget>[
                        const SizedBox(height: 4),
                        Text(provenance, style: theme.textTheme.bodySmall),
                      ],
                    ],
                  ),
                ),
                if (score != null) ...<Widget>[
                  const SizedBox(width: 12),
                  Column(
                    children: <Widget>[
                      Text(score.round().toString(),
                          style: theme.textTheme.headlineMedium),
                      Text('overall', style: theme.textTheme.bodySmall),
                    ],
                  ),
                ],
              ],
            ),
            // Said in words rather than left blank. An existing score is
            // never hidden: when there is one, a second line says what it
            // was measured against.
            if (noShotScoring) ...<Widget>[
              const SizedBox(height: 12),
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Icon(Icons.info_outline,
                      size: 18, color: theme.colorScheme.onSurfaceVariant),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        Text(kNoShotSpecificScoringMessage,
                            style: theme.textTheme.bodyMedium),
                        if (score != null) ...<Widget>[
                          const SizedBox(height: 4),
                          Text(
                            kGenericScoreMessage,
                            style: theme.textTheme.bodySmall?.copyWith(
                              color: theme.colorScheme.onSurfaceVariant,
                            ),
                          ),
                        ],
                      ],
                    ),
                  ),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// The user chose one shot and the swing looked like another.
///
/// Information, not an error: the same calm container as [_PartialBanner],
/// and never the error colour.
class _MismatchNote extends StatelessWidget {
  const _MismatchNote({required this.analysis});

  final AnalysisResponse analysis;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Card(
      color: theme.colorScheme.tertiaryContainer,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Icon(Icons.info_outline,
                color: theme.colorScheme.onTertiaryContainer),
            const SizedBox(width: 12),
            Expanded(
              child: Text(
                mismatchMessage(analysis),
                style: theme.textTheme.bodyMedium?.copyWith(
                  color: theme.colorScheme.onTertiaryContainer,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Ball speed with its framing and definition. Never a standalone hero number.
class _BallSpeedCard extends StatelessWidget {
  const _BallSpeedCard({required this.ballSpeed});

  final BallSpeedResult ballSpeed;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);

    if (!ballSpeed.hasSpeed) {
      // Reached only when the user calibrated, so the reason is worth telling.
      final BallSpeedUnavailableReason? reason = ballSpeed.unavailableReason;
      return _SectionCard(
        title: 'Ball speed',
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(
              'No speed for this clip.',
              style: theme.textTheme.bodyMedium,
            ),
            if (reason != null) ...<Widget>[
              const SizedBox(height: 6),
              Text(reason.explanation, style: theme.textTheme.bodyMedium),
            ],
          ],
        ),
      );
    }

    return _SectionCard(
      title: 'Approximate ball speed',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            crossAxisAlignment: CrossAxisAlignment.center,
            children: <Widget>[
              Text(ballSpeed.displaySpeed, style: theme.textTheme.titleLarge),
              const SizedBox(width: 12),
              // Deliberately a neutral chip. `medium` is the normal, healthy
              // result on 30 fps footage and is never coloured as a warning.
              Chip(
                label: Text(ballSpeed.confidence.label),
                visualDensity: VisualDensity.compact,
              ),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            'This is an approximate figure.',
            style: theme.textTheme.bodyMedium,
          ),
          const SizedBox(height: 6),
          Text(
            ballSpeed.measurementDefinition,
            style: theme.textTheme.bodySmall,
          ),
        ],
      ),
    );
  }
}

class _ImprovementCard extends StatelessWidget {
  const _ImprovementCard({required this.improvement});

  final Improvement improvement;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                CircleAvatar(
                  radius: 12,
                  backgroundColor: theme.colorScheme.primaryContainer,
                  child: Text(
                    '${improvement.priority}',
                    style: theme.textTheme.labelMedium?.copyWith(
                      color: theme.colorScheme.onPrimaryContainer,
                    ),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(improvement.title,
                      style: theme.textTheme.titleMedium),
                ),
              ],
            ),
            if (improvement.why.isNotEmpty) ...<Widget>[
              const SizedBox(height: 10),
              Text(improvement.why),
            ],
            if (improvement.cue.isNotEmpty) ...<Widget>[
              const SizedBox(height: 10),
              _LabelledLine(label: 'Cue', text: improvement.cue),
            ],
            if (improvement.drill.isNotEmpty) ...<Widget>[
              const SizedBox(height: 6),
              _LabelledLine(label: 'Drill', text: improvement.drill),
            ],
          ],
        ),
      ),
    );
  }
}

class _LabelledLine extends StatelessWidget {
  const _LabelledLine({required this.label, required this.text});

  final String label;
  final String text;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Text('$label: ',
            style: theme.textTheme.bodyMedium
                ?.copyWith(fontWeight: FontWeight.bold)),
        Expanded(child: Text(text, style: theme.textTheme.bodyMedium)),
      ],
    );
  }
}

/// The weighted category breakdown behind the overall score.
///
/// Same [_SectionCard] + divider-separated-rows shape as [_MetricsSection], so
/// it reads as a peer of the metric list rather than a new kind of thing.
class _CategoriesSection extends StatelessWidget {
  const _CategoriesSection({required this.analysis});

  final AnalysisResponse analysis;

  @override
  Widget build(BuildContext context) {
    final List<CategoryScore> categories = analysis.categories;
    if (categories.isEmpty) return const SizedBox.shrink();
    // This one swing's shape. A single series, so no legend: the card title
    // names it. The rows below are its exact numbers.
    final SkillProfile? profile = buildSkillProfile(analysis);
    return _SectionCard(
      title: 'Score breakdown',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          if (profile != null) ...<Widget>[
            SkillRadar(profile: profile),
            const Divider(height: 24),
          ],
          for (int i = 0; i < categories.length; i++) ...<Widget>[
            if (i > 0) const Divider(height: 24),
            _CategoryRow(category: categories[i]),
          ],
        ],
      ),
    );
  }
}

class _CategoryRow extends StatelessWidget {
  const _CategoryRow({required this.category});

  final CategoryScore category;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Expanded(
              child: Text(category.displayName,
                  style: theme.textTheme.titleSmall),
            ),
            const SizedBox(width: 12),
            // An unmeasured category says so. It is never shown as 0, which
            // would read as a score the user earned. Flexible, as in
            // [_MetricRow], so "not measured" wraps instead of overflowing a
            // narrow phone at large text.
            Flexible(
              child: Text(
                category.displayScore,
                textAlign: TextAlign.end,
                style: category.isMeasured
                    ? theme.textTheme.titleSmall
                    : theme.textTheme.bodySmall,
              ),
            ),
          ],
        ),
        const SizedBox(height: 4),
        Text(category.displayCoverage, style: theme.textTheme.bodySmall),
      ],
    );
  }
}

/// Every scored measurement, behind an expander that starts closed: plain
/// name, value in a readable unit, what it measures, the target and the gap.
class _MetricsSection extends StatelessWidget {
  const _MetricsSection({required this.items});

  final List<CoachingItem> items;

  @override
  Widget build(BuildContext context) {
    if (items.isEmpty) return const SizedBox.shrink();
    final ThemeData theme = Theme.of(context);
    final bool anyTorso = items.any((CoachingItem c) =>
        c.metric.unit == MetricUnit.torsoUnits ||
        c.metric.unit == MetricUnit.torsoUnitsPerSec);
    return Card(
      clipBehavior: Clip.antiAlias,
      child: ExpansionTile(
        // The card already draws the outline; no extra lines when open.
        shape: const Border(),
        collapsedShape: const Border(),
        tilePadding: const EdgeInsets.symmetric(horizontal: 16),
        childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
        expandedCrossAxisAlignment: CrossAxisAlignment.start,
        title: Text('See all measurements',
            style: theme.textTheme.titleMedium),
        children: <Widget>[
          for (int i = 0; i < items.length; i++) ...<Widget>[
            if (i > 0) const Divider(height: 24),
            _MetricRow(item: items[i]),
          ],
          if (anyTorso) ...<Widget>[
            const Divider(height: 24),
            Text(kTorsoLengthExplainer, style: theme.textTheme.bodySmall),
          ],
        ],
      ),
    );
  }
}

class _MetricRow extends StatelessWidget {
  const _MetricRow({required this.item});

  final CoachingItem item;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final MetricScore metric = item.metric;
    final String? target = item.targetText;
    final String? what = item.what;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Row(
          // Pushes the value to the right edge: a Flexible value leaves its
          // unused share as free space, which goes between the two.
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Expanded(
              child: Text(item.label, style: theme.textTheme.titleSmall),
            ),
            const SizedBox(width: 12),
            // Flexible so a long value at large text wraps instead of
            // overflowing a narrow phone.
            Flexible(
              child: Text(
                item.valueText,
                textAlign: TextAlign.end,
                style: item.isMeasured
                    ? theme.textTheme.titleSmall
                    : theme.textTheme.bodySmall,
              ),
            ),
          ],
        ),
        if (what != null) ...<Widget>[
          const SizedBox(height: 4),
          Text(
            what,
            style: theme.textTheme.bodySmall
                ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
          ),
        ],
        const SizedBox(height: 4),
        // One wrapping line rather than a Row of fixed pieces, so it cannot
        // overflow a narrow phone at large text. The verdict is left out for
        // an unmeasured value: the value already says "not measured".
        Text(
          <String>[
            if (item.isMeasured ||
                metric.verdict != MetricVerdict.unavailable)
              metric.verdict.label,
            if (target != null) 'target $target',
            if (item.isOffTarget && item.gapText != null) item.gapText!,
            if (metric.viewSensitive) 'camera-angle sensitive',
          ].join(' · '),
          style: theme.textTheme.bodySmall,
        ),
        if (metric.hasIdealBand) ...<Widget>[
          const SizedBox(height: 8),
          _RangeBar(metric: metric),
        ],
      ],
    );
  }
}

/// Section titles for the deterministic coaching.
const String kWorkOnTitle = 'What to work on';
const String kWorkingTitle = 'What is working';

/// How many in-range items the results show.
const int kResultsPraiseItems = 2;

/// How many fixes the results lead with, most off-target first.
const int kResultsFixItems = 3;

/// PURE. The pointer to the off-target measurements not in the top fixes.
String moreOffTargetLine(int count) => count == 1
    ? '1 more measurement is off target. Fix these first, then check it '
        'under "See all measurements" below.'
    : '$count more measurements are off target. Fix these first, then check '
        'them under "See all measurements" below.';

/// A reference-band indicator: the ideal band as a filled segment, with the
/// measured value marked on it. An unmeasurable value draws the band alone.
class _RangeBar extends StatelessWidget {
  const _RangeBar({required this.metric});

  final MetricScore metric;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final double lo = metric.idealMin!;
    final double hi = metric.idealMax!;
    final double? value = metric.value;

    // Domain: the band plus the value, padded so nothing sits on the edge.
    double domainMin = math.min(lo, value ?? lo);
    double domainMax = math.max(hi, value ?? hi);
    final double span = domainMax - domainMin;
    final double pad = span == 0 ? 1.0 : span * 0.15;
    domainMin -= pad;
    domainMax += pad;
    final double domain = domainMax - domainMin;

    double fraction(double v) =>
        domain <= 0 ? 0.5 : ((v - domainMin) / domain).clamp(0.0, 1.0);

    return LayoutBuilder(
      builder: (BuildContext context, BoxConstraints constraints) {
        final double width = constraints.maxWidth;
        final double bandLeft = fraction(lo) * width;
        final double bandWidth = (fraction(hi) - fraction(lo)) * width;

        return SizedBox(
          height: 18,
          child: Stack(
            children: <Widget>[
              Positioned(
                left: 0,
                right: 0,
                top: 7,
                child: Container(
                  height: 4,
                  decoration: BoxDecoration(
                    color: theme.colorScheme.surfaceContainerHighest,
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
              ),
              Positioned(
                left: bandLeft,
                width: math.max(bandWidth, 2),
                top: 7,
                child: Container(
                  height: 4,
                  decoration: BoxDecoration(
                    // Was `primaryContainer`, which on the dark theme is
                    // DARKER than the track behind it — the ideal band read as
                    // a hole rather than a highlight. A translucent primary
                    // keeps it a dim green segment that the solid primary
                    // value marker still stands clear of.
                    color: theme.colorScheme.primary.withValues(alpha: 0.38),
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
              ),
              if (value != null)
                Positioned(
                  left: (fraction(value) * width - 5).clamp(0.0, width - 10),
                  top: 2,
                  child: Container(
                    width: 10,
                    height: 14,
                    decoration: BoxDecoration(
                      color: theme.colorScheme.primary,
                      borderRadius: BorderRadius.circular(3),
                    ),
                  ),
                ),
            ],
          ),
        );
      },
    );
  }
}

class _SectionCard extends StatelessWidget {
  const _SectionCard({required this.title, required this.child});

  final String title;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(title, style: theme.textTheme.titleMedium),
            const SizedBox(height: 10),
            child,
          ],
        ),
      ),
    );
  }
}

class _Bullet extends StatelessWidget {
  const _Bullet({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const Text('•  '),
          Expanded(child: Text(text)),
        ],
      ),
    );
  }
}
