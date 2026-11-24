import 'dart:async';

import 'package:flutter/material.dart';

import '../services/account_deletion.dart';
import '../services/api_client.dart';
import '../services/haptics.dart';
import '../theme/app_theme.dart';

/// Confirms, then performs, the permanent deletion of the account.
///
/// Three deliberate steps stand between the menu and the request: the menu
/// entry only opens this dialog; the delete button stays disabled until
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

  /// True from the confirming tap until the server answers.
  ///
  /// Same idea as the record screen's intake guard: set synchronously, before
  /// the first await, so a second tap in the same frame is refused by the
  /// check in [_confirm] even before the rebuild disables the button. While it
  /// is true the dialog also cannot be popped — the user must not be left
  /// wondering whether a request they walked away from went through.
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
