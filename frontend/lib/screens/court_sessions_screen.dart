import 'package:flutter/material.dart';

import '../models/match.dart';
import '../theme/app_theme.dart';
import '../widgets/content_width.dart';
import 'matches_screen.dart';

/// The Matches tab: full matches and rally sessions behind one switch.
///
/// Both are the same court analysis with a different `kind`, so they share a
/// tab instead of taking two of the five places in the bottom bar. Each list
/// keeps its own state and scroll position across switches.
class CourtSessionsScreen extends StatefulWidget {
  const CourtSessionsScreen({super.key});

  @override
  State<CourtSessionsScreen> createState() => _CourtSessionsScreenState();
}

class _CourtSessionsScreenState extends State<CourtSessionsScreen> {
  SessionKind _kind = SessionKind.match;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        bottom: false,
        child: Column(
          children: <Widget>[
            ContentWidth(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(
                  AppSpacing.lg,
                  AppSpacing.md,
                  AppSpacing.lg,
                  AppSpacing.xs,
                ),
                child: SizedBox(
                  width: double.infinity,
                  child: SegmentedButton<SessionKind>(
                    showSelectedIcon: false,
                    segments: const <ButtonSegment<SessionKind>>[
                      ButtonSegment<SessionKind>(
                        value: SessionKind.match,
                        icon: Icon(Icons.sports_tennis),
                        label: Text('Matches'),
                      ),
                      ButtonSegment<SessionKind>(
                        value: SessionKind.rally,
                        icon: Icon(Icons.swap_horiz),
                        label: Text('Rallies'),
                      ),
                    ],
                    selected: <SessionKind>{_kind},
                    onSelectionChanged: (Set<SessionKind> picked) =>
                        setState(() => _kind = picked.first),
                  ),
                ),
              ),
            ),
            Expanded(
              child: IndexedStack(
                index: _kind == SessionKind.match ? 0 : 1,
                children: const <Widget>[
                  MatchesScreen(),
                  MatchesScreen(kind: SessionKind.rally),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
