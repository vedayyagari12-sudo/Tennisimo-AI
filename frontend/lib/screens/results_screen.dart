import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../models/analysis_response.dart';
import '../models/ball_speed.dart';
import '../models/coaching_feedback.dart';
import '../models/enums.dart';
import '../models/metric_score.dart';

/// Renders one finished analysis.
class ResultsScreen extends StatelessWidget {
  const ResultsScreen({super.key, required this.analysis});

  final AnalysisResponse analysis;

  @override
  Widget build(BuildContext context) {
    final CoachingFeedback? feedback = analysis.feedback;

    return Scaffold(
      appBar: AppBar(title: const Text('Your swing')),
      body: ListView(
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
          if (feedback != null && feedback.summary.isNotEmpty) ...<Widget>[
            _SectionCard(
              title: 'Coach summary',
              child: Text(feedback.summary),
            ),
            const SizedBox(height: 16),
          ],
          ..._buildBallSpeed(context),
          if (feedback != null && feedback.strengths.isNotEmpty) ...<Widget>[
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
            ...feedback.improvements.map(
              (Improvement improvement) => Padding(
                padding: const EdgeInsets.only(bottom: 16),
                child: _ImprovementCard(improvement: improvement),
              ),
            ),
          ],
          _MetricsSection(metrics: analysis.metrics),
        ],
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

class _ShotHeader extends StatelessWidget {
  const _ShotHeader({required this.analysis});

  final AnalysisResponse analysis;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final double? confidence = analysis.shotTypeConfidence;
    final double? score = analysis.overallScore;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(analysis.shotType.label,
                      style: theme.textTheme.headlineSmall),
                  const SizedBox(height: 4),
                  Text(
                    confidence == null
                        ? 'Detected from your technique.'
                        : 'Detected from your technique · '
                            '${(confidence * 100).round()}% confidence',
                    style: theme.textTheme.bodySmall,
                  ),
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

class _MetricsSection extends StatelessWidget {
  const _MetricsSection({required this.metrics});

  final List<MetricScore> metrics;

  @override
  Widget build(BuildContext context) {
    if (metrics.isEmpty) return const SizedBox.shrink();
    return _SectionCard(
      title: 'Metrics',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          for (int i = 0; i < metrics.length; i++) ...<Widget>[
            if (i > 0) const Divider(height: 24),
            _MetricRow(metric: metrics[i]),
          ],
        ],
      ),
    );
  }
}

class _MetricRow extends StatelessWidget {
  const _MetricRow({required this.metric});

  final MetricScore metric;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final String? band = metric.displayIdealBand;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Expanded(
              child: Text(metric.displayName,
                  style: theme.textTheme.titleSmall),
            ),
            const SizedBox(width: 12),
            Text(
              metric.displayValue,
              style: metric.isMeasurable
                  ? theme.textTheme.titleSmall
                  : theme.textTheme.bodySmall,
            ),
          ],
        ),
        const SizedBox(height: 4),
        Row(
          children: <Widget>[
            Text(metric.verdict.label, style: theme.textTheme.bodySmall),
            if (band != null) ...<Widget>[
              Text(' · ', style: theme.textTheme.bodySmall),
              Text('reference $band', style: theme.textTheme.bodySmall),
            ],
            if (metric.viewSensitive) ...<Widget>[
              Text(' · ', style: theme.textTheme.bodySmall),
              Text('camera-angle sensitive',
                  style: theme.textTheme.bodySmall),
            ],
          ],
        ),
        if (metric.hasIdealBand) ...<Widget>[
          const SizedBox(height: 8),
          _RangeBar(metric: metric),
        ],
      ],
    );
  }
}

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
                    color: theme.colorScheme.primaryContainer,
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
