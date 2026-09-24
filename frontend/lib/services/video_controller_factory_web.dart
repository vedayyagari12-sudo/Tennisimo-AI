import 'package:video_player/video_player.dart';

/// Builds the player used only to read a picked clip's duration — WEB, and the
/// default branch of the conditional import in `video_duration.dart`.
///
/// On web the picked file has no filesystem path. What it does have is a
/// `blob:` URL minted by the browser for the selected `File`, and
/// `VideoPlayerController.networkUrl` hands exactly that to the `<video>`
/// element's `src`, which is how a blob is meant to be consumed. No bytes are
/// copied and nothing is uploaded: the element decodes locally, purely so the
/// real duration can be read instead of guessed.
///
/// This file must not import `dart:io`. It is also the fallback chosen on any
/// platform without `dart:io`, so it stays free of web-only imports too.
VideoPlayerController videoControllerForUri(Uri uri) {
  return VideoPlayerController.networkUrl(uri);
}
