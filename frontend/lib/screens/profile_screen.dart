import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../services/account_deletion.dart';
import '../services/haptics.dart';
import '../theme/app_theme.dart';
import '../theme/theme_controller.dart';
import '../widgets/app_card.dart';
import '../widgets/content_width.dart';
import '../widgets/delete_account_dialog.dart';
import 'dashboard_screen.dart' show DashboardDataSource;
import 'home_shell.dart' show kTabContentPadding;
import 'login_screen.dart';
import 'privacy_policy_screen.dart';

/// The Profile tab: the signed-in account, preferences, help and legal, and
/// the account actions (sign out, delete account).
///
/// Sign out and Delete account are also in the Home screen's overflow menu;
/// both places run the same dialog and the same steps.
class ProfileScreen extends StatefulWidget {
  const ProfileScreen({super.key, this.dataSource = const DashboardDataSource()});

  /// Always the default in the app; see [DashboardDataSource].
  final DashboardDataSource dataSource;

  @override
  State<ProfileScreen> createState() => _ProfileScreenState();
}

class _ProfileScreenState extends State<ProfileScreen> {
  static const Map<ThemeMode, String> _themeLabels = <ThemeMode, String>{
    ThemeMode.system: 'System',
    ThemeMode.light: 'Light',
    ThemeMode.dark: 'Dark',
  };

  Future<void> _setHaptics(bool on) async {
    setState(() => haptics.enabled = on);
    final SharedPreferences prefs = await SharedPreferences.getInstance();
    await prefs.setBool(kHapticsEnabledPrefKey, on);
  }

