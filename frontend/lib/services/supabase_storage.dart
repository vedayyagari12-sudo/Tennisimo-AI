import 'package:flutter/foundation.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import 'api_client.dart';

/// Executes the signed upload issued by `POST /v1/uploads/ticket`.
///
/// The storage path is server-chosen
/// (`swing-videos/{user_id}/{uuid}.{mp4|mov|webm}`); the first segment is the
/// bucket and the remainder is the object key. Returns null on success, or a
/// typed failure.
///
/// Takes BYTES, not a `File`, for two reasons. A web build has no `dart:io`, so
/// a `File` parameter would make this whole leg mobile-only. And the clip
/// reaching here may never have existed on disk at all: a browser recording is
/// a Blob, and a picked file on web is a `blob:` URL.
///
/// [contentType] is sniffed from those same bytes by `sniffVideoContentType`
/// and is passed to Storage explicitly. Without it `uploadBinaryToSignedUrl`
/// would send its own default and the object would sit in the bucket labelled
/// as something it is not, even though the ticket named the right type.
///
/// This request does NOT go to the FastAPI backend. It is a cross-origin PUT
/// from the browser straight to Supabase Storage, so it is Supabase's CORS
/// configuration that governs it, not the API's. Supabase's gateway answers the
/// preflight with `Access-Control-Allow-Origin: *` and allows PUT, so no
/// project setting is required for this to work from a browser.
Future<ApiFailure?> uploadVideoToSignedUrl({
  required String storagePath,
  required String uploadToken,
  required Uint8List bytes,
  required String contentType,
}) async {
  if (currentAccessToken() == null) {
    return const ApiFailure(
      kind: ApiFailureKind.notSignedIn,
      message: 'You are not signed in. Sign in and try again.',
    );
  }

  final int slash = storagePath.indexOf('/');
  if (slash <= 0 || slash == storagePath.length - 1) {
    return ApiFailure(
      kind: ApiFailureKind.badResponse,
      message: 'The server returned an unusable upload path ($storagePath).',
    );
  }
  final String bucket = storagePath.substring(0, slash);
  final String objectPath = storagePath.substring(slash + 1);

  try {
    await Supabase.instance.client.storage
        .from(bucket)
        .uploadBinaryToSignedUrl(
          objectPath,
          uploadToken,
          bytes,
          FileOptions(contentType: contentType),
        );
    return null;
  } on StorageException catch (e) {
    return ApiFailure(
      kind: ApiFailureKind.server,
      message: 'Upload failed: ${e.message}',
    );
  } catch (e) {
    return ApiFailure(
      kind: ApiFailureKind.network,
      message: 'Upload failed. Check your connection. ($e)',
    );
  }
}
