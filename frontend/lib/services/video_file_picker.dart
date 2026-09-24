import 'package:file_picker/file_picker.dart';
import 'package:flutter/foundation.dart';

import '../models/video_clip.dart';
import 'video_duration.dart';
import 'video_intake.dart';

/// What came back from the OS / browser file chooser.
///
/// Sealed, so the caller cannot forget that "the user pressed cancel" and
/// "the file is unusable" are different outcomes: one is silent, the other owes
/// the user an explanation.
@immutable
sealed class VideoPickOutcome {
  const VideoPickOutcome();
}

/// The user dismissed the chooser. Say nothing.
class VideoPickCancelled extends VideoPickOutcome {
  const VideoPickCancelled();
}

/// A usable clip, already validated against the server's limits.
class VideoPickSucceeded extends VideoPickOutcome {
  const VideoPickSucceeded(this.clip);

  final VideoClip clip;
}

/// A file was chosen but cannot be analysed. [message] is written for a user
/// and says what to do next.
class VideoPickRejected extends VideoPickOutcome {
  const VideoPickRejected(this.message);

  final String message;
}

/// Opens the platform file chooser and returns a ready-to-upload clip.
///
/// One implementation for mobile and web. `file_picker` gives back a
/// [PlatformFile] whose byte access works the same on both, which is what makes
/// that possible: on web `path` is null and the bytes come from a `blob:` URL,
/// on Android they may come through the Storage Access Framework, and
/// `readAsBytes()` hides both.
///
/// Everything that could be assumed is measured instead:
///  * the size is asked of the file before the bytes are read, so an
///    over-limit file is refused without pulling 300 MB into memory;
///  * the content type is sniffed from the bytes
///    ([sniffVideoContentType]), never from the extension or the platform;
///  * the duration is decoded from the file ([probeVideoDuration]), never
///    defaulted.
///
/// Nothing is uploaded here, and a rejection costs the user no network time.
Future<VideoPickOutcome> pickVideoClip() async {
  final PlatformFile? file;
  try {
    file = await FilePicker.pickFile(type: FileType.video);
  } catch (e) {
    return VideoPickRejected('The file chooser could not be opened. ($e)');
  }
  if (file == null) return const VideoPickCancelled();

  // Reported size first. On web this is the browser's `File.size` and on mobile
  // the picker's own stat, so an over-limit file is caught before readAsBytes.
  // It is advisory only — `bytes.length` below is what is actually sent.
  final int? reportedSize = await file.length();
  if (reportedSize != null && reportedSize > kMaxUploadBytes) {
    return VideoPickRejected(oversizeMessage(reportedSize));
  }

  final Uint8List bytes;
  try {
    bytes = await file.readAsBytes();
  } catch (e) {
    return VideoPickRejected(
      'That file could not be read from this device. ($e)',
    );
  }

  final String? contentType = sniffVideoContentType(bytes);
  if (contentType == null) {
    // Stop here: without a container we recognise there is nothing honest to
    // put in the ticket's content_type, and guessing is the failure this whole
    // path exists to avoid.
    return const VideoPickRejected(kUnrecognisedVideoMessage);
  }

  final Duration? duration = await probeVideoDuration(file.uri);
  final double durationSeconds =
      duration == null ? 0 : duration.inMilliseconds / 1000.0;

  final String? reason = videoRejectionReason(
    contentType: contentType,
    sizeBytes: bytes.length,
    durationSeconds: durationSeconds,
  );
  if (reason != null) return VideoPickRejected(reason);

  return VideoPickSucceeded(
    VideoClip(
      bytes: bytes,
      contentType: contentType,
      durationSeconds: durationSeconds,
      displayName: file.name,
    ),
  );
}