  /// Same steps as the Home menu's entry: confirm in the dialog (which sends
  /// the request itself), then leave for the login screen with a notice.
  Future<void> _deleteAccount() async {
    final bool? deleted = await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (BuildContext context) =>
          DeleteAccountDialog(onDelete: widget.dataSource.deleteAccount),
    );
    if (deleted != true || !mounted) return;
    LoginNotice.post(kAccountDeletedNotice);
    Navigator.of(context).popUntil((Route<dynamic> route) => route.isFirst);
    try {
      await widget.dataSource.signOut();
    } catch (_) {
      // The server already deleted the user; the local session is gone.
    }
  }

  void _showFilmingTips() {
    unawaited(showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      isScrollControlled: true,
      builder: (BuildContext context) => const _FilmingTipsSheet(),
    ));
  }

  void _showSupport() {
    unawaited(showDialog<void>(
      context: context,
      builder: (BuildContext context) => AlertDialog(
        title: const Text('Contact support'),
        content: const Text(
          'Email us at $kSupportEmail and say what happened. A screenshot helps.',
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () {
              unawaited(Clipboard.setData(const ClipboardData(text: kSupportEmail)));
              Navigator.of(context).pop();
              ScaffoldMessenger.of(this.context).showSnackBar(
                const SnackBar(content: Text('Email address copied')),
              );
            },
            child: const Text('Copy email'),
          ),
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('Close'),
          ),
        ],
      ),
    ));
  }

  void _showTerms() {
    unawaited(showDialog<void>(
      context: context,
      builder: (BuildContext context) => AlertDialog(
        title: const Text('Terms of service'),
        content: const Text(
          'The full terms are not published yet. Questions about using '
          'Tennisimo: $kSupportEmail.',
        ),
        actions: <Widget>[
          TextButton(onPressed: () => Navigator.of(context).pop(), child: const Text('Close')),
        ],
      ),
    ));
  }

  void _showPermissions() {
    unawaited(showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      builder: (BuildContext context) => const _PermissionsSheet(),
    ));
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final ThemeController? themeController = ThemeScope.maybeOf(context);
    final String? email = widget.dataSource.accountEmail;
    final String initial = (email == null || email.isEmpty) ? '?' : email[0].toUpperCase();
    final bool showHaptics = !haptics.isSilent;
    final Color destructive = theme.colorScheme.error;

    return Scaffold(
      body: SafeArea(
        bottom: false,
        child: ContentWidth(
          child: ListView(
            padding: kTabContentPadding,
            children: <Widget>[
              Text(
                'Profile',
                style: theme.textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w800, letterSpacing: -0.5),
              ),
              const SizedBox(height: AppSpacing.lg),
              AppCard(
                child: Row(
                  children: <Widget>[
                    CircleAvatar(
                      radius: 28,
                      backgroundColor: theme.colorScheme.primaryContainer,
                      child: Text(
                        initial,
                        style: theme.textTheme.titleLarge?.copyWith(
                          color: theme.colorScheme.primary,
                          fontWeight: FontWeight.w800,
                        ),
                      ),
                    ),
                    const SizedBox(width: AppSpacing.md),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: <Widget>[
                          Text('Signed in', style: theme.textTheme.titleMedium),
                          if (email != null && email.isNotEmpty)
                            Text(
                              email,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: theme.textTheme.bodyMedium
                                  ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                            ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
              if (themeController != null || showHaptics) ...<Widget>[
                const _GroupLabel('Preferences'),
                _Group(
                  children: <Widget>[
                    if (themeController != null)
                      ListTile(
                        leading: const _RowIcon(Icons.contrast),
                        title: const Text('Appearance'),
                        trailing: DropdownButton<ThemeMode>(
                          value: themeController.mode,
                          underline: const SizedBox.shrink(),
                          onChanged: (ThemeMode? mode) {
                            if (mode != null) unawaited(themeController.setMode(mode));
                          },
                          items: <DropdownMenuItem<ThemeMode>>[
                            for (final MapEntry<ThemeMode, String> entry in _themeLabels.entries)
                              DropdownMenuItem<ThemeMode>(value: entry.key, child: Text(entry.value)),
                          ],
                        ),
                      ),
                    if (showHaptics)
                      SwitchListTile(
                        secondary: const _RowIcon(Icons.vibration),
                        title: const Text('Haptics'),
                        value: haptics.enabled,
                        onChanged: (bool on) => unawaited(_setHaptics(on)),
                      ),
                  ],
                ),
              ],
              const _GroupLabel('Help & legal'),
              _Group(
                children: <Widget>[
                  ListTile(
                    leading: const _RowIcon(Icons.videocam_outlined),
                    title: const Text('How to film a good clip'),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: _showFilmingTips,
                  ),
                  ListTile(
                    leading: const _RowIcon(Icons.mail_outline),
                    title: const Text('Contact support'),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: _showSupport,
                  ),
                  ListTile(
                    leading: const _RowIcon(Icons.shield_outlined),
                    title: const Text('Privacy policy'),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: () => unawaited(Navigator.of(context).push(
                      MaterialPageRoute<void>(builder: (BuildContext context) => const PrivacyPolicyScreen()),
                    )),
                  ),
                  ListTile(
                    leading: const _RowIcon(Icons.gavel_outlined),
                    title: const Text('Terms of service'),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: _showTerms,
                  ),
                  ListTile(
                    leading: const _RowIcon(Icons.lock_outline),
                    title: const Text('Data & permissions'),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: _showPermissions,
                  ),
                  ListTile(
                    leading: const _RowIcon(Icons.code),
                    title: const Text('Open-source licences'),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: () => showLicensePage(context: context, applicationName: 'Tennisimo'),
                  ),
                ],
              ),
              const _GroupLabel('Account'),
              _Group(
                children: <Widget>[
                  ListTile(
                    leading: const _RowIcon(Icons.logout),
                    title: const Text('Sign out'),
                    onTap: () => unawaited(widget.dataSource.signOut()),
                  ),
                  ListTile(
                    leading: _RowIcon(Icons.delete_outline, color: destructive, tint: const Color(0xFFFDECEC)),
                    title: Text('Delete account', style: TextStyle(color: destructive)),
                    onTap: () => unawaited(_deleteAccount()),
                  ),
                ],
              ),
              const SizedBox(height: AppSpacing.md),
              Text(
                'Tennisimo $kAppVersion',
                textAlign: TextAlign.center,
                style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _GroupLabel extends StatelessWidget {
  const _GroupLabel(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(AppSpacing.xs, AppSpacing.xl, AppSpacing.xs, AppSpacing.sm),
      child: Text(
        text.toUpperCase(),
        style: theme.textTheme.labelSmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
      ),
    );
  }
}

/// A card of list rows separated by hairlines.
class _Group extends StatelessWidget {
  const _Group({required this.children});

  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return AppCard(
      padding: EdgeInsets.zero,
      child: Column(
        children: <Widget>[
          for (int i = 0; i < children.length; i++) ...<Widget>[
            if (i > 0) const Divider(height: 1),
            children[i],
          ],
        ],
      ),
    );
  }
}

/// The same set-up advice the Record screen shows before filming.
class _FilmingTipsSheet extends StatelessWidget {
  const _FilmingTipsSheet();

  static const List<String> _tips = <String>[
    'Film side-on: the camera to your side, not in front of or behind you.',
    'Keep the phone level. Do not tilt it down more than a few degrees.',
    'Stand the phone 5–10 m to the side, on a tripod or a steady surface.',
    'Keep your whole body in frame, and hit down the line.',
    'Optional: recording at 60 fps can sharpen the ball-speed reading. Any frame rate works.',
  ];

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(AppSpacing.xl, 0, AppSpacing.xl, AppSpacing.xl),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text('How to film a good clip', style: theme.textTheme.titleLarge),
            const SizedBox(height: AppSpacing.md),
            for (final String tip in _tips)
              Padding(
                padding: const EdgeInsets.only(bottom: AppSpacing.sm),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    const Text('•  '),
                    Expanded(child: Text(tip, style: theme.textTheme.bodyMedium)),
                  ],
                ),
              ),
            const SizedBox(height: AppSpacing.sm),
            Text(
              'For matches and rallies, film from behind the baseline instead: '
              'the New match screen has those tips.',
              style: theme.textTheme.bodySmall,
            ),
          ],
        ),
      ),
    );
  }
}

/// The app's version, as in pubspec.yaml. Update the two together.
const String kAppVersion = '1.0.0';

/// The redesign's row icon: a small rounded tile behind the glyph.
class _RowIcon extends StatelessWidget {
  const _RowIcon(this.icon, {this.color, this.tint});

  final IconData icon;
  final Color? color;
  final Color? tint;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Container(
      width: 32,
      height: 32,
      decoration: BoxDecoration(
        color: tint ?? theme.colorScheme.surfaceContainerHigh,
        borderRadius: BorderRadius.circular(10),
      ),
      child: Icon(icon, size: 18, color: color ?? theme.colorScheme.onSurfaceVariant),
    );
  }
}

