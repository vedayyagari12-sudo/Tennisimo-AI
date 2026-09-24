/// PURE video-intake rules: what the server will accept, and how the client
/// works out what it is actually holding.
///
/// Nothing in this file does I/O, touches a plugin, or looks at the platform.
/// That is deliberate: every decision here is unit-tested with synthetic bytes
/// (CLAUDE.md, "every math function gets a unit test with synthetic data"), and
/// a decision that cannot be tested without a device is a decision that ships
/// untested.
library;

import 'dart:typed_data';

/// Maximum upload size the backend will issue a ticket for, in bytes.
///
/// NOT a number invented here. It is `MAX_UPLOAD_BYTES` in
/// `backend/app/config.py` (52_428_800 = 50 MiB), which is also the default of
/// `Settings.max_upload_bytes` and the value `routes_uploads.py` compares
/// `size_bytes` against before raising `413 file_too_large`. The backend's own
/// comment records why it is that number: it matches the Supabase free-tier
/// per-file limit, so a larger client cap would be accepted by the API and then
/// rejected by Storage, after the upload.
///
/// Dart cannot import a Python constant. If the server value changes, change
/// this one — exactly the arrangement already documented for
/// [kServerHeartbeatStaleSeconds] in `api_client.dart`.
const int kMaxUploadBytes = 52428800;

/// Maximum clip length the backend will issue a ticket for, in seconds.
///
/// From `UploadTicketRequest.duration_s` in `backend/app/models/requests.py`,
/// which carries `le=60.0`, and matched by `Settings.max_clip_seconds`
/// (default 60.0) in `backend/app/config.py`. Over this, the ticket request
/// fails Pydantic validation and comes back `400 invalid_request` — a slow,
/// opaque failure for something the client can see instantly.
const double kMaxClipSeconds = 60.0;

/// The content types `ALLOWED_CONTENT_TYPES` accepts
/// (`backend/app/models/requests.py`), and the extension each maps to.
///
/// Three entries because three things really produce clips: the Flutter camera
/// plugin and iOS Safari's MediaRecorder emit MP4, an iOS photo-library pick
/// emits QuickTime, and Android Chrome's MediaRecorder emits WebM.
const Map<String, String> kAcceptedVideoContentTypes = <String, String>{
  'video/mp4': 'mp4',
  'video/quicktime': 'mov',
  'video/webm': 'webm',
};

/// Number of leading bytes [sniffVideoContentType] needs.
///
/// 64 is enough for both container signatures: an ISO-BMFF `ftyp` box puts its
/// brand at offset 8, and Matroska's `DocType` element sits inside the EBML
/// header, which browsers write within the first few dozen bytes.
const int kVideoSniffHeadBytes = 64;

/// Returns the content type of [head] as read out of the bytes themselves, or
/// null when it is not one of the three accepted containers.
///
/// THIS IS THE ONLY PLACE THE CONTENT TYPE IS DECIDED, and it reads the file
/// rather than the platform. A client that assumed "Android records MP4" would
/// be wrong (Chrome's MediaRecorder has no MP4 muxer and writes WebM), and one
/// that trusted the file name would send `video/mp4` for anything a user had
/// renamed. Both are accepted at the ticket endpoint — the declared type is
/// what names the stored object — and both then fail late, or store a clip
/// under a content type that is a lie.
///
/// The declared MIME type from a picker is deliberately NOT consulted, even as
/// a fallback: on web it is `File.type`, which the browser itself derives from
/// the file extension for anything it does not recognise, so falling back to it
/// would quietly reintroduce the guess this function exists to remove.
///
/// Recognises:
///  * ISO base media (`....ftyp`) — MP4 and, for the `qt  ` brand, QuickTime.
///  * Matroska (`1A 45 DF A3`) carrying the `webm` DocType.
///
/// Returns null for everything else, including plain Matroska (`.mkv`), 3GPP,
/// AVI and a head too short to identify. Null is not an error state here; it is
/// the input to a clear, pre-upload rejection message.
String? sniffVideoContentType(Uint8List head) {
  if (head.length >= 12 &&
      head[4] == 0x66 && // f
      head[5] == 0x74 && // t
      head[6] == 0x79 && // y
      head[7] == 0x70) {
    // p
    final String brand = String.fromCharCodes(head.sublist(8, 12));
    if (brand == 'qt  ') return 'video/quicktime';
    // 3gp4 / 3gp5 / 3g2a are video/3gpp, which the server does not accept.
    if (brand.startsWith('3g')) return null;
    // Everything else with an ftyp box is MP4 family: isom, iso2, iso5, mp41,
    // mp42, avc1, M4V_, dash, mmp4 — including the fragmented MP4 iOS Safari's
    // MediaRecorder produces.
    return 'video/mp4';
  }

  if (head.length >= 4 &&
      head[0] == 0x1A &&
      head[1] == 0x45 &&
      head[2] == 0xDF &&
      head[3] == 0xA3) {
    // EBML. The DocType string decides: "webm" is accepted, "matroska" is not,
    // because the server's ALLOWED_CONTENT_TYPES has no video/x-matroska entry.
    final int limit =
        head.length < kVideoSniffHeadBytes ? head.length : kVideoSniffHeadBytes;
    final String header = String.fromCharCodes(head.sublist(0, limit));
    if (header.contains('webm')) return 'video/webm';
    return null;
  }

  return null;
}

