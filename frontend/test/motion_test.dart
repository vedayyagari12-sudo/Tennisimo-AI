import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/theme/app_theme.dart';
import 'package:tennisimo_ai/theme/motion.dart';
import 'package:tennisimo_ai/widgets/score_ring.dart';
import 'package:tennisimo_ai/widgets/skeleton_block.dart';
import 'package:tennisimo_ai/widgets/staggered_entrance.dart';

/// Mounts [child] on the real theme, optionally with the platform's
/// reduced-motion signals turned on.
Widget _host(
  Widget child, {
  bool disableAnimations = false,
  bool accessibleNavigation = false,
}) =>
    MaterialApp(
      theme: buildAppTheme(),
      home: MediaQuery(
        data: MediaQueryData(
          disableAnimations: disableAnimations,
          accessibleNavigation: accessibleNavigation,
        ),
        child: Scaffold(body: Center(child: child)),
      ),
    );

void main() {
  group('reduced-motion signals', () {
    testWidgets('either signal alone is enough to stand motion down',
        (WidgetTester tester) async {
      final List<bool> seen = <bool>[];
      Widget probe() => Builder(
            builder: (BuildContext context) {
              seen.add(prefersReducedMotion(context));
              return const SizedBox.shrink();
            },
          );

      await tester.pumpWidget(_host(probe()));
      await tester.pumpWidget(_host(probe(), disableAnimations: true));
      await tester.pumpWidget(_host(probe(), accessibleNavigation: true));

      expect(seen, <bool>[false, true, true]);
    });

    testWidgets('motionDuration collapses to zero, it does not just shorten',
        (WidgetTester tester) async {
      late Duration normal;
      late Duration reduced;
      await tester.pumpWidget(_host(Builder(
        builder: (BuildContext context) {
          normal = motionDuration(context, kScoreRingDuration);
          return const SizedBox.shrink();
        },
      )));
      await tester.pumpWidget(_host(
        Builder(
          builder: (BuildContext context) {
            reduced = motionDuration(context, kScoreRingDuration);
            return const SizedBox.shrink();
          },
        ),
        disableAnimations: true,
      ));

      expect(normal, kScoreRingDuration);
      expect(reduced, Duration.zero);
    });
  });

  group('ScoreRing count-up', () {
    testWidgets('a real score counts up and lands on its own value',
        (WidgetTester tester) async {
      await tester.pumpWidget(_host(const ScoreRing(score: 72.4)));

      // First frame: the sweep has not started, so the numeral is a real 0.
      expect(find.text('0'), findsOneWidget);
      expect(find.text('72'), findsNothing);

      await tester.pump(kScoreRingDuration ~/ 2);
      final String midway = tester.widget<Text>(find.byType(Text).first).data!;
      expect(int.parse(midway), greaterThan(0));
      expect(int.parse(midway), lessThan(72));

      await tester.pumpAndSettle();
      expect(find.text('72'), findsOneWidget);
    });

    testWidgets('under reduced motion the final score is on the first frame',
        (WidgetTester tester) async {
      await tester.pumpWidget(
        _host(const ScoreRing(score: 72.4), disableAnimations: true),
      );

      expect(find.text('72'), findsOneWidget);
      expect(find.text('0'), findsNothing);
      // Nothing is left running to settle.
      expect(tester.binding.transientCallbackCount, 0);
    });

    testWidgets('a null score animates nothing and never shows a zero',
        (WidgetTester tester) async {
      await tester.pumpWidget(_host(const ScoreRing(score: null)));

      expect(find.text('\u2014'), findsOneWidget);
      expect(find.text('0'), findsNothing);
      // No sweep towards a value that does not exist.
      expect(tester.binding.transientCallbackCount, 0);
    });

    testWidgets('a real 0.0 is a measurement, so it renders as 0',
        (WidgetTester tester) async {
      await tester.pumpWidget(_host(const ScoreRing(score: 0)));
      await tester.pumpAndSettle();

      expect(find.text('0'), findsOneWidget);
      expect(find.text('\u2014'), findsNothing);
    });
  });

  group('StaggeredEntrance', () {
    testWidgets('items arrive one after another, later ones later',
        (WidgetTester tester) async {
      await tester.pumpWidget(_host(Column(
        children: const <Widget>[
          StaggeredEntrance(index: 0, child: Text('first')),
          StaggeredEntrance(index: 4, child: Text('fifth')),
        ],
      )));

      double opacityOf(String text) => tester
          .widget<Opacity>(
            find.ancestor(of: find.text(text), matching: find.byType(Opacity)),
          )
          .opacity;

      await tester.pump(kEntranceStagger);
      expect(opacityOf('first'), greaterThan(opacityOf('fifth')));

      await tester.pumpAndSettle();
      expect(opacityOf('first'), 1.0);
      expect(opacityOf('fifth'), 1.0);
    });

    testWidgets('reduced motion removes the animation entirely',
        (WidgetTester tester) async {
      await tester.pumpWidget(_host(
        const StaggeredEntrance(index: 4, child: Text('row')),
        disableAnimations: true,
      ));

      expect(find.text('row'), findsOneWidget);
      expect(find.byType(TweenAnimationBuilder<double>), findsNothing);
      expect(tester.binding.transientCallbackCount, 0);
    });
  });

  group('SkeletonBlock', () {
    testWidgets('the shimmer runs while loading', (WidgetTester tester) async {
      await tester.pumpWidget(_host(const SkeletonBlock(height: 40)));
      await tester.pump(const Duration(milliseconds: 100));

      expect(tester.binding.transientCallbackCount, greaterThan(0));
    });

    testWidgets('reduced motion leaves a plain static block',
        (WidgetTester tester) async {
      await tester.pumpWidget(
        _host(const SkeletonBlock(height: 40), disableAnimations: true),
      );
      await tester.pumpAndSettle();

      expect(tester.binding.transientCallbackCount, 0);
      final Container block = tester.widget<Container>(
        find.descendant(
          of: find.byType(SkeletonBlock),
          matching: find.byType(Container),
        ),
      );
      final BoxDecoration decoration = block.decoration! as BoxDecoration;
      expect(decoration.gradient, isNull);
      expect(decoration.color, isNotNull);
    });
  });
}
