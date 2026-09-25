import 'package:flutter/material.dart';

/// Widest the app's content is ever laid out, in logical pixels.
///
/// 720 is chosen because the whole UI was designed at phone width: the cards,
/// the trend chart and the score ring all size themselves to their parent, and
/// stretching them to a 2560 px browser window turns a "Retry upload" button
/// into a full-bleed bar and a paragraph of coaching feedback into a single
/// unreadable line. 720 is a comfortable reading measure for the body copy,
/// still wide enough that the charts and two-up stat tiles get more room than
/// they ever had on a phone, and it matches the top of Material 3's *medium*
/// window class — i.e. roughly "a tablet in portrait", which is the widest this
/// layout was ever meant to look like.
const double kContentMaxWidth = 720;

/// Viewport width at or below which [ContentWidth] is a complete no-op.
///
/// 600 is Material 3's compact -> medium window boundary. Every phone viewport
/// in portrait — native app and phone *browser* alike — is below it, which is
/// what makes "the phone layout does not change" a structural guarantee here
/// rather than a hope: below this width [ContentWidth.build] returns its child
/// unwrapped, so the widget tree is byte-for-byte the tree that shipped before.
const double kContentWidthBreakpoint = 600;

/// Constrains content to [kContentMaxWidth] and centres it on wide viewports.
///
/// Wrap a screen's `body` (inside the [Scaffold], so the scaffold background
/// and any deliberately full-bleed chrome stay full-bleed). At or below
/// [kContentWidthBreakpoint] this widget adds NOTHING to the tree.
///
/// Gutters come out of the centring: above [kContentMaxWidth] the leftover
/// space is split evenly either side, and between the breakpoint and the max
/// width the screens' own horizontal padding is still the gutter, exactly as on
/// a phone.
class ContentWidth extends StatelessWidget {
  const ContentWidth({super.key, required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    if (MediaQuery.sizeOf(context).width <= kContentWidthBreakpoint) {
      return child;
    }
    return Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: kContentMaxWidth),
        child: child,
      ),
    );
  }
}
