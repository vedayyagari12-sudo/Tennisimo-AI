import 'dart:math' as math;
import 'dart:ui' as ui;

import 'package:flutter/material.dart';

import '../models/court.dart';
import '../theme/app_theme.dart';
import 'app_card.dart';
import 'line_call_card.dart';
import 'rally_timeline.dart' show clockText;

/// Which landings the map shows (SESSION_MODE_PHASE2.md 8.1).
enum CourtMapSide { all, yours, opponent }

class CourtMapFilter {
  const CourtMapFilter({this.side = CourtMapSide.all, this.verdict, this.servesOnly = false, this.shotType});

  final CourtMapSide side;

  /// Null shows every verdict, including landings with no call.
  final LineVerdict? verdict;
  final bool servesOnly;

  /// A wire shot type such as `forehand`; null shows every shot. A landing
  /// with no shot type is shown only when this is null.
  final String? shotType;

  CourtMapFilter copyWith({
    CourtMapSide? side,
    LineVerdict? verdict,
    bool clearVerdict = false,
    bool? servesOnly,
    String? shotType,
    bool clearShotType = false,
  }) =>
      CourtMapFilter(
        side: side ?? this.side,
        verdict: clearVerdict ? null : (verdict ?? this.verdict),
        servesOnly: servesOnly ?? this.servesOnly,
        shotType: clearShotType ? null : (shotType ?? this.shotType),
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
    if (shotType != null && landing.shotType != shotType) return false;
    return true;
  }

  List<ShotLanding> apply(List<ShotLanding> landings) =>
      <ShotLanding>[for (final ShotLanding l in landings) if (accepts(l)) l];
}

/// "forehand" -> "Forehand", "two_handed_backhand" -> "Two handed backhand".
String shotTypeLabel(String wire) {
  final String words = wire.replaceAll('_', ' ').trim();
  if (words.isEmpty) return 'Other';
  return words[0].toUpperCase() + words.substring(1);
}

/// The shot types present, for the filter chips: serves have their own chip,
/// and a landing with no shot type adds none.
List<String> shotTypesIn(List<ShotLanding> landings) {
  final Set<String> types = <String>{
    for (final ShotLanding l in landings)
      if (l.shotType != null && !l.isServe && l.shotType != 'serve') l.shotType!,
  };
  return types.toList()..sort();
}

/// Zone grid edges, mirrored from `backend/app/analysis/court_model.py`.
const double kServiceLineFromNetM = 6.40;
const double kHalfLengthM = 11.885;
const double kMidDeepEdgeM = (kServiceLineFromNetM + kHalfLengthM) / 2;
const double kSinglesHalfWidthM = 4.115;
const double kWidthThirdM = 2 * kSinglesHalfWidthM / 3;

/// One cell of the placement grid: a half, a depth band, a width third.
typedef ZoneCell = (CourtEnd half, DepthZone depth, WidthZone width);

/// The cell a landing's point estimate falls in, or null outside the singles
/// court. Edges belong to the zone nearer the net and to the centre third,
/// as in the backend, so a ball on a line counts inside.
ZoneCell? zoneCellOf(CourtPoint p) {
  final double along = p.y.abs();
  if (along > kHalfLengthM || p.x.abs() > kSinglesHalfWidthM) return null;
  final DepthZone depth = along <= kServiceLineFromNetM
      ? DepthZone.short
      : along <= kMidDeepEdgeM
          ? DepthZone.mid
          : DepthZone.deep;
  final WidthZone width = p.x < -kWidthThirdM / 2
      ? WidthZone.left
      : p.x > kWidthThirdM / 2
          ? WidthZone.right
          : WidthZone.centre;
  return (p.y >= 0 ? CourtEnd.far : CourtEnd.near, depth, width);
}

