import 'dart:async';

import 'package:video_player/video_player.dart';

import 'video_controller_factory_web.dart'
    if (dart.library.io) 'video_controller_factory_io.dart';

/// Reads the real duration of a picked clip, or null if it cannot be read.
///
/// A recorded clip does not need this — the record screen times its own
/// recording — but a picked file carries its length only inside the container,
/// and `duration_s` is a required field on the upload ticket that the server
/// bounds at 60 s. The options were to invent a number or to decode the file;
/// inventing one would put a fabricated value into a request the server trusts,
/// so the file is decoded.
///
/// The platform's own video decoder does the reading (`video_player`), which is
/// the only thing that is correct for all three accepted containers on all
/// three targets. The controller is created, initialised, read and disposed
/// here; it is never attached to the widget tree and never plays anything.
///
/// Returns null rather than throwing, and null rather than zero, on every
/// failure path: an unreadable file, a codec the device cannot decode, a
/// container with no duration recorded. The caller turns null into a plain
/// refusal. Nothing downstream ever sees a zero standing in for "unknown".
Future<Duration?> probeVideoDuration(Uri uri) async {
  final VideoPlayerController controller = videoControllerForUri(uri);
  try {
    await controller.initialize();
    final Duration duration = controller.value.duration;
    return duration > Duration.zero ? duration : null;
  } catch (_) {
    return null;
  } finally {
    unawaited(controller.dispose());
  }
}
