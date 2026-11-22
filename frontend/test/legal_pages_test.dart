import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/models/legal_content.dart';
import 'package:tennisimo_ai/screens/profile_screen.dart';
import 'package:tennisimo_ai/theme/app_theme.dart';

import '../tool/gen_legal_pages.dart';
import 'support/dashboard_fixtures.dart';

void main() {
  group('public web copies', () {
    test('match the in-app text (run tool/gen_legal_pages.dart)', () {
      buildLegalPages().forEach((String path, String html) {
        expect(File(path).existsSync(), isTrue, reason: '$path is missing');
        expect(File(path).readAsStringSync(), html,
            reason: '$path is out of date: dart run tool/gen_legal_pages.dart');
      });
    });

    test('the deletion page says where the in-app control is', () {
      final String html = buildLegalPages()['web/delete-account.html']!;
      expect(html, contains('Profile'));
      expect(html, contains('Delete account'));
      expect(html, contains(kSupportEmail));
    });
  });

  group('Profile', () {
    Future<void> pump(WidgetTester tester) async {
      tester.view.physicalSize = const Size(412, 915);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(MaterialApp(
        theme: buildAppTheme(),
        home: ProfileScreen(dataSource: forehandOnly().source),
      ));
      await tester.pumpAndSettle();
    }

    testWidgets('lists every legal page, sign out and delete account',
        (WidgetTester tester) async {
      await pump(tester);
      for (final String label in <String>[
        'Privacy policy',
        'Terms of service',
        'Data & permissions',
        'Contact support',
        'Open-source licences',
        'Sign out',
        'Delete account',
      ]) {
        expect(find.text(label), findsOneWidget, reason: label);
      }
    });

    testWidgets('privacy policy opens with its summary and every section',
        (WidgetTester tester) async {
      await pump(tester);
      await tester.tap(find.text('Privacy policy'));
      await tester.pumpAndSettle();
      expect(find.text('The short version'), findsOneWidget);
      for (final LegalSection s in kPrivacyPolicy) {
        await tester.scrollUntilVisible(find.text(s.title), 200,
            scrollable: find.byType(Scrollable).first);
        expect(find.text(s.title), findsOneWidget);
      }
    });

    testWidgets('delete account opens the typed confirmation, deleting nothing',
        (WidgetTester tester) async {
      await pump(tester);
      await tester.tap(find.text('Delete account'));
      await tester.pumpAndSettle();
      expect(find.text('Delete your account?'), findsOneWidget);
      final TextButton button = tester.widget(
          find.widgetWithText(TextButton, 'Delete forever'));
      expect(button.onPressed, isNull);
    });
  });
}