/// Whole-percent share of each half's landings in each cell. The share is
/// of EVERY landing in that half, out ones included, so a half's cells add
/// up to at most 100 and long or wide balls are not hidden. Empty cells are
/// left out.
Map<ZoneCell, int> zonePercents(List<ShotLanding> landings) {
  final Map<CourtEnd, int> perHalf = <CourtEnd, int>{};
  final Map<ZoneCell, int> counts = <ZoneCell, int>{};
  for (final ShotLanding l in landings) {
    final CourtEnd half = l.courtXy.y >= 0 ? CourtEnd.far : CourtEnd.near;
    perHalf[half] = (perHalf[half] ?? 0) + 1;
    final ZoneCell? cell = zoneCellOf(l.courtXy);
    if (cell != null) counts[cell] = (counts[cell] ?? 0) + 1;
  }
  return <ZoneCell, int>{
    for (final MapEntry<ZoneCell, int> e in counts.entries)
      e.key: (e.value / perHalf[e.key.$1]! * 100).round(),
  };
}

/// How many shown landings have a measured path, for the copy under the map.
String pathsLine(List<ShotLanding> landings) {
  final int withPath = landings.where((ShotLanding l) => l.hitXy != null).length;
  if (withPath == 0) return 'No shot paths: where the hitter stood was not measured.';
  if (withPath == landings.length) return 'Lines run from where the hitter stood to where the ball landed.';
  return 'Lines run from where the hitter stood to where the ball landed '
      '($withPath of ${landings.length} shots measured).';
}

/// The court's own colours, the same in light and dark: the map is a picture
/// of a real green hard court, as in the redesign.
const Color kCourtGreen = Color(0xFF2F7D4F);
const Color kCourtRunoff = Color(0xFF276A43);
const Color kCourtIn = Color(0xFFD4F04C);
const Color kCourtOut = Color(0xFFFFA25E);
const Color kCourtNeutral = Color(0xFFDCE6DF);

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

class _CourtMapState extends State<CourtMap> with SingleTickerProviderStateMixin {
  late CourtMapFilter _filter = widget.filter;
  String? _selected;
  bool _paths = true;
  bool _zones = false;
  bool _trace = false;

