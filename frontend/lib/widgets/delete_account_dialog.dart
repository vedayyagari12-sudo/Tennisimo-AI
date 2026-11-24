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
    final TextStyle? body = theme.textTheme.bodyMedium;

    return PopScope<bool>(
      canPop: !_inFlight,
      child: Dialog(
        insetPadding: const EdgeInsets.all(AppSpacing.lg),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(24)),
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(22, 22, 22, 18),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              Align(
                alignment: Alignment.centerLeft,
                child: Container(
                  width: 44,
                  height: 44,
                  decoration: BoxDecoration(
                    color: colors.error.withValues(alpha: 0.10),
                    shape: BoxShape.circle,
                  ),
                  child: Icon(Icons.delete_outline, color: colors.error),
                ),
              ),
              const SizedBox(height: AppSpacing.md),
              Semantics(
                header: true,
                child: Text(
                  'Delete your account?',
                  style: theme.textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w800),
                ),
              ),
              const SizedBox(height: AppSpacing.sm),
              Text('This permanently removes:', style: body),
              const SizedBox(height: AppSpacing.xs),
              for (final String line in const <String>[
                'every swing video you uploaded',
                'all scores, matches and rallies',
                'your login',
              ])
                Padding(
                  padding: const EdgeInsets.only(left: 4, bottom: 2),
                  child: Text('•  $line', style: body),
                ),
              const SizedBox(height: AppSpacing.sm),
              Text(
                'This can’t be undone.',
                style: body?.copyWith(fontWeight: FontWeight.w700),
              ),
              const SizedBox(height: AppSpacing.lg),
              Text(
                'Type $kDeleteConfirmationPhrase to confirm',
                style: theme.textTheme.labelLarge,
              ),
              const SizedBox(height: AppSpacing.sm),
              TextField(
                controller: _typed,
                enabled: !_inFlight,
                autocorrect: false,
                enableSuggestions: false,
                textCapitalization: TextCapitalization.characters,
                decoration: InputDecoration(
                  hintText: kDeleteConfirmationPhrase,
                  border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
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
                    style: body?.copyWith(color: colors.onErrorContainer),
                  ),
                ),
              ],
              const SizedBox(height: AppSpacing.lg),
              FilledButton(
                style: FilledButton.styleFrom(
                  backgroundColor: colors.error,
                  foregroundColor: colors.onError,
                  minimumSize: const Size.fromHeight(50),
                ),
                onPressed: _canConfirm ? _confirm : null,
                child: Text(
                  _inFlight
                      ? 'Deleting…'
                      : (failure == null ? 'Delete account' : 'Try again'),
                ),
              ),
              const SizedBox(height: AppSpacing.sm),
              TextButton(
                style: TextButton.styleFrom(minimumSize: const Size.fromHeight(48)),
                onPressed: _inFlight ? null : () => Navigator.of(context).pop(false),
                child: const Text('Cancel'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
