/// Profile: the account, preferences, help and legal pages, sign out and
/// account deletion, in one predictable place.
///
/// These used to hide in the dashboard's overflow menu. App stores expect
/// account deletion and the privacy policy to be easy to find, and a player
/// looking for them looks for a Profile tab, so they live here — reachable
/// both from the bottom bar and from the avatar on the dashboard.
library;

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../models/legal_content.dart';
import '../services/account_deletion.dart';
import '../services/api_client.dart';
import '../services/haptics.dart';
import '../theme/app_theme.dart';
import '../theme/theme_controller.dart';
import '../widgets/app_card.dart';
import '../widgets/content_width.dart';
import 'dashboard_screen.dart' show DashboardDataSource;
import 'legal_screens.dart';
import 'login_screen.dart';

/// The tooltip on the dashboard's avatar button, which opens this screen.
const String kProfileTooltip = 'Profile and settings';

class ProfileScreen extends StatelessWidget {
  const ProfileScreen({
    super.key,
    this.dataSource = const DashboardDataSource(),
    this.showBackButton = false,
  });

  /// The account, sign-out and deletion. Always the default in the app.
  final DashboardDataSource dataSource;

  /// True when pushed as a page (from the dashboard avatar) rather than shown
  /// as a tab.
  final bool showBackButton;

  /// Confirm, delete, then leave.
  ///
  /// The dialog does the request itself and only closes with `true` once the
  /// server has answered success, so nothing below runs on a failed or
  /// unfinished deletion.
  Future<void> _deleteAccount(BuildContext context) async {
    final NavigatorState navigator = Navigator.of(context);
    final bool? deleted = await showDialog<bool>(
      context: context,
      // Only an explicit Cancel closes it: a stray tap outside must not look
      // like an answer to a question this serious.
      barrierDismissible: false,
      builder: (BuildContext context) =>
          DeleteAccountDialog(onDelete: dataSource.deleteAccount),
    );
    if (deleted != true) return;

    // Queued BEFORE signing out: the sign-out is what makes the AuthGate build
    // the login screen that shows it.
    LoginNotice.post(kAccountDeletedNotice);
    navigator.popUntil((Route<dynamic> route) => route.isFirst);
    try {
      // Local scope (gotrue's default): the session is removed from memory and
      // storage FIRST, then the server is told. The server has already
      // deleted the user, so that second step may fail — harmlessly, since
      // the local session, and with it the cached token, is already gone.
      await dataSource.signOut();
    } catch (_) {
      // See above: nothing is left signed in on this device.
    }
  }

  Future<void> _signOut(BuildContext context) async {
    Navigator.of(context).popUntil((Route<dynamic> route) => route.isFirst);
    await dataSource.signOut();
  }

