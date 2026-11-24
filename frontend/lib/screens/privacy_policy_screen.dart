import 'package:flutter/material.dart';

import '../theme/app_theme.dart';
import '../widgets/app_card.dart';
import '../widgets/content_width.dart';

/// Where questions about privacy go.
const String kSupportEmail = 'va.appstudio@gmail.com';

/// When the policy text below last changed. Update it with the text.
const String kPrivacyPolicyUpdated = 'November 22, 2026';

/// The privacy policy, in plain words. Opened from Profile.
///
/// Every line here is a promise about how the app behaves, so a change to the
/// app's data handling must change this text in the same commit.
class PrivacyPolicyScreen extends StatelessWidget {
  const PrivacyPolicyScreen({super.key});

  static const List<String> _shortVersion = <String>[
    'We only use your videos to analyse your tennis.',
    'Match and rally videos are deleted right after analysis.',
    'The AI coach sees numbers only, never your video.',
    'We never sell your data or show ads.',
    'Delete your account any time, in the app.',
  ];

  static const List<(String, String)> _sections = <(String, String)>[
    (
      'What we collect',
      'Your email address (to sign in), the videos you choose to upload, and the '
          'swing and court numbers we compute from them. We also note when you '
          'last opened the app.',
    ),
    (
      'How we use it',
      'To measure your swing, track your progress over time and write coaching '
          'tips. Pose tracking runs on our server; no face recognition is used.',
    ),
    (
      'Who processes it',
      'Supabase (sign-in and storage), Google Cloud (analysis server) and Google '
          'Gemini (turns your numbers into readable tips).',
    ),
    (
      'Your choices',
      'Delete any match or rally from its screen. Delete your account in '
          'Profile › Delete account: that removes every video, result and your '
          'login permanently.',
    ),
  ];

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: const Text('Privacy policy')),
      body: SafeArea(
        child: ContentWidth(
          child: ListView(
            padding: AppSpacing.screenPadding,
            children: <Widget>[
              AppCard(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text('The short version', style: theme.textTheme.titleMedium),
                    const SizedBox(height: AppSpacing.sm),
                    for (final String line in _shortVersion)
                      Padding(
                        padding: const EdgeInsets.only(bottom: AppSpacing.xs),
                        child: Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: <Widget>[
                            const Text('•  '),
                            Expanded(child: Text(line, style: theme.textTheme.bodyMedium)),
                          ],
                        ),
                      ),
                  ],
                ),
              ),
              for (final (String title, String body) in _sections) ...<Widget>[
                const SizedBox(height: AppSpacing.md),
                AppCard(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(title, style: theme.textTheme.titleSmall),
                      const SizedBox(height: AppSpacing.xs),
                      Text(body, style: theme.textTheme.bodyMedium),
                    ],
                  ),
                ),
              ],
              const SizedBox(height: AppSpacing.lg),
              Text(
                'Last updated $kPrivacyPolicyUpdated · Questions: $kSupportEmail',
                textAlign: TextAlign.center,
                style: theme.textTheme.bodySmall
                    ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
