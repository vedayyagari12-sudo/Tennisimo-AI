import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../models/court.dart';
import '../theme/app_theme.dart';

/// Which landings the map shows (SESSION_MODE_PHASE2.md 8.1).
enum CourtMapSide { all, yours, opponent }

class CourtMapFilter {
  const CourtMapFilter({this.side = CourtMapSide.all, this.verdict, this.servesOnly = false});

  final CourtMapSide side;

  /// Null shows every verdict, including landings with no call.
  final LineVerdict? verdict;
  final bool servesOnly;

  CourtMapFilter copyWith({CourtMapSide? side, LineVerdict? verdict, bool clearVerdict = false, bool? servesOnly}) =>
      CourtMapFilter(
        side: side ?? this.side,
        verdict: clearVerdict ? null : (verdict ?? this.verdict),
        servesOnly: servesOnly ?? this.servesOnly,
      );

  /// "Your shots" are the near player's until a score config names players
  /// (brief, Decision 1 assumption). An unknown hitter is in neither side.
  bool accepts(ShotLanding landing) {
    switch (side) {
      case CourtMapSide.all:
        break;
      case CourtMapSide.yours:
        if (landing.hitterEnd != CourtEnd.near) return false;
      case CourtMapSide.opponent:
        if (landing.hitterEnd != CourtEnd.far) return false;
    }
    if (verdict != null && landing.verdict != verdict) return false;
    if (servesOnly && !landing.isServe) return false;
    return true;
  }

  List<ShotLanding> apply(List<ShotLanding> landings) =>
      <ShotLanding>[for (final ShotLanding l in landings) if (accepts(l)) l];
}

const String kCourtMapTitle = 'Where your shots landed';
const String kCourtMapFootnote = 'Estimates. Far-end landings are less certain.';

/// Shares of the depth zones for [landings], from each landing's
/// probabilities (expected counts), as whole percents. Empty when none.
Map<DepthZone, int> depthPercents(List<ShotLanding> landings) {
  if (landings.isEmpty) return <DepthZone, int>{};
  final Map<DepthZone, double> sums = <DepthZone, double>{
    for (final DepthZone z in DepthZone.ordered) z: 0,
  };
  for (final ShotLanding l in landings) {
    for (final DepthZone z in DepthZone.ordered) {
      sums[z] = sums[z]! + (l.depthProbabilities[z] ?? 0);
    }
  }
  return <DepthZone, int>{
    for (final DepthZone z in DepthZone.ordered) z: (sums[z]! / landings.length * 100).round(),
  };
}

/// One line of copy for the depth zones, e.g. "Depth: Short 19% Mid 42% ...".
String depthLine(List<ShotLanding> landings) {
  final Map<DepthZone, int> percents = depthPercents(landings);
  if (percents.isEmpty) return 'Depth: no landings yet';
  return 'Depth: ${DepthZone.ordered.map((DepthZone z) => '${z.label} ${percents[z]}%').join('  ')}';
}

/// Counts for the screen reader and the header. Too close and no-call
/// landings are counted separately and never as in.
String courtMapSummary(List<ShotLanding> landings) {
  final int inside = landings.where((ShotLanding l) => l.verdict == LineVerdict.inside).length;
  final int out = landings.where((ShotLanding l) => l.verdict == LineVerdict.out).length;
  final int close = landings.where((ShotLanding l) => l.verdict == LineVerdict.tooCloseToCall).length;
  final int uncalled = landings.where((ShotLanding l) => l.lineCall == null).length;
  return <String>[
    '${landings.length} ${landings.length == 1 ? 'landing' : 'landings'}',
    '$inside in',
    '$out out',
    '$close too close to call',
    if (uncalled > 0) '$uncalled not called',
  ].join(', ');
}

/// The shot map: a portrait court, far baseline at the top, with one mark
/// per landing. Verdict is carried by SHAPE (dot, cross, ring), never by
/// colour alone.
class CourtMap extends StatefulWidget {
  const CourtMap({
    super.key,
    required this.landings,
    this.filter = const CourtMapFilter(),
    this.onSelect,
    this.showFilters = true,
  });

  final List<ShotLanding> landings;
  final CourtMapFilter filter;
  final ValueChanged<ShotLanding>? onSelect;
  final bool showFilters;

  @override
  State<CourtMap> createState() => _CourtMapState();
}

class _CourtMapState extends State<CourtMap> {
  late CourtMapFilter _filter = widget.filter;
  String? _selected;

  @override
  void didUpdateWidget(CourtMap old) {
    super.didUpdateWidget(old);
    if (old.filter != widget.filter) _filter = widget.filter;
  }

