import 'dart:io';

import 'package:video_player/video_player.dart';

/// Builds the player used only to read a picked clip's duration — MOBILE.
///
/// Selected by the `dart.library.io` branch of the conditional import in
/// `video_duration.dart`, so `dart:io` never reaches a web build.
///
/// The picker does not always hand back the same kind of URI:
///  * `file:` — `file_picker_darwin` always, and `android_file_picker`
///    whenever the pick resolves to a real path.
///  * `content:` — Android's Storage Access Framework, when the file lives
///    behind a document provider and has no readable path. `File()` cannot
///    open one of those; `VideoPlayerController.contentUri` can, because
///    ExoPlayer resolves it through the ContentResolver.
///
/// Anything else is handed to the URL constructor rather than being rejected,
/// so an unexpected scheme degrades to "duration could not be read" — which the
/// caller reports honestly — instead of throwing.
VideoPlayerController videoControllerForUri(Uri uri) {
  switch (uri.scheme) {
    case 'file':
      return VideoPlayerController.file(File(uri.toFilePath()));
    case 'content':
      return VideoPlayerController.contentUri(uri);
    default:
      return VideoPlayerController.networkUrl(uri);
  }
}
