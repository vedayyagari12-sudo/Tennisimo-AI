import 'package:flutter/material.dart';

/// A label that takes the row, with a short trailing text pinned to the far
/// edge.
///
/// `Expanded` + `Flexible` looks like it does this and does not: the two split
/// the free space by flex, so the "right-aligned" trailing text started at the
/// card's midline. Here the trailing text is laid out first at its own width,
/// capped at [maxTrailingFraction] of the row so it wraps rather than
/// overflowing at a large font scale, and the label gets everything left.
class SplitRow extends StatelessWidget {
  const SplitRow({
    super.key,
    required this.label,
    required this.trailing,
    this.gap = 8,
    this.maxTrailingFraction = 0.5,
    this.crossAxisAlignment = CrossAxisAlignment.center,
  });

  final Widget label;
  final Widget trailing;
  final double gap;
  final double maxTrailingFraction;
  final CrossAxisAlignment crossAxisAlignment;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (BuildContext context, BoxConstraints constraints) {
        return Row(
          crossAxisAlignment: crossAxisAlignment,
          children: <Widget>[
            Expanded(child: label),
            SizedBox(width: gap),
            ConstrainedBox(
              constraints: BoxConstraints(
                maxWidth: constraints.maxWidth * maxTrailingFraction,
              ),
              child: trailing,
            ),
          ],
        );
      },
    );
  }
}