  void _tap(Offset p, Size size, List<ShotLanding> shown) {
    final ShotLanding? hit = CourtMapPainter.landingAt(p, size, shown);
    if (hit == null) return;
    setState(() => _selected = hit.bounceId);
    widget.onSelect?.call(hit);
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final List<ShotLanding> shown = _filter.apply(widget.landings);
    final AppPalette palette = context.palette;

    Widget chip(String label, bool selected, VoidCallback onTap) => FilterChip(
          label: Text(label),
          selected: selected,
          onSelected: (_) => onTap(),
        );

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        Text(kCourtMapTitle, style: theme.textTheme.titleMedium),
        if (widget.showFilters) ...<Widget>[
          const SizedBox(height: AppSpacing.sm),
          Wrap(
            spacing: AppSpacing.sm,
            runSpacing: AppSpacing.xs,
            children: <Widget>[
              chip('All', _filter.side == CourtMapSide.all,
                  () => setState(() => _filter = _filter.copyWith(side: CourtMapSide.all))),
              chip('Your shots', _filter.side == CourtMapSide.yours,
                  () => setState(() => _filter = _filter.copyWith(side: CourtMapSide.yours))),
              chip('Opponent', _filter.side == CourtMapSide.opponent,
                  () => setState(() => _filter = _filter.copyWith(side: CourtMapSide.opponent))),
            ],
          ),
          const SizedBox(height: AppSpacing.xs),
          Wrap(
            spacing: AppSpacing.sm,
            runSpacing: AppSpacing.xs,
            children: <Widget>[
              for (final LineVerdict v in <LineVerdict>[
                LineVerdict.inside,
                LineVerdict.out,
                LineVerdict.tooCloseToCall,
              ])
                chip(v == LineVerdict.tooCloseToCall ? 'Too close' : v.label, _filter.verdict == v,
                    () => setState(() => _filter = _filter.verdict == v
                        ? _filter.copyWith(clearVerdict: true)
                        : _filter.copyWith(verdict: v))),
              chip('Serves', _filter.servesOnly,
                  () => setState(() => _filter = _filter.copyWith(servesOnly: !_filter.servesOnly))),
            ],
          ),
        ],
        const SizedBox(height: AppSpacing.md),
        Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 320, maxHeight: 640),
            child: AspectRatio(
              aspectRatio: CourtMapPainter.aspectRatio,
              child: LayoutBuilder(
                builder: (BuildContext context, BoxConstraints box) {
                  final Size size = Size(box.maxWidth, box.maxHeight);
                  return Semantics(
                    label: 'Court map. ${courtMapSummary(shown)}',
                    child: GestureDetector(
                      behavior: HitTestBehavior.opaque,
                      onTapUp: (TapUpDetails d) => _tap(d.localPosition, size, shown),
                      child: CustomPaint(
                        key: const Key('court-map-canvas'),
                        size: size,
                        painter: CourtMapPainter(
                          landings: shown,
                          selectedId: _selected,
                          courtFill: theme.colorScheme.surfaceContainerHigh,
                          lineColor: theme.colorScheme.outline,
                          inColor: palette.primaryFill,
                          outColor: palette.errorFill,
                          neutralColor: palette.onSurfaceVariant,
                          selectColor: theme.colorScheme.onSurface,
                        ),
                      ),
                    ),
                  );
                },
              ),
            ),
          ),
        ),
        const SizedBox(height: AppSpacing.md),
        Text(courtMapSummary(shown), style: theme.textTheme.bodyMedium),
        const SizedBox(height: AppSpacing.xs),
        Text(depthLine(shown), style: theme.textTheme.bodyMedium),
        const SizedBox(height: AppSpacing.xs),
        Text(kCourtMapFootnote,
            style: theme.textTheme.bodySmall?.copyWith(color: palette.onSurfaceVariant)),
      ],
    );
  }
}

/// Paints the court and the landings. Public for the hit test, like
/// `DonutPainter.sliceAt`.
class CourtMapPainter extends CustomPainter {
  CourtMapPainter({
    required this.landings,
    required this.courtFill,
    required this.lineColor,
    required this.inColor,
    required this.outColor,
    required this.neutralColor,
    required this.selectColor,
    this.selectedId,
  });

  final List<ShotLanding> landings;
  final String? selectedId;
  final Color courtFill;
  final Color lineColor;
  final Color inColor;
  final Color outColor;
  final Color neutralColor;
  final Color selectColor;

  /// Court metres shown: the doubles court plus run-off on every side.
  static const double halfWidthM = 6.5;
  static const double halfLengthM = 14.5;
  static const double aspectRatio = halfWidthM / halfLengthM;

  static const double _doublesX = 5.485;
  static const double _singlesX = 4.115;
  static const double _baselineY = 11.885;
  static const double _serviceY = 6.40;

