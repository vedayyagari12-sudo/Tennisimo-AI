/// The words of the app's legal and help pages, kept as plain data.
///
/// One source for the in-app screens (`screens/legal_screens.dart`) and the
/// public web copies (`web/privacy.html`, `web/terms.html`,
/// `web/delete-account.html`), which app stores require at a public URL. A
/// test keeps the web copies from drifting away from this text.
///
/// Every statement here describes what the code actually does:
///
/// * swing clips are uploaded to the user's own storage folder and kept until
///   the account is deleted (`routes_account.py` removes the folder);
/// * match and rally videos are deleted as soon as processing ends
///   (`match_jobs.py`, SESSION_MODE.md Decision 3), keeping one court picture;
/// * Gemini receives computed numbers only, never video, frames, landmarks or
///   the user id (CLAUDE.md scope boundary, `extra="forbid"` payloads);
/// * no face recognition and no stored landmark time series;
/// * the only telemetry is a server-stamped "last opened" time
///   (`services/last_seen.dart`).
library;

/// The address users write to for help and privacy requests.
const String kSupportEmail = 'va.appstudio@gmail.com';

/// When the policy text below last changed. Update with any wording change.
const String kLegalLastUpdated = 'November 22, 2026';

/// One titled block of a legal page.
///
/// Pure Dart (no Flutter import) so `tool/gen_legal_pages.dart` can run it
/// with plain `dart run`.
class LegalSection {
  const LegalSection(this.title, this.paragraphs);

  final String title;

  /// Each entry renders as its own paragraph; a leading "• " renders as a
  /// bullet line.
  final List<String> paragraphs;
}

/// The five-line summary shown above the full privacy policy.
const List<String> kPrivacySummary = <String>[
  'Your videos are used only to analyse your tennis.',
  'Match and rally videos are deleted right after they are analysed.',
  'The AI coach is sent numbers only, never your video.',
  'We do not sell your data and there are no ads.',
  'You can delete your account and everything in it from the app at any time.',
];

const List<LegalSection> kPrivacyPolicy = <LegalSection>[
  LegalSection('What we collect', <String>[
    '• Account: your email address and a password (stored hashed by our '
        'sign-in provider). We never see your password.',
    '• Videos you choose to upload: single-swing clips, and match or rally '
        'videos.',
    '• Results we compute from them: pose-based swing measurements, scores, '
        'ball speed (when you calibrate), shot maps, rally counts and coaching '
        'text.',
    '• Usage: the time you last opened the app and the platform (web, '
        'Android or iOS). Nothing else is tracked, and there are no '
        'advertising or analytics SDKs.',
  ]),
  LegalSection('What we do not collect', <String>[
    'We do not use face recognition or build any biometric template. Body '
        'keypoints are computed in memory to measure the swing and are not '
        'stored. We do not access your contacts, location or microphone. The '
        'camera is used only while you are recording a clip.',
  ]),
  LegalSection('How we use it', <String>[
    'Only to provide the app: measuring your swing, showing your progress over '
        'time, and writing coaching tips. We do not sell, rent or share your '
        'data for advertising.',
  ]),
  LegalSection('The AI coach', <String>[
    'Coaching text is written by Google Gemini from the numbers our own code '
        'computed (for example "elbow angle 148°"). Gemini is never sent your '
        'video, images, body keypoints, email or account id.',
  ]),
  LegalSection('Who processes your data', <String>[
    '• Supabase: sign-in, database and video storage.',
    '• Google Cloud Run: the server that analyses your videos.',
    '• Google Gemini API: turns computed numbers into coaching text.',
    'Each processes data only to run this service for us.',
  ]),
  LegalSection('How long we keep it', <String>[
    '• Match and rally videos: deleted as soon as they are analysed. One still '
        'picture of the court is kept so you can check the lines we used.',
    '• Swing clips, results and your account: kept until you delete them or '
        'your account.',
    '• Deleting a match or rally removes its results immediately.',
  ]),
  LegalSection('Your rights and choices', <String>[
    'You can delete any match or rally from its screen, and delete your whole '
        'account from Profile › Delete account. Account deletion permanently '
        'removes every video, every result and your login, and cannot be '
        'undone. To ask for a copy of your data or for any other privacy '
        'request, email $kSupportEmail.',
  ]),
  LegalSection('Children', <String>[
    'Tennisimo is not directed at children under 13, and we do not knowingly '
        'collect data from them. If you believe a child has created an '
        'account, contact us and we will delete it.',
  ]),
  LegalSection('Security', <String>[
    'All traffic is encrypted (HTTPS). Videos are stored in a private folder '
        'only your account can reach, and every request is checked against '
        'your signed-in session.',
  ]),
  LegalSection('Changes', <String>[
    'If this policy changes we will update the date below and, for '
        'significant changes, tell you in the app.',
  ]),
];