  /// The entrance: ball flights draw themselves in, once.
  late final AnimationController _reveal =
      AnimationController(vsync: this, duration: const Duration(milliseconds: 900));
  bool _started = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_started) return;
    _started = true;
    if (MediaQuery.disableAnimationsOf(context)) {
      _reveal.value = 1;
    } else {
      _reveal.forward();
    }
  }

  @override
  void dispose() {
    _reveal.dispose();
    super.dispose();
  }

  @override
  void didUpdateWidget(CourtMap old) {
    super.didUpdateWidget(old);
    if (old.filter != widget.filter) _filter = widget.filter;
  }

  ShotLanding? _selectedLanding(List<ShotLanding> shown) {
    for (final ShotLanding l in shown) {
      if (l.bounceId == _selected) return l;
    }
    return null;
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
    final List<String> shotTypes = shotTypesIn(widget.landings);

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
          if (shotTypes.isNotEmpty) ...<Widget>[
            const SizedBox(height: AppSpacing.xs),
            Wrap(
              spacing: AppSpacing.sm,
              runSpacing: AppSpacing.xs,
              children: <Widget>[
                for (final String type in shotTypes)
                  chip(shotTypeLabel(type), _filter.shotType == type,
                      () => setState(() => _filter = _filter.shotType == type
                          ? _filter.copyWith(clearShotType: true)
                          : _filter.copyWith(shotType: type))),
              ],
            ),
          ],
          const SizedBox(height: AppSpacing.xs),
          Wrap(
            spacing: AppSpacing.sm,
            runSpacing: AppSpacing.xs,
            children: <Widget>[
              chip('Paths', _paths, () => setState(() => _paths = !_paths)),
              chip('Zones', _zones, () => setState(() => _zones = !_zones)),
              chip('Rally trace', _trace, () => setState(() => _trace = !_trace)),
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
                    child: ClipRRect(
                      borderRadius: BorderRadius.circular(AppSpacing.innerRadius),
                      child: GestureDetector(
                        behavior: HitTestBehavior.opaque,
                        onTapUp: (TapUpDetails d) => _tap(d.localPosition, size, shown),
                        child: AnimatedBuilder(
                          animation: _reveal,
                          builder: (BuildContext context, Widget? _) => CustomPaint(
                            key: const Key('court-map-canvas'),
                            size: size,
                            painter: CourtMapPainter(
                              landings: shown,
                              selectedId: _selected,
                              // The redesign's court: a fixed hard-court green
                              // with white lines in every theme, lime balls
                              // that landed in and orange rings that went out.
                              courtFill: kCourtGreen,
                              runoffFill: kCourtRunoff,
                              lineColor: const Color(0xFFFFFFFF),
                              inColor: kCourtIn,
                              outColor: kCourtOut,
                              neutralColor: kCourtNeutral,
                              selectColor: const Color(0xFFFFFFFF),
                              showPaths: _paths,
                              showTrace: _trace,
                              reveal: _reveal.value,
                              labelStyle: theme.textTheme.labelSmall?.copyWith(
                                  color: const Color(0xB3FFFFFF), letterSpacing: 1.6),
                              zones: _zones ? zonePercents(shown) : null,
                              zoneColor: kCourtIn,
                              zoneTextStyle: theme.textTheme.labelSmall?.copyWith(color: const Color(0xFFFFFFFF)),
                            ),
                          ),
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
        _MapLegend(landings: shown),
        if (_selectedLanding(shown) != null) ...<Widget>[
          const SizedBox(height: AppSpacing.md),
          _ShotDetail(landing: _selectedLanding(shown)!),
        ],
        const SizedBox(height: AppSpacing.md),
        Text(courtMapSummary(shown), style: theme.textTheme.bodyMedium),
        const SizedBox(height: AppSpacing.xs),
        Text(depthLine(shown), style: theme.textTheme.bodyMedium),
        if (_paths) ...<Widget>[
          const SizedBox(height: AppSpacing.xs),
          Text(pathsLine(shown), style: theme.textTheme.bodyMedium),
        ],
        const SizedBox(height: AppSpacing.xs),
        Text(kCourtMapFootnote,
            style: theme.textTheme.bodySmall?.copyWith(color: palette.onSurfaceVariant)),
      ],
    );
  }
}

/// One letter for a shot type, for the mark on the map: forehand F, any
/// backhand B, serve S, volley V, overhead O; otherwise the first letter.
/// Null when the shot type is not known (the mark then has no letter).
String? shotTypeLetter(ShotLanding landing) {
  if (landing.isServe) return 'S';
  final String? type = landing.shotType;
  if (type == null || type.isEmpty) return null;
  if (type.contains('backhand')) return 'B';
  if (type.contains('forehand')) return 'F';
  if (type.contains('volley')) return 'V';
  if (type.contains('overhead') || type.contains('smash')) return 'O';
  return type[0].toUpperCase();
}

/// "Forehand · landed in", for the detail card under the map.
String shotHeadline(ShotLanding l) {
  final String what = l.isServe
      ? 'Serve'
      : (l.shotType == null ? 'Shot' : shotTypeLabel(l.shotType!));
  final String how = switch (l.verdict) {
    LineVerdict.inside => 'landed in',
    LineVerdict.out => 'landed out',
    LineVerdict.tooCloseToCall => 'too close to call',
    _ => 'not called',
  };
  return '$what · $how';
}

/// The facts about one tapped shot, from numbers already measured.
class _ShotDetail extends StatelessWidget {
  const _ShotDetail({required this.landing});

  final ShotLanding landing;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final AppPalette palette = context.palette;
    final ShotLanding l = landing;
    final String? sentence = lineCallSentence(l.lineCall);
    final List<String> facts = <String>[
      'At ${clockText(l.timeS)}',
      if (l.shotIndexInRally != null) 'Shot ${l.shotIndexInRally} of its rally',
      '${l.depthZone.label} depth',
      if (l.widthZone != WidthZone.unrecognized) '${l.widthZone.wire[0].toUpperCase()}${l.widthZone.wire.substring(1)} third',
      if (l.hitterEnd == CourtEnd.near) 'Hit from your end',
      if (l.hitterEnd == CourtEnd.far) 'Hit from the far end',
      'Confidence ${(l.confidence * 100).round()}%',
    ];
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(shotHeadline(l), style: theme.textTheme.titleSmall),
          if (sentence != null) ...<Widget>[
            const SizedBox(height: AppSpacing.xs),
            Text(sentence, style: theme.textTheme.bodyMedium),
          ],
          const SizedBox(height: AppSpacing.sm),
          Wrap(
            spacing: AppSpacing.sm,
            runSpacing: AppSpacing.xs,
            children: <Widget>[
              for (final String fact in facts)
                DecoratedBox(
                  decoration: BoxDecoration(
                    color: theme.colorScheme.surfaceContainerHighest,
                    borderRadius: BorderRadius.circular(999),
                  ),
                  child: Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                    child: Text(fact, style: theme.textTheme.labelMedium?.copyWith(color: palette.onSurfaceVariant)),
                  ),
                ),
            ],
          ),
        ],
      ),
    );
  }
}

