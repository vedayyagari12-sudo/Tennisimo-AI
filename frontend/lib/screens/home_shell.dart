import 'package:flutter/material.dart';

import '../theme/app_theme.dart';
import 'dashboard_screen.dart';
import 'history_screen.dart';
import 'record_screen.dart';

/// The signed-in shell: Dashboard · Record · History.
///
/// Record is an ACTION, not a tab. Selecting it pushes [RecordScreen]
/// full-screen and puts the selection straight back where it was, so the
/// camera is only ever open on a screen the user deliberately opened —
/// embedding a live preview in a persistent tab would hold the camera while
/// they browsed their history.
class HomeShell extends StatefulWidget {
  const HomeShell({super.key});

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  static const int _dashboardTab = 0;
  static const int _recordTab = 1;
  static const int _historyTab = 2;

  int _selected = _dashboardTab;

  /// Lets the Record action tell the dashboard a new analysis may exist.
  final GlobalKey<DashboardScreenState> _dashboardKey =
      GlobalKey<DashboardScreenState>();

  Future<void> _onDestinationSelected(int index) async {
    if (index != _recordTab) {
      setState(() => _selected = index);
      return;
    }

    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (BuildContext context) => const RecordScreen(),
      ),
    );
    if (!mounted) return;
    // A swing may have been analysed while we were away.
    await _dashboardKey.currentState?.refresh();
  }

  @override
  Widget build(BuildContext context) {
    final ColorScheme scheme = Theme.of(context).colorScheme;

    return Scaffold(
      // IndexedStack keeps each tab's state and scroll position alive across
      // switches. Only the two real tabs live in it.
      body: IndexedStack(
        index: _selected == _historyTab ? 1 : 0,
        children: <Widget>[
          DashboardScreen(
            key: _dashboardKey,
            onSeeAllHistory: () => setState(() => _selected = _historyTab),
          ),
          const HistoryScreen(),
        ],
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _selected,
        onDestinationSelected: _onDestinationSelected,
        destinations: <Widget>[
          const NavigationDestination(
            icon: Icon(Icons.insights_outlined),
            selectedIcon: Icon(Icons.insights),
            label: 'Dashboard',
          ),
          NavigationDestination(
            // The primary action, styled as the one filled accent in the bar.
            icon: Container(
              width: 40,
              height: 40,
              decoration: BoxDecoration(
                color: scheme.primary,
                borderRadius: BorderRadius.circular(14),
              ),
              child: Icon(Icons.videocam, color: scheme.onPrimary, size: 22),
            ),
            label: 'Record',
          ),
          const NavigationDestination(
            icon: Icon(Icons.timeline_outlined),
            selectedIcon: Icon(Icons.timeline),
            label: 'History',
          ),
        ],
      ),
    );
  }
}

/// Shared vertical rhythm between the shell's two tabs.
const EdgeInsets kTabContentPadding = EdgeInsets.fromLTRB(
  AppSpacing.lg,
  AppSpacing.sm,
  AppSpacing.lg,
  AppSpacing.xxl,
);
