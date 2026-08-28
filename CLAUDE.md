# Project: TennisForm AI

## What this app does
Analyzes a user's tennis swing from a phone video. Extracts pose keypoints,
detects the contact frame, computes swing-path metrics, and generates coaching
feedback. It does measure ball spin, ball speed, and count rallies.
Shot type is INFERRED FROM TECHNIQUE, not from the ball.

## Non-negotiable scope boundaries

- Gemini's ONLY job is turning computed numbers into readable coaching text.
  Gemini NEVER performs the measurement or classification itself.
- All swing math is deterministic Python. It must be unit-testable without
  a network call or an AI model.

## Stack
- Frontend: Flutter (Dart), Material 3
- Backend: FastAPI (Python 3.11), deployed on Render
- DB/Auth/Storage: Supabase (JWT ES256, verified via PyJWT + PyJWKClient)
- Pose: MediaPipe Pose (BlazePose), run server-side in FastAPI
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