/// What each mark means. Shape carries the verdict; a letter carries the
/// shot type when it is known.
class _MapLegend extends StatelessWidget {
  const _MapLegend({required this.landings});

  final List<ShotLanding> landings;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final AppPalette palette = context.palette;
    final TextStyle? style = theme.textTheme.bodySmall?.copyWith(color: palette.onSurfaceVariant);
    final Map<String, String> letters = <String, String>{
      for (final ShotLanding l in landings)
        if (shotTypeLetter(l) != null)
          shotTypeLetter(l)!: l.isServe ? 'Serve' : shotTypeLabel(l.shotType!),
    };
    Widget item(CustomPainter mark, String text) => Row(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            CustomPaint(size: const Size(14, 14), painter: mark),
            const SizedBox(width: 6),
            Flexible(child: Text(text, style: style)),
          ],
        );
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Wrap(
          spacing: AppSpacing.md,
          runSpacing: AppSpacing.xs,
          children: <Widget>[
            item(const _LegendMark(_MarkShape.disc, kCourtIn), 'Landed in'),
            item(const _LegendMark(_MarkShape.cross, kCourtOut), 'Landed out'),
            item(_LegendMark(_MarkShape.ring, palette.onSurfaceVariant), 'Too close to call'),
            item(_LegendMark(_MarkShape.square, palette.onSurfaceVariant), 'Not called'),
          ],
        ),
        if (letters.isNotEmpty) ...<Widget>[
          const SizedBox(height: AppSpacing.xs),
          Text(
            <String>[for (final MapEntry<String, String> e in letters.entries) '${e.key} = ${e.value}'].join('   '),
            style: style,
          ),
        ],
      ],
    );
  }
}

enum _MarkShape { disc, cross, ring, square }

class _LegendMark extends CustomPainter {
  const _LegendMark(this.shape, this.colour);

  final _MarkShape shape;
  final Color colour;

  @override
  void paint(Canvas canvas, Size size) {
    final Offset c = size.center(Offset.zero);
    CourtMapPainter.paintMark(canvas, c, 5, colour, switch (shape) {
      _MarkShape.disc => LineVerdict.inside,
      _MarkShape.cross => LineVerdict.out,
      _MarkShape.ring => LineVerdict.tooCloseToCall,
      _MarkShape.square => null,
    });
  }

  @override
  bool shouldRepaint(_LegendMark old) => old.colour != colour || old.shape != shape;
}