/// What the app may access on the phone, and why. Mirrors the shipped
/// Android permission set; keep the two in step.
class _PermissionsSheet extends StatelessWidget {
  const _PermissionsSheet();

  static const List<(String, String)> _rows = <(String, String)>[
    ('Camera', 'To record your swing. Asked for the first time you record.'),
    ('Internet', 'To upload a video for analysis and load your results.'),
    ('Keep screen awake', 'So a video can play without the screen turning off.'),
    ('Network status', 'So video playback can tell whether you are online.'),
  ];

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(AppSpacing.xl, 0, AppSpacing.xl, AppSpacing.xl),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text('Data & permissions', style: theme.textTheme.titleLarge),
            const SizedBox(height: AppSpacing.md),
            for (final (String name, String why) in _rows)
              Padding(
                padding: const EdgeInsets.only(bottom: AppSpacing.sm),
                child: Text.rich(TextSpan(children: <InlineSpan>[
                  TextSpan(text: '$name: ', style: const TextStyle(fontWeight: FontWeight.w700)),
                  TextSpan(text: why),
                ])),
              ),
            const SizedBox(height: AppSpacing.xs),
            Text(
              'No microphone, location or contacts. Videos are deleted after '
              'analysis; see the privacy policy for the rest.',
              style: theme.textTheme.bodySmall,
            ),
          ],
        ),
      ),
    );
  }
}