/// Rounds [bytes] to whole megabytes for a message a user can act on.
///
/// Uses the MiB the cap is actually expressed in, so "50 MB" in the copy and
/// [kMaxUploadBytes] are the same number rather than a marketing-megabyte
/// approximation of it.
String formatMegabytes(int bytes) {
  final double mb = bytes / (1024 * 1024);
  return '${mb.toStringAsFixed(mb >= 10 ? 0 : 1)} MB';
}

/// Shown when the bytes are not one of the three accepted containers.
///
/// Names the formats rather than the MIME types: a user picking from their
/// camera roll knows "MOV", not "video/quicktime".
const String kUnrecognisedVideoMessage =
    'That file is not a video this app can analyse. Choose an MP4, MOV or '
    'WebM clip.';

/// Shown when a clip is over [kMaxUploadBytes].
///
/// Public because the file picker refuses an over-limit file on its *reported*
/// size, before reading it into memory, and must say the same thing there as
/// [videoRejectionReason] says later.
String oversizeMessage(int sizeBytes) {
  return 'That clip is ${formatMegabytes(sizeBytes)}, over the '
      '${formatMegabytes(kMaxUploadBytes)} limit. Trim it, or record a '
      'shorter swing.';
}

/// Returns a user-facing reason this clip cannot be uploaded, or null when it
/// can.
///
/// Called before the upload ticket is requested, on BOTH intake paths, so a
/// too-big or too-long or unrecognised clip is refused instantly instead of
/// after a slow upload that ends in a 400 or a 413.
///
/// [contentType] is the result of [sniffVideoContentType]; null means the bytes
/// were not one of the accepted containers. [durationSeconds] must be a
/// measured value — there is no default and no placeholder, because a
/// fabricated duration is exactly the kind of invented number this app refuses
/// to show.
String? videoRejectionReason({
  required String? contentType,
  required int sizeBytes,
  required double durationSeconds,
}) {
  if (contentType == null ||
      !kAcceptedVideoContentTypes.containsKey(contentType)) {
    return kUnrecognisedVideoMessage;
  }
  if (sizeBytes <= 0) {
    return 'That file is empty, so there is nothing to analyse.';
  }
  if (sizeBytes > kMaxUploadBytes) {
    return oversizeMessage(sizeBytes);
  }
  if (durationSeconds <= 0) {
    return 'The length of that clip could not be read, so it cannot be sent '
        'for analysis. Try another file, or record a swing instead.';
  }
  if (durationSeconds > kMaxClipSeconds) {
    return 'That clip is ${durationSeconds.toStringAsFixed(0)} seconds, over '
        'the ${kMaxClipSeconds.toStringAsFixed(0)} second limit. Trim it to '
        'just the swing.';
  }
  return null;
}
