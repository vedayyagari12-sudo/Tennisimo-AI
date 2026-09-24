# Project: Tennisimo AI

## What this app does
Analyzes a user's tennis swing from a phone video. Extracts pose keypoints,
detects the contact frame, computes swing-path metrics, and generates coaching
feedback. It does NOT measure ball spin or count rallies; it DOES measure
ball speed via user calibration (two tapped court reference points).
Shot type is INFERRED FROM TECHNIQUE, not from the ball.

## Non-negotiable scope boundaries

- Gemini's ONLY job is turning computed numbers into readable coaching text.
  Gemini NEVER performs the measurement or classification itself.
- All swing math is deterministic Python. It must be unit-testable without
  a network call or an AI model.

## Stack
- Frontend: Flutter (Dart), Material 3
- Backend: FastAPI (Python 3.13), deployed on Google Cloud Run (`us-east1`)
- Production backend URL: `https://tennisform-api-143709056949.us-east1.run.app`.
  This URL is hardcoded client-side as the `baseUrl` constant in
  frontend/lib/services/api_client.dart. There is no build-time override; if the
  URL changes it must be edited by hand.
- DB/Auth/Storage: Supabase (JWT ES256, verified via PyJWT + PyJWKClient)
- Pose: MediaPipe Tasks PoseLandmarker (BlazePose `full` bundle, VIDEO running
  mode), pinned `mediapipe==0.10.35`, run server-side in FastAPI. The legacy
  `mediapipe.solutions` API does NOT exist in this version -- do not write
  against it. Model bundle is vendored at
  backend/app/pose/models/pose_landmarker_full.task. See PIPELINE.md Stage 6.
- LLM: Gemini 2.5 Flash, text-only payloads

## Code rules
- Type hints on every Python function. Pydantic models for all request/response bodies.
- No hardcoded secrets. Everything via environment variables.
- Every math function in the analysis pipeline gets a unit test with synthetic
  keypoint data. No exceptions.
- Keep the analysis pipeline pure: functions take keypoint arrays in, return
  numbers out. No I/O inside math functions.

## Repo layout
backend/
  app/
    api/          # FastAPI routes
    pose/         # MediaPipe extraction
    analysis/     # contact detection + swing math (PURE, no I/O)
    feedback/     # Gemini prompt construction
    models/       # Pydantic schemas
  tests/
frontend/
  lib/
    screens/
    services/
    models/

## Known issues and decisions

- DECISION: `video_player` stays, and with it the two permissions
  androidx.media3 merges into the Android manifest -- `ACCESS_NETWORK_STATE`
  and `WAKE_LOCK`. `video_player` is genuinely required: reading a picked
  file's duration is mandatory for the upload ticket (`duration_s`), no file
  picker reports it, and the only alternative would be fabricating the number.
  Both are NORMAL (non-dangerous) permissions -- no runtime prompt. This is
  deliberately NOT the same case as the `RECORD_AUDIO` /
  `NSMicrophoneUsageDescription` reverted in `b2163ee`: that one had no code
  path using it at all, which is what made it a store-review liability rather
  than the cost of a needed feature.
- UNRESOLVED: `RECORD_AUDIO` still merges into the APK from
  `camera_android_camerax`'s own manifest despite `b2163ee`. Fully stripping it
  would need `tools:node="remove"`. Recorded as a known issue, not a decision.
