import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:tennisimo_ai/services/video_intake.dart';

/// Synthetic container heads. Real byte layouts, hand-built, so every intake
/// decision is exercised without a camera, a browser or a file on disk.

/// An ISO base media file: `[size] 'ftyp' [major brand] [compatible brands]`.
Uint8List isoBmff(String majorBrand, {String compatible = 'isomiso2avc1mp41'}) {
  return Uint8List.fromList(<int>[
    0x00, 0x00, 0x00, 0x20, // box size
    ...'ftyp'.codeUnits,
    ...majorBrand.codeUnits,
    0x00, 0x00, 0x02, 0x00, // minor version
    ...compatible.codeUnits,
  ]);
}

/// An EBML header carrying a `DocType` element (id 0x4282) with [docType].
Uint8List ebml(String docType) {
  return Uint8List.fromList(<int>[
    0x1A, 0x45, 0xDF, 0xA3, // EBML magic
    0x9F, // header size
    0x42, 0x86, 0x81, 0x01, // EBMLVersion = 1
    0x42, 0xF7, 0x81, 0x01, // EBMLReadVersion = 1
    0x42, 0x82, docType.length, ...docType.codeUnits,
  ]);
}

void main() {
  group('sniffVideoContentType', () {
    test('an MP4 from a phone camera is video/mp4', () {
      expect(sniffVideoContentType(isoBmff('isom')), 'video/mp4');
    });

    test('a QuickTime file is video/quicktime, not video/mp4', () {
      // The `qt  ` brand is the ONLY thing separating a .mov from an .mp4 at
      // the byte level; both are ISO base media.
      expect(sniffVideoContentType(isoBmff('qt  ')), 'video/quicktime');
    });

    test('a WebM from a browser MediaRecorder is video/webm', () {
      expect(sniffVideoContentType(ebml('webm')), 'video/webm');
    });

    test('a rejected type — AVI — resolves to nothing at all', () {
      final Uint8List avi = Uint8List.fromList(<int>[
        ...'RIFF'.codeUnits,
        0x24, 0x00, 0x00, 0x00,
        ...'AVI '.codeUnits,
        ...'LIST'.codeUnits,
      ]);
      expect(sniffVideoContentType(avi), isNull);
    });

    test('Matroska that is not WebM is refused, since the server has no '
        'video/x-matroska entry', () {
      expect(sniffVideoContentType(ebml('matroska')), isNull);
    });

    test('3GPP is refused rather than passed off as MP4', () {
      // Same ftyp box shape as an MP4, so a looser check would call this
      // video/mp4 and the server would accept a type it cannot be sure of.
      expect(sniffVideoContentType(isoBmff('3gp4')), isNull);
    });

    test('fragmented MP4 — what iOS Safari records — is still video/mp4', () {
      expect(sniffVideoContentType(isoBmff('iso5')), 'video/mp4');
      expect(sniffVideoContentType(isoBmff('mp42')), 'video/mp4');
      expect(sniffVideoContentType(isoBmff('dash')), 'video/mp4');
    });

    test('a head too short to identify yields null, not a guess', () {
      expect(sniffVideoContentType(Uint8List.fromList(<int>[0x00])), isNull);
      expect(sniffVideoContentType(Uint8List(0)), isNull);
      // First four bytes of an ftyp box, and nothing after them.
      expect(
        sniffVideoContentType(Uint8List.fromList(<int>[0, 0, 0, 0x20])),
        isNull,
      );
    });

    test('the type comes from the bytes, so identical names sniff '
        'differently and different names sniff the same', () {
      // The anti-hardcoding assertion. Nothing about these calls mentions a
      // platform or a file name, and three different inputs give three
      // different answers — which a hardcoded 'video/mp4' could not do.
      final Set<String?> resolved = <String?>{
        sniffVideoContentType(isoBmff('isom')),
        sniffVideoContentType(isoBmff('qt  ')),
        sniffVideoContentType(ebml('webm')),
      };
      expect(resolved, <String>{'video/mp4', 'video/quicktime', 'video/webm'});
    });

    test('everything it can produce is a type the server accepts', () {
      for (final Uint8List head in <Uint8List>[
        isoBmff('isom'),
        isoBmff('qt  '),
        ebml('webm'),
      ]) {
        expect(kAcceptedVideoContentTypes, contains(sniffVideoContentType(head)));
      }
    });
  });

  group('server limits', () {
    test('the cap mirrors MAX_UPLOAD_BYTES in backend/app/config.py', () {
      expect(kMaxUploadBytes, 52428800);
    });

    test('the clip cap mirrors duration_s le=60.0 on UploadTicketRequest', () {
      expect(kMaxClipSeconds, 60.0);
    });

    test('the accepted set mirrors ALLOWED_CONTENT_TYPES exactly', () {
      expect(kAcceptedVideoContentTypes, <String, String>{
        'video/mp4': 'mp4',
        'video/quicktime': 'mov',
        'video/webm': 'webm',
      });
    });
  });

  group('videoRejectionReason', () {
    test('an ordinary clip is accepted', () {
      expect(
        videoRejectionReason(
          contentType: 'video/webm',
          sizeBytes: 4 * 1024 * 1024,
          durationSeconds: 12.5,
        ),
        isNull,
      );
    });

    test('an unrecognised container is refused before any upload', () {
      expect(
        videoRejectionReason(
          contentType: null,
          sizeBytes: 1024,
          durationSeconds: 5,
        ),
        kUnrecognisedVideoMessage,
      );
    });

    test('a type outside the accepted set is refused even if it looks like a '
        'video', () {
      expect(
        videoRejectionReason(
          contentType: 'video/x-matroska',
          sizeBytes: 1024,
          durationSeconds: 5,
        ),
        kUnrecognisedVideoMessage,
      );
    });

    test('exactly at the size cap is allowed; one byte over is not', () {
      expect(
        videoRejectionReason(
          contentType: 'video/mp4',
          sizeBytes: kMaxUploadBytes,
          durationSeconds: 5,
        ),
        isNull,
      );
      final String? reason = videoRejectionReason(
        contentType: 'video/mp4',
        sizeBytes: kMaxUploadBytes + 1,
        durationSeconds: 5,
      );
      expect(reason, isNotNull);
      // The user is told both numbers, not just that it failed.
      expect(reason, contains('50 MB'));
    });

    test('an empty file is refused with its own wording', () {
      final String? reason = videoRejectionReason(
        contentType: 'video/mp4',
        sizeBytes: 0,
        durationSeconds: 5,
      );
      expect(reason, contains('empty'));
    });

    test('exactly at the duration cap is allowed; just over is not', () {
      expect(
        videoRejectionReason(
          contentType: 'video/mp4',
          sizeBytes: 1024,
          durationSeconds: kMaxClipSeconds,
        ),
        isNull,
      );
      final String? reason = videoRejectionReason(
        contentType: 'video/mp4',
        sizeBytes: 1024,
        durationSeconds: kMaxClipSeconds + 0.5,
      );
      expect(reason, isNotNull);
      expect(reason, contains('60 second'));
    });

    test('an unreadable duration is reported as unreadable, never sent as 0', () {
      final String? reason = videoRejectionReason(
        contentType: 'video/mp4',
        sizeBytes: 1024,
        durationSeconds: 0,
      );
      expect(reason, isNotNull);
      expect(reason, contains('could not be read'));
    });

    test('size is checked before duration, so the bigger problem is named '
        'first', () {
      expect(
        videoRejectionReason(
          contentType: 'video/mp4',
          sizeBytes: kMaxUploadBytes + 1,
          durationSeconds: 900,
        ),
        oversizeMessage(kMaxUploadBytes + 1),
      );
    });
  });

  group('formatMegabytes', () {
    test('the cap prints as the round number the copy promises', () {
      expect(formatMegabytes(kMaxUploadBytes), '50 MB');
    });

    test('small sizes keep a decimal so they are not all "0 MB"', () {
      expect(formatMegabytes(1536 * 1024), '1.5 MB');
    });
  });
}
