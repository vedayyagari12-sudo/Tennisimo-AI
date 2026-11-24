import 'dart:async';

import 'package:flutter/material.dart';

import '../services/haptics.dart';
import '../services/last_seen.dart';
import '../theme/app_theme.dart';
import 'dashboard_screen.dart';
import 'history_screen.dart';
import 'matches_screen.dart';
import 'profile_screen.dart';
import 'record_screen.dart';

/// The signed-in shell: Home · History · Record · Matches · Profile.
///
/// Matches holds both matches and rallies behind a switch at its top, which
/// keeps the bar to five destinations with Record in the middle.
///
/// Record is an ACTION, not a tab. Selecting it pushes [RecordScreen]
/// full-screen and puts the selection straight back where it was, so the
/// camera is only ever open on a screen the user deliberately opened —
/// embedding a live preview in a persistent tab would hold the camera while
/// they browsed their history.
class HomeShell extends StatefulWidget {
  const HomeShell({super.key, this.lastSeenRecorder});

  /// Test seam only; the app passes nothing and gets a real
  /// [LastSeenRecorder].
  final LastSeenRecorder? lastSeenRecorder;

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> with WidgetsBindingObserver {
  static const int _dashboardTab = 0;
  static const int _historyTab = 1;
  static const int _recordTab = 2;
  static const int _matchesTab = 3;
  static const int _profileTab = 4;

  int _selected = _dashboardTab;

  /// Lets the Record action tell the dashboard a new analysis may exist.
  final GlobalKey<DashboardScreenState> _dashboardKey =
      GlobalKey<DashboardScreenState>();

  late final LastSeenRecorder _lastSeen;

  @override
  void initState() {
    super.initState();
    _lastSeen = widget.lastSeenRecorder ?? LastSeenRecorder();
    WidgetsBinding.instance.addObserver(this);
    // The shell is only built for a signed-in user, so this is an app open:
    // a cold start with a restored session, or a fresh sign-in.
    // Fire-and-forget: the recorder never throws and is never awaited.
    unawaited(_lastSeen.record());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      unawaited(_lastSeen.recordIfDue());
    }
  }

  Future<void> _onDestinationSelected(int index) async {
    if (index != _recordTab) {
      if (index != _selected) unawaited(haptics.tabSelected());
      setState(() => _selected = index);
      return;
    }

    // Opening the camera is the app's primary action, so it gets the same
    // tick as a tab change rather than passing silently.
    unawaited(haptics.tabSelected());

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
    final AppPalette palette = context.palette;

    return Scaffold(
      // IndexedStack keeps each tab's state and scroll position alive across
      // switches. Record is an action, so it has no page here.
      body: IndexedStack(
        index: _selected == _historyTab
            ? 1
            : _selected == _matchesTab
                ? 2
                : _selected == _profileTab
                    ? 3
                    : 0,
        children: <Widget>[
          DashboardScreen(
            key: _dashboardKey,
            onSeeAllHistory: () => setState(() => _selected = _historyTab),
            onOpenProfile: () => setState(() => _selected = _profileTab),
          ),
          const HistoryScreen(),
          const MatchesHubScreen(),
          const ProfileScreen(),
        ],
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _selected,
        onDestinationSelected: _onDestinationSelected,
        destinations: <Widget>[
          const NavigationDestination(
            icon: Icon(Icons.home_outlined),
            selectedIcon: Icon(Icons.home),
            label: 'Home',
          ),
          const NavigationDestination(
            icon: Icon(Icons.timeline_outlined),
            selectedIcon: Icon(Icons.timeline),
            label: 'History',
          ),
          NavigationDestination(
            // The primary action, styled as the one filled accent in the bar.
            icon: Container(
              width: 40,
              height: 40,
              decoration: BoxDecoration(
                color: palette.recordAction,
                borderRadius: BorderRadius.circular(14),
              ),
              child: Icon(
                Icons.videocam,
                color: palette.onRecordAction,
                size: 22,
              ),
            ),
            label: 'Record',
          ),
          const NavigationDestination(
            icon: Icon(Icons.sports_tennis_outlined),
            selectedIcon: Icon(Icons.sports_tennis),
            label: 'Matches',
          ),
          const NavigationDestination(
            icon: Icon(Icons.person_outline),
            selectedIcon: Icon(Icons.person),
            label: 'Profile',
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
