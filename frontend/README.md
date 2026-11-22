# Tennisimo

A new Flutter project.

## Getting Started

This project is a starting point for a Flutter application.

A few resources to get you started if this is your first Flutter project:

- [Learn Flutter](https://docs.flutter.dev/get-started/learn-flutter)
- [Write your first Flutter app](https://docs.flutter.dev/get-started/codelab)
- [Flutter learning resources](https://docs.flutter.dev/reference/learning-resources)

For help getting started with Flutter development, view the
[online documentation](https://docs.flutter.dev/), which offers tutorials,
samples, guidance on mobile development, and a full API reference.

## Builds and palettes

Web, Android and iOS ship the SAME look: the default green palette, the same
screens and the same icons, so a player sees one app on every device. Build
with no define:

```
flutter build web --release
flutter build apk
```

The files in `web/` (`index.html` background, `manifest.json` colours, favicon
and icons) match the default palette. The alternate `BRAND=school` palette
still compiles but no build ships it.

The public legal pages (`web/privacy.html`, `web/terms.html`,
`web/delete-account.html`) are generated from `lib/models/legal_content.dart`:

```
dart run tool/gen_legal_pages.dart
```