  void _contactSupport(BuildContext context) {
    showDialog<void>(
      context: context,
      builder: (BuildContext context) => AlertDialog(
        title: const Text('Contact support'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            const Text(
              'Questions, bugs or privacy requests — email us and we will '
              'get back to you.',
            ),
            const SizedBox(height: AppSpacing.md),
            SelectableText(
              kSupportEmail,
              style: Theme.of(context).textTheme.titleMedium,
            ),
          ],
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () {
              Clipboard.setData(const ClipboardData(text: kSupportEmail));
              Navigator.of(context).pop();
              ScaffoldMessenger.maybeOf(context)?.showSnackBar(
                const SnackBar(content: Text('Email address copied')),
              );
            },
            child: const Text('Copy address'),
          ),
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('Close'),
          ),
        ],
      ),
    );
  }

  void _push(BuildContext context, Widget page) {
    Navigator.of(context).push(
      MaterialPageRoute<void>(builder: (BuildContext context) => page),
    );
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final ColorScheme scheme = theme.colorScheme;
    final String? email = dataSource.accountEmail;
    final String initial =
        (email == null || email.isEmpty) ? '?' : email[0].toUpperCase();
    final ThemeController? themeController = ThemeScope.maybeOf(context);

    final List<Widget> children = <Widget>[
      if (!showBackButton)
        Padding(
          padding: const EdgeInsets.only(
            left: AppSpacing.xs,
            bottom: AppSpacing.md,
          ),
          child: Text('Profile', style: theme.textTheme.headlineSmall),
        ),
      AppCard(
        child: Row(
          children: <Widget>[
            CircleAvatar(
              radius: 28,
              backgroundColor: scheme.primaryContainer,
              child: Text(
                initial,
                style: theme.textTheme.titleLarge
                    ?.copyWith(color: scheme.primary),
              ),
            ),
            const SizedBox(width: AppSpacing.lg),
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
                          ?.copyWith(color: scheme.onSurfaceVariant),
                    ),
                ],
              ),
            ),
          ],
        ),
      ),
      if (themeController != null) ...<Widget>[
        const _GroupLabel('Appearance'),
        AppCard(
          child: ListenableBuilder(
            listenable: themeController,
            builder: (BuildContext context, Widget? _) =>
                _ThemeChoice(controller: themeController),
          ),
        ),
      ],
      const _GroupLabel('Help & legal'),
      AppCard(
        padding: EdgeInsets.zero,
        child: Column(
          children: <Widget>[
            _SettingsRow(
              icon: Icons.videocam_outlined,
              label: 'How to film a good clip',
              onTap: () => _push(
                context,
                const LegalPageScreen(
                  title: 'Filming tips',
                  sections: kFilmingHelp,
                  showLastUpdated: false,
                ),
              ),
            ),
            _SettingsRow(
              icon: Icons.mail_outline,
              label: 'Contact support',
              onTap: () => _contactSupport(context),
            ),
            _SettingsRow(
              icon: Icons.shield_outlined,
              label: 'Privacy policy',
              onTap: () => openPrivacyPolicy(context),
            ),
            _SettingsRow(
              icon: Icons.description_outlined,
              label: 'Terms of service',
              onTap: () => openTerms(context),
            ),
            _SettingsRow(
              icon: Icons.lock_outline,
              label: 'Data & permissions',
              onTap: () => _push(
                context,
                const LegalPageScreen(
                  title: 'Data & permissions',
                  sections: kDataAndPermissions,
                ),
              ),
            ),
            _SettingsRow(
              icon: Icons.code,
              label: 'Open-source licences',
              last: true,
              onTap: () => showLicensePage(
                context: context,
                applicationName: 'Tennisimo',
              ),
            ),
          ],
        ),
      ),
      const _GroupLabel('Account'),
      AppCard(
        padding: EdgeInsets.zero,
        child: Column(
          children: <Widget>[
            _SettingsRow(
              icon: Icons.logout,
              label: 'Sign out',
              onTap: () => _signOut(context),
            ),
            _SettingsRow(
              icon: Icons.delete_outline,
              label: 'Delete account',
              destructive: true,
              last: true,
              onTap: () => _deleteAccount(context),
            ),
          ],
        ),
      ),
      const SizedBox(height: AppSpacing.lg),
      Text(
        'Deleting your account permanently removes every video, score and '
        'match. It cannot be undone.',
        textAlign: TextAlign.center,
        style: theme.textTheme.bodySmall
            ?.copyWith(color: scheme.onSurfaceVariant),
      ),
    ];

    final Widget list = ContentWidth(
      child: ListView(
        padding: const EdgeInsets.fromLTRB(
          AppSpacing.lg,
          AppSpacing.lg,
          AppSpacing.lg,
          AppSpacing.xxl,
        ),
        children: children,
      ),
    );

    if (showBackButton) {
      return Scaffold(
        appBar: AppBar(title: const Text('Profile')),
        body: SafeArea(top: false, child: list),
      );
    }
    return Scaffold(body: SafeArea(bottom: false, child: list));
  }
}

class _GroupLabel extends StatelessWidget {
  const _GroupLabel(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.lg,
        AppSpacing.xl,
        AppSpacing.lg,
        AppSpacing.sm,
      ),
      child: Text(
        text.toUpperCase(),
        style: theme.textTheme.labelSmall?.copyWith(
          color: theme.colorScheme.onSurfaceVariant,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }
}

class _SettingsRow extends StatelessWidget {
  const _SettingsRow({
    required this.icon,
    required this.label,
    required this.onTap,
    this.destructive = false,
    this.last = false,
  });

  final IconData icon;
  final String label;
  final VoidCallback onTap;
  final bool destructive;
  final bool last;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final ColorScheme scheme = theme.colorScheme;
    final Color ink = destructive ? scheme.error : scheme.onSurface;

    return Column(
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        ListTile(
          minTileHeight: 56,
          onTap: onTap,
          leading: Container(
            width: 34,
            height: 34,
            decoration: BoxDecoration(
              color: destructive
                  ? scheme.errorContainer
                  : scheme.surfaceContainerHigh,
              borderRadius: BorderRadius.circular(10),
            ),
            child: Icon(
              icon,
              size: 19,
              color: destructive ? scheme.error : scheme.onSurfaceVariant,
            ),
          ),
          title: Text(
            label,
            style: theme.textTheme.bodyLarge?.copyWith(color: ink),
          ),
          trailing: destructive
              ? null
              : Icon(Icons.chevron_right, color: scheme.onSurfaceVariant),
        ),
        if (!last) const Divider(height: 1, indent: 66),
      ],
    );
  }
}

/// Light / dark / follow-system. Web only: rendered only under a ThemeScope.
class _ThemeChoice extends StatelessWidget {
  const _ThemeChoice({required this.controller});