const List<LegalSection> kTermsOfService = <LegalSection>[
  LegalSection('Using Tennisimo', <String>[
    'Tennisimo analyses tennis videos you record or upload and gives you '
        'measurements and coaching tips. By creating an account you agree to '
        'these terms and to the Privacy Policy.',
  ]),
  LegalSection('Your account', <String>[
    'Keep your password safe; you are responsible for activity on your '
        'account. You can delete your account at any time from Profile.',
  ]),
  LegalSection('Your content', <String>[
    'You keep ownership of the videos you upload. You give us permission to '
        'store and process them only to provide the service to you. Only '
        'upload videos you have the right to use, and do not film people who '
        'have not agreed to it.',
  ]),
  LegalSection('Measurements and coaching', <String>[
    'Scores, speeds and line calls are estimates from phone video and can be '
        'wrong. Coaching tips are general guidance, not professional or '
        'medical advice. Warm up, play within your limits and stop if '
        'something hurts.',
  ]),
  LegalSection('Acceptable use', <String>[
    'Do not misuse the service: no attempts to access other accounts, overload '
        'the servers, or upload unlawful content.',
  ]),
  LegalSection('No warranty', <String>[
    'The service is provided "as is", without warranties of any kind. To the '
        'extent the law allows, we are not liable for indirect or '
        'consequential losses.',
  ]),
  LegalSection('Changes and contact', <String>[
    'We may update these terms; we will change the date below when we do. '
        'Questions: $kSupportEmail.',
  ]),
];

/// The "Data & permissions" page: each permission and why the app asks.
const List<LegalSection> kDataAndPermissions = <LegalSection>[
  LegalSection('Camera', <String>[
    'Used only on the Record screen, to film your swing. Sound is not '
        'recorded.',
  ]),
  LegalSection('Files and photos', <String>[
    'When you choose "Upload a video", the system picker gives the app only '
        'the one file you pick. The app cannot see the rest of your library.',
  ]),
  LegalSection('Network', <String>[
    'Needed to upload a clip and fetch your results.',
  ]),
  LegalSection('What is stored on this device', <String>[
    'Your sign-in session and, on the web, your light/dark choice. Recorded '
        'clips are kept in the app\'s temporary cache only until they are '
        'uploaded.',
  ]),
];

/// Short, practical filming advice for the Help page.
const List<LegalSection> kFilmingHelp = <LegalSection>[
  LegalSection('Single swings', <String>[
    '• Film side-on, about 4–6 m away, with your whole body in frame.',
    '• Hold the phone still: a tripod or a fence clip works best.',
    '• Good light, and avoid filming straight into the sun.',
    '• Choose the shot type before recording so the app can cross-check it.',
  ]),
  LegalSection('Ball speed', <String>[
    'Tap the two court reference points when asked. Without them the swing is '
        'still scored, but no speed is shown.',
  ]),
  LegalSection('Matches and rallies', <String>[
    '• Film from behind the baseline, high up if you can, with the whole far '
        'court visible.',
    '• Record at 720p; videos can be up to 5 minutes.',
    '• Keep the camera still for the whole video so line calls stay accurate.',
  ]),
];
