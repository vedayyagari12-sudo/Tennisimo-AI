import 'dart:io';

import 'package:supabase_flutter/supabase_flutter.dart';

import 'api_client.dart';

/// Executes the signed upload issued by `POST /v1/uploads/ticket`.
///
/// The storage path is server-chosen (`swing-videos/{user_id}/{uuid}.mp4`); the
/// first segment is the bucket and the remainder is the object key. Returns
/// null on success, or a typed failure.
Future<ApiFailure?> uploadVideoToSignedUrl({
  required String storagePath,
  required String uploadToken,
  required File file,
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
        .uploadToSignedUrl(objectPath, uploadToken, file);
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