  final ThemeController controller;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: double.infinity,
      child: SegmentedButton<ThemeMode>(
        showSelectedIcon: false,
        segments: const <ButtonSegment<ThemeMode>>[
          ButtonSegment<ThemeMode>(
            value: ThemeMode.system,
            label: Text('System'),
          ),
          ButtonSegment<ThemeMode>(value: ThemeMode.light, label: Text('Light')),
          ButtonSegment<ThemeMode>(value: ThemeMode.dark, label: Text('Dark')),
        ],
        selected: <ThemeMode>{controller.mode},
        onSelectionChanged: (Set<ThemeMode> picked) =>
            controller.setMode(picked.first),
      ),
    );
  }
}

/// Confirms, then performs, the permanent deletion of the account.
///
/// Three deliberate steps stand between the Profile row and the request: the
/// row only opens this dialog; the delete button stays disabled until
/// [kDeleteConfirmationPhrase] is typed (see its doc for why a typed word);
/// and only then does a tap send anything.
///
/// The request runs HERE rather than after the dialog closes, so a failure is
/// shown in the same place the user asked, with the typed word still in the
/// field and the button relabelled "Try again". The dialog closes with `true`
/// only on a server-confirmed success; nothing ever pretends otherwise.
class DeleteAccountDialog extends StatefulWidget {
  const DeleteAccountDialog({super.key, required this.onDelete});

  final Future<ApiResult<void>> Function() onDelete;

  @override
  State<DeleteAccountDialog> createState() => _DeleteAccountDialogState();
}

class _DeleteAccountDialogState extends State<DeleteAccountDialog> {
  final TextEditingController _typed = TextEditingController();

  /// True from the confirming tap until the server answers. Set synchronously,
  /// before the first await, so a second tap in the same frame is refused.
  /// While it is true the dialog also cannot be popped.
  bool _inFlight = false;

  /// The last failure, already worded for this action. See
  /// [accountDeletionFailure].
  ApiFailure? _failure;

  @override
  void dispose() {
    _typed.dispose();
    super.dispose();
  }

  bool get _canConfirm => !_inFlight && deleteConfirmationMatches(_typed.text);

  Future<void> _confirm() async {
    if (!_canConfirm) return;
    setState(() {
      _inFlight = true;
      _failure = null;
    });
    unawaited(haptics.accountDeletionConfirmed());

    final ApiResult<void> result = await widget.onDelete();
    if (!mounted) return;

    if (result.isOk) {
      Navigator.of(context).pop(true);
      return;
    }
    setState(() {
      _inFlight = false;
      _failure = accountDeletionFailure(result.failure!);
    });
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final ColorScheme colors = theme.colorScheme;
    final ApiFailure? failure = _failure;

    return PopScope<bool>(
      canPop: !_inFlight,
      child: AlertDialog(
        scrollable: true,
        icon: Icon(Icons.delete_outline, color: colors.error, size: 32),
        title: const Text('Delete your account?'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            const Text(
              'This permanently deletes your account and every swing you have '
              'recorded: the videos, the scores and the coaching notes. It '
              'cannot be undone.',
            ),
            const SizedBox(height: AppSpacing.lg),
            const Text('Type $kDeleteConfirmationPhrase to confirm.'),
            const SizedBox(height: AppSpacing.sm),
            TextField(
              controller: _typed,
              enabled: !_inFlight,
              autocorrect: false,
              enableSuggestions: false,
              textCapitalization: TextCapitalization.characters,
              decoration: const InputDecoration(
                labelText: kDeleteConfirmationPhrase,
                border: OutlineInputBorder(),
              ),
              onChanged: (_) => setState(() {}),
              onSubmitted: (_) => _confirm(),
            ),
            if (failure != null) ...<Widget>[
              const SizedBox(height: AppSpacing.lg),
              Container(
                padding: const EdgeInsets.all(AppSpacing.md),
                decoration: BoxDecoration(
                  color: colors.errorContainer,
                  borderRadius: BorderRadius.circular(AppSpacing.innerRadius),
                ),
                child: Text(
                  failure.plainLanguageWithDebugDetail,
                  style: theme.textTheme.bodyMedium
                      ?.copyWith(color: colors.onErrorContainer),
                ),
              ),
            ],
          ],
        ),
        actions: <Widget>[
          TextButton(
            onPressed: _inFlight ? null : () => Navigator.of(context).pop(false),
            child: const Text('Cancel'),
          ),
          TextButton(
            style: TextButton.styleFrom(foregroundColor: colors.error),
            onPressed: _canConfirm ? _confirm : null,
            child: Text(
              _inFlight
                  ? 'Deleting…'
                  : (failure == null ? 'Delete forever' : 'Try again'),
            ),
          ),
        ],
      ),
    );
  }
}
