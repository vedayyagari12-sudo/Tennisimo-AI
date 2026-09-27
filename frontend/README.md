# Tennisimo AI

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

One codebase ships two builds, selected at compile time by `BRAND`:

- **Mobile app** (Android / iOS): the default palette. Build with no define,
  e.g. `flutter build apk`.
- **Web**: the alternate palette (powder blue, gold and white). The files in
  `web/` (`index.html` background, `manifest.json` colours, favicon and icons)
  are themed to match it, so always build the web version with the define:

```
flutter build web --release --dart-define=BRAND=school
```

The output lands in `build/web/`. A web build made without the define would
paint the default palette inside a page themed for the alternate one.
