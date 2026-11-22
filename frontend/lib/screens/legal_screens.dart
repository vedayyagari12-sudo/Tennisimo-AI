/// Read-only pages reached from Profile: privacy policy, terms, data and
/// permissions, and filming help. The words live in `models/legal_content.dart`.
library;

import 'package:flutter/material.dart';

import '../models/legal_content.dart';
import '../theme/app_theme.dart';
import '../widgets/app_card.dart';
import '../widgets/content_width.dart';

/// A titled page of [LegalSection]s, optionally led by a summary card.
class LegalPageScreen extends StatelessWidget {
  const LegalPageScreen({
    super.key,
    required this.title,
    required this.sections,
    this.summary,
    this.showLastUpdated = true,
  });

  final String title;
  final List<LegalSection> sections;

  /// Short bullet lines shown first, in a highlighted card.
  final List<String>? summary;

  final bool showLastUpdated;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final List<String>? summary = this.summary;

    return Scaffold(
      appBar: AppBar(title: Text(title)),
      body: SafeArea(
        top: false,
        child: ContentWidth(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(
              AppSpacing.lg,
              AppSpacing.sm,
              AppSpacing.lg,
              AppSpacing.xxl,
            ),
            children: <Widget>[
              if (summary != null) ...<Widget>[
                _SummaryCard(lines: summary),
                const SizedBox(height: AppSpacing.md),
              ],
              for (final LegalSection section in sections) ...<Widget>[
                AppCard(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(section.title, style: theme.textTheme.titleMedium),
                      const SizedBox(height: AppSpacing.sm),
                      for (final String p in section.paragraphs)
                        Padding(
                          padding: const EdgeInsets.only(bottom: AppSpacing.xs),
                          child: Text(
                            p,
                            style: theme.textTheme.bodyMedium?.copyWith(
                              color: theme.colorScheme.onSurfaceVariant,
                              height: 1.5,
                            ),
                          ),
                        ),
                    ],
                  ),
                ),
                const SizedBox(height: AppSpacing.md),
              ],
              if (showLastUpdated)
                Padding(
                  padding: const EdgeInsets.only(top: AppSpacing.sm),
                  child: Text(
                    'Last updated $kLegalLastUpdated · $kSupportEmail',
                    textAlign: TextAlign.center,
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: theme.colorScheme.onSurfaceVariant,
                    ),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

class _SummaryCard extends StatelessWidget {
  const _SummaryCard({required this.lines});

  final List<String> lines;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final ColorScheme scheme = theme.colorScheme;
    return Container(
      padding: AppSpacing.cardPadding,
      decoration: BoxDecoration(
        color: scheme.primaryContainer,
        borderRadius: BorderRadius.circular(AppSpacing.cardRadius),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text('The short version', style: theme.textTheme.titleMedium),
          const SizedBox(height: AppSpacing.sm),
          for (final String line in lines)
            Padding(
              padding: const EdgeInsets.only(bottom: AppSpacing.xs),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Padding(
                    padding: const EdgeInsets.only(top: 2),
                    child: Icon(Icons.check_circle,
                        size: 18, color: scheme.primary),
                  ),
                  const SizedBox(width: AppSpacing.sm),
                  Expanded(
                    child: Text(line, style: theme.textTheme.bodyMedium),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

/// Opens the privacy policy.
Future<void> openPrivacyPolicy(BuildContext context) =>
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (BuildContext context) => const LegalPageScreen(
          title: 'Privacy policy',
          summary: kPrivacySummary,
          sections: kPrivacyPolicy,
        ),
      ),
    );

/// Opens the terms of service.
Future<void> openTerms(BuildContext context) => Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (BuildContext context) => const LegalPageScreen(
          title: 'Terms of service',
          sections: kTermsOfService,
        ),
      ),
    );
