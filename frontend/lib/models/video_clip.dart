import 'package:flutter/foundation.dart';

/// One swing clip, ready to upload, however it was obtained.
///
/// This is the single hand-off type between the two intake paths — live
/// recording and file pick, on mobile and on web — and everything downstream
/// (ticket, upload, analysis, polling) takes it. There is deliberately no
/// `path` field: on web `XFile.path` is a `blob:` URL rather than a filesystem
/// path, so a path-shaped clip cannot survive both platforms. Bytes can.
///
/// Every field is measured, never assumed: [bytes] are the real file, [sizeBytes]
/// is their length, [contentType] is sniffed from [bytes], and
/// [durationSeconds] is either the recorder's own elapsed time or a value read
/// back from the decoded file.
@immutable
class VideoClip {
  const VideoClip({
    required this.bytes,
    required this.contentType,
    required this.durationSeconds,
    required this.displayName,
  });

  /// The whole file. Capped well below memory pressure by `kMaxUploadBytes`
  /// (50 MiB), which is checked before the bytes are read where the source can
  /// report its size first.
  final Uint8List bytes;

  /// Sniffed from [bytes] by `sniffVideoContentType`. Sent verbatim as the
  /// ticket's `content_type` and as the Storage upload's `Content-Type`.
  final String contentType;

  /// Measured clip length. Never a placeholder.
  final double durationSeconds;

  /// A short label for the source, e.g. the picked file's name. Shown to the
  /// user so it is obvious which clip is about to be analysed.
  final String displayName;

  int get sizeBytes => bytes.length;
}