/// Paints the court and the landings. Public for the hit test, like
/// `MixBarPainter.segmentAt`.
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
    this.showPaths = true,
    this.zones,
    this.zoneColor,
    this.zoneTextStyle,
    this.runoffFill,
    this.labelStyle,
    this.showTrace = false,
    this.reveal = 1.0,
  });

  final List<ShotLanding> landings;
  final String? selectedId;
  final Color courtFill;
  final Color lineColor;
  final Color inColor;
  final Color outColor;
  final Color neutralColor;
  final Color selectColor;

  /// Draw each measured shot path, hitter's feet to landing.
  final bool showPaths;

  /// Placement grid percents to shade, or null for no grid.
  final Map<ZoneCell, int>? zones;
  final Color? zoneColor;
  final TextStyle? zoneTextStyle;

  /// The run-off outside the court lines; defaults to [courtFill].
  final Color? runoffFill;

  /// For "FAR END", "NET" and "NEAR END"; null draws no labels.
  final TextStyle? labelStyle;

  /// Join each rally's landings in order with a dashed line.
  final bool showTrace;

  /// 0 to 1: how much of each ball flight is drawn, and how visible the
  /// marks are. 1 once the entrance has played.
  final double reveal;

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

  /// One mark, centred on [c]: the verdict as a shape, never by colour alone.
  /// In is a disc, out a cross, too close a ring, no call a square.
  static void paintMark(Canvas canvas, Offset c, double r, Color colour, LineVerdict? verdict) {
    switch (verdict) {
      case LineVerdict.inside:
        // A dark edge, as in the redesign, so the lime ball reads on the
        // green court and on a white card alike.
        canvas.drawCircle(c, r + 1.2, Paint()..color = const Color(0xFF142000));
        canvas.drawCircle(c, r, Paint()..color = colour);
      case LineVerdict.out:
        final Paint cross = Paint()
          ..color = colour
          ..strokeWidth = 2.4
          ..strokeCap = StrokeCap.round;
        canvas.drawLine(c + Offset(-r, -r), c + Offset(r, r), cross);
        canvas.drawLine(c + Offset(-r, r), c + Offset(r, -r), cross);
      case LineVerdict.tooCloseToCall:
      case LineVerdict.unrecognized:
        canvas.drawCircle(
            c,
            r,
            Paint()
              ..color = colour
              ..style = PaintingStyle.stroke
              ..strokeWidth = 1.8);
      case null:
        canvas.drawRect(
            Rect.fromCenter(center: c, width: r * 1.6, height: r * 1.6),
            Paint()
              ..color = colour
              ..style = PaintingStyle.stroke
              ..strokeWidth = 1.4);
    }
  }

  @override
  void paint(Canvas canvas, Size size) {
    final double scale = _pxPerM(size);
    Offset at(double x, double y) => toCanvas(CourtPoint(x, y), size);

    _paintCourt(canvas, size, scale, at);

    final Map<ZoneCell, int>? grid = zones;
    if (grid != null) _paintZones(canvas, size, grid);

    if (showTrace) _paintTrace(canvas, size);
    if (showPaths) {
      for (final ShotLanding l in landings) {
        if (l.bounceId != selectedId) {
          _paintFlight(canvas, size, l, selected: false);
        }
      }
      for (final ShotLanding l in landings) {
        if (l.bounceId == selectedId) {
          _paintFlight(canvas, size, l, selected: true);
        }
      }
    }

    final double fade = Curves.easeOut.transform(reveal.clamp(0.0, 1.0));
    for (final ShotLanding l in landings) {
      final Color colour = _colourOf(l);
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
      canvas.drawOval(oval, Paint()..color = colour.withValues(alpha: 0.14 * fade));
      canvas.drawOval(
          oval,
          Paint()
            ..color = colour.withValues(alpha: 0.45 * fade)
            ..style = PaintingStyle.stroke
            ..strokeWidth = 1);
      canvas.restore();

      final String? letter = shotTypeLetter(l);
      final double r = letter == null ? 4.5 : 7.5;
      final double grow = 0.4 + 0.6 * fade;
      if (l.bounceId == selectedId) {
        canvas.drawCircle(
            c,
            r + 6,
            Paint()
              ..color = selectColor
              ..style = PaintingStyle.stroke
              ..strokeWidth = 1.5);
      }
      paintMark(canvas, c, r * grow, colour.withValues(alpha: fade), l.verdict);
      if (letter != null && fade > 0.6) {
        _paintLetter(canvas, c, r, letter, colour, l.verdict);
      }
    }
  }

  /// A small bold label on the map, in the theme's font (the style passed as
  /// ``labelStyle``) without its letter spacing.
  TextStyle _markText(Color color, double size, FontWeight weight) =>
      (labelStyle ?? const TextStyle()).copyWith(
        color: color,
        fontSize: size,
        fontWeight: weight,
        letterSpacing: 0,
        height: 1,
      );

  void _paintLetter(Canvas canvas, Offset c, double r, String letter, Color colour, LineVerdict? verdict) {
    final bool onDisc = verdict == LineVerdict.inside;
    final Color ink = onDisc
        ? (colour.computeLuminance() > 0.5 ? const Color(0xDE000000) : const Color(0xFFFFFFFF))
        : selectColor;
    final TextPainter text = TextPainter(
      text: TextSpan(
        text: letter,
        style: _markText(ink, onDisc ? 9.5 : 9, FontWeight.w800),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    // A cross has no room inside: its letter sits beside it.
    final Offset centre = verdict == LineVerdict.out ? c + Offset(r + 3, -r - 1) : c;
    text.paint(canvas, centre - Offset(text.width / 2, text.height / 2));
    text.dispose();
  }

  /// The court, drawn as a hard court from above: run-off, court, tinted
  /// doubles alleys and service boxes, crisp white lines, and a net with posts.
  void _paintCourt(Canvas canvas, Size size, double scale, Offset Function(double, double) at) {
    canvas.drawRect(Offset.zero & size, Paint()..color = runoffFill ?? courtFill);
    final Rect doubles = Rect.fromPoints(at(-_doublesX, _baselineY), at(_doublesX, -_baselineY));
    canvas.drawRect(doubles, Paint()..color = courtFill);
    // Alleys: the strips between the singles and doubles sidelines.
    final Paint alley = Paint()..color = lineColor.withValues(alpha: 0.07);
    canvas.drawRect(Rect.fromPoints(at(-_doublesX, _baselineY), at(-_singlesX, -_baselineY)), alley);
    canvas.drawRect(Rect.fromPoints(at(_singlesX, _baselineY), at(_doublesX, -_baselineY)), alley);
    // Service boxes, one shade lighter.
    final Paint boxes = Paint()..color = lineColor.withValues(alpha: 0.05);
    canvas.drawRect(Rect.fromPoints(at(-_singlesX, _serviceY), at(_singlesX, -_serviceY)), boxes);

    final double w = math.max(1.2, 0.07 * scale);
    final Paint line = Paint()
      ..color = lineColor
      ..style = PaintingStyle.stroke
      ..strokeWidth = w
      ..strokeJoin = StrokeJoin.round;
    canvas.drawRect(doubles, line);
    for (final double x in <double>[-_singlesX, _singlesX]) {
      canvas.drawLine(at(x, _baselineY), at(x, -_baselineY), line);
    }
    for (final double y in <double>[_serviceY, -_serviceY]) {
      canvas.drawLine(at(-_singlesX, y), at(_singlesX, y), line);
    }
    canvas.drawLine(at(0, _serviceY), at(0, -_serviceY), line);
    for (final double y in <double>[_baselineY, -_baselineY]) {
      canvas.drawLine(at(0, y), at(0, y > 0 ? y - 0.3 : y + 0.3), line);
    }

    // The net: a mesh band, a taut top cord, and a post each side.
    final Offset left = at(-_doublesX - 0.9, 0);
    final Offset right = at(_doublesX + 0.9, 0);
    canvas.drawLine(left, right, Paint()
      ..color = lineColor.withValues(alpha: 0.25)
      ..strokeWidth = 0.28 * scale);
    for (double x = left.dx + 3; x < right.dx; x += 4) {
      canvas.drawLine(Offset(x, left.dy - 0.13 * scale), Offset(x, left.dy + 0.13 * scale),
          Paint()
            ..color = lineColor.withValues(alpha: 0.35)
            ..strokeWidth = 0.6);
    }
    canvas.drawLine(left, right, Paint()
      ..color = lineColor
      ..strokeWidth = w * 1.6);
    for (final Offset post in <Offset>[left, right]) {
      canvas.drawCircle(post, math.max(2.2, 0.16 * scale), Paint()..color = lineColor);
    }

    final TextStyle? style = labelStyle;
    if (style != null) {
      void label(String text, Offset centre) {
        final TextPainter p = TextPainter(
          text: TextSpan(text: text, style: style),
          textDirection: TextDirection.ltr,
          maxLines: 1,
        )..layout(maxWidth: size.width);
        p.paint(canvas, centre - Offset(p.width / 2, p.height / 2));
        p.dispose();
      }

      label('FAR END', Offset(size.width / 2, (at(0, _baselineY).dy) / 2));
      label('NEAR END', Offset(size.width / 2, (at(0, -_baselineY).dy + size.height) / 2));
    }
  }

  /// A ball flight from where the hitter stood to where it landed, drawn as
  /// a curved arc that fades in towards the landing, with an arrowhead.
  /// Only when the hitter's position was measured: no guess is drawn.
  void _paintFlight(Canvas canvas, Size size, ShotLanding l, {required bool selected}) {
    final CourtPoint? from = l.hitXy;
    if (from == null) return;
    final Color colour = _colourOf(l);
    final Offset a = toCanvas(from, size);
    final Offset b = toCanvas(l.courtXy, size);
    final Offset d = b - a;
    final double length = d.distance;
    if (length < 4) return;
    final Offset normal = Offset(-d.dy, d.dx) / length;
    final Offset control = (a + b) / 2 + normal * length * 0.14;
    final Path arc = Path()
      ..moveTo(a.dx, a.dy)
      ..quadraticBezierTo(control.dx, control.dy, b.dx, b.dy);
    final ui.PathMetric metric = arc.computeMetrics().first;
    final Path drawn = metric.extractPath(0, metric.length * Curves.easeOut.transform(reveal.clamp(0.0, 1.0)));
    final double alpha = selected ? 0.95 : 0.55;
    canvas.drawPath(
      drawn,
      Paint()
        ..shader = ui.Gradient.linear(
            a, b, <Color>[colour.withValues(alpha: 0.05), colour.withValues(alpha: alpha)])
        ..style = PaintingStyle.stroke
        ..strokeWidth = selected ? 2.6 : 1.6
        ..strokeCap = StrokeCap.round,
    );
    // Where the hitter stood: a small ring, never a landing shape.
    canvas.drawCircle(
        a,
        3,
        Paint()
          ..color = neutralColor
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1.2);
    if (reveal >= 0.95) {
      // An arrowhead just before the landing mark, along the arc's end.
      final Offset tangent = (b - control) / (b - control).distance;
      final Offset tip = b - tangent * 9;
      final Offset side = Offset(-tangent.dy, tangent.dx) * 3.2;
      canvas.drawPath(
        Path()
          ..moveTo(tip.dx, tip.dy)
          ..lineTo((tip - tangent * 5 + side).dx, (tip - tangent * 5 + side).dy)
          ..lineTo((tip - tangent * 5 - side).dx, (tip - tangent * 5 - side).dy)
          ..close(),
        Paint()..color = colour.withValues(alpha: selected ? 0.95 : 0.7),
      );
    }
  }

  /// Each rally's landings joined in time order by a dashed line. The tapped
  /// shot's rally is drawn solid and brighter, numbered by shot.
  void _paintTrace(Canvas canvas, Size size) {
    final Map<String, List<ShotLanding>> rallies = <String, List<ShotLanding>>{};
    for (final ShotLanding l in landings) {
      final String? id = l.rallyId;
      if (id != null) (rallies[id] ??= <ShotLanding>[]).add(l);
    }
    String? selectedRally;
    for (final ShotLanding l in landings) {
      if (l.bounceId == selectedId) selectedRally = l.rallyId;
    }
    for (final MapEntry<String, List<ShotLanding>> e in rallies.entries) {
      if (e.value.length < 2) continue;
      final List<ShotLanding> ordered = <ShotLanding>[...e.value]
        ..sort((ShotLanding x, ShotLanding y) => x.timeS.compareTo(y.timeS));
      final bool current = e.key == selectedRally;
      final Path path = Path();
      for (int i = 0; i < ordered.length; i++) {
        final Offset p = toCanvas(ordered[i].courtXy, size);
        i == 0 ? path.moveTo(p.dx, p.dy) : path.lineTo(p.dx, p.dy);
      }
      final Paint paint = Paint()
        ..color = selectColor.withValues(alpha: current ? 0.8 : 0.28)
        ..style = PaintingStyle.stroke
        ..strokeWidth = current ? 1.8 : 1.1;
      for (final ui.PathMetric m in path.computeMetrics()) {
        double at = 0;
        while (at < m.length) {
          canvas.drawPath(m.extractPath(at, math.min(at + 5, m.length)), paint);
          at += 9;
        }
      }
      if (current) {
        for (int i = 0; i < ordered.length; i++) {
          final Offset p = toCanvas(ordered[i].courtXy, size) + const Offset(9, 9);
          final TextPainter text = TextPainter(
            text: TextSpan(text: '${i + 1}', style: _markText(selectColor, 10, FontWeight.w700)),
            textDirection: TextDirection.ltr,
          )..layout();
          text.paint(canvas, p - Offset(text.width / 2, text.height / 2));
          text.dispose();
        }
      }
    }
  }

  Color _colourOf(ShotLanding l) => switch (l.verdict) {
        LineVerdict.inside => inColor,
        LineVerdict.out => outColor,
        _ => neutralColor,
      };

  /// The singles-court rectangle of one cell, in court metres.
  static Rect cellRectM(ZoneCell cell) {
    final (CourtEnd half, DepthZone depth, WidthZone width) = cell;
    final (double near, double far) = switch (depth) {
      DepthZone.short => (0, kServiceLineFromNetM),
      DepthZone.mid => (kServiceLineFromNetM, kMidDeepEdgeM),
      _ => (kMidDeepEdgeM, kHalfLengthM),
    };
    final (double left, double right) = switch (width) {
      WidthZone.left => (-kSinglesHalfWidthM, -kWidthThirdM / 2),
      WidthZone.right => (kWidthThirdM / 2, kSinglesHalfWidthM),
      _ => (-kWidthThirdM / 2, kWidthThirdM / 2),
    };
    final double sign = half == CourtEnd.near ? -1 : 1;
    return Rect.fromLTRB(left, sign * near, right, sign * far);
  }

  void _paintZones(Canvas canvas, Size size, Map<ZoneCell, int> grid) {
    final Color fill = zoneColor ?? inColor;
    for (final MapEntry<ZoneCell, int> e in grid.entries) {
      final Rect m = cellRectM(e.key);
      final Rect r = Rect.fromPoints(
        toCanvas(CourtPoint(m.left, m.top), size),
        toCanvas(CourtPoint(m.right, m.bottom), size),
      ).deflate(1);
      canvas.drawRect(r, Paint()..color = fill.withValues(alpha: 0.08 + 0.5 * e.value / 100));
      final TextPainter text = TextPainter(
        text: TextSpan(text: '${e.value}%', style: zoneTextStyle),
        textDirection: TextDirection.ltr,
        maxLines: 1,
      )..layout(maxWidth: r.width);
      if (text.height <= r.height) {
        text.paint(canvas, r.center - Offset(text.width / 2, text.height / 2));
      }
      text.dispose();
    }
  }

  @override
  bool shouldRepaint(CourtMapPainter old) =>
      old.showPaths != showPaths ||
      old.showTrace != showTrace ||
      old.reveal != reveal ||
      old.zones != zones ||
      old.landings != landings ||
      old.selectedId != selectedId ||
      old.courtFill != courtFill ||
      old.runoffFill != runoffFill ||
      old.lineColor != lineColor ||
      old.inColor != inColor ||
      old.outColor != outColor;
}