  /// Ellipses are drawn at the width the call is judged at (brief G16).
  static const double ellipseZ = 1.96;

  /// Finger-sized tap radius, in logical pixels.
  static const double tapRadius = 18;

  static Offset toCanvas(CourtPoint p, Size size) => Offset(
        (p.x + halfWidthM) / (2 * halfWidthM) * size.width,
        (halfLengthM - p.y) / (2 * halfLengthM) * size.height,
      );

  static double _pxPerM(Size size) => size.width / (2 * halfWidthM);

  /// The landing nearest a tap at [p], within [tapRadius], or null.
  static ShotLanding? landingAt(Offset p, Size size, List<ShotLanding> landings) {
    ShotLanding? best;
    double bestD = tapRadius;
    for (final ShotLanding l in landings) {
      final double d = (toCanvas(l.courtXy, size) - p).distance;
      if (d <= bestD) {
        bestD = d;
        best = l;
      }
    }
    return best;
  }

  @override
  void paint(Canvas canvas, Size size) {
    final double scale = _pxPerM(size);
    Offset at(double x, double y) => toCanvas(CourtPoint(x, y), size);

    canvas.drawRect(Offset.zero & size, Paint()..color = courtFill);
    final Paint line = Paint()
      ..color = lineColor
      ..style = PaintingStyle.stroke
      ..strokeWidth = math.max(1, 0.05 * scale);
    canvas.drawRect(Rect.fromPoints(at(-_doublesX, _baselineY), at(_doublesX, -_baselineY)), line);
    for (final double x in <double>[-_singlesX, _singlesX]) {
      canvas.drawLine(at(x, _baselineY), at(x, -_baselineY), line);
    }
    for (final double y in <double>[_serviceY, -_serviceY]) {
      canvas.drawLine(at(-_singlesX, y), at(_singlesX, y), line);
    }
    canvas.drawLine(at(0, _serviceY), at(0, -_serviceY), line);
    canvas.drawLine(at(-_doublesX - 0.9, 0), at(_doublesX + 0.9, 0), line..strokeWidth = line.strokeWidth * 2.5);

    for (final ShotLanding l in landings) {
      final Color colour = switch (l.verdict) {
        LineVerdict.inside => inColor,
        LineVerdict.out => outColor,
        _ => neutralColor,
      };
      final Offset c = toCanvas(l.courtXy, size);
      // Uncertainty, to scale: a far-baseline ellipse is visibly long.
      canvas.save();
      canvas.translate(c.dx, c.dy);
      canvas.rotate(-l.sigma.angleDeg * math.pi / 180); // canvas y points down
      final Rect oval = Rect.fromCenter(
        center: Offset.zero,
        width: math.max(2 * ellipseZ * l.sigma.major * scale, 2),
        height: math.max(2 * ellipseZ * l.sigma.minor * scale, 2),
      );
      canvas.drawOval(oval, Paint()..color = colour.withValues(alpha: 0.14));
      canvas.drawOval(
          oval,
          Paint()
            ..color = colour.withValues(alpha: 0.45)
            ..style = PaintingStyle.stroke
            ..strokeWidth = 1);
      canvas.restore();

      const double r = 4;
      switch (l.verdict) {
        case LineVerdict.inside:
          canvas.drawCircle(c, r, Paint()..color = colour);
        case LineVerdict.out:
          final Paint cross = Paint()
            ..color = colour
            ..strokeWidth = 2
            ..strokeCap = StrokeCap.round;
          canvas.drawLine(c + const Offset(-r, -r), c + const Offset(r, r), cross);
          canvas.drawLine(c + const Offset(-r, r), c + const Offset(r, -r), cross);
        case LineVerdict.tooCloseToCall:
        case LineVerdict.unrecognized:
          canvas.drawCircle(
              c,
              r,
              Paint()
                ..color = colour
                ..style = PaintingStyle.stroke
                ..strokeWidth = 1.5);
        case null:
          // Not called (too few court taps): a small square, never a dot.
          canvas.drawRect(Rect.fromCenter(center: c, width: 5, height: 5),
              Paint()
                ..color = colour
                ..style = PaintingStyle.stroke
                ..strokeWidth = 1.2);
      }
      if (l.bounceId == selectedId) {
        canvas.drawCircle(
            c,
            r + 4,
            Paint()
              ..color = selectColor
              ..style = PaintingStyle.stroke
              ..strokeWidth = 1.5);
      }
    }
  }

  @override
  bool shouldRepaint(CourtMapPainter old) =>
      old.landings != landings ||
      old.selectedId != selectedId ||
      old.courtFill != courtFill ||
      old.inColor != inColor ||
      old.outColor != outColor;
}
