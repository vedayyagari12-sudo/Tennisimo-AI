# TennisForm AI — Analysis Pipeline Architecture

**Document status:** design contract. No implementation code. Pydantic model definitions and function signatures below are the *contract*, not the implementation.
**Pipeline version:** `v2`
**Rubric version:** `rubric_v1` (unchanged — ball speed is deliberately not scored)
**Detector version:** `ball_cv_v1`
**Revision note:** this document supersedes the `v1` pose-only plan. The only substantive change is the addition of measured ball speed via user calibration (§0.2, Stages 10–11, §2.x, §3 `app/ball/`, §4.4, §1.20 budget, Stage 16 payload). Everything else — async job model, direct-to-Storage upload, the `PoseSequence` seam, the rubric, the Gemini numeric guard — is carried forward unchanged.

---

## 0. Scope

### 0.1 What this pipeline produces

`CLAUDE.md` line 6 now reads:

> "It does NOT measure ball spin or count rallies; it DOES measure ball speed via user calibration (two tapped court reference points)."

That is the scope this document implements, exactly. The `v1` document treated the original line 6 as a typo and designed a pose-only pipeline with no ball of any kind; that reading has been superseded by the corrected line, and this revision implements the corrected scope.

- **Ball spin — out of scope.** Permanently. Spin requires either resolving the ball's surface texture across frames (impossible at phone resolution with a 6.7 cm object under motion blur) or a Magnus-effect trajectory fit over a long flight arc (which needs an accurate 3D reconstruction we do not have). There is no honest path to it and there is no field for it in the schema.
- **Rally counting — out of scope.** Requires multi-shot segmentation, both players, and bounce detection over a long clip. The analysis window is a single swing.
- **Ball speed — IN scope**, as a nullable integer, produced by a real detection-and-calibration pipeline, subject to hard gates that return `null` rather than a guess.
- **Shot type — inferred from technique**, never from the ball. The ball detector's output does **not** feed shot-type inference, does not feed the rubric, and does not feed `overall_score`. It is a reported measurement standing beside the swing analysis, not inside it.

### 0.2 Why ball speed is a justified exception to the anti-fabrication stance

The `v1` document took a hard line: no metric units anywhere, everything in **torso units (TU)**, because "inventing a '112 mph racket speed' from a 2D wrist trajectory would be the single most damaging thing this product could do." That line still stands and is unchanged for every swing metric. `peak_hand_speed_tu_s` is still TU/s and is still never converted.

Ball speed is different in kind, and the difference is the whole justification:

| | Swing metrics | Ball speed |
|---|---|---|
| Source signal | Human body landmarks from a monocular projection | A directly detected object with a known physical size travelling in the image |
| Metric scale | **None.** There is no length reference anywhere in the frame. | **A real one, supplied by the user.** Two tapped points on a court feature of a standardised, published length. |
| What "mph" would mean | A number manufactured by assuming a body dimension we never measured | A pixel displacement divided by a measured px-per-metre and a measured Δt |

The mph unit is permitted to exist in this schema **specifically because it is backed by a measurement pipeline** — actual ball detection plus a user-supplied real-world distance — rather than being inferred or guessed from body landmarks the way the swing metrics are. The unit's presence is earned by a calibration step the user performs, not asserted by the software.

This exception is deliberately narrow and is fenced in four ways:

1. **`ball_speed_mph` is an integer, never a float.** The measurement is not precise enough to justify a decimal and the type itself enforces that (§2.5). No float mph value exists anywhere in the codebase; the single `round()` to `int` happens once, in `analysis/speed.py`.
2. **`null` is a first-class, common, non-error outcome.** No calibration → `null`. Fewer than 3 valid detections → `null`. Implausible result → `null`. There is no fallback estimator, no "best guess", no interpolation from swing speed. The absence of a number is reported honestly with a machine-readable reason.
3. **It is excluded from the rubric and the score.** There is no "ideal ball speed" band. `MetricUnit` deliberately does **not** gain an `mph` member (§2.1), so a scored metric cannot even be expressed in mph by construction.
4. **The Gemini numeric guard is extended, not relaxed** (§17). `mph` remains a banned substring whenever `ball_speed_mph is null`, and when it is non-null the model may emit `mph` only immediately after the exact integer we supplied.

All swing distances remain in **torso units (TU)**, defined in Stage 7. The unit glossary sent to Gemini now contains exactly two kinds of unit — `mph` for the one measured physical quantity, and everything else body-relative.

### 0.3 Confirmed constants

| Value | Setting | Note |
|---|---|---|
| 50 MB upload cap | `max_upload_bytes = 52_428_800` | 50 MiB. Set to match Supabase's free-tier per-file upload limit — a larger cap would be accepted by the API and then rejected by Storage. |
| 30 fps pose resample | `analysis_fps = 30.0` | fixed |
| 60 s max clip | `max_clip_seconds = 60.0` | intake cap; the *analysis window* remains 8 s, located by a keyframe motion scan (Stage 5) |

The 60 s intake cap with an 8 s analysis window is what makes a keyframe motion scan necessary for long clips; that is specified in Stage 5 and budgeted in §1.20.

---

## 1. The Exact Processing Sequence

### Execution model decision (made first, because it shapes everything)

**Analysis runs as an asynchronous background job, not inside the HTTP request.** The client gets `202 Accepted` with an `analysis_id` and polls.

Justification, concretely:

- Render's edge terminates HTTP requests that produce no response bytes for ~100 s. Our warm-path budget is 21–37 s (§1.20), which fits — but a cold start on the free tier adds 30–60 s of container spin-up *before* our code runs. Warm-path-fits-in-budget is not the same as always-fits, and a timeout mid-analysis gives the user a spinner that dies with no error.
- Render free/starter gives ~0.5 shared vCPU and 512 MB RAM. MediaPipe inference is CPU-bound, synchronous, and holds the GIL. A single synchronous analysis request blocks the entire ASGI worker — health checks included. Two concurrent uploads on a synchronous design will time out both.
- A background job lets us **serialize** CPU work behind a `ThreadPoolExecutor(max_workers=1)` and return `429` past a queue depth of 4, which is the only reliable OOM guard on a 512 MB container. This matters more in `v2`, not less: OpenCV's baseline RSS is now part of the picture (§1.20).

We do **not** introduce Celery/Redis/RQ for the MVP. The job runner is an in-process single-worker executor; the **source of truth for job state is the Supabase `analysis_jobs` row**, not process memory. On instance restart, in-flight jobs are orphaned — handled by a staleness rule: any job in `running` whose `heartbeat_at` is older than 180 s is reported to the poller as `failed` with `ErrorCode.WORKER_LOST`, and the client may retry. This costs one timestamp column and removes the entire class of "spinner forever" bugs.

**Upload path decision:** the client uploads the video **directly to Supabase Storage** using its own user JWT, then POSTs only the storage path to FastAPI. Reasons: (a) avoids a second full transfer of a 15–50 MB file over a mobile network through Render, (b) a slow mobile upload can exceed the Render request timeout before analysis even begins, (c) the file must live in Storage anyway for replay, (d) Storage RLS enforces per-user isolation for free. There is exactly one upload path — no multipart fallback endpoint.

**Temp-file lifetime decision (changed in v2):** the downloaded video previously could be deleted immediately after pose extraction. It can no longer be — Stage 10 performs a **second, independent decode pass** over the same file. The `finally` that deletes the temp file now lives in the orchestrator's outermost frame and fires after Stage 11, regardless of outcome. This is called out because "delete after pose" is the natural thing for an implementer to write and it would break ball detection intermittently and confusingly.

---

### Stage 1 — Upload ticket issuance

| | |
|---|---|
| **Owner** | `backend/app/api/routes_uploads.py` |
| **In** | `POST /v1/uploads/ticket`, `Authorization: Bearer <supabase_jwt>`, body `UploadTicketRequest` |
| **Out** | `UploadTicketResponse` — signed upload URL + canonical `storage_path` |
| **Fails** | `401 AUTH_INVALID_TOKEN`; `400 UNSUPPORTED_CONTENT_TYPE`; `413 FILE_TOO_LARGE` (declared `size_bytes` > 50 MB); `503 STORAGE_UNAVAILABLE` |

Path is server-chosen, never client-chosen: `swing-videos/{user_id}/{uuid4}.{ext}`. The `user_id` prefix is what makes the later ownership check in Stage 3 a pure string comparison rather than a database round trip.

---

### Stage 2 — Auth (applies to every route)

| | |
|---|---|
| **Owner** | `backend/app/api/auth.py` |
| **In** | `Authorization` header (str) |
| **Out** | `AuthedUser` dataclass — `user_id: UUID`, `email: str \| None`, `raw_claims: dict` |
| **Fails** | `401` — missing header, malformed, expired, bad signature, wrong `aud`, wrong `iss`, unknown `kid` |

`PyJWKClient` pointed at `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`, constructed **once at app startup** and held on `app.state` with `cache_keys=True, lifespan=300`. Constructing it per-request causes a JWKS fetch per request and will rate-limit under load.

Verification parameters, fixed: `algorithms=["ES256"]`, `audience="authenticated"`, `issuer=f"{SUPABASE_URL}/auth/v1"`, `options={"require": ["exp", "sub", "aud", "iss"]}`, `leeway=10` seconds. `sub` becomes `user_id`.

Only ES256 asymmetric signing keys are supported, per CLAUDE.md. Legacy HS256 shared-secret tokens are explicitly rejected — accepting both would mean an attacker who obtains the anon key could forge tokens. If the Supabase project has not been migrated to asymmetric keys, that migration is a prerequisite, not a fallback.

---

### Stage 3 — Analysis request intake and validation

| | |
|---|---|
| **Owner** | `backend/app/api/routes_analyses.py` |
| **In** | `POST /v1/analyses`, `CreateAnalysisRequest` + `AuthedUser` |
| **Out** | `202` + `CreateAnalysisResponse` |
| **Fails** | `403 STORAGE_PATH_FORBIDDEN` (path prefix ≠ `swing-videos/{user_id}/`); `404 OBJECT_NOT_FOUND`; `413 FILE_TOO_LARGE`; `400 CALIBRATION_INVALID`; `429 QUEUE_FULL` (depth > 4) |

Writes `analysis_jobs` row with `status="queued"`, submits to the executor, returns immediately. Ownership is enforced by the path prefix check *before* any download — a user cannot point the analyzer at another user's object.

**New in v2:** the optional `ball_speed_calibration` block is validated here, synchronously, because every check that can be done without the video should fail loudly at request time rather than silently at job time:

- Two tapped points must be at least 0.05 apart in normalized units (a double-tap on the same spot yields a near-zero pixel segment and a divide-by-almost-zero scale).
- If `reference != CUSTOM`, `distance_m` must equal the canonical court dimension for that reference within 0.01 m. The client sends both so the server can detect a desynced client build; a mismatch is `CALIBRATION_INVALID`, not a silent server-side override.
- `capture_width_px`/`capture_height_px` must be positive and their aspect ratio must be a plausible video aspect (between 0.4 and 2.5).

**Absence of `ball_speed_calibration` is not an error and never blocks anything.** It skips Stages 10 and 11 entirely, saves 1–2.5 s of wall time, and yields `ball_speed.ball_speed_mph = null` with `unavailable_reason = "not_calibrated"`.

---

### Stage 4 — Video fetch (IMPURE)

| | |
|---|---|
| **Owner** | `backend/app/services/storage.py` |
| **In** | `storage_path: str` |
| **Out** | `Path` to a temp file on local disk |
| **Fails** | `OBJECT_NOT_FOUND`, `STORAGE_UNAVAILABLE`, `FILE_TOO_LARGE` (actual size exceeds declared) |

Downloaded with the **service-role key**, streamed to disk in 1 MB chunks. Never `.read()` into memory — a 50 MB `bytes` object alongside a loaded MediaPipe graph in a 512 MB container is the most likely OOM in this system. Temp file deleted in a `finally` block regardless of outcome — **now placed after Stage 11**, per the lifetime decision above. Budget: 1–3 s.

---

### Stage 5 — Decode, orientation correction, and frame sampling (IMPURE)

| | |
|---|---|
| **Owner** | `backend/app/pose/video_io.py` |
| **In** | `Path` |
| **Out** | `VideoMeta` + a **generator** of `(timestamp_s: float, frame_rgb: np.ndarray)` |
| **Fails** | `DECODE_FAILED`, `UNSUPPORTED_CODEC`, `VIDEO_TOO_SHORT` (< 2.0 s), `VIDEO_TOO_LONG` (> 60 s at intake; analysis window capped at 8 s), `NO_VIDEO_STREAM` |

Decisions, all opinionated:

- **PyAV, not OpenCV, for decode.** `cv2.VideoCapture` handles container rotation metadata inconsistently across builds — some auto-apply the display matrix, some ignore it. Silent 90° errors are catastrophic here because a sideways human breaks pose detection entirely. PyAV exposes the stream's **display-matrix side data** explicitly so we apply rotation deliberately and record it in `VideoMeta.rotation_deg`. (OpenCV is now a dependency for ball detection, but it is used only as an image-processing library on arrays we decoded ourselves — never as a demuxer.)
- **Rotation source is the MP4 `tkhd` display matrix, not EXIF.** EXIF orientation applies to still images (JPEG/HEIC); phone video carries rotation in the container track header. iPhones record landscape-sensor frames with a 90° display matrix for portrait captures. We apply `rot90` k times after decode.
- **Rotation self-check.** If, after Stage 6, the pose-detection success rate is below 20 %, retry the whole clip once with rotation `+180°`. Upright-person assumption is baked into BlazePose; a wrongly oriented clip produces near-zero detections rather than wrong landmarks, which makes this check cheap and unambiguous. One retry only.
- **Timestamps come from PTS × time_base, never from `avg_frame_rate`.** Phone video is variable-frame-rate: iOS "Auto FPS" drops 30 → 24 fps in low light mid-clip. Trusting nominal fps corrupts every velocity in the pipeline — and, in v2, corrupts the ball-speed denominator directly.
- **Analysis frame rate: 30 fps, fixed.** For each target time `t_k = t_0 + k/30`, select the decoded frame whose PTS is nearest `t_k`. We record the **actual** PTS of each selected frame in `PoseSequence.timestamps_s` and compute all derivatives with real Δt, not an assumed 1/30. If the source is 24 fps, some target slots select the same frame twice; the recorded timestamps expose this and the velocity math stays correct. *(This duplicate-selection behaviour is exactly why ball detection does **not** use this stream — see Stage 10.2.)*
  - Why 30 and not 60: 60 fps doubles MediaPipe cost, which is ~70 % of our budget. Why not 24: contact is a ~4 ms event; at 30 fps we localize it to ±17 ms, which is inside the coaching-relevant resolution, and peak-hand-speed estimation degrades noticeably below 30.
- **Downscale to longest side = 640 px** before inference. BlazePose resizes internally to 256×256; feeding 1080p burns CPU in the resize for zero accuracy gain.
- **Cap: 240 sampled frames (8.0 s).**
- **Analysis-window location for long clips (new in v2, forced by the 60 s intake cap).** For clips longer than 10 s we cannot afford to decode every frame just to find the swing. Instead we run a **keyframe-only motion scan**: PyAV with `stream.codec_context.skip_frame = "NONKEY"`, decoding only I-frames (phone encoders emit roughly one per 1–2 s, so 30–60 frames for a 60 s clip), downscaled to 160 px long edge, and take the mean absolute difference between consecutive keyframes. The 8 s analysis window is centred on the keyframe with maximum motion energy, clamped to the clip bounds. This locates the swing to roughly ±1 s, which an 8 s window absorbs comfortably. Cost 0.2–0.8 s (§1.20), versus ~14 s to decode a 60 s clip at full rate. Clips ≤ 10 s skip the scan and use the whole clip.
- **Streaming, never buffered.** 240 frames at 640×360×3 bytes is 166 MB held simultaneously. The generator yields one frame at a time; only landmarks are retained. This is a hard requirement, not an optimization.

Budget: 2–4 s (plus 0.2–0.8 s motion scan for long clips).

---

### Stage 6 — MediaPipe Pose extraction (IMPURE)

| | |
|---|---|
| **Owner** | `backend/app/pose/extractor.py` |
| **In** | frame generator from Stage 5 |
| **Out** | `RawPoseSequence` — `landmarks: np.ndarray (T, 33, 4)` float32, `world: np.ndarray (T, 33, 3)` float32, `timestamps_s: np.ndarray (T,)`, `detected: np.ndarray (T,)` bool |
| **Fails** | `NO_POSE_DETECTED` (< 40 % of frames), `MULTIPLE_SUBJECTS_SUSPECTED` |

Configuration, fixed: `model_complexity=1`, `static_image_mode=False`, `smooth_landmarks=False`, `min_detection_confidence=0.5`, `min_tracking_confidence=0.5`.

- `model_complexity=1` not `2`: complexity 2 is roughly 2× the cost for a modest landmark-accuracy gain, and our budget is CPU-bound on a shared vCPU.
- `static_image_mode=False` enables the detector→tracker pipeline, which is both faster and temporally more coherent across a continuous clip.
- `smooth_landmarks=False` — **we do our own smoothing in the pure layer.** MediaPipe's built-in one-euro filter is an uncontrolled, untestable, stateful transform sitting between the sensor and our math. Disabling it moves 100 % of the filtering into `analysis/smoothing.py` where it is deterministic and unit-tested.

The `Pose` object is created **once per job and closed in a `finally`** — MediaPipe graph construction costs ~300 ms and leaks native memory if not closed. **In v2 the close is also a memory-ordering requirement:** the extractor must be closed *before* Stage 10 opens its decode pass, so that the ~100 MB graph + TFLite arena is released before the ball stage allocates its ~25 MB working set. Peak RSS depends on this ordering (§1.20).

Per-landmark channels: `x, y` normalized to `[0,1]` of image width/height (**y grows downward**), `z` relative depth (same scale as `x`, origin at hip midpoint), `visibility` in `[0,1]`. `world` landmarks are in meters relative to hip midpoint and are used **only** as a cross-check signal, never as a reported measurement (see §5).

Budget: **14–22 s** at ~60–90 ms/frame on 0.5 vCPU. This stage is ~65 % of total wall time.

---

### THE SEAM — `PoseSequence`

Stage 6's output, wrapped in a frozen dataclass, is the boundary. **Everything before it is impure; Stages 7–9 are pure math.** Full definition in §4.1. v2 adds a **second seam, `BallTrack`** (§4.2), because the ball path re-enters impure territory at Stage 10 and must come back out into pure code before any number is derived.

---

### Stage 7 — Gap handling, smoothing, normalization (PURE)

| | |
|---|---|
| **Owner** | `analysis/smoothing.py`, `analysis/normalize.py` |
| **In** | `PoseSequence` |
| **Out** | `NormalizedSequence` + `PoseQuality` |
| **Fails** | Returns `PoseQuality.usable=False` → orchestrator raises `POSE_QUALITY_TOO_LOW`. Never raises from inside a math function. |

Order of operations is fixed and matters:

1. **Gate on visibility.** A frame is valid if the mean `visibility` of the 12 core landmarks (shoulders, elbows, wrists, hips, knees, ankles) ≥ 0.5. Invalid frames become gaps.
2. **Interpolate gaps ≤ 3 frames** (100 ms) linearly per coordinate. Longest gap > 3 → `usable=False`.
3. **Aspect correction.** Multiply `x` by `width/height`. Skipping this is the classic silent bug: on a 9:16 portrait clip, one unit of normalized x is 0.56 the pixel distance of one unit of y, and every computed angle is wrong by a view-dependent amount. *(The same trap applies to the calibration points — see Stage 11.1, which is why `scale_from_calibration` operates on true pixels, never on normalized coordinates.)*
4. **Flip y** so up is positive.
5. **Origin shift** to mid-hip: `origin_t = (L_HIP + R_HIP)/2` per frame. All coordinates become body-relative, killing camera pan/translation.
6. **Scale normalization — torso units.** `torso_px = median_t( |mid_shoulder_t − mid_hip_t| )`. Divide all coordinates by it. One TU = one shoulder-to-hip torso length.
   - Why torso length and not shoulder width or height: shoulder width foreshortens catastrophically as the player turns side-on (it can collapse by 60 % during a takeback), and full height requires reliable ankle and head landmarks simultaneously. Torso length is the most view-stable body segment through a tennis swing and stays visible in every phase.
   - The **median over frames**, not per-frame, so the scale is a single constant per clip — a per-frame scale would inject the torso's own foreshortening into every derived velocity.
7. **Smoothing — Savitzky-Golay, `window_length=7`, `polyorder=2`,** applied independently per coordinate along the time axis, with edge handling by polynomial fit rather than reflection.
   - Why SG over a moving average: a moving average attenuates and *time-shifts* peaks. Contact detection is fundamentally the localization of a velocity peak; an algorithm that moves peaks is disqualified. In v2 a shifted contact frame would also mislocate the ball measurement window, so this choice now protects two stages.
   - Why SG over zero-phase Butterworth (`filtfilt`), the biomechanics standard: SG is a single scipy call with no filter-design step, no phase concerns by construction, and its window is directly interpretable in frames. Window 7 at 30 fps is a 233 ms support, roughly a 6 Hz corner — comfortably above the 3–5 Hz fundamental of a swing and below sensor noise.
8. **Derivatives.** Velocity by central difference on smoothed coordinates using **actual Δt** from `timestamps_s`. Acceleration by central difference on velocity. Units: TU/s and TU/s².
9. **Swing direction sign.** `swing_direction_sign ∈ {−1,+1}` = sign of racket-hand x-displacement from takeback-end to contact. Every signed-x metric is multiplied by it, so "forward" means "toward the target" regardless of which way the player faces the camera.

---

### Stage 8 — Handedness detection (PURE)

| | |
|---|---|
| **Owner** | `analysis/handedness.py` |
| **In** | `NormalizedSequence`, `hint: Handedness \| None` |
| **Out** | `HandednessResult` |
| **Fails** | Cannot raise. Low separation → `confidence < 0.5`, and if a hint exists the hint wins with `source="user_hint"` and a warning is appended. |

Racket hand = the wrist with the greater score on: integrated path length (Σ‖Δp‖) over the clip, **and** peak radial distance from mid-hip. Confidence = normalized margin between the two wrists, clipped to `[0,1]`. Tie-break: the wrist further from mid-hip at the peak-speed frame.

Two-handed backhands are the known failure case — both wrists move nearly identically. This is *why* `Handedness` is detected separately from grip: two-handedness is decided in Stage 13 by wrist separation, and handedness falls back to the user hint whenever confidence < 0.5. The client always sends `handedness_hint` from the user profile, so this is a fallback that is essentially always available.

---

### Stage 9 — Contact-frame detection (PURE)

| | |
|---|---|
| **Owner** | `analysis/contact.py` |
| **In** | `NormalizedSequence`, `HandednessResult` |
| **Out** | `ContactDetection` |
| **Fails** | Cannot raise. `confidence` reflects peak ambiguity; below 0.35 the orchestrator downgrades the response to `partial` **and refuses to run ball speed** (Stage 11 gate `contact_unreliable`). |

There is no ball landmark at this point in the pipeline, so contact must be inferred from the kinematic signature. **Contact detection remains pose-only and is never revised by the ball detector** — the dependency runs one way, contact → ball window, and never back. Letting a noisy classical detector adjust the contact frame would put a non-deterministic signal into the swing metrics, which is precisely what CLAUDE.md forbids.

Algorithm, fixed:

1. Compute racket-hand speed `s(t)` in TU/s.
2. Find the global maximum `t_peak`.
3. Restrict to the **forward-swing window**: frames after the last local minimum of forward displacement preceding `t_peak`.
4. **Contact = the first frame at or after `t_peak` where `s` has dropped by ≥ 5 % from its peak** — the deceleration onset. Rationale: in a well-struck ball the hand is still accelerating or at plateau *into* the ball; the abrupt decel is the impact and the start of the arm's braking. Using the raw argmax alone systematically lands 1–2 frames early.
5. Sanity gates, each of which lowers confidence rather than rejecting: wrist must be forward of mid-hip (`x·swing_direction_sign > 0`), arm near-extended relative to its own clip range, contact not within 4 frames of either clip end.
6. `prominence_ratio` = peak speed ÷ second-highest well-separated peak. Confidence is a monotone function of this ratio and the sanity gates.

`confidence` is surfaced in the response and gates the feedback tone (Stage 16).

**New field: `contact_absolute_time_s`.** `time_s` is measured from `analysis_window_start_s`; Stage 10 seeks into the *original* file and therefore needs an absolute PTS. `contact_absolute_time_s = analysis_window_start_s + time_s` is computed once, here, and carried explicitly. On a 60 s clip with an 8 s window this is the difference between measuring the right 250 ms and measuring nothing — an off-by-a-window bug that would be invisible on the short test clips a developer would naturally use.

---

### Stage 10 — Ball detection (IMPURE frame acquisition + PURE-CV detection) **[NEW]**

| | |
|---|---|
| **Owner** | `ball/frames.py` (impure), `ball/detector.py` (pure-cv), `ball/geometry.py` (pure), `ball/track.py` (pure) |
| **In** | temp video `Path`, `ContactDetection.contact_absolute_time_s`, `BallSpeedCalibration`, `NormalizedSequence` (for player exclusion boxes), `VideoMeta` |
| **Out** | `BallTrack \| None` + `BallDetectionSummary` |
| **Fails** | Never fails the job. Every failure path yields `BallTrack = None` and a `BallSpeedUnavailableReason`. Hard wall-clock deadline of 6 s → `detection_timeout`. |

Skipped entirely when `ball_speed_calibration` is absent, when `contact.confidence < 0.35`, or when `PoseQuality.estimated_camera_view ∈ {front, behind}` (§10.5).

There is **no learned detector here**. We have no labelled tennis-ball dataset, no budget to collect one, and shipping an untrained or off-the-shelf COCO detector for a 6 px object would be worse than classical CV in both accuracy and honesty. This is background subtraction, HSV thresholding, and contour filtering — three deterministic operations whose parameters are written down below and whose behaviour is unit-testable on programmatically rendered frames.

#### 10.1 Coordinate space — `CAL_SPACE`

One canonical pixel space is defined for everything ball- and calibration-related:

**`CAL_SPACE` = the decoded frame after display-matrix rotation, downscaled so that the long edge is `ball_detection_long_edge_px = 1280`, aspect preserved.**

- Calibration taps are mapped into it by `ball/geometry.map_calibration_points()`.
- Ball detections are produced natively in it.
- The pose stream's 640 px space is a pure 2× scaling of it, so player exclusion boxes map across with a single scalar.

Keeping this as a named, single space rather than "whatever the frame happens to be" is the mitigation for the most likely silent bug in this whole feature: a scale factor computed in one space and applied in another produces a plausible-looking but wrong mph value with no error anywhere.

#### 10.2 Temporal resolution — ball detection runs on NATIVE-fps frames, not the 30 fps pose stream

**Decision: ball detection performs a second, independent decode pass at the source's native frame rate. It does not consume `PoseSequence` frames.** This is not an optimization; the 30 fps resampled stream is unusable for this purpose, for three separate reasons:

1. **Duplicate frames.** Stage 5 selects, for each 1/30 s slot, the *nearest* decoded frame by PTS. On a 24 fps source, or on any VFR clip that dips below 30 fps, some slots select the same source frame twice. A duplicated frame contributes a **zero pixel displacement** step. Zero is not an outlier the median shrugs off — a run of them drags the median directly toward zero and produces a confidently reported, badly low mph. There is no way to detect this after the fact from the resampled stream alone.
2. **Resolution.** The pose stream is downscaled to 640 px long edge, chosen because BlazePose internally resizes to 256×256 anyway. At 640 px a tennis ball in typical framing is **~3 px across**. Three pixels does not survive a contour filter; there is no shape to filter on. At 1280 px it is ~6 px across, and a motion streak is roughly 5 px thick, which is the practical floor.
3. **Sample count.** A 0.25 s measurement window yields at most 8 frames at 30 fps but 16 at 60 fps. The `high` confidence tier (§11.4) is unreachable at 30 fps by construction. See §11.5 for the capture policy this implies: **30 fps is fully supported and is the expected case**, and `high` is treated as an upside on 60 fps footage rather than a target the capture screen pushes users toward.

**What "native fps" actually buys, honestly.** Phone video is overwhelmingly 30 or 60 fps. Native decoding therefore gives at most 2× more samples than the pose stream, sometimes exactly the same number. The categorical win is eliminating duplicate frames and getting real PTS for every sample; the sample-count win is conditional on the user capturing at 60 fps. We do not pretend this is high-speed videography. We do surface `native_fps` in `BallDetectionSummary` so the confidence level and the fps are always visible together.

**Motion blur, and what it does to the circularity criterion.** This is the second half of the temporal-resolution problem and it is the one that actually threatens the contour filter. Consider a representative capture: player filmed from ~8 m to the side, court width filling ~80 % of a 1920 px frame → roughly **140 px/m** at the court plane, or **93 px/m** in `CAL_SPACE` at 1280 px. A ball struck at 30 m/s:

| Quantity | Value |
|---|---|
| Ball diameter (6.7 cm) | ~6 px in `CAL_SPACE` |
| Displacement per frame at 60 fps | 0.50 m ≈ **47 px** |
| Displacement per frame at 30 fps | 1.00 m ≈ **93 px** |
| Streak length at 1/60 s exposure | 0.50 m ≈ **47 px** |
| Streak length at 1/250 s exposure (bright daylight) | 0.12 m ≈ **11 px** |

So the ball's image is a **capsule roughly 6 px thick and 11–47 px long**, with aspect ratios from ~2:1 to ~8:1. Its circularity `4πA/P²` is nowhere near 1.0 — for a 6×47 capsule it is approximately 0.24. **A circularity threshold tuned for a round ball would reject essentially every in-flight detection.** This is exactly the failure the concern anticipates, and designing around it silently would mean shipping a detector with near-zero recall.

**Resolution: a two-tier contour filter (§10.4) that explicitly accepts elongated blur streaks**, with the loss of the circularity constraint compensated by three replacement constraints that a blur streak satisfies and the realistic false positives do not — solidity, minor-axis bounds, and **streak-orientation agreement with the track's own displacement direction**. The orientation test is the important one: a motion streak is by definition aligned with the ball's motion, so once the track has one step, every subsequent streak must point along it. Court-line fragments, shoe edges, and clothing highlights have no such obligation.

**What this costs elsewhere.** A second decode pass is not free and its consequences are budgeted rather than hidden: it forces the temp file to survive past Stage 9 (see the execution-model preamble), it decodes at 1280 px rather than 640 px, it pays a keyframe-seek penalty of up to ~2 s of discarded frames, and it adds ~25 MB of working set. The memory cost is absorbed only because MediaPipe's ~100 MB is released first (Stage 6, §1.20). Numbers in §1.20.

#### 10.3 Frame acquisition and background model

- **Seek.** `container.seek()` to the keyframe at or before `t_bg_start`, then decode forward discarding frames until `t_bg_start`. Phone GOPs are typically 1–2 s, so up to ~2 s of frames are decoded and thrown away. This is budgeted, not hidden (§1.20).
- **Time ranges**, all absolute PTS:
  - Background pre-roll: `[contact_abs − 0.55 s, contact_abs − 0.05 s]`, from which **15 frames** are sampled evenly.
  - Measurement window: `[contact_abs, contact_abs + 0.25 s]`.
- **Background model: fixed temporal median.** The 15 pre-roll frames are converted to grayscale, stacked, and reduced by per-pixel `median` to a single background image, computed **once** and held fixed across the whole measurement window.
  - Why median rather than MOG2: (a) it is a pure function of a stacked array, so it lives in `ball/detector.py` and is unit-testable with a synthetic stack — no OpenCV state machine, no learning-rate tuning, no warm-up semantics to get wrong; (b) a small fast object is excluded from a per-pixel median by construction; (c) a per-frame-updated model risks absorbing a ball that briefly slows or is momentarily occluded.
  - Why computed once rather than rolling: a rolling 15-frame median at 1280×720 costs ~150–250 ms *per frame*, which alone would blow the stage budget. Computing it once costs ~250 ms total.
  - **Honest cost of freezing it:** the camera is handheld and drifts. Over the ~0.8 s span from pre-roll to window end, drift of a few pixels produces thin ghost edges along high-contrast static features — principally court lines. These ghosts are removed downstream by the HSV colour gate (white lines fail the yellow branch) and by the minor-axis and length bounds (a line ghost is 1–3 px thick and hundreds of px long). We accept them rather than adding global-motion compensation, which would be a second estimator with its own failure modes.
- **Motion mask:** `absdiff(frame_gray, background_gray) > 18` (8-bit levels). 18 is above typical sensor noise (σ ≈ 3–6 levels in daylight, higher indoors) and below the contrast of a lit ball against court surface.
- **Morphology:** `MORPH_OPEN` with a 3×3 ellipse (removes isolated speckle), then `MORPH_CLOSE` with a 5×5 ellipse (reconnects a streak broken by a mid-streak intensity dip). Order matters — closing first would fuse speckle into fake blobs.

#### 10.4 Colour gate and the two-tier contour filter

**HSV thresholding** is applied to the original colour frame and ANDed with the motion mask. Two branches, either of which passes:

| Branch | Condition (OpenCV HSV, H ∈ [0,179]) | Purpose |
|---|---|---|
| Optic-yellow | `H ∈ [25, 45]`, `S ∈ [60, 255]`, `V ∈ [120, 255]` | Standard yellow tennis ball in daylight |
| Achromatic-bright | `V > 200`, `S < 60` | Blown-out ball in direct sun, or a white/worn ball |

The achromatic branch is dangerous on its own — it matches every white court line and every white shoe. **It is only ever evaluated inside the motion mask**, which static court lines fail categorically. This is why the motion mask is applied first and is not optional.

Contours are extracted with `RETR_EXTERNAL` + `CHAIN_APPROX_SIMPLE`. Each contour is reduced to features: `area`, `perimeter`, `circularity = 4πA/P²`, `min_area_rect` (major/minor axis, orientation), `aspect = major/minor`, `solidity = area / convex_hull_area`, `fill = area / rect_area`, and the median hue of the enclosed pixels.

**Tier A — round (low-blur, short exposure, or slow ball):**

| Criterion | Bound |
|---|---|
| area | 12–1200 px² |
| circularity | ≥ 0.65 |
| aspect (major/minor) | ≤ 1.6 |
| solidity | ≥ 0.85 |
| fill | ≥ 0.55 |

Note on the circularity bound: a discretized disc does not reach 1.0. `cv2.arcLength` over-estimates perimeter on small blobs, giving circularity ≈ 0.75–0.85 at r = 3 px and ≈ 0.90 at r = 10 px. 0.65 leaves headroom for that discretization floor without admitting obviously non-round shapes.

**Tier B — streak (the motion-blur case, and the common one):**

| Criterion | Bound | Why it survives the loss of circularity |
|---|---|---|
| area | 12–1500 px² | Rejects large moving objects (a player limb is thousands of px²) |
| circularity | ≥ 0.20 | Loosened deliberately; a 6×47 capsule sits at ~0.24 |
| aspect | 1.6–12.0 | Upper bound rejects court-line ghosts, which are 100:1 or worse |
| minor axis | 3–20 px | The ball's *thickness* does not blur; this is the strongest single constraint and is nearly invariant to shutter speed |
| solidity | ≥ 0.80 | A blur streak is a convex capsule. Limb fragments, shoe edges, and shadow slivers are concave and fail. |
| fill | ≥ 0.50 | |
| orientation agreement | major-axis direction within **25°** of the track's last step direction | Only applied once the track has ≥ 1 step. This is the primary false-positive killer for streaks. |

**Player exclusion.** A per-frame exclusion box is computed from the pose landmarks: the bounding box of shoulders, hips, knees, and ankles, dilated 15 %, mapped from the 640 px pose space into `CAL_SPACE`. Contours whose centroid falls inside it are discarded. Crucially, **the arms, racket hand, and head are not excluded** — the ball is adjacent to the racket hand at and immediately after contact, and excluding that region would delete the seed detection. The box therefore covers the torso and legs only, which is where bright clothing and shoes live.

**Named false positives and the criterion that kills each:**

| False positive | Killed by |
|---|---|
| Court lines (static) | Motion mask; residual drift ghosts fail aspect ≤ 12 and minor axis ≥ 3 |
| Shoes | Torso/leg exclusion box; failing that, solidity and hue |
| Other players / bystanders | area ≤ 1500 px²; a person's motion blob is far larger |
| Bright clothing on the subject | Exclusion box; failing that, area and hue |
| Bright clothing on others | area; orientation agreement |
| Racket frame / strings | Adjacent to the excluded hand region but fails aspect (long thin frame) and hue |
| Sun glare, lens flare | Static or slow-moving → fails motion mask; fails solidity |
| Shadow edges | Fail the V threshold in both colour branches |

**Honest residual on false positives.** Loosening circularity to 0.20 for tier B unavoidably widens the candidate set; the replacement criteria are narrower than circularity was, but not free. Expect a handful of spurious candidates per frame in cluttered scenes — the design tolerates this because the *tracker*, not the detector, makes the final call: a spurious candidate must additionally sit inside a kinematic gate, agree in direction with the previous step to within 35°, and do so consistently enough to survive a median over ≥ 2 steps. Precision is enforced at the association layer, not the contour layer.

#### 10.5 Association across frames

Greedy nearest-neighbour with a kinematic gate, in `ball/track.py` (pure numpy, no cv2):

1. **Seed.** In the first measurement-window frame, take the accepted candidate nearest the racket-hand wrist position (mapped into `CAL_SPACE`). Reject if that distance exceeds `0.8 TU` expressed in `CAL_SPACE` pixels. No seed → `no_track_seeded`.
2. **Gate radius.** For each subsequent frame, the maximum plausible displacement is `r_max = v_max · Δt · px_per_m`, with `v_max = 71.5 m/s` (257 km/h, above any recorded serve). Candidates outside `r_max` of the predicted position are ignored.
3. **Prediction.** First step: predict = last position (gate is `r_max`). Subsequent steps: constant-velocity extrapolation from the previous step, gate shrunk to `max(8 px, 0.35 · r_max)`.
4. **Direction gate.** A candidate whose step direction differs from the previous step's by more than **35°** is rejected and the track **terminates** rather than continuing. A real ball's trajectory is smooth over 250 ms; a >35° turn means either a bad association or a bounce/net contact — and after a bounce the speed is no longer the speed we set out to measure, so stopping is correct in both cases.
5. **Minimum step.** A step under 2 px is rejected as a static artifact.
6. **Coasting.** Up to **2 consecutive** frames with no accepted candidate are coasted on the predicted position (and contribute no detection). A third consecutive miss terminates the track.
7. **Exit-frame trim.** Detections within 12 px of any frame edge are dropped — a partially-visible ball has a biased centroid.

A track with fewer than 3 accepted detections yields `BallTrack = None` and `too_few_detections`.

#### 10.6 The PLANE / DEPTH ERROR problem

**This is the most serious known source of error in ball speed and it is not fully solvable in this design. Stating it plainly rather than burying it.**

**The mechanism.** Two tapped court points establish a px-to-metres scale that is valid **only at the depth of those points**, on the court plane. Under a pinhole projection, the metres-per-pixel at range `Z` is proportional to `Z`. If the calibration segment sits at range `Z_cal` and the ball is at range `Z_ball`, then

```
estimated_speed = true_speed × (Z_cal / Z_ball) × cos θ
```

where `θ` is the angle between the ball's velocity and the image plane.

**Direction of the bias:**

- Ball **farther** from the camera than the calibration segment → `Z_ball > Z_cal` → **speed is under-read.**
- Ball **nearer** than the calibration segment → **speed is over-read.**
- Any velocity component along the optical axis (`θ > 0`) → **additionally under-read** by `cos θ`.

Both dominant terms push the same way, so **the systematic bias is toward under-reading.** That is the better direction to be wrong in for a consumer product — we will occasionally tell a user their ball was slower than it was, and we will rarely flatter them — but it is still a bias, and it is not small.

**Rough magnitudes.** Camera 8 m from the hitting player, ball at 30 m/s, 0.25 s window (7.5 m of travel):

| Out-of-plane angle θ | Depth travelled | Mid-window range | `Z_cal/Z̄_ball` | × cos θ | Net reading |
|---|---|---|---|---|---|
| 0° (pure down-the-line, perpendicular camera) | 0 m | 8.0 m | 1.00 | 1.00 | **~0 % error** |
| 15° | 1.9 m | 9.0 m | 0.89 | 0.97 | **~14 % low** |
| 30° | 3.8 m | 9.9 m | 0.81 | 0.87 | **~30 % low** |
| 45° | 5.3 m | 10.7 m | 0.75 | 0.71 | **~47 % low** |
| 90° (straight away — filmed from behind) | 7.5 m | 11.8 m | 0.68 | 0.00 | **unusable** |

A second, separate term is the **height of the ball above the court plane**. This one is much smaller than intuition suggests: with a *level* camera (optical axis horizontal), raising an object by 1 m changes its image row but barely changes its range, so the scale is essentially unaffected. It only becomes significant when the camera is **tilted downward**, because then the court-plane scale varies steeply with image row and a segment lying on the ground is imaged with a strong perspective gradient. Which leads directly to the mitigations.

**What the design does about it — five concrete measures:**

1. **Constrain which court points may be tapped.** `CourtReference` is an enum of specific court features, not free-form points. For the required side-on view, only two are accepted, and both lie **along the near sideline**, which runs across the image at approximately constant depth:
   - `SIDELINE_BASELINE_TO_NET` = **11.885 m** (preferred: longest segment → best relative precision; both endpoints are unambiguous line intersections)
   - `SIDELINE_BASELINE_TO_SERVICE_LINE` = **5.485 m**

   A segment at constant depth has no intra-segment scale gradient, which removes the camera-tilt term almost entirely. Segments running *away* from the camera (the baseline, viewed from the side) are rejected for that view precisely because their two endpoints are at different ranges and the resulting "average" scale is valid nowhere.

   `BASELINE_SINGLES_WIDTH` (8.23 m) and `BASELINE_DOUBLES_WIDTH` (10.97 m) are constant-depth only from behind or in front — and those are exactly the views where `θ ≈ 90°` and speed is unmeasurable. They exist in the enum for future use and are currently rejected by the view gate below.

2. **Require a roughly perpendicular camera view.** `PoseQuality.estimated_camera_view` already exists (Stage 7). Ball speed is **refused outright** (`camera_view_unsuitable`) for `front` and `behind`, and **capped at `low` confidence** for `oblique`. The capture UI additionally instructs: phone level (not tilted down more than ~10°), 5–10 m to the side, and *hit down the line* — i.e. parallel to the near sideline, which is the `θ ≈ 0` condition.

3. **Restrict the measurement window to the trajectory segment nearest the calibration plane.** The 0.25 s window (§11.2) bounds total travel to ≤ 7.5 m at 30 m/s. The ball is closest to the calibration geometry immediately after contact and diverges monotonically; truncating early is the single cheapest way to bound the `Z_cal/Z_ball` term.

4. **Measure the depth drift instead of assuming it away.** The detected blob's **minor axis** is a direct, in-band proxy for range: as the ball recedes, its image shrinks. `blob_size_drift_ratio = minor_axis[last] / minor_axis[first]`. Gates:
   - drift ∈ [0.75, 1.33] → no penalty
   - drift ∈ [0.60, 0.75) or (1.33, 1.67] → **confidence capped at `low`**
   - drift outside [0.60, 1.67] → **`ball_speed_mph = null`**, reason `depth_drift_exceeded`

   Honest caveat: minor axis at 3–20 px is a noisy quantity, quantized in whole pixels, and confounded by exposure changes. That is exactly why the thresholds are coarse (±33 % before any penalty) — this is a gross-error detector, not a depth sensor. It catches the crosscourt ball that would read 30–47 % low; it does not catch a 14 % error.

5. **We deliberately do NOT apply a depth correction.** An obvious-looking fix would be to estimate `Z_ball` from apparent ball size and rescale. We reject it: apparent size is dominated by motion blur and exposure, not range, so such a correction would inject a large random error while *increasing* the apparent authority of the output. Correcting a bias with a noisier estimator than the bias itself is fabrication with extra steps. The bias is instead disclosed via confidence and the measurement definition.

**Residual error that survives all of this.** With a compliant capture — side-on, level phone, sideline calibration, ball hit down the line — expect roughly **±10–15 %**, i.e. about **±7–10 mph at a 70 mph reading**, dominated by residual out-of-plane angle and centroid noise on a 6 px object. With a partially compliant capture that still passes the gates (a mildly crosscourt ball, an oblique camera), readings can be **15–30 % low**, and the confidence level will read `low` but the number will still be shown. Users filming from behind get `null`, not a bad number. This is why the value is an integer, why it is never scored, and why the response and the Gemini payload both carry the measurement definition alongside the value.

---

### Stage 11 — Speed calculation (PURE) **[NEW]**

| | |
|---|---|
| **Owner** | `analysis/speed.py` (estimator), `ball/geometry.py` (calibration scale + coordinate mapping) |
| **In** | `BallTrack`, `CalibrationScale`, `CameraView`, `BallDetectionSummary`, `contact.confidence` |
| **Out** | `BallSpeedResult` |
| **Fails** | Cannot raise. Every failure is a `null` speed plus a `BallSpeedUnavailableReason`. |

Pure numpy. No cv2, no I/O, no network. Fully unit-testable against a synthetic `BallTrack` whose true speed is known by construction (§4.5).

#### 11.1 Calibration scale factor

The two tapped points arrive as normalized coordinates on the **preview surface the user actually tapped**, together with that surface's pixel dimensions and the rotation the client applied to produce it. `map_calibration_points()` inverts that chain:

```
preview-normalized → preview px → un-rotate by capture_rotation_deg
                   → recorded-frame normalized → apply container display-matrix rotation
                   → CAL_SPACE px
```

Then:

```
segment_px = ‖p_a − p_b‖   (true pixels in CAL_SPACE, NOT normalized units)
px_per_m   = segment_px / distance_m
```

Three guards, each of which exists because the failure it catches is silent:

- **Never compute the segment length in normalized coordinates.** On a 9:16 frame a normalized unit of x and a normalized unit of y are different physical lengths; a diagonal segment measured in normalized units is wrong by a view-dependent factor. This is the same trap as the aspect correction in Stage 7.3 and it is the single most likely implementation bug in this feature.
- **Frame-shape self-check.** The aspect ratio implied by `capture_width_px / capture_height_px` must match the decoded post-rotation aspect within 2 %. A mismatch means the taps were made on a cropped or letterboxed preview and the mapping is invalid → `null`, reason `calibration_frame_mismatch`. Without this check, a client that presents a centre-cropped preview would produce a quietly wrong scale on every clip.
- **Plausibility bound on the result itself.** For a 1280 px `CAL_SPACE` and a 5–12 m court segment, `px_per_m` should land roughly in 20–120. Outside `[8, 300]` → `null`, reason `calibration_implausible`. This turns a whole class of coordinate-space bugs into a visible `null` instead of an invisible wrong number.

#### 11.2 The measurement window, and what the number actually means

**Window: `[contact_abs, contact_abs + 0.25 s]`, with the first inter-frame step discarded.**

- **Why the first step is discarded.** At and immediately after contact, the ball is spatially coincident with the racket head and hand; its blob merges with them and the centroid is pulled toward the racket. The first *reliable* displacement is therefore from the second in-window detection onward.
- **Why 0.25 s and not longer — the drag argument.** A tennis ball decelerates substantially in flight. With ρ = 1.2 kg/m³, C_d ≈ 0.55, A = 3.53 × 10⁻³ m², m = 0.057 kg, at v = 30 m/s the drag deceleration is

  ```
  a = ½ρ·C_d·A·v² / m = (0.5)(1.2)(0.55)(3.53e-3)(900) / 0.057 ≈ 18 m/s²
  ```

  That is ~18 m/s of speed lost per second of flight. Over a 0.35 s window the median step lands near t ≈ 0.175 s, under-reading contact-instant speed by ~3.1 m/s ≈ 7 mph — about 10 % — before any geometric error. Shortening to 0.25 s puts the median near t ≈ 0.125 s and roughly halves that to ~2.3 m/s ≈ 5 mph.
- **Why we do not correct for drag.** A drag correction requires the spin state (topspin changes both C_d and the trajectory), and spin is explicitly out of scope. Estimating it would be fabrication.
- **Instead, we define the quantity honestly.** `ball_speed_mph` is defined as the **mean ball speed over the first 0.25 seconds after contact — not the instantaneous speed off the racket.** That definition is a constant string in the schema (`measurement_definition`), is echoed into the Gemini payload, and is what the UI must display alongside the number. Redefining the measurand to match what we can actually measure removes the bias by definition rather than by correction — which is the only intellectually clean option available.
- **Why 0.25 s is still long enough.** At 30 fps it yields up to 8 frames (7 steps); at 60 fps up to 16 (15 steps). Both clear the 3-detection floor with margin.
- **Early termination.** The window ends early at whichever comes first: the direction gate firing (bounce or net), the ball reaching the frame-edge margin, or the third consecutive coasted frame.

#### 11.3 The estimator

```
d_i   = ‖xy[i+1] − xy[i]‖                     for each accepted consecutive pair, in CAL_SPACE px
Δt_i  = timestamps_s[i+1] − timestamps_s[i]   actual PTS deltas
v_px  = median_i( d_i / Δt_i )                px per second
v_ms  = v_px / px_per_m                       metres per second
mph   = round( v_ms × 2.23694 )               → int
```

The specification — *median frame-to-frame pixel distance, divided by the calibration scale factor, divided by elapsed time* — is exactly this when Δt is constant. We normalize each step by **its own** Δt before taking the median because phone video is VFR (Stage 5) and because native decoding can present genuinely uneven PTS deltas; with constant Δt the two formulations are algebraically identical, and with VFR this one is strictly correct where the literal one is not.

**Why median and not mean.** The failure mode of a greedy associator is not Gaussian noise — it is a single catastrophically wrong step (a decoy blob accepted once, or a frame where the ball merged with a line). A mean is destroyed by one such step; a median with k ≥ 4 steps tolerates one outright. This is also directly asserted in the unit tests (§4.5): a track with one grossly wrong step must produce the same answer as the clean track.

`px_per_m` is a division, not a multiplication, matching the "divided by the calibration scale factor" formulation with the scale factor defined as **pixels per metre**.

#### 11.4 Confidence levels and their thresholds

Confidence is **derived from the number of accepted, associated detections** `n` that fed the estimate, then **capped downward** by geometry gates. Caps can only lower the level; nothing raises it.

| `n` detections | Steps `k = n−1` | Outliers the median tolerates `⌊(k−1)/2⌋` | Level |
|---|---|---|---|
| 0–2 | ≤ 1 | — | `unavailable` → `ball_speed_mph = null` |
| 3–4 | 2–3 | 0–1 | `low` |
| 5–8 | 4–7 | 1–3 | `medium` |
| ≥ 9 | ≥ 8 | ≥ 3 | `high` |

**Defence of these thresholds.** The levels are set by the median's breakdown behaviour, which is the actual statistical property that determines whether the estimate can survive an association error:

- **`n < 3` → null.** With 2 detections there is exactly one step and no redundancy whatsoever; a single bad association is undetectable and unmitigable. This is the floor the requirement specifies and it is the right floor.
- **`n = 3–4` → `low`.** Two or three steps. The median of two steps is their mean — zero outlier tolerance. Three steps tolerates one. Usable, clearly hedged.
- **`n = 5–8` → `medium`.** Four to seven steps, tolerating one to three bad associations. This is the realistic ceiling for a 30 fps capture (0.25 s → ≤ 7 frames after the skipped first step → ≤ 6 steps), so `medium` is the best a 30 fps clip can achieve.
- **`n ≥ 9` → `high`.** Requires ≥ 9 detections in 0.25 s, i.e. **> 36 fps**, i.e. in practice a 60 fps capture with near-perfect recall. `high` is therefore structurally unreachable at 30 fps. This is a consequence of the median's breakdown behaviour, not a lever for pushing a capture setting — see §11.5.

**Downward caps** (any one applies; the lowest wins):

| Condition | Effect |
|---|---|
| `estimated_camera_view ∈ {front, behind}` | `null` — `camera_view_unsuitable` |
| `estimated_camera_view == oblique` | cap `low` |
| `blob_size_drift_ratio` ∈ [0.60, 0.75) ∪ (1.33, 1.67] | cap `low` |
| `blob_size_drift_ratio` outside [0.60, 1.67] | `null` — `depth_drift_exceeded` |
| `reference == CUSTOM` (user-typed distance) | cap `medium` |
| `segment_px < 150` in `CAL_SPACE` | cap `low` (short baseline → amplified relative scale error) |
| `segment_px < 60` | `null` — `calibration_implausible` |
| `contact.confidence < 0.35` | `null` — `contact_unreliable` (stage skipped upstream) |
| median step displacement < 4 px | `null` — `displacement_below_noise_floor` |
| result outside `[15, 160]` mph | `null` — `implausible_speed` |

**Plausibility bounds, defended.** 15 mph is below any struck ball worth reporting (a soft drop shot leaves the racket at roughly 25 mph); anything under it is a tracking artifact, typically a slow-moving distractor. 160 mph sits just above the fastest serve ever recorded (Samuel Groth, 263 km/h ≈ 163.4 mph, 2012; the fastest ATP-official is ~157 mph), so a reading above it is definitionally a tracking error and never a real swing by this app's users. Both bounds are enforced by the Pydantic field itself (`ge=15, le=160`), which means an out-of-range value cannot be serialized even if a future bug produced one.

#### 11.5 Capture-rate policy — 30 fps is supported and expected

**Decision: the app accepts the device's default capture frame rate. The Flutter recording screen does not force, request, or default to 60 fps.**

The consequence, stated plainly so nobody reads the confidence tiers as a defect:

| Capture rate | Max detections in the 0.25 s window | Best reachable confidence |
|---|---|---|
| 30 fps (the common case) | ~7 after the discarded first step | **`medium`** |
| 60 fps | ~15 after the discarded first step | `high` |

**On standard 30 fps phone footage, a good reading caps at `medium`, and that is the normal, healthy outcome — not a degraded one.** `high` is reachable only at 60 fps or above. `medium` already means the estimate survived four to seven association steps with the median tolerating one to three bad ones; it is a perfectly sound number. Nothing about a `medium` reading is provisional, and the UI must not present it as such.

**Why the capture screen does not push 60 fps.** Frame rate is not the dominant error term. The geometric bias documented in §10.6 runs to ±10–15 % on a compliant capture and up to 30 % on a marginal one; moving from 30 to 60 fps does not touch that bias at all — it only buys redundancy against association errors, which the median already handles at 30 fps. Spending the user's attention on a frame-rate setting would buy a confidence-badge upgrade while leaving the actual accuracy limit untouched, and it would cost us the thing that *does* matter: compliance with the side-on, level-camera framing that §10.6's mitigations depend on. There is one setup instruction worth a user's attention and it is camera position, not fps.

Two further reasons: 60 fps capture is not uniformly available or default across the Android device range this app targets, so requiring it would turn ball speed from an optional extra into a device-compatibility gate; and 60 fps roughly doubles file size against a 50 MB upload cap on mobile data.

**What the recording screen may say.** A single optional line in the setup hint, phrased as an aside and never as a prompt, a warning, a blocking dialog, or a toggle the user must dismiss:

> *Optional: recording at 60 fps can sharpen the ball-speed reading. Any frame rate works.*

**What it must not do:** show a "your camera is set to 30 fps" warning, default the camera to 60 fps, gate calibration or ball speed behind a frame-rate check, or render a `medium` badge in a cautionary colour. `native_fps` is already surfaced in `BallDetectionSummary` for diagnostics; that is where the frame rate belongs, not in the user's way.

**Backend impact: none.** No thresholds change. §11.4's tiers stand exactly as specified — this section documents the expected distribution of outcomes, it does not alter the estimator, the gates, or the schema. The practical effect is that production dashboards should expect `medium` to be the modal confidence value on real traffic, and a low `high` rate is not a regression signal.

---

### Stage 12 — Swing-phase segmentation (PURE)

| | |
|---|---|
| **Owner** | `analysis/phases.py` |
| **In** | `NormalizedSequence`, `HandednessResult`, `ContactDetection` |
| **Out** | `SwingPhases` — five `SwingPhase` entries, ordered, non-overlapping, contiguous |
| **Fails** | Cannot raise. Degenerate phases collapse to zero duration and add a warning. |

| Phase | Boundary rule |
|---|---|
| `ready` | clip start → hand speed first exceeds 10 % of peak |
| `takeback` | → frame of maximum backward hand displacement (racket furthest back) |
| `forward_swing` | → contact frame − 1 |
| `contact` | contact frame ± 1 |
| `follow_through` | → hand speed drops below 15 % of peak, or clip end |

`tempo_ratio = takeback_duration / forward_swing_duration`. Degenerate cases (a phase would be < 2 frames) collapse the phase to zero duration and add a warning; segmentation never raises.

---

### Stage 13 — Swing metric computation (PURE)

| | |
|---|---|
| **Owner** | `analysis/metrics.py` |
| **In** | `NormalizedSequence`, `HandednessResult`, `ContactDetection`, `SwingPhases` |
| **Out** | `SwingMetrics` — every field `float \| None` |
| **Fails** | Cannot raise. Insufficient visibility → `None` for that metric. |

Any metric whose supporting landmarks fall below visibility threshold at the required frame returns `None`. **A missing metric is `None`, never a default, never zero.** `None` propagates all the way to Gemini as "unavailable" and is excluded from scoring weights, which are renormalized over available metrics only.

Each metric carries a `view_sensitive: bool`. This flag is the load-bearing mitigation for §5.

**Ball speed is not in this table and never will be.** `SwingMetrics` is the body-relative measurement set; ball speed is a physical measurement with entirely different error characteristics and it lives in its own top-level `ball_speed` block. Mixing them would let a 15 % geometric error propagate into a technique score.

| Metric | Unit | View-sensitive | Definition |
|---|---|---|---|
| `shoulder_hip_separation_deg` | deg | **No** | Max signed angle between shoulder line and hip line during takeback ("X-factor"). Both lines share a projection, so the difference is largely projection-stable. |
| `shoulder_turn_deg` | deg | **Yes** | Shoulder-line angle range relative to image x-axis. |
| `hip_rotation_deg` | deg | **Yes** | Hip-line angle range across the swing. |
| `elbow_angle_at_contact_deg` | deg | **Yes** | Shoulder–elbow–wrist angle, racket arm, at contact. |
| `wrist_lag_deg` | deg | **Yes** | Angle between forearm vector and hand-velocity vector at contact − 3 frames. Proxy only — the racket head is not observable. |
| `contact_height_ratio` | ratio | **No** | Wrist y at contact mapped to 0 = mid-hip, 1 = mid-shoulder. |
| `contact_point_forward_tu` | TU | Partial | Signed wrist x at contact relative to mid-hip, × `swing_direction_sign`. |
| `peak_hand_speed_tu_s` | TU/s | Partial | Max racket-hand speed. **Never converted to mph or m/s.** The presence of a measured `ball_speed_mph` elsewhere in the response makes this rule *more* important, not less — the two must never be confused or cross-derived. |
| `swing_path_angle_deg` | deg | **No** (sign), Yes (magnitude) | Angle of the least-squares line through the hand trajectory over `[contact−6, contact+3]`. Positive = low-to-high. |
| `swing_plane_deviation_tu` | TU | Partial | RMS residual about that line — path straightness. |
| `knee_flexion_min_deg` | deg | **Yes** | Minimum front-knee angle during forward swing. |
| `weight_transfer_tu` | TU | Partial | Mid-hip x displacement, takeback-end → contact, × `swing_direction_sign`. |
| `follow_through_height_tu` | TU | **No** | Max wrist y after contact, relative to mid-shoulder. |
| `balance_sway_tu` | TU | **No** | Std-dev of mid-hip position over `takeback → follow_through`. |
| `head_stillness_tu` | TU | **No** | Std-dev of nose position during forward swing. |
| `tempo_ratio` | ratio | **No** | From Stage 12. |
| `wrist_separation_at_contact_tu` | TU | **No** | Distance between wrists at contact — feeds two-handed detection. |

---

### Stage 14 — Shot-type inference from technique (PURE)

| | |
|---|---|
| **Owner** | `analysis/shot_type.py` |
| **In** | `SwingMetrics`, `NormalizedSequence`, `HandednessResult`, `ContactDetection`, `SwingPhases` |
| **Out** | `ShotTypeInference` |
| **Fails** | Cannot raise. Ambiguous → `unknown`. |

A deterministic weighted rule set. **No model, no network, no training.** Classes: `forehand_topspin`, `forehand_slice`, `backhand_one_handed`, `backhand_two_handed`, `serve`, `volley`, `unknown`.

**The ball detector's output is not an input here.** Shot type is inferred from technique, per CLAUDE.md, and the presence of a ball track must not change that. `infer_shot_type()` does not accept a `BallTrack` parameter, which makes the rule structural rather than a convention.

Discriminating features, in evaluation order:

1. **Serve** — `contact_height_ratio > 1.30` (wrist above shoulder line) AND wrist above nose at contact AND trunk extended. Serve is checked first because its signature is unambiguous and it would otherwise pollute forehand scoring.
2. **Two-handed** — `wrist_separation_at_contact_tu < 0.35` sustained across `contact ± 3`. The sustain requirement rejects incidental hand proximity.
3. **Forehand vs backhand** — sign of `wrist_x_at_contact` relative to mid-hip, combined with detected handedness. Dominant side → forehand; across-body → backhand.
4. **Slice vs topspin** — `swing_path_angle_deg < −10°` AND short takeback (`takeback_displacement_tu < 0.6`).
5. **Volley** — total hand path length < 1.0 TU AND swing duration < 0.35 s AND contact forward of the body.

Output includes `class_scores: dict[str, float]` summing to 1.0 and `evidence: list[str]` — human-readable deterministic strings such as `"wrist separation 0.21 TU < 0.35 threshold → two-handed"`. **The evidence strings are generated by Python, not Gemini**, and are what Gemini is later asked to *paraphrase* rather than derive.

If `top_score < 0.45` or the margin to second place is `< 0.15` → `unknown`. An `unknown` result is not a failure: scoring falls back to a shot-type-agnostic rubric subset and the feedback becomes general.

---

### Stage 15 — Scoring and threshold evaluation (PURE)

| | |
|---|---|
| **Owner** | `analysis/rubric.py` (constants), `analysis/scoring.py` (math) |
| **In** | `SwingMetrics`, `ShotType` |
| **Out** | `Scorecard` |
| **Fails** | Cannot raise. All-`None` metrics → `overall_score = None`. |

`RUBRIC_V1: dict[ShotType, dict[str, MetricBand]]`, where `MetricBand` carries `ideal_min`, `ideal_max`, `hard_min`, `hard_max`, `weight`, `category`, `direction`.

Per metric: `verdict ∈ {low, ideal, high, unavailable}` and `score_0_100` computed by a piecewise-linear ramp — 100 inside `[ideal_min, ideal_max]`, decaying to 0 at `hard_min`/`hard_max`. Metrics with `value is None` score `unavailable`, are excluded, and their weight is redistributed proportionally across the remaining metrics in the same category.

Five categories: `preparation`, `contact`, `swing_path`, `balance`, `follow_through`. `overall_score` is the weight-normalized mean of category scores, rounded to one decimal.

**`ball_speed_mph` is deliberately absent from the rubric.** There is no defensible "ideal ball speed" — it depends on shot intent, opponent, and tactics, and a 14-year-old hitting 55 mph is not worse than an adult hitting 75 mph. Scoring it would also import ball speed's ±10–15 % geometric error into `overall_score`, degrading a number that is currently a function of well-controlled body-relative measurements. `rubric_version` stays `rubric_v1` for exactly this reason: the rubric is unchanged by this revision.

**This is where all coaching judgment lives.** It is a table of numbers plus one ramp function — fully deterministic, fully unit-testable, and versioned so that changing thresholds after launch does not silently invalidate stored analyses (`rubric_version` is persisted on every row).

---

### Stage 16 — Gemini payload construction (PURE)

| | |
|---|---|
| **Owner** | `feedback/payload.py`, `feedback/prompt.py` |
| **In** | `AnalysisCore` |
| **Out** | `FeedbackPayload` (a plain dict) + rendered prompt `str` |
| **Fails** | Cannot raise. |

The payload contains **only numbers, enum strings, and verdicts**. No video, no frames, no landmarks, no image data, no user email, no user id, no free-text notes from the user, no calibration tap coordinates. Text-only, per CLAUDE.md.

```json
{
  "pipeline_version": "v2",
  "rubric_version": "rubric_v1",
  "shot_type": "forehand_topspin",
  "shot_type_confidence": 0.81,
  "shot_type_evidence": [
    "wrist separation 0.62 TU exceeds 0.35 two-handed threshold",
    "contact on dominant side of mid-hip",
    "swing path angle +24.1 deg indicates low-to-high"
  ],
  "handedness": "right",
  "contact_confidence": 0.74,
  "overall_score": 71.5,
  "category_scores": {
    "preparation": 78.0, "contact": 64.0, "swing_path": 82.0,
    "balance": 69.0, "follow_through": 64.5
  },
  "metrics": [
    {"name":"shoulder_hip_separation_deg","value":38.4,"unit":"deg",
     "verdict":"low","score":64.0,"ideal_min":45.0,"ideal_max":65.0,
     "view_sensitive":false},
    {"name":"contact_height_ratio","value":0.86,"unit":"ratio",
     "verdict":"ideal","score":100.0,"ideal_min":0.75,"ideal_max":1.05,
     "view_sensitive":false},
    {"name":"knee_flexion_min_deg","value":null,"unit":"deg",
     "verdict":"unavailable","score":null,"ideal_min":135.0,"ideal_max":160.0,
     "view_sensitive":true}
  ],
  "ball_speed": {
    "mph": 68,
    "confidence": "medium",
    "detections_used": 6,
    "definition": "average speed over the first 0.25 seconds after contact, not the instantaneous speed off the racket",
    "hedge": "approximate",
    "reporting_rule": "State this value at most twice, only as the exact integer 68 immediately followed by 'mph'. Never round it, never alter it, never convert it, never state a range around it, never compare it to a target or ideal value. It is a measurement, not a score."
  },
  "priority_metric_names": ["shoulder_hip_separation_deg","follow_through_height_tu"],
  "unit_glossary": {
    "TU": "torso units; 1 TU = one shoulder-to-hip torso length of this player",
    "deg": "degrees",
    "ratio": "dimensionless; 0 = hip height, 1 = shoulder height",
    "mph": "miles per hour; the ONLY real-world unit in this payload, measured from ball detection plus a user-supplied court calibration distance"
  },
  "unavailable_metrics": ["knee_flexion_min_deg"],
  "low_confidence_warnings": []
}
```

When ball speed is unavailable, the block is present but neutered — it is never omitted, because an absent key invites the model to fill the gap:

```json
"ball_speed": {
  "mph": null,
  "confidence": "unavailable",
  "reason": "not_calibrated",
  "definition": "Ball speed was not measured for this clip.",
  "reporting_rule": "Do not state, estimate, imply, or hint at any ball speed. Do not use the word 'mph' or any speed unit. If the user would expect a speed, say only that speed was not measured for this clip."
}
```

Hedging by confidence, applied by Python not by the model:

| Confidence | `hedge` value | Expected phrasing |
|---|---|---|
| `high` | `"measured"` | "Ball speed 68 mph." |
| `medium` | `"approximate"` | "Ball speed came out around 68 mph." |
| `low` | `"rough"` | "A rough reading put ball speed near 68 mph — few detections, so treat it loosely." |

`priority_metric_names` is chosen by Python — the two lowest-scoring metrics weighted by rubric importance. **Gemini does not choose what to coach.** It is told what to coach and writes the sentences. Ball speed is never a priority metric, because it is not a coachable rubric entry.

System instruction, verbatim intent (updated for v2, changes in **bold**):

> You are a text formatter for a tennis coaching app. You will receive measurements that have already been computed by a deterministic analysis system. You must NEVER compute, estimate, infer, convert, or invent any number. Every numeric value in your output must appear verbatim in the input JSON. Never convert torso units to feet, meters, inches, or miles per hour. Never state a spin rate or a rally count — this system does not measure those. **Ball speed: if `ball_speed.mph` is a number, you may state it at most twice, exactly as that integer immediately followed by the word "mph", and you must respect the `hedge` field. If `ball_speed.mph` is null, you must not mention ball speed, mph, km/h, or any speed at all.** Never derive ball speed from hand speed or from any other value. Do not contradict the provided shot_type. Do not add measurements that are not in the input. If a metric is null, say the measurement was unavailable for this clip.

Generation config: `temperature=0.3`, `max_output_tokens=800`, `response_mime_type="application/json"`, and an explicit `response_schema` matching `GeminiFeedbackDraft`. Structured output removes an entire class of failure — the model cannot embed a rogue measurement in a stray paragraph, because there is no free paragraph slot.

---

### Stage 17 — Gemini call (IMPURE) + numeric guard (PURE)

| | |
|---|---|
| **Owner** | `feedback/gemini_client.py` (impure), `feedback/guard.py` (pure), `feedback/template.py` (pure), `feedback/service.py` (orchestration) |
| **In** | rendered prompt + numeric allowlist + `ball_speed_mph` |
| **Out** | `CoachingFeedback` + `NumericGuardReport` |
| **Fails** | Never propagates. Any failure → deterministic template. |

Call: 8 s timeout, 1 retry with 500 ms backoff, `gemini-2.5-flash`. Budget 1.5–4 s.

**The guard is the actual enforcement mechanism; the prompt is only a request.** `guard.validate_numeric_claims()` is a pure function:

1. Build an allowlist of numeric string renderings from the payload — each float at 0 and 1 decimal places, each integer, each score. **`ball_speed.mph` and `ball_speed.detections_used` are added as integers only** — never with a decimal rendering, so `"68.0 mph"` is a violation by construction.
2. Regex every numeric token out of each Gemini output field: `[-+]?\d+(?:\.\d+)?%?`.
3. Any token not in the allowlist, and not in a small ordinal allowlist (`1`–`5`, for list positions), is a violation.
4. Scan for banned unit substrings: `km/h`, `m/s`, `kph`, `rpm`, `rally`, `spin rate`, `revolutions`, `meters`, `metres`, `feet`, `inches`. These are banned unconditionally, always.
5. **`mph` is handled by a dedicated conditional rule** — this is the v2 addition and it is the only place the guard was loosened, so it is specified tightly:
   - If `ball_speed_mph is None`: `mph` is a **banned substring**, exactly as in v1. Any occurrence → field discarded.
   - If `ball_speed_mph == V`: every occurrence of `mph` (case-insensitive) must be immediately preceded by the exact integer `V` and a single separator (space or non-breaking space, normalized before matching). Formally, the count of `mph` occurrences must equal the count of matches of `\bV\s?mph\b`, and must be ≤ 2. Any other arrangement — `"68.0 mph"`, `"about 70 mph"`, `"mph"` alone, `"68 mph"` appearing 3 times — is a violation.
   - `V` is allowlisted as a bare integer, but the `mph` binding rule is evaluated **independently**, so the model cannot detach the number from its unit or attach the unit to a different number.
6. A field containing a violation is **discarded**, not rewritten. If the `summary` is discarded, or more than 2 fields are discarded, fall back entirely to the deterministic template.

The result is reported honestly in the response as `feedback.source ∈ {gemini, gemini_partial, template}` plus a `NumericGuardReport` carrying `mph_rule ∈ {"banned", "bound_to:<int>"}`. This is observable in production — a rising `template` rate is a direct signal that the prompt or model has drifted.

`feedback/template.py` renders complete, useful coaching prose from the rubric verdicts with zero network dependency, and includes the ball-speed sentence when available (and silently omits it when not, rather than writing "speed unavailable" on the majority of clips). **The API never fails because Gemini failed.** It degrades to slightly stiffer language.

---

### Stage 18 — Response assembly and persistence (IMPURE)

| | |
|---|---|
| **Owner** | `services/orchestrator.py`, `services/repository.py` |
| **In** | `AnalysisCore` + `CoachingFeedback` + `StageTimings` |
| **Out** | persisted `analyses` row; `analysis_jobs.status = succeeded` |
| **Fails** | DB write failure → job marked `failed` with `INTERNAL_ERROR`; temp file deleted regardless |

Assembles `AnalysisResponse`, writes the `analyses` row (full response JSONB plus indexed columns `user_id`, `shot_type`, `overall_score`, `ball_speed_mph`, `created_at`, `pipeline_version`, `rubric_version`), flips `analysis_jobs.status` to `succeeded`, deletes the temp file. Budget 200–500 ms.

Supabase tables:

- `analysis_jobs` — `id`, `user_id`, `storage_path`, `status`, `error_code`, `error_message`, `heartbeat_at`, `created_at`, `updated_at`. RLS: `user_id = auth.uid()`.
- `analyses` — `id` (= job id), `user_id`, `storage_path`, `shot_type`, `overall_score`, **`ball_speed_mph` (integer, nullable)**, `pipeline_version`, `rubric_version`, `payload` JSONB, `created_at`. RLS: `user_id = auth.uid()`.

`ball_speed_mph` is promoted to an indexed column (not just JSONB) so a "personal best" query and a production-health query ("what fraction of calibrated clips produced a speed?") are both cheap. The column is `integer`, not `numeric` — the database enforces the no-decimals rule alongside Pydantic.

Storing the full response as JSONB means schema evolution does not require a backfill, and `pipeline_version` lets the client render older analyses correctly — `v1` rows have no `ball_speed` block, and the client must handle that.

---

### Stage 19 — Poll / fetch

| | |
|---|---|
| **Owner** | `backend/app/api/routes_analyses.py` |
| **In** | `GET /v1/analyses/{id}` + `AuthedUser` |
| **Out** | `AnalysisJobStatusResponse` while `queued`/`running`; full `AnalysisResponse` once `succeeded` |
| **Fails** | `404` when not found or not owned; `WORKER_LOST` when `heartbeat_at` is stale |

Ownership enforced by `user_id` match. Client polls every 2 s, gives up at 120 s.

---

### §1.20 Timing and memory budget (revised for v2)

Warm instance, 12 s source clip at 60 fps, 240 sampled pose frames, calibration present.

| Stage | Wall time | Peak added RSS |
|---|---|---|
| Storage download (streamed) | 1–3 s | ~2 MB |
| Keyframe motion scan (clips > 10 s only) | 0.2–0.8 s | ~3 MB |
| Decode + rotate + sample @ 640 px | 2–4 s | ~5 MB (one frame at a time) |
| MediaPipe extraction | **14–22 s** | **~100 MB** (graph + TFLite arena) |
| — extractor closed; ~100 MB released — | | |
| Pure kinematics (Stages 7–9) | < 80 ms | < 1 MB (`(240,33,4)` f32 ≈ 127 KB) |
| Ball frame pass: seek + decode ~0.9 s of video @ 1280 px | 0.5–1.1 s | ~5.5 MB (2 working frames) |
| Background median (15 × 1280×720 grayscale) | 0.20–0.30 s | ~14 MB (ring, transient) + 0.9 MB (result) |
| Ball detection, per-frame CV (~16–45 processed frames) | 0.5–1.4 s | ~6 MB (masks, contours) |
| Speed calculation | < 5 ms | negligible |
| Pure analysis (Stages 12–15) | < 120 ms | < 1 MB |
| Gemini | 1.5–4 s | negligible |
| Persist | 0.2–0.5 s | negligible |
| **Total warm, calibrated** | **~21–37 s** | **~110 MB above baseline** |
| **Total warm, uncalibrated** | **~20–35 s** | **~110 MB above baseline** |

**Per-frame CV cost breakdown** at 1280×720, 0.5 shared vCPU: absdiff + threshold ~2 ms, morphology open+close ~3 ms, HSV convert + two threshold branches ~4 ms, `findContours` + feature extraction ~2 ms ≈ **11 ms/frame**. At 60 fps the 0.25 s window plus coasting margin is ~18 processed frames ≈ 0.2 s; the upper bound in the table covers 1280×960 portrait framing and a longer keyframe seek.

**The memory claim, and the ordering requirement it depends on.** Peak added RSS is **unchanged from v1 at ~110 MB**, despite adding a whole new stage, for one reason only: the ball stage's ~25 MB working set is allocated **strictly after** MediaPipe's ~100 MB graph is released. If an implementer runs ball detection before closing the extractor — or holds the extractor open "in case of a retry" — peak goes to ~135 MB and the OOM headroom narrows. This is why Stage 6 specifies the close as a memory-ordering requirement and why the orchestrator's structure (`_collect_pose` returns and its `with` block exits before `_collect_ball` is called) is part of the contract, not an implementation detail.

**Baseline RSS did increase, and that is the real memory cost of this feature.** Adding OpenCV raises the container's idle footprint:

| | v1 baseline | v2 baseline |
|---|---|---|
| Python + FastAPI + numpy + PyAV + MediaPipe | ~150 MB | ~150 MB |
| `opencv-python-headless` | — | **+55–75 MB** |
| **Idle total** | ~150 MB | **~210–225 MB** |
| Idle + peak job | ~260 MB | **~320–335 MB** |
| Headroom on 512 MB | ~250 MB | **~175–190 MB** |

**`opencv-python-headless` is a hard requirement in `requirements.txt`, not a preference.** The full `opencv-python` wheel pulls GTK, Qt, and X11 shared objects that are dead weight in a container and add roughly 40–60 MB of resident mapping for functionality we never call. The headroom drop from ~250 MB to ~180 MB is the honest cost of this feature; it is still comfortable at `job_queue_max_depth = 4` with a single worker, but it is the reason the queue depth must not be raised and the reason `max_workers` must stay at 1.

**Wall-clock guard.** Ball detection carries a hard 6 s deadline enforced in the orchestrator. On exceed, the stage abandons, returns `detection_timeout`, and the job continues to Gemini normally. A pathological clip cannot turn a 25 s job into a 90 s job.

**Long-clip note.** A 60 s intake clip does not change the pose or ball budgets — the analysis window is still 8 s and the ball window is still 0.25 s. It adds only the keyframe motion scan (0.2–0.8 s) and a larger download (up to ~2.5 s at the 50 MB cap). The 60 s cap costs roughly one second of wall time, which is why it is affordable.

`estimated_seconds` in the 202 response is `25 + (3 if calibrated else 0) + 2 × queue_depth`, rounded up. Cold start adds 30–60 s.

---

## 2. API Contract — Pydantic v2 Models

### 2.1 Enums

```python
# backend/app/models/enums.py
from enum import StrEnum

class Handedness(StrEnum):
    RIGHT = "right"
    LEFT = "left"
    UNKNOWN = "unknown"

class HandednessSource(StrEnum):
    DETECTED = "detected"
    USER_HINT = "user_hint"
    HINT_OVERRODE_DETECTION = "hint_overrode_detection"

class ShotType(StrEnum):
    FOREHAND_TOPSPIN = "forehand_topspin"
    FOREHAND_SLICE = "forehand_slice"
    BACKHAND_ONE_HANDED = "backhand_one_handed"
    BACKHAND_TWO_HANDED = "backhand_two_handed"
    SERVE = "serve"
    VOLLEY = "volley"
    UNKNOWN = "unknown"

class CameraView(StrEnum):
    SIDE_ON = "side_on"
    BEHIND = "behind"
    FRONT = "front"
    OBLIQUE = "oblique"
    UNKNOWN = "unknown"

class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

class AnalysisStatus(StrEnum):
    COMPLETE = "complete"      # all gates passed
    PARTIAL = "partial"        # produced, but low confidence or missing metrics

class MetricVerdict(StrEnum):
    LOW = "low"
    IDEAL = "ideal"
    HIGH = "high"
    UNAVAILABLE = "unavailable"

class ScoreCategory(StrEnum):
    PREPARATION = "preparation"
    CONTACT = "contact"
    SWING_PATH = "swing_path"
    BALANCE = "balance"
    FOLLOW_THROUGH = "follow_through"

class SwingPhaseName(StrEnum):
    READY = "ready"
    TAKEBACK = "takeback"
    FORWARD_SWING = "forward_swing"
    CONTACT = "contact"
    FOLLOW_THROUGH = "follow_through"

class FeedbackSource(StrEnum):
    GEMINI = "gemini"
    GEMINI_PARTIAL = "gemini_partial"
    TEMPLATE = "template"

class MetricUnit(StrEnum):
    """Units a SCORED metric may carry.

    Deliberately has NO `mph` member. Ball speed is a measurement, not a scored
    metric, and giving MetricUnit an mph value would make it structurally possible
    for a future rubric entry to be expressed in mph. Enforced by a unit test.
    """
    DEGREES = "deg"
    TORSO_UNITS = "TU"
    TORSO_UNITS_PER_SEC = "TU/s"
    SECONDS = "s"
    RATIO = "ratio"

# ---- v2: ball speed ----

class CourtReference(StrEnum):
    """Court features the user may tap. Canonical lengths in COURT_REFERENCE_METRES."""
    SIDELINE_BASELINE_TO_NET = "sideline_baseline_to_net"                    # 11.885 m
    SIDELINE_BASELINE_TO_SERVICE_LINE = "sideline_baseline_to_service_line"  #  5.485 m
    BASELINE_SINGLES_WIDTH = "baseline_singles_width"                        #  8.230 m
    BASELINE_DOUBLES_WIDTH = "baseline_doubles_width"                        # 10.970 m
    CUSTOM = "custom"

COURT_REFERENCE_METRES: dict[CourtReference, float] = {
    CourtReference.SIDELINE_BASELINE_TO_NET: 11.885,
    CourtReference.SIDELINE_BASELINE_TO_SERVICE_LINE: 5.485,
    CourtReference.BASELINE_SINGLES_WIDTH: 8.230,
    CourtReference.BASELINE_DOUBLES_WIDTH: 10.970,
}

# Segments valid for a side-on capture must lie at CONSTANT DEPTH from the camera,
# i.e. along the near sideline. See Stage 10.6.
SIDE_ON_ALLOWED_REFERENCES: frozenset[CourtReference] = frozenset({
    CourtReference.SIDELINE_BASELINE_TO_NET,
    CourtReference.SIDELINE_BASELINE_TO_SERVICE_LINE,
    CourtReference.CUSTOM,
})

class BallSpeedConfidence(StrEnum):
    HIGH = "high"              # >= 9 detections
    MEDIUM = "medium"          # 5-8 detections
    LOW = "low"                # 3-4 detections, or capped by a geometry gate
    UNAVAILABLE = "unavailable"  # ball_speed_mph is null

class BallSpeedUnavailableReason(StrEnum):
    NOT_CALIBRATED = "not_calibrated"
    CONTACT_UNRELIABLE = "contact_unreliable"
    CAMERA_VIEW_UNSUITABLE = "camera_view_unsuitable"
    CALIBRATION_FRAME_MISMATCH = "calibration_frame_mismatch"
    CALIBRATION_IMPLAUSIBLE = "calibration_implausible"
    NO_TRACK_SEEDED = "no_track_seeded"
    TOO_FEW_DETECTIONS = "too_few_detections"
    DEPTH_DRIFT_EXCEEDED = "depth_drift_exceeded"
    DISPLACEMENT_BELOW_NOISE_FLOOR = "displacement_below_noise_floor"
    IMPLAUSIBLE_SPEED = "implausible_speed"
    DETECTION_TIMEOUT = "detection_timeout"
    DETECTION_DISABLED = "detection_disabled"

class BallDetectionTier(StrEnum):
    ROUND = "round"
    STREAK = "streak"

class ErrorCode(StrEnum):
    AUTH_INVALID_TOKEN = "auth_invalid_token"
    STORAGE_PATH_FORBIDDEN = "storage_path_forbidden"
    OBJECT_NOT_FOUND = "object_not_found"
    STORAGE_UNAVAILABLE = "storage_unavailable"
    FILE_TOO_LARGE = "file_too_large"
    UNSUPPORTED_CONTENT_TYPE = "unsupported_content_type"
    UNSUPPORTED_CODEC = "unsupported_codec"
    DECODE_FAILED = "decode_failed"
    NO_VIDEO_STREAM = "no_video_stream"
    VIDEO_TOO_SHORT = "video_too_short"
    VIDEO_TOO_LONG = "video_too_long"
    NO_POSE_DETECTED = "no_pose_detected"
    POSE_QUALITY_TOO_LOW = "pose_quality_too_low"
    CONTACT_NOT_FOUND = "contact_not_found"
    CALIBRATION_INVALID = "calibration_invalid"   # v2: request-time validation only
    QUEUE_FULL = "queue_full"
    WORKER_LOST = "worker_lost"
    INTERNAL_ERROR = "internal_error"
```

Note that **no ball-detection outcome is an `ErrorCode`.** `ErrorCode` fails the job; ball speed never fails the job. The only ball-related `ErrorCode` is `CALIBRATION_INVALID`, raised synchronously at Stage 3 for a malformed calibration block — everything discoverable only at job time is a `BallSpeedUnavailableReason`.

### 2.2 Request models

```python
# backend/app/models/requests.py
from datetime import datetime
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.models.enums import CameraView, CourtReference, Handedness, ShotType, COURT_REFERENCE_METRES

class UploadTicketRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_type: str = Field(
        pattern=r"^video/(mp4|quicktime)$",
        description="MIME type of the video the client is about to upload.",
    )
    size_bytes: int = Field(gt=0, le=52_428_800, description="Declared size; hard cap 50 MiB (Supabase free-tier per-file limit).")
    duration_s: float = Field(gt=0.0, le=60.0, description="Client-measured clip duration, seconds.")

class UploadTicketResponse(BaseModel):
    storage_path: str = Field(description="Canonical path: swing-videos/{user_id}/{uuid4}.mp4")
    upload_url: str = Field(description="Supabase Storage signed upload URL.")
    upload_token: str = Field(description="Token to pass to the Supabase Storage client.")
    expires_at: datetime = Field(description="UTC expiry of the signed URL.")


class NormalizedPoint(BaseModel):
    """A tap location, normalized to the PREVIEW SURFACE the user touched.

    Not normalized to the recorded frame. The two differ whenever the client
    letterboxes, crops, or rotates the preview -- which is the common case on
    Android. capture_width_px / capture_height_px / capture_rotation_deg on
    BallSpeedCalibration describe the space these coordinates live in, and the
    server inverts that transform (Stage 11.1).
    """
    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0.0, le=1.0, description="Fraction of preview surface width.")
    y: float = Field(ge=0.0, le=1.0, description="Fraction of preview surface height, y DOWN.")


class BallSpeedCalibration(BaseModel):
    """One-time pre-recording calibration: two tapped court points + a real distance.

    Absent from CreateAnalysisRequest => ball detection and speed calculation are
    skipped entirely. That is a normal, non-error outcome.
    """
    model_config = ConfigDict(extra="forbid")

    point_a: NormalizedPoint
    point_b: NormalizedPoint
    reference: CourtReference = Field(
        description="Which court feature was tapped. Determines the canonical distance "
                    "and which camera views the calibration is valid for.",
    )
    distance_m: float = Field(
        gt=0.5, le=25.0,
        description="Real-world distance between the two points, metres. Must match the "
                    "canonical value for `reference` unless reference == custom.",
    )
    capture_width_px: int = Field(gt=0, description="Preview surface width the taps were made on.")
    capture_height_px: int = Field(gt=0, description="Preview surface height the taps were made on.")
    capture_rotation_deg: Literal[0, 90, 180, 270] = Field(
        default=0,
        description="Rotation the client applied to recorded frames to produce that preview. "
                    "The server inverts this before applying the container display matrix.",
    )
    tapped_at: datetime | None = Field(
        default=None,
        description="When calibration was performed. Advisory: a calibration reused across a "
                    "camera move is invalid, and a stale timestamp is the only hint we get.",
    )

    @model_validator(mode="after")
    def _validate(self) -> "BallSpeedCalibration":
        """Raises -> 400 CALIBRATION_INVALID.

        Checks: (1) the two points are at least 0.05 apart in normalized units;
        (2) if reference != CUSTOM, distance_m equals COURT_REFERENCE_METRES[reference]
            within 0.01 m -- catches a desynced client build rather than silently
            overriding it server-side;
        (3) capture aspect ratio is within [0.4, 2.5].
        """
        ...


class CreateAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    storage_path: str = Field(
        min_length=1, max_length=512,
        description="Path returned by /v1/uploads/ticket. Must be prefixed swing-videos/{user_id}/.",
    )
    handedness_hint: Handedness | None = Field(
        default=None,
        description="From the user profile. Used when auto-detection confidence < 0.5.",
    )
    camera_view_hint: CameraView = Field(
        default=CameraView.UNKNOWN,
        description="Which framing guide the user filmed with. Advisory only; the server's own "
                    "estimate_camera_view() is what gates ball speed.",
    )
    client_capture_fps: float | None = Field(
        default=None, gt=0.0, le=240.0,
        description="Nominal capture fps reported by the phone. Advisory; real timing comes from PTS.",
    )
    label_hint: ShotType | None = Field(
        default=None,
        description="Optional user-declared shot type. Recorded for eval ONLY; never overrides "
                    "technique inference and never enters the Gemini payload.",
    )
    ball_speed_calibration: BallSpeedCalibration | None = Field(
        default=None,
        description="Optional. Absent => ball speed is not measured, ball_speed_mph is null with "
                    "reason 'not_calibrated', and the analysis proceeds normally. Never an error.",
    )

class CreateAnalysisResponse(BaseModel):
    analysis_id: UUID
    status: JobStatus
    poll_url: str = Field(description="Relative URL: /v1/analyses/{analysis_id}")
    estimated_seconds: int = Field(description="Rough ETA including queue depth and calibration.")
    ball_speed_requested: bool = Field(
        description="Echo: whether a calibration block was supplied and accepted. Lets the client "
                    "show the right placeholder while polling.",
    )
```

### 2.3 Nested response models

```python
# backend/app/models/responses.py
from datetime import datetime
from typing import Annotated
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, StrictInt

class VideoMeta(BaseModel):
    duration_s: float = Field(description="Decoded clip duration, seconds.")
    source_fps: float = Field(description="Effective source fps measured from PTS deltas, not metadata.")
    analysis_fps: float = Field(description="Fixed pose resample rate. Always 30.0.")
    rotation_deg: int = Field(description="Container display-matrix rotation applied. 0/90/180/270.")
    width_px: int = Field(description="Pose-stream frame width AFTER rotation and downscale (640 long edge).")
    height_px: int = Field(description="Pose-stream frame height AFTER rotation and downscale.")
    cal_space_width_px: int = Field(description="CAL_SPACE width: post-rotation, 1280 long edge.")
    cal_space_height_px: int = Field(description="CAL_SPACE height.")
    frames_decoded: int
    frames_sampled: int = Field(description="Frames actually fed to MediaPipe. Cap 240.")
    analysis_window_start_s: float
    analysis_window_end_s: float
    motion_scan_used: bool = Field(description="True if the keyframe motion scan located the window (clips > 10 s).")

class PoseQuality(BaseModel):
    frames_with_pose: int
    frames_missing: int
    detection_rate: float = Field(ge=0.0, le=1.0)
    mean_visibility: float = Field(ge=0.0, le=1.0, description="Mean over the 12 core landmarks.")
    longest_gap_frames: int
    interpolated_frames: int
    torso_scale_px: float = Field(description="Median shoulder-to-hip length in pixels. Defines 1 TU.")
    estimated_camera_view: CameraView
    usable: bool
    flags: list[str] = Field(default_factory=list, description="e.g. 'ankles_not_visible'.")

class HandednessResult(BaseModel):
    handedness: Handedness
    confidence: float = Field(ge=0.0, le=1.0)
    source: HandednessSource
    racket_hand_path_length_tu: float | None = Field(default=None)
    off_hand_path_length_tu: float | None = Field(default=None)

class ContactDetection(BaseModel):
    frame_index: int = Field(description="Index into the 30 fps sampled sequence.")
    time_s: float = Field(description="Seconds from analysis_window_start_s.")
    absolute_time_s: float = Field(
        description="analysis_window_start_s + time_s. ABSOLUTE PTS in the source file. This is "
                    "what the ball-detection seek uses; on a long clip with an 8 s window, "
                    "confusing it with time_s measures the wrong 250 ms.",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    peak_hand_speed_tu_s: float = Field(description="Peak racket-hand speed, torso units per second.")
    peak_frame_index: int
    prominence_ratio: float = Field(description="Primary peak / next distinct peak. Higher = less ambiguous.")
    method: str = Field(default="peak_speed_decel_onset_v1")

class SwingPhase(BaseModel):
    name: SwingPhaseName
    start_frame: int
    end_frame: int
    start_time_s: float
    end_time_s: float
    duration_s: float

class SwingPhases(BaseModel):
    phases: list[SwingPhase] = Field(description="Ordered, contiguous, non-overlapping. Length 5.")
    tempo_ratio: float | None = Field(
        default=None, description="takeback_duration / forward_swing_duration. Dimensionless."
    )

class SwingMetrics(BaseModel):
    """All values in the body-normalized frame. None = not measurable in this clip.

    Ball speed is deliberately NOT a member. It is a physical measurement with
    different error characteristics and lives in AnalysisResponse.ball_speed.
    """
    shoulder_hip_separation_deg: float | None = None
    shoulder_turn_deg: float | None = None
    hip_rotation_deg: float | None = None
    elbow_angle_at_contact_deg: float | None = None
    wrist_lag_deg: float | None = None
    contact_height_ratio: float | None = Field(default=None, description="0 = hip, 1 = shoulder.")
    contact_point_forward_tu: float | None = None
    peak_hand_speed_tu_s: float | None = None
    swing_path_angle_deg: float | None = Field(default=None, description="Positive = low-to-high.")
    swing_plane_deviation_tu: float | None = None
    knee_flexion_min_deg: float | None = None
    weight_transfer_tu: float | None = None
    follow_through_height_tu: float | None = None
    balance_sway_tu: float | None = None
    head_stillness_tu: float | None = None
    wrist_separation_at_contact_tu: float | None = None
    takeback_displacement_tu: float | None = None

class ShotTypeInference(BaseModel):
    shot_type: ShotType
    confidence: float = Field(ge=0.0, le=1.0)
    class_scores: dict[str, float] = Field(description="Per-class scores, sum to 1.0.")
    evidence: list[str] = Field(description="Deterministic Python-generated rationale strings.")
    rule_version: str = Field(default="shot_rules_v1")

class MetricScore(BaseModel):
    name: str
    value: float | None
    unit: MetricUnit
    verdict: MetricVerdict
    score_0_100: float | None = Field(default=None, description="None iff verdict == unavailable.")
    weight: float = Field(description="Rubric weight within its category, pre-renormalization.")
    ideal_min: float | None = None
    ideal_max: float | None = None
    category: ScoreCategory
    view_sensitive: bool = Field(
        description="True if this value shifts materially with camera yaw. Client should de-emphasize."
    )

class CategoryScore(BaseModel):
    category: ScoreCategory
    score_0_100: float | None
    weight: float
    metric_names: list[str]
    metrics_available: int
    metrics_total: int

class Scorecard(BaseModel):
    overall_score: float | None = Field(default=None, ge=0.0, le=100.0)
    categories: list[CategoryScore]
    metrics: list[MetricScore]
    rubric_version: str = Field(default="rubric_v1")
```

### 2.4 Ball-speed response models (v2)

```python
# backend/app/models/responses.py (continued)

class CalibrationEcho(BaseModel):
    """What the server actually derived from the tapped points.

    Echoed so a coordinate-space bug is visible in the payload rather than hidden
    inside a plausible-looking mph value: a px_per_m of 4 instead of 40 is obvious
    here and invisible in the speed alone.
    """
    reference: CourtReference
    distance_m: float
    segment_px: float = Field(description="Tapped-point separation in CAL_SPACE pixels.")
    px_per_m: float = Field(description="segment_px / distance_m. The scale factor.")
    point_a_cal_px: tuple[float, float] = Field(description="Mapped tap A, CAL_SPACE pixels.")
    point_b_cal_px: tuple[float, float] = Field(description="Mapped tap B, CAL_SPACE pixels.")
    frame_shape_check_passed: bool = Field(
        description="Preview aspect matched decoded post-rotation aspect within 2%."
    )

class BallDetectionSummary(BaseModel):
    """Observability for the classical-CV stage. Never contains image data."""
    frames_scanned: int = Field(description="Native-fps frames processed in the measurement window.")
    native_fps: float = Field(description="Measured source fps in the window, from PTS deltas.")
    detection_long_edge_px: int = Field(description="CAL_SPACE long edge. 1280.")
    raw_candidates: int = Field(description="Contours passing either tier, before association.")
    accepted_detections: int = Field(description="Detections associated into the track. Drives confidence.")
    round_tier_detections: int
    streak_tier_detections: int
    coasted_frames: int = Field(description="Frames with no candidate, bridged by prediction.")
    window_start_s: float = Field(description="Absolute PTS.")
    window_end_s: float = Field(description="Absolute PTS. Early-terminated windows end sooner.")
    terminated_by: str = Field(description="'window_end' | 'direction_gate' | 'frame_edge' | 'miss_limit'")
    median_step_px: float | None = None
    median_step_dt_s: float | None = None
    blob_size_drift_ratio: float | None = Field(
        default=None,
        description="minor_axis[last] / minor_axis[first]. Coarse depth-travel proxy; see Stage 10.6.",
    )
    detector_version: str = Field(default="ball_cv_v1")
    elapsed_ms: int

class BallSpeedResult(BaseModel):
    """Always present in AnalysisResponse. The SPEED inside it is nullable."""
    model_config = ConfigDict(extra="forbid")

    ball_speed_mph: Annotated[StrictInt, Field(ge=15, le=160)] | None = Field(
        default=None,
        description=(
            "Whole miles per hour, or null. StrictInt: a float is a validation error, not a "
            "coercion -- the measurement is not precise enough to justify decimals and the type "
            "enforces that. Null when calibration was skipped, when fewer than 3 valid "
            "post-contact ball detections exist, or when any geometry gate fires. NEVER a "
            "fabricated or best-guess number; there is no fallback estimator."
        ),
    )
    confidence: BallSpeedConfidence = Field(
        description="Derived from accepted_detections (>=9 high, 5-8 medium, 3-4 low, <3 unavailable), "
                    "then capped downward by geometry gates. Gates can only lower it.",
    )
    detections_used: int = Field(
        ge=0, description="Accepted, associated detections that fed the median. Sets the confidence tier.",
    )
    unavailable_reason: BallSpeedUnavailableReason | None = Field(
        default=None, description="Non-null iff ball_speed_mph is null.",
    )
    confidence_caps_applied: list[str] = Field(
        default_factory=list,
        description="Which downward caps fired, e.g. ['oblique_view', 'blob_drift'].",
    )
    measurement_definition: str = Field(
        default=(
            "Average ball speed over the first 0.25 seconds after contact, not the "
            "instantaneous speed off the racket. A tennis ball decelerates at roughly "
            "18 m/s^2 in flight; this window is defined rather than drag-corrected because "
            "correcting would require spin, which this system does not measure."
        ),
        description="Constant. Must be surfaced in the UI next to the number.",
    )
    calibration: CalibrationEcho | None = Field(
        default=None, description="Null when no calibration was supplied.",
    )
    detection: BallDetectionSummary | None = Field(
        default=None, description="Null when the ball stage was skipped entirely.",
    )

class Improvement(BaseModel):
    priority: int = Field(ge=1, le=3)
    title: str
    why: str = Field(description="Prose explanation. Contains only numbers from the payload.")
    cue: str = Field(description="One short on-court cue.")
    drill: str
    metric_refs: list[str] = Field(description="Names of MetricScore entries this refers to.")

class NumericGuardReport(BaseModel):
    passed: bool
    rejected_tokens: list[str] = Field(default_factory=list)
    fields_discarded: list[str] = Field(default_factory=list)
    fell_back_to_template: bool
    mph_rule: str = Field(
        default="banned",
        description="'banned' when ball_speed_mph is null; 'bound_to:<int>' when it is not. "
                    "Recorded so a guard-behaviour regression is visible in stored analyses.",
    )

class CoachingFeedback(BaseModel):
    summary: str
    strengths: list[str]
    improvements: list[Improvement]
    source: FeedbackSource
    model: str | None = Field(default=None, description="e.g. 'gemini-2.5-flash'. None if template.")
    guard: NumericGuardReport
    latency_ms: int | None = None

class StageTimings(BaseModel):
    download_ms: int
    motion_scan_ms: int
    decode_ms: int
    pose_ms: int
    analysis_ms: int
    ball_detect_ms: int = Field(description="0 when the ball stage was skipped.")
    ball_speed_ms: int
    feedback_ms: int
    persist_ms: int
    total_ms: int

class AnalysisError(BaseModel):
    code: ErrorCode
    message: str
    stage: str
    retryable: bool

class AnalysisJobStatusResponse(BaseModel):
    analysis_id: UUID
    status: JobStatus
    queue_position: int | None = None
    estimated_seconds_remaining: int | None = None
    error: AnalysisError | None = None

class AnalysisResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: UUID
    user_id: UUID
    created_at: datetime
    status: AnalysisStatus
    pipeline_version: str = Field(default="v2")

    video: VideoMeta
    pose_quality: PoseQuality
    handedness: HandednessResult
    contact: ContactDetection
    phases: SwingPhases
    shot_type: ShotTypeInference
    metrics: SwingMetrics
    ball_speed: BallSpeedResult = Field(
        description="Always present. ball_speed.ball_speed_mph is the nullable value.",
    )
    scorecard: Scorecard
    feedback: CoachingFeedback
    warnings: list[str] = Field(default_factory=list)
    timings: StageTimings
```

### 2.5 Why `StrictInt` and not `int`

`ball_speed_mph: Annotated[StrictInt, Field(ge=15, le=160)] | None` is chosen over plain `int | None` deliberately. Pydantic v2's lax mode coerces `72.0 -> 72` and rejects `72.4`; that would let a float leak into the pipeline and be silently truncated at the boundary depending on which value happened to be whole. `StrictInt` rejects **any** float, including `72.0`, so the only way a value reaches this field is through the single `round(...) -> int` in `analysis/speed.py`.

Three layers enforce whole numbers, and each catches a different failure:

| Layer | Catches |
|---|---|
| `StrictInt` in Pydantic | a float leaking from the estimator or from a future caller |
| `integer` column in Postgres | a float leaking from a raw SQL path or a backfill script |
| Guard allowlist adds the integer rendering **only** (§17.1) | Gemini writing `"68.0 mph"` |

`ge=15, le=160` is the plausibility bound from Stage 11.4 restated at the schema boundary, so an out-of-range value cannot be serialized even if a future bug produced one.

### 2.6 Example response body

```json
{
  "analysis_id": "9f2c1a44-3b7e-4f0d-9a11-6c5c2e8b71aa",
  "user_id": "b1d9f0a2-1c4e-4a77-8f21-0f9a2c3d4e55",
  "created_at": "2026-08-28T14:22:09.481Z",
  "status": "complete",
  "pipeline_version": "v2",
  "video": {
    "duration_s": 6.24, "source_fps": 59.94, "analysis_fps": 30.0,
    "rotation_deg": 90, "width_px": 360, "height_px": 640,
    "cal_space_width_px": 720, "cal_space_height_px": 1280,
    "frames_decoded": 374, "frames_sampled": 187,
    "analysis_window_start_s": 0.0, "analysis_window_end_s": 6.23,
    "motion_scan_used": false
  },
  "pose_quality": {
    "frames_with_pose": 183, "frames_missing": 4, "detection_rate": 0.979,
    "mean_visibility": 0.88, "longest_gap_frames": 2, "interpolated_frames": 4,
    "torso_scale_px": 138.6, "estimated_camera_view": "side_on",
    "usable": true, "flags": ["left_ankle_low_visibility"]
  },
  "handedness": {
    "handedness": "right", "confidence": 0.91, "source": "detected",
    "racket_hand_path_length_tu": 7.83, "off_hand_path_length_tu": 2.41
  },
  "contact": {
    "frame_index": 96, "time_s": 3.20, "absolute_time_s": 3.20, "confidence": 0.74,
    "peak_hand_speed_tu_s": 11.62, "peak_frame_index": 95,
    "prominence_ratio": 2.34, "method": "peak_speed_decel_onset_v1"
  },
  "phases": {
    "phases": [
      {"name":"ready","start_frame":0,"end_frame":51,"start_time_s":0.0,"end_time_s":1.70,"duration_s":1.70},
      {"name":"takeback","start_frame":52,"end_frame":78,"start_time_s":1.73,"end_time_s":2.60,"duration_s":0.87},
      {"name":"forward_swing","start_frame":79,"end_frame":95,"start_time_s":2.63,"end_time_s":3.17,"duration_s":0.54},
      {"name":"contact","start_frame":95,"end_frame":97,"start_time_s":3.17,"end_time_s":3.23,"duration_s":0.06},
      {"name":"follow_through","start_frame":98,"end_frame":140,"start_time_s":3.27,"end_time_s":4.67,"duration_s":1.40}
    ],
    "tempo_ratio": 1.61
  },
  "shot_type": {
    "shot_type": "forehand_topspin",
    "confidence": 0.81,
    "class_scores": {
      "forehand_topspin":0.81,"forehand_slice":0.07,"backhand_one_handed":0.05,
      "backhand_two_handed":0.03,"serve":0.02,"volley":0.02
    },
    "evidence": [
      "wrist separation 0.62 TU exceeds 0.35 two-handed threshold",
      "contact on dominant side of mid-hip for right-handed player",
      "swing path angle +24.1 deg indicates low-to-high"
    ],
    "rule_version": "shot_rules_v1"
  },
  "metrics": {
    "shoulder_hip_separation_deg": 38.4,
    "shoulder_turn_deg": 71.2,
    "hip_rotation_deg": 41.9,
    "elbow_angle_at_contact_deg": 152.7,
    "wrist_lag_deg": 27.3,
    "contact_height_ratio": 0.86,
    "contact_point_forward_tu": 0.71,
    "peak_hand_speed_tu_s": 11.62,
    "swing_path_angle_deg": 24.1,
    "swing_plane_deviation_tu": 0.061,
    "knee_flexion_min_deg": null,
    "weight_transfer_tu": 0.28,
    "follow_through_height_tu": 0.44,
    "balance_sway_tu": 0.093,
    "head_stillness_tu": 0.037,
    "wrist_separation_at_contact_tu": 0.62,
    "takeback_displacement_tu": 1.14
  },
  "ball_speed": {
    "ball_speed_mph": 68,
    "confidence": "medium",
    "detections_used": 6,
    "unavailable_reason": null,
    "confidence_caps_applied": [],
    "measurement_definition": "Average ball speed over the first 0.25 seconds after contact, not the instantaneous speed off the racket. A tennis ball decelerates at roughly 18 m/s^2 in flight; this window is defined rather than drag-corrected because correcting would require spin, which this system does not measure.",
    "calibration": {
      "reference": "sideline_baseline_to_net",
      "distance_m": 11.885,
      "segment_px": 604.2,
      "px_per_m": 50.84,
      "point_a_cal_px": [92.0, 838.5],
      "point_b_cal_px": [668.4, 655.1],
      "frame_shape_check_passed": true
    },
    "detection": {
      "frames_scanned": 15,
      "native_fps": 59.94,
      "detection_long_edge_px": 1280,
      "raw_candidates": 23,
      "accepted_detections": 6,
      "round_tier_detections": 1,
      "streak_tier_detections": 5,
      "coasted_frames": 2,
      "window_start_s": 3.20,
      "window_end_s": 3.38,
      "terminated_by": "frame_edge",
      "median_step_px": 25.83,
      "median_step_dt_s": 0.0167,
      "blob_size_drift_ratio": 0.91,
      "detector_version": "ball_cv_v1",
      "elapsed_ms": 1180
    }
  },
  "scorecard": {
    "overall_score": 71.5,
    "categories": [
      {"category":"preparation","score_0_100":78.0,"weight":0.25,
       "metric_names":["shoulder_hip_separation_deg","hip_rotation_deg","takeback_displacement_tu"],
       "metrics_available":3,"metrics_total":3},
      {"category":"contact","score_0_100":64.0,"weight":0.30,
       "metric_names":["contact_height_ratio","contact_point_forward_tu","elbow_angle_at_contact_deg","wrist_lag_deg"],
       "metrics_available":4,"metrics_total":4},
      {"category":"swing_path","score_0_100":82.0,"weight":0.20,
       "metric_names":["swing_path_angle_deg","swing_plane_deviation_tu","peak_hand_speed_tu_s"],
       "metrics_available":3,"metrics_total":3},
      {"category":"balance","score_0_100":69.0,"weight":0.15,
       "metric_names":["balance_sway_tu","head_stillness_tu","weight_transfer_tu","knee_flexion_min_deg"],
       "metrics_available":3,"metrics_total":4},
      {"category":"follow_through","score_0_100":64.5,"weight":0.10,
       "metric_names":["follow_through_height_tu","tempo_ratio"],
       "metrics_available":2,"metrics_total":2}
    ],
    "metrics": [
      {"name":"shoulder_hip_separation_deg","value":38.4,"unit":"deg","verdict":"low",
       "score_0_100":64.0,"weight":0.5,"ideal_min":45.0,"ideal_max":65.0,
       "category":"preparation","view_sensitive":false},
      {"name":"contact_height_ratio","value":0.86,"unit":"ratio","verdict":"ideal",
       "score_0_100":100.0,"weight":0.3,"ideal_min":0.75,"ideal_max":1.05,
       "category":"contact","view_sensitive":false},
      {"name":"follow_through_height_tu","value":0.44,"unit":"TU","verdict":"low",
       "score_0_100":52.0,"weight":0.6,"ideal_min":0.60,"ideal_max":1.10,
       "category":"follow_through","view_sensitive":false},
      {"name":"knee_flexion_min_deg","value":null,"unit":"deg","verdict":"unavailable",
       "score_0_100":null,"weight":0.25,"ideal_min":135.0,"ideal_max":160.0,
       "category":"balance","view_sensitive":true}
    ],
    "rubric_version": "rubric_v1"
  },
  "feedback": {
    "summary": "Solid forehand with a clean low-to-high swing path of 24.1 degrees and a contact height ratio of 0.86, right in the ideal window. Ball speed came out around 68 mph off this one. The main thing holding this swing back is body coil: shoulder-hip separation measured 38.4 degrees against an ideal range of 45 to 65.",
    "strengths": [
      "Swing path angle of 24.1 degrees is well inside the topspin-producing range.",
      "Contact height ratio of 0.86 sits in the ideal 0.75 to 1.05 band.",
      "Head stillness of 0.037 torso units shows a very steady head through contact."
    ],
    "improvements": [
      {
        "priority": 1,
        "title": "Coil more in the takeback",
        "why": "Shoulder-hip separation reached 38.4 degrees, below the ideal 45 to 65 degree range. Less separation means less stored energy to release into the ball.",
        "cue": "Turn your shoulders until your chin touches your front shoulder while your hips stay quieter.",
        "drill": "Shadow swings against a fence: take the racket back until your back is half-visible to the net, hold two seconds, then swing.",
        "metric_refs": ["shoulder_hip_separation_deg"]
      },
      {
        "priority": 2,
        "title": "Finish higher",
        "why": "Follow-through height reached 0.44 torso units, short of the ideal 0.60 to 1.10 range. A truncated finish usually means the swing is being decelerated early.",
        "cue": "Finish with the racket hand over your opposite shoulder.",
        "drill": "Ten feed balls where the only goal is catching the racket behind your opposite shoulder.",
        "metric_refs": ["follow_through_height_tu"]
      }
    ],
    "source": "gemini",
    "model": "gemini-2.5-flash",
    "guard": {
      "passed": true, "rejected_tokens": [], "fields_discarded": [],
      "fell_back_to_template": false, "mph_rule": "bound_to:68"
    },
    "latency_ms": 2140
  },
  "warnings": [
    "knee_flexion_min_deg unavailable: left ankle visibility below threshold during forward swing",
    "ball speed is an average over the first 0.25 s after contact and carries roughly +/-10-15% geometric error"
  ],
  "timings": {
    "download_ms": 1840, "motion_scan_ms": 0, "decode_ms": 2610, "pose_ms": 16920,
    "analysis_ms": 118, "ball_detect_ms": 1180, "ball_speed_ms": 3,
    "feedback_ms": 2140, "persist_ms": 310, "total_ms": 25121
  }
}
```

### 2.7 Example — calibration skipped

The common case. Only the differing blocks shown:

```json
{
  "pipeline_version": "v2",
  "ball_speed": {
    "ball_speed_mph": null,
    "confidence": "unavailable",
    "detections_used": 0,
    "unavailable_reason": "not_calibrated",
    "confidence_caps_applied": [],
    "measurement_definition": "Average ball speed over the first 0.25 seconds after contact, not the instantaneous speed off the racket. A tennis ball decelerates at roughly 18 m/s^2 in flight; this window is defined rather than drag-corrected because correcting would require spin, which this system does not measure.",
    "calibration": null,
    "detection": null
  },
  "feedback": {
    "guard": { "passed": true, "rejected_tokens": [], "fields_discarded": [],
               "fell_back_to_template": false, "mph_rule": "banned" }
  },
  "timings": { "ball_detect_ms": 0, "ball_speed_ms": 0 }
}
```

Note `status` is still `"complete"`. A missing ball speed does **not** downgrade the analysis to `partial` — `partial` means the *swing analysis* is degraded, and ball speed is not part of the swing analysis. Conflating the two would make the majority of clips (uncalibrated) look broken.

---

## 3. File and Module Breakdown

```
backend/
  app/
    main.py
    config.py
    errors.py
    deps.py
    api/
      __init__.py
      router.py
      auth.py
      routes_health.py
      routes_uploads.py
      routes_analyses.py
    pose/
      __init__.py
      landmarks.py
      video_io.py
      extractor.py
      sequence.py
    ball/                     # NEW in v2
      __init__.py
      params.py               # PURE   — detector constants, court dimensions
      frames.py               # IMPURE — second decode pass, native fps, CAL_SPACE
      geometry.py             # PURE   — coordinate mapping, calibration scale, exclusion boxes
      detector.py             # PURE-CV— background/HSV/contour. numpy + cv2 + app.ball.track only.
      track.py                # PURE   — cross-frame association; owns the shared value objects
                              #          (ContourFeatures, BallCandidate, BallTrack,
                              #          BallDetectionResult) and the tier constants
      sequence.py             # PURE   — builds the BallTrack seam object
    analysis/
      __init__.py
      types.py
      smoothing.py
      normalize.py
      kinematics.py
      handedness.py
      contact.py
      phases.py
      metrics.py
      speed.py                # NEW — ball-speed estimator. PURE, numpy only, no cv2.
      shot_type.py
      rubric.py
      scoring.py
      pipeline.py
    feedback/
      __init__.py
      payload.py
      prompt.py
      gemini_client.py
      guard.py
      template.py
      service.py
    models/
      __init__.py
      enums.py
      requests.py
      responses.py
      internal.py
    services/
      __init__.py
      storage.py
      repository.py
      jobs.py
      orchestrator.py
  tests/
  pyproject.toml
  requirements.txt
```

**Deviations from the CLAUDE.md skeleton, declared explicitly:**

- `app/services/` — added in v1. Holds Supabase storage/DB I/O and the job runner. These are impure and belong in neither `api/` (routes) nor `analysis/` (must stay pure).
- `app/config.py`, `app/errors.py`, `app/deps.py` — added in v1. Settings via `pydantic-settings`, the `ErrorCode` taxonomy, FastAPI dependency wiring.
- `app/models/internal.py` — **planned; not present on disk.** `backend/app/models/` currently contains only `__init__.py`, `enums.py` and `feedback.py`. When built, it is the intended home for internal dataclasses (`PoseSequence`, `NormalizedSequence`, `KinematicCore`, `AnalysisCore`, `CalibrationScale`) that are not part of the HTTP contract. It is **not** the home of the ball value objects: `BallTrack`, `BallCandidate`, `ContourFeatures` and `BallDetectionResult` ship in `app/ball/track.py` and stay there permanently, for the reason given in §3.1 under `BALL_PURITY_EDGE_ONE_WAY`. `CalibrationScale` is not implemented anywhere yet.
- **`app/ball/` — added in v2.** Justified below.

### 3.1 Where ball detection lives, and why it is not in `analysis/`

This is the one genuinely contested placement in the revision, so the reasoning is written out rather than assumed.

CLAUDE.md says `analysis/` is "contact detection + swing math (PURE, no I/O)" and "functions take keypoint arrays in, return numbers out. No I/O inside math functions." Ball detection has three properties that pull in different directions:

1. It **consumes video frames**, which must be decoded — unambiguously I/O.
2. Its core is **deterministic image processing** — a pure function from a numpy array to a list of candidate blobs. No network, no model weights, no randomness, no clock.
3. It **requires OpenCV**, which is currently on the banned-import list that `tests/architecture/test_purity_boundary.py` enforces against `app/analysis/**`.

Putting it all in `analysis/` would require relaxing the purity test to permit `cv2` — and once `cv2` is importable from `analysis/`, nothing structurally stops a future contributor from calling `cv2.VideoCapture` inside a metric function. Putting it all in `pose/` is worse: `pose/` is the MediaPipe boundary and ball detection has nothing to do with pose. Putting it in `services/` would bury deterministic, unit-testable math inside the I/O layer where nobody would think to test it.

**Resolution: a new top-level package `app/ball/`, with the impure part isolated to exactly one file, and a second purity seam defined as precisely as `PoseSequence`.**

This introduces a third purity class, which is named and defined rather than left implicit:

| Class | Definition | May import | Where it lives |
|---|---|---|---|
| **PURE** | Deterministic; no I/O, no network, no model, no clock, no cv2. Arrays and scalars in, arrays and scalars out. | `numpy`, `scipy`, stdlib math, `app.models` | `app/analysis/**`, `app/ball/geometry.py`, `app/ball/track.py`, `app/ball/sequence.py`, `app/ball/params.py`, `app/pose/sequence.py`, `app/pose/landmarks.py` |
| **PURE-CV** | Deterministic; no I/O, no network, no model weights, no clock. Array in, array or dataclass out. Depends on OpenCV as an *image-processing library only*. | `numpy`, `cv2`, `app.models`, `app.ball.track` | `app/ball/detector.py` — **and nothing else, ever** |
| **IMPURE** | Anything touching the filesystem, network, a decoder, a model, or the clock. | anything | `api/**`, `pose/video_io.py`, `pose/extractor.py`, `ball/frames.py`, `feedback/gemini_client.py`, `feedback/service.py`, `services/**` |

`PURE-CV` is not a loophole. It carries the same testability guarantee that CLAUDE.md actually cares about: **every function in `ball/detector.py` is unit-testable with synthetic, programmatically generated inputs — no video files, no network, no model** (§4.5). What it gives up is only the ability to live under `app/analysis/`, and the purity test is extended, not weakened, to enforce that:

- `app/analysis/**` — banned imports unchanged, **plus `cv2`** explicitly added to the banned list. `analysis/speed.py` is strict-pure numpy; it never sees a frame.
- `app/ball/geometry.py`, `track.py`, `sequence.py`, `params.py` — same banned list as `analysis/`, including `cv2` and `av`.
- `app/ball/detector.py` — banned list is everything except `numpy`, `cv2`, `app.models`, and `app.ball.track`. Specifically banned: `av`, `pathlib`, `open`, `socket`, `httpx`, `supabase`, `mediapipe`, `app.services`, `time`.
- `app/ball/frames.py` — the only file in `app/ball/` permitted to import `av` or touch the filesystem. It is the exact analogue of `pose/video_io.py`.

**Invariant `BALL_PURITY_EDGE_ONE_WAY` — the dependency edge inside `app/ball/` points one way only.**

- `app/ball/detector.py` **MAY** import `app/ball/track.py`.
- `app/ball/track.py` **MUST NOT** import `app/ball/detector.py`, nor any other module that carries `cv2` transitively.
- General form: a **PURE-CV** module may depend on a **PURE** module; a **PURE** module may never depend on a **PURE-CV** one. Both directions are asserted, not just the banned-import one — a test that only checks `track.py` never names `cv2` would pass while `track.py` imported `detector.py` and pulled `cv2` in anyway.

The direction is not arbitrary, so the reasoning is written out. The shared value objects (`ContourFeatures`, `BallCandidate`, `BallTrack`, `BallDetectionResult`) and the tier constants (`TIER_ROUND`, `TIER_STREAK`, `TIER_REJECTED`) are needed by both halves: `detector.py` constructs candidates, and `associate_candidates()` in `track.py` reads `tier` against `TIER_STREAK` at runtime and constructs the `BallTrack` seam object. They have to live in exactly one of the two files. Had they been placed in `detector.py` — the intuitive choice, since that is where a `ContourFeatures` is first built — then `track.py` would import `detector.py`, and importing `detector.py` executes `import cv2`. `track.py` would silently become PURE-CV, the PURE class inside `app/ball/` would collapse to `geometry.py` and `params.py`, and the association logic would no longer be testable in an environment without OpenCV. Placing them in `track.py` costs nothing in return: dataclasses of floats and ints have no OpenCV dependency, and `detector.py` already imports `cv2` by definition, so one more import into it changes no purity class. `BallTrack` is specified once in §3.2 and lives here for the same reason.

### 3.2 THE SECOND SEAM — `BallTrack`

`PoseSequence` is the seam where MediaPipe's impure output becomes pure numpy. `BallTrack` is the identical construction for the ball path, and it is defined with the same rigor:

**Shipped today, in `app/ball/track.py`, verbatim:**

```python
@dataclass(frozen=True)
class BallTrack:
    """The ball-side seam object (PIPELINE.md 4.2 / 3.2). Plain numpy + scalars.

    Frozen, ``.npz``-serializable, no handles and no cv2 objects. The temporal
    fields of the 3.2 definition (``timestamps_s``, ``contact_time_s``,
    ``window_start_s``, ``window_end_s``) are NOT populated here: they come from
    the impure frame-acquisition pass, which this module deliberately does not
    perform. ``frame_offsets`` carries the index within the frame stack it was
    given, which is what a frames-in/positions-out module can honestly know.
    """

    #: (N, 2) float32 -- centroids in CAL_SPACE pixels, y DOWN.
    xy_px: npt.NDArray[np.float32]
    #: (N,) int32 -- index within the frame stack passed in.
    frame_offsets: npt.NDArray[np.int32]
    #: (N,) float32 -- depth-drift proxy (PIPELINE.md 10.6).
    minor_axis_px: npt.NDArray[np.float32]
    #: (N,) float32
    major_axis_px: npt.NDArray[np.float32]
    #: (N,) uint8 -- 1 = round, 2 = streak.
    tier: npt.NDArray[np.uint8]
    terminated_by: str
    coasted_frames: int
```

`terminated_by` is one of the three `Final[str]` constants in `track.py`: `TERMINATED_WINDOW_END` (`"window_end"`), `TERMINATED_DIRECTION_CHANGE` (`"direction_change"`), `TERMINATED_COAST_EXHAUSTED` (`"coast_exhausted"`).

**Planned, gated on `ball/frames.py` (which does not exist):** `timestamps_s: np.ndarray` (N, float64, absolute PTS per detection), `contact_time_s: float`, `window_start_s: float`, `window_end_s: float`. These are absent by design, not by oversight — `track.py` never reads a clock and never decodes, so it cannot know a PTS. `frame_offsets` is the honest stand-in until the impure pass exists to attach real timestamps.

A track is never returned bare. The entry point returns the frozen wrapper, also shipped in `track.py`:

```python
@dataclass(frozen=True)
class BallDetectionResult:
    """Either a track, or ``None`` plus the reason no track was produced."""

    track: BallTrack | None
    reason: BallSpeedUnavailableReason | None
```

Invariants for `ball/sequence.validate_track_invariants()`, mirroring `pose/sequence.py`. **`app/ball/sequence.py` does not exist yet**, so nothing below is enforced by a shipped function; the first group is nonetheless assertable directly against any `BallTrack` that `associate_candidates()` returns today.

*Assertable against shipped code:*
- All five arrays (`xy_px`, `frame_offsets`, `minor_axis_px`, `major_axis_px`, `tier`) share length `N`.
- `frame_offsets` is strictly increasing — the association loop only ever appends a candidate from a later frame index.
- `xy_px` is finite and within `CAL_SPACE` bounds.
- `N >= MIN_ACCEPTED_DETECTIONS` (3). A `BallTrack` with fewer detections is **never** constructed: `associate_candidates()` returns `BallDetectionResult(None, TOO_FEW_DETECTIONS)` instead. An empty track is therefore *not* a legal `BallTrack` — "no detections" is represented by `track is None` plus a reason, which supersedes the v1 `N >= 0` wording.
- `terminated_by` is one of the three `TERMINATED_*` constants; `coasted_frames <= MAX_COASTED_FRAMES` (2).

*Future requirement, gated on the `ball/frames.py` pass — NOT assertable today:*
- `timestamps_s` is strictly increasing.
- `window_start_s <= timestamps_s[0]` and `timestamps_s[-1] <= window_end_s`.

These two are retained rather than deleted because they are the contract the frame pass must satisfy when it lands, and §3.2's job is to specify the seam. They must not be written into a test until the fields exist.

`CalibrationScale` is the second frozen input to the pure estimator. **It is not implemented yet** — no module in `backend/app/` defines it, and neither does `CourtReference`. Specified here so the estimator's input surface is fixed before it is written:

```python
@dataclass(frozen=True)
class CalibrationScale:
    px_per_m: float
    segment_px: float
    point_a_cal_px: tuple[float, float]
    point_b_cal_px: tuple[float, float]
    reference: CourtReference
    distance_m: float
    frame_shape_check_passed: bool
```

`analysis/speed.estimate_ball_speed()` takes `(BallTrack, CalibrationScale, CameraView)` and returns `BallSpeedResult`. That is its entire input surface: three frozen, serializable values. It cannot decode, cannot detect, cannot reach the network, and can be exhaustively tested from constructed dataclasses.

### 3.3 Consequence for `analysis/pipeline.py` — the pure entry point splits in two

In v1, `analyze(seq) -> AnalysisCore` was a single pure call spanning Stages 7–15. That is no longer possible: Stage 10 is an impure excursion sitting between contact detection (Stage 9) and phase segmentation (Stage 12), because ball detection *depends on* the contact frame and *requires* video frames.

Three options were considered:

1. **Pass the video path into `analyze()`.** Rejected — it makes the pure entry point impure and destroys the golden-fixture strategy.
2. **Move ball detection after all pure analysis (Stage 15.5).** Rejected — the requirement places it after contact detection, and more importantly the ordering is causally right: nothing between Stages 12–15 informs ball detection, so deferring it would only obscure the dependency.
3. **Split the pure entry point.** Chosen.

```python
def analyze_kinematics(seq: PoseSequence,
                       handedness_hint: Handedness | None = None) -> KinematicCore: ...

def analyze_swing(kin: KinematicCore,
                  ball: BallTrack | None = None,
                  scale: CalibrationScale | None = None) -> AnalysisCore: ...
```

`KinematicCore` carries `NormalizedSequence`, `PoseQuality`, `HandednessResult`, `ContactDetection` and nothing else. Both halves are pure, both are golden-fixture testable, and the impure excursion lives entirely in `services/orchestrator.py` where every other impure thing already lives. The orchestrator reads:

```
seq   = collect_pose(video_path)            # impure, extractor closed on exit
kin   = analyze_kinematics(seq, hint)       # pure
track = collect_ball(video_path, kin, cal)  # impure excursion; None if uncalibrated
core  = analyze_swing(kin, track, scale)    # pure
```

The determinism property that made v1 testable survives intact: given the same `PoseSequence` and the same `BallTrack`, `analyze_kinematics` + `analyze_swing` produce a bit-identical `AnalysisCore`.

---

### 3.4 New and changed module signatures

Only new or changed files are listed. Every other file in §3's tree is unchanged from `v1`.

> **Implementation status, as of this revision.** Of the modules in §3's tree, only the following exist on disk: `app/models/enums.py`, `app/models/feedback.py`, `app/feedback/gemini.py`, `app/feedback/references.py`, `app/ball/detector.py` and `app/ball/track.py`. Of the four PURE modules planned for `app/ball/` — `params.py`, `frames.py`, `geometry.py`, `sequence.py` — **none exist**; `backend/app/ball/` contains exactly `__init__.py`, `detector.py` and `track.py`. `app/analysis/` does not exist as a package at all. The §3 tree is a target layout, not an inventory. Sections below are individually marked **SHIPPED** or **PLANNED**; only the SHIPPED ones may be asserted against by a signature or purity test.

#### `app/config.py` — **changed**
```python
class Settings(BaseSettings):
    supabase_url: str
    supabase_service_role_key: SecretStr
    supabase_jwks_url: str
    supabase_jwt_audience: str = "authenticated"
    supabase_storage_bucket: str = "swing-videos"
    gemini_api_key: SecretStr
    gemini_model: str = "gemini-2.5-flash"
    gemini_timeout_s: float = 8.0
    max_upload_bytes: int = 52_428_800          # 50 MiB — Supabase free-tier per-file limit
    max_clip_seconds: float = 60.0              # confirmed (was 12.0 in v1)
    analysis_window_seconds: float = 8.0
    analysis_fps: float = 30.0                  # confirmed
    max_analysis_frames: int = 240
    target_long_edge_px: int = 640
    motion_scan_threshold_s: float = 10.0       # clips longer than this get the keyframe scan
    motion_scan_long_edge_px: int = 160
    job_queue_max_depth: int = 4
    job_heartbeat_stale_s: int = 180

    # --- v2: ball speed ---
    ball_detection_enabled: bool = True         # kill switch; False => reason 'detection_disabled'
    ball_cal_space_long_edge_px: int = 1280
    ball_window_post_s: float = 0.25
    ball_background_preroll_s: float = 0.50
    ball_background_gap_s: float = 0.05
    ball_background_frames: int = 15
    ball_min_detections: int = 3
    ball_detection_deadline_s: float = 6.0
    ball_speed_min_mph: int = 15
    ball_speed_max_mph: int = 160

def get_settings() -> Settings: ...
```

#### `app/ball/params.py` — **PURE — PLANNED, DOES NOT EXIST**

> **Not implemented.** Every constant below ships today as a module-level `Final` literal: masking, tier A, tier B and `PLAYER_BOX_DILATION` in `detector.py`; `V_MAX_M_PER_S`, `GATE_SHRINK_FACTOR`, `MIN_GATE_RADIUS_PX`, `DIRECTION_CHANGE_LIMIT_DEG`, `MIN_STEP_PX`, `MAX_COASTED_FRAMES`, `EXIT_FRAME_MARGIN_PX`, `MIN_ACCEPTED_DETECTIONS`, `STREAK_ORIENTATION_TOLERANCE_DEG`, `CAL_SPACE_PX_PER_M` and `DEFAULT_FPS` in `track.py`. Consequently **no shipped function takes a params object** — every one takes bare keyword floats/ints with module-constant defaults. `DetectorParams`, `TrackParams`, `SpeedParams`, `DETECTOR_V1`, `TRACK_V1`, `SPEED_V1` and `DETECTOR_VERSION` are the intended future refactor and must not be asserted against.

Owns, once built: every detector constant and court dimension, as named frozen values. Exists so no other module contains the literal `0.65` or `11.885`, and so a parameter sweep is a single-file diff.
```python
@dataclass(frozen=True)
class DetectorParams:
    motion_threshold: int = 18
    open_kernel_px: int = 3
    close_kernel_px: int = 5

    hue_min: int = 25            # OpenCV H in [0, 179]
    hue_max: int = 45
    sat_min: int = 60
    val_min: int = 120
    achromatic_val_min: int = 200
    achromatic_sat_max: int = 60

    # Tier A -- round
    round_area_min: float = 12.0
    round_area_max: float = 1200.0
    round_circularity_min: float = 0.65
    round_aspect_max: float = 1.6
    round_solidity_min: float = 0.85
    round_fill_min: float = 0.55

    # Tier B -- motion-blur streak
    streak_area_min: float = 12.0
    streak_area_max: float = 1500.0
    streak_circularity_min: float = 0.20
    streak_aspect_min: float = 1.6
    streak_aspect_max: float = 12.0
    streak_minor_axis_min: float = 3.0
    streak_minor_axis_max: float = 20.0
    streak_solidity_min: float = 0.80
    streak_fill_min: float = 0.50
    streak_orientation_tolerance_deg: float = 25.0

    player_box_dilation: float = 0.15
    frame_edge_margin_px: int = 12

@dataclass(frozen=True)
class TrackParams:
    max_ball_speed_mps: float = 71.5        # 257 km/h, above any recorded serve
    seed_max_dist_tu: float = 0.8
    direction_tolerance_deg: float = 35.0
    min_step_px: float = 2.0
    max_consecutive_misses: int = 2
    predicted_gate_fraction: float = 0.35
    predicted_gate_floor_px: float = 8.0

@dataclass(frozen=True)
class SpeedParams:
    window_post_s: float = 0.25
    skip_first_step: bool = True
    min_detections: int = 3
    conf_medium_min: int = 5
    conf_high_min: int = 9
    drift_soft_lo: float = 0.75
    drift_soft_hi: float = 1.33
    drift_hard_lo: float = 0.60
    drift_hard_hi: float = 1.67
    min_segment_px_for_medium: float = 150.0
    min_segment_px: float = 60.0
    min_median_step_px: float = 4.0
    px_per_m_min: float = 8.0
    px_per_m_max: float = 300.0
    min_mph: int = 15
    max_mph: int = 160

DETECTOR_V1: DetectorParams
TRACK_V1: TrackParams
SPEED_V1: SpeedParams
DETECTOR_VERSION: str = "ball_cv_v1"
```

#### `app/ball/frames.py` — **IMPURE — PLANNED, DOES NOT EXIST**
Owns, once built: the second decode pass. The only file in `app/ball/` that will import `av` or touch the filesystem. Until it lands, `detect_ball_track()` receives an already-decoded `(T, H, W, 3)` CAL_SPACE stack from its caller and the temporal `BallTrack` fields of §3.2 stay unpopulated.
```python
@dataclass(frozen=True)
class NativeFrameWindow:
    background_frames: list[np.ndarray]        # grayscale, CAL_SPACE, len == ball_background_frames
    window_frames: list[tuple[float, np.ndarray]]  # (absolute_pts_s, BGR CAL_SPACE)
    native_fps: float
    frames_seeked_and_discarded: int

def seek_to(container: "av.container.InputContainer",
            stream: "av.video.stream.VideoStream", t_s: float) -> None: ...

def iter_native_frames(path: Path, start_s: float, end_s: float,
                       long_edge_px: int, rotation_deg: int,
                       ) -> Iterator[tuple[float, np.ndarray]]: ...

def collect_window(path: Path, contact_abs_s: float, meta: VideoMeta,
                   settings: Settings) -> NativeFrameWindow: ...
```
`collect_window` is the only entry point the orchestrator calls. It streams — `window_frames` holds at most the measurement window (~16 frames at 60 fps, ~5.5 MB in `CAL_SPACE`), and `background_frames` are grayscale and released immediately after the median is computed.

#### `app/ball/geometry.py` — **PURE — PLANNED, DOES NOT EXIST** (numpy only; no cv2, no av)
Owns, once built: everything coordinate-space and calibration. Separated from `detector.py` precisely so the highest-risk arithmetic in the feature has no cv2 dependency and is trivially testable. Note that one function from this planned list has already shipped **in `detector.py` instead**: the torso+leg exclusion box, as `torso_leg_exclusion_box()` (see below). It takes a point array rather than a `NormalizedSequence`, which keeps it free of any dependency on pose types; it should move here unchanged when this module is created.
```python
def preview_to_cal_space(point: NormalizedPoint, capture_w_px: int, capture_h_px: int,
                         capture_rotation_deg: int, meta: VideoMeta) -> tuple[float, float]: ...

def map_calibration_points(cal: BallSpeedCalibration, meta: VideoMeta
                           ) -> tuple[tuple[float, float], tuple[float, float]]: ...

def frame_shape_check(cal: BallSpeedCalibration, meta: VideoMeta,
                      tolerance: float = 0.02) -> bool: ...

def scale_from_calibration(cal: BallSpeedCalibration, meta: VideoMeta,
                           params: SpeedParams) -> CalibrationScale | None: ...

def pose_px_to_cal_px(xy_pose_px: np.ndarray, meta: VideoMeta) -> np.ndarray: ...

def player_exclusion_boxes(seq: NormalizedSequence, meta: VideoMeta,
                           dilation: float = 0.15) -> np.ndarray: ...
    # -> (T, 4) float32, [x0, y0, x1, y1] in CAL_SPACE px.
    # Torso + legs ONLY (shoulders, hips, knees, ankles). Arms, hand and head are
    # deliberately NOT excluded: the ball is adjacent to the racket hand at contact
    # and excluding that region would delete the seed detection.

def exclusion_box_at(boxes: np.ndarray, pose_timestamps_s: np.ndarray,
                     t_s: float) -> np.ndarray: ...
    # Nearest-neighbour lookup from a native-fps time into the 30 fps pose stream.
```

#### `app/ball/detector.py` — **PURE-CV — SHIPPED** (numpy + cv2 + `app.ball.track` + `app.models.enums` only)
Owns: background subtraction, HSV thresholding, contour features, the two-tier filter, and the `detect_ball_track()` entry point. No I/O, no clock, no `av`. It does **not** own the shared value objects: `ContourFeatures`, `BallCandidate`, `BallDetectionResult` and the tier constants are defined in `app/ball/track.py` and imported from here, per invariant `BALL_PURITY_EDGE_ONE_WAY` (§3.1). Constants are inline `Final` values (there is no `params.py`), so every function takes bare keyword scalars.

```python
from app.ball.track import (          # §3.1 — PURE-CV may depend on PURE, never the reverse
    CAL_SPACE_PX_PER_M, DEFAULT_FPS,
    TIER_REJECTED, TIER_ROUND, TIER_STREAK,
    BallCandidate, BallDetectionResult, ContourFeatures,
    associate_candidates,
)
from app.models.enums import BallSpeedUnavailableReason

def to_grayscale(frames: npt.NDArray[np.uint8]) -> npt.NDArray[np.uint8]: ...
    # (T, H, W, 3) BGR stack -> (T, H, W) grayscale stack.

def sample_background_frames(
    frames: npt.NDArray[np.uint8], count: int = BACKGROUND_SAMPLE_FRAMES
) -> npt.NDArray[np.uint8]: ...
    # Evenly spaced sample; returns `frames` unchanged when T <= count.

def build_background(frames: npt.NDArray[np.uint8]) -> npt.NDArray[np.uint8]: ...
    # Takes a (T, H, W, 3) BGR stack, NOT grayscale: it samples then greyscales
    # internally. Returns (H, W) uint8 per-pixel median. Raises ValueError on a
    # non-4D or empty stack.

def motion_mask(
    frame_bgr: npt.NDArray[np.uint8], background_gray: npt.NDArray[np.uint8]
) -> npt.NDArray[np.uint8]: ...
    # Takes BGR, not grayscale, and greyscales internally. absdiff > 18, then
    # MORPH_OPEN 3x3 then MORPH_CLOSE 5x5 — the morphology is folded in here, not
    # a separate `morphology()` function. Order is load-bearing: closing first
    # fuses speckle into fake blobs.

def colour_mask(frame_bgr: npt.NDArray[np.uint8]) -> npt.NDArray[np.uint8]: ...
    # Yellow branch OR achromatic-bright branch. No params argument.

def candidate_mask(
    frame_bgr: npt.NDArray[np.uint8], background_gray: npt.NDArray[np.uint8]
) -> npt.NDArray[np.uint8]: ...
    # colour_mask AND motion_mask. Motion is applied first and is not optional:
    # the achromatic branch is only safe inside it.

def contour_features(contour: npt.NDArray[np.int32]) -> ContourFeatures | None: ...
    # One contour in, features out. Does NOT take a frame or an HSV image: no
    # per-contour colour is sampled, because the colour gate already ran as a
    # mask before contour extraction. Returns None for a degenerate contour
    # (zero area, zero perimeter, zero-width min-area rect, zero hull area).

def classify_contour(features: ContourFeatures) -> int: ...
    # Returns TIER_ROUND, TIER_STREAK or TIER_REJECTED. Replaces the v1
    # passes_round_tier / passes_streak_tier pair. The tier-B orientation-
    # agreement criterion is deliberately NOT evaluated here: it needs the track's
    # last step direction and is applied in track.associate_candidates() once the
    # track has >= 1 step.

def torso_leg_exclusion_box(
    points_xy: npt.NDArray[np.floating], dilation: float = PLAYER_BOX_DILATION
) -> tuple[float, float, float, float]: ...
    # (K, 2) shoulder/hip/knee/ankle points already in CAL_SPACE -> a dilated
    # (x_min, y_min, x_max, y_max) tuple. Arms, racket hand and head must not be
    # passed in by the caller: the ball sits next to the racket hand at contact.
    # Raises ValueError on a non-(K, 2) or empty array. Belongs in geometry.py
    # once that module exists.

def find_candidates(
    frame_bgr: npt.NDArray[np.uint8],
    background_gray: npt.NDArray[np.uint8],
    frame_index: int,
    exclusion_box: tuple[float, float, float, float] | None = None,
) -> list[BallCandidate]: ...
    # Replaces the v1 detect_candidates(). Every contour in one frame that passes
    # tier A or tier B, with the exclusion box applied to the centroid.

def detect_ball_track(
    frames: npt.NDArray[np.uint8],
    *,
    background_frames: npt.NDArray[np.uint8] | None = None,
    exclusion_boxes: Sequence[tuple[float, float, float, float] | None] | None = None,
    fps: float = DEFAULT_FPS,
    px_per_m: float = CAL_SPACE_PX_PER_M,
    seed_xy: tuple[float, float] | None = None,
    seed_gate_px: float | None = None,
) -> BallDetectionResult: ...
    # The module's single entry point. Validates the stack (ValueError on a
    # non-(T, H, W, 3) shape, non-uint8 dtype, or an exclusion_boxes length
    # mismatch), builds the background, runs find_candidates per frame, and
    # delegates to track.associate_candidates(). Returns
    # BallDetectionResult(None, NO_TRACK_SEEDED) for an empty stack. When
    # background_frames is omitted the median is taken over `frames` themselves.
```

Private helpers, not part of the public surface: `_normalize_axis_deg` (folds an axis angle into `[0, 180)`) and `_is_inside_box`. `__all__` is the authoritative export list and names none of them.

**Dropped from the v1 design and deliberately absent:** `morphology()` (folded into `motion_mask`), `candidate_contours()` (inlined into `find_candidates`), `passes_round_tier()` / `passes_streak_tier()` (replaced by `classify_contour`), `in_exclusion_box()` (private `_is_inside_box`), `detect_candidates()` (renamed `find_candidates`). No shipped function takes an `expected_direction_deg` argument — that criterion moved wholesale to the association layer.

#### `app/ball/track.py` — **PURE — SHIPPED** (numpy + stdlib `math` + `app.models.enums`; no cv2)
Owns the value objects and tier constants shared across `app/ball/`, so that `detector.py` can import them without this module ever importing `cv2` (§3.1, `BALL_PURITY_EDGE_ONE_WAY`). `BallTrack` and `BallDetectionResult` are constructed here; their field-by-field specification is given once in §3.2 and is not restated.

Tiers are module-level `Final[int]` constants, **not** an enum — there is no `BallDetectionTier` type anywhere in the codebase, and `BallCandidate.tier` is a plain `int`:

```python
TIER_ROUND: Final[int] = 1
TIER_STREAK: Final[int] = 2
TIER_REJECTED: Final[int] = 0
```

`BallTrack.tier` is a `(N,) uint8` array carrying those same values. `TIER_REJECTED` never appears in a `BallCandidate` — `find_candidates()` drops rejected contours before constructing one.

```python
@dataclass(frozen=True)
class ContourFeatures:
    """Geometry of one candidate contour, in CAL_SPACE px (PIPELINE.md 10.4)."""

    x: float                     # centroid x; a scalar, not a centroid_px tuple
    y: float                     # centroid y
    area: float
    perimeter: float
    circularity: float           # 4*pi*A / P^2
    major_axis_px: float
    minor_axis_px: float
    aspect: float                # major / minor
    solidity: float              # area / convex_hull_area
    fill: float                  # area / (major * minor)
    #: Major-axis orientation folded to [0, 180); undirected, y DOWN.
    orientation_deg: float

@dataclass(frozen=True)
class BallCandidate:
    frame_index: int             # index within the frame stack
    features: ContourFeatures
    tier: int                    # TIER_ROUND or TIER_STREAK

    @property
    def x(self) -> float: ...    # -> features.x
    @property
    def y(self) -> float: ...    # -> features.y

def direction_difference_deg(first_deg: float, second_deg: float) -> float: ...
    # Absolute difference between two DIRECTED angles, folded to [0, 180].

def axis_difference_deg(axis_deg: float, direction_deg: float) -> float: ...
    # Difference between an UNDIRECTED axis and a directed angle, in [0, 90].
    # This is what enforces the tier-B orientation-agreement criterion, which the
    # contour layer deliberately does not evaluate.

def associate_candidates(
    candidates_by_frame: Sequence[Sequence[BallCandidate]],
    frame_shape: tuple[int, int],
    *,
    fps: float = DEFAULT_FPS,
    px_per_m: float = CAL_SPACE_PX_PER_M,
    seed_xy: tuple[float, float] | None = None,
    seed_gate_px: float | None = None,
) -> BallDetectionResult: ...
    # Greedy nearest-neighbour with the kinematic and direction gates of 10.5.
    # frame_shape is (height, width), needed only for the exit-frame trim.
    # Takes no timestamps and no CalibrationScale: the gate radius is derived
    # from bare `fps` and `px_per_m` floats. Raises ValueError if either is
    # non-positive. Returns BallDetectionResult(None, NO_TRACK_SEEDED) when no
    # seed is found, and (None, TOO_FEW_DETECTIONS) below MIN_ACCEPTED_DETECTIONS.
```

Private helpers, not part of the public surface: `_direction_deg` (directed step angle in `(-180, 180]`), `_is_near_frame_edge` (exit-frame trim), `_select_seed` (nearest to `seed_xy`, else largest-area candidate), `_build_track` (assembles the frozen seam object). Note `_normalize_axis_deg` lives in `detector.py`, not here. `__all__` is the authoritative export list and names none of them.

**Dropped from the v1 design and deliberately absent:** `gate_radius_px()` (inlined as `V_MAX_M_PER_S * (1.0 / fps) * px_per_m`), `seed_index()` (private `_select_seed`, returning a candidate rather than an index), `step_direction_deg()` (private `_direction_deg`, taking two floats not two arrays), `direction_delta_deg()` (renamed `direction_difference_deg`), `associate()` (renamed `associate_candidates`, returning a `BallDetectionResult` rather than a bare `BallTrack`).

#### `app/ball/sequence.py` — **PURE — PLANNED, DOES NOT EXIST**
Owns, once built: validating the `BallTrack` seam object's invariants (§3.2) and summarizing a detection run. The exact analogue of `pose/sequence.py`. Assembly has *already* shipped elsewhere — the frozen `BallTrack` is constructed by the private `_build_track()` in `track.py`, called from `associate_candidates()`. `TrackStep` and `BallDetectionSummary` do not exist yet, so `build_ball_track()` and `summarize_detection()` below are speculative; only `validate_track_invariants()` has a concrete, mostly-assertable spec today.
```python
def build_ball_track(steps: Sequence[TrackStep], contact_abs_s: float,
                     window_start_s: float, window_end_s: float,
                     terminated_by: str, coasted_frames: int) -> BallTrack: ...
def validate_track_invariants(track: BallTrack) -> None: ...
def summarize_detection(track: BallTrack | None, native_fps: float,
                        frames_scanned: int, raw_candidates: int,
                        elapsed_ms: int) -> BallDetectionSummary: ...
```

#### `app/analysis/speed.py` — **PURE — PLANNED, DOES NOT EXIST** (numpy only; no cv2, no av)
The ball-speed estimator. `BallTrack` + `CalibrationScale` in, `BallSpeedResult` out. `app/analysis/` is not yet a package on disk, and every signature below takes a `SpeedParams` that `params.py` would define — so the whole block is design intent. Note also that `step_intervals_s()` cannot be written until `BallTrack.timestamps_s` exists (§3.2, future requirement): the shipped track carries `frame_offsets` only.
```python
def step_displacements_px(track: BallTrack, skip_first: bool = True) -> np.ndarray: ...
def step_intervals_s(track: BallTrack, skip_first: bool = True) -> np.ndarray: ...
def median_step_speed_px_s(track: BallTrack, params: SpeedParams) -> float | None: ...
def blob_size_drift(track: BallTrack) -> float | None: ...
def px_s_to_mph(v_px_s: float, px_per_m: float) -> float: ...
def confidence_from_detections(n: int, params: SpeedParams) -> BallSpeedConfidence: ...
def apply_confidence_caps(base: BallSpeedConfidence, drift: float | None,
                          scale: CalibrationScale, view: CameraView,
                          params: SpeedParams) -> tuple[BallSpeedConfidence, list[str]]: ...
def estimate_ball_speed(track: BallTrack | None, scale: CalibrationScale | None,
                        view: CameraView, contact_confidence: float,
                        detection: BallDetectionSummary | None,
                        params: SpeedParams) -> BallSpeedResult: ...
```
`estimate_ball_speed` is the single place where a float becomes an int. Nothing else in the codebase calls `round()` on a speed.

#### `app/analysis/pipeline.py` — **PURE (changed)**
```python
def analyze_kinematics(seq: PoseSequence,
                       handedness_hint: Handedness | None = None) -> KinematicCore: ...
def analyze_swing(kin: KinematicCore, ball: BallTrack | None = None,
                  scale: CalibrationScale | None = None) -> AnalysisCore: ...
```
Neither function imports from `services`, `feedback`, `pose.extractor`, `ball.frames`, `httpx`, `mediapipe`, `av`, or `cv2` — enforced by a test (§4.3).

#### `app/services/orchestrator.py` — **IMPURE (changed)**
```python
class AnalysisOrchestrator:
    def __init__(self, storage: StorageService, repo: AnalysisRepository,
                 feedback: FeedbackService, settings: Settings) -> None: ...
    def run(self, analysis_id: UUID, user_id: UUID, request: CreateAnalysisRequest) -> None: ...
    def _collect_pose(self, video_path: Path) -> tuple[PoseSequence, VideoMeta, dict[str, int]]: ...
    def _collect_ball(self, video_path: Path, kin: KinematicCore, meta: VideoMeta,
                      cal: BallSpeedCalibration | None
                      ) -> tuple[BallTrack | None, CalibrationScale | None,
                                 BallDetectionSummary | None]: ...
```
`_collect_ball` returns `(None, None, None)` and never raises for any ball-side failure; it owns the 6 s deadline and the try/except that converts every exception into a `BallSpeedUnavailableReason` carried on the summary. `_collect_pose` must have fully exited its `PoseExtractor` context before `_collect_ball` is called (§1.20 memory ordering).

#### `app/feedback/prompt.py` — **PURE (changed)**
```python
SYSTEM_INSTRUCTION: str
BANNED_UNIT_SUBSTRINGS: frozenset[str]        # km/h, m/s, kph, rpm, rally, spin rate, ...
CONDITIONAL_MPH_SUBSTRING: str = "mph"        # banned iff ball_speed_mph is None

def render_user_prompt(payload: FeedbackPayload) -> str: ...
def response_schema() -> dict[str, Any]: ...
def ball_speed_block(result: BallSpeedResult) -> dict[str, Any]: ...
def hedge_for(confidence: BallSpeedConfidence) -> str: ...
```

#### `app/feedback/guard.py` — **PURE (changed)**
```python
NUMERIC_TOKEN_RE: re.Pattern[str]
MPH_TOKEN_RE: re.Pattern[str]                 # (?i)\bmph\b
BOUND_MPH_RE_TEMPLATE: str                    # r"(?i)\b{value}\s?mph\b"

def extract_numeric_tokens(text: str) -> list[str]: ...
def find_banned_units(text: str) -> list[str]: ...
def check_mph_binding(text: str, ball_speed_mph: int | None,
                      max_mentions: int = 2) -> list[str]: ...
    # Returns violations. When ball_speed_mph is None, ANY 'mph' is a violation.
    # When it is V, count(mph) must equal count(matches of "V mph") and be <= 2.
def validate_text_field(text: str, allowlist: frozenset[str],
                        ball_speed_mph: int | None) -> list[str]: ...
def enforce(draft: GeminiFeedbackDraft, allowlist: frozenset[str],
            ball_speed_mph: int | None) -> tuple[GeminiFeedbackDraft | None, NumericGuardReport]: ...
```

#### `app/feedback/payload.py` / `template.py` — **PURE (changed)**
```python
# payload.py
def build_feedback_payload(core: AnalysisCore) -> FeedbackPayload: ...
def numeric_allowlist(payload: FeedbackPayload) -> frozenset[str]: ...
    # ball_speed.mph and detections_used are added as INTEGER renderings ONLY --
    # never with a ".0" form, so "68.0 mph" is a violation by construction.

# template.py
def render_template_feedback(core: AnalysisCore) -> CoachingFeedback: ...
def describe_metric(score: MetricScore) -> str: ...
def describe_ball_speed(result: BallSpeedResult) -> str | None: ...
    # Returns None when ball_speed_mph is None, so the template silently omits the
    # sentence rather than writing "speed unavailable" on the majority of clips.
```

---

### 3.5 `backend/tests/` — mirrored layout

```
backend/tests/
  conftest.py                     # synthetic-keypoint fixtures, fake clients
  fixtures/
    synthetic.py                  # PROGRAMMATIC swing generators (no video files)
    ball_synthetic.py             # NEW -- programmatic frame + track generators
    golden/                       # frozen PoseSequence .npz from 15 real clips
    golden_ball/                  # NEW -- frozen BallTrack .npz from the same clips
  unit/
    pose/
      test_landmarks.py
      test_sequence.py
      test_video_io_rotation.py
      test_video_io_motion_scan.py    # NEW -- keyframe window location
    ball/                             # NEW
      test_params.py
      test_geometry_mapping.py
      test_geometry_calibration.py
      test_geometry_exclusion.py
      test_detector_background.py
      test_detector_masks.py
      test_detector_contour_features.py
      test_detector_tiers.py
      test_detector_false_positives.py
      test_track_association.py
      test_sequence_invariants.py
    analysis/
      test_smoothing.py
      test_normalize.py
      test_kinematics.py
      test_handedness.py
      test_contact.py
      test_phases.py
      test_metrics_preparation.py
      test_metrics_contact.py
      test_metrics_swing_path.py
      test_metrics_balance.py
      test_metrics_follow_through.py
      test_speed.py                   # NEW
      test_shot_type.py
      test_rubric.py
      test_scoring.py
      test_pipeline.py
    feedback/
      test_payload.py
      test_prompt.py
      test_guard.py
      test_guard_mph.py               # NEW -- the conditional mph rule, in isolation
      test_template.py
    models/
      test_schema_contract.py         # example JSON round-trips through AnalysisResponse
      test_ball_speed_types.py        # NEW -- StrictInt, bounds, MetricUnit has no mph
  integration/
    test_auth_jwt.py
    test_routes_uploads.py
    test_routes_analyses.py
    test_routes_calibration_validation.py  # NEW -- 400 CALIBRATION_INVALID paths
    test_orchestrator.py                   # fake storage + fake extractor + fake Gemini
    test_orchestrator_ball.py              # NEW -- fake frame source, ordering, deadline
  architecture/
    test_purity_boundary.py         # AST import scan, extended for app/ball/
```

---

## 4. Purity Table

### 4.1 The first seam — `PoseSequence`

**Impure data collection ends and pure computation begins at `PoseSequence`.**

```python
@dataclass(frozen=True)
class PoseSequence:
    landmarks: np.ndarray      # (T, 33, 4) float32
    world: np.ndarray          # (T, 33, 3) float32
    timestamps_s: np.ndarray   # (T,) float64
    detected: np.ndarray       # (T,) bool
    width_px: int
    height_px: int
```

Produced by `app/pose/sequence.build_pose_sequence()`, consumed by `app/analysis/pipeline.analyze_kinematics()`. It is plain numpy plus two ints — no file handles, no clients, no MediaPipe objects, no lazy iterators. It serializes to `.npz` in one line, which is what makes the golden-fixture strategy possible: capture 15 real clips once, freeze their `PoseSequence` to disk, and every subsequent regression test of the entire analysis pipeline runs in CI in milliseconds with no video, no model, and no network.

### 4.2 The second seam — `BallTrack` (new in v2)

**Impure/PURE-CV ball detection ends and pure computation begins at `BallTrack`**, defined in §3.2 with the same rigor as `PoseSequence`: plain numpy arrays plus scalars, frozen, `.npz`-serializable, no handles, no cv2 objects, no iterators.

Produced by `app/ball/sequence.build_ball_track()`, consumed by `app/analysis/speed.estimate_ball_speed()`. The same golden-fixture strategy applies: freeze a `BallTrack` from a real clip once, and every ball-speed regression test runs with no video, no OpenCV, and no network.

The symmetry is deliberate. There is now one seam per sensor — pose in, ball in — and the pure layer downstream of both is the only place a reported number is ever computed.

### 4.3 The third seam — `FeedbackPayload` / `GeminiFeedbackDraft`

Unchanged from v1: `FeedbackPayload` (pure dict, built from `AnalysisCore`) goes out to Gemini (impure), and `GeminiFeedbackDraft` comes back into `guard.enforce()` (pure). The untrusted LLM output re-enters pure, deterministic, testable code before it can reach the user. In v2 the guard also carries `ball_speed_mph` so the conditional `mph` rule is evaluated in the same pure function.

### 4.4 Classification

| # | Stage | Module | Class | Reason |
|---|---|---|---|---|
| 1 | Upload ticket | `api/routes_uploads.py` | IMPURE | Supabase Storage API |
| 2 | JWT verification | `api/auth.py` | IMPURE | JWKS HTTP fetch (cached) |
| 3 | Request intake | `api/routes_analyses.py` | IMPURE | DB write, executor submit |
| 3b | Calibration request validation | `models/requests.BallSpeedCalibration` | PURE | Field + model validators only |
| 4 | Video download | `services/storage.py` | IMPURE | Network + filesystem |
| 5a | Keyframe motion scan | `pose/video_io.py` | IMPURE | Filesystem, libav |
| 5b | Decode + rotate + sample | `pose/video_io.py` | IMPURE | Filesystem, libav |
| 5c | Rotation matrix math | `pose/video_io.read_rotation_degrees` | PURE | Pure parse of side data |
| 6 | MediaPipe inference | `pose/extractor.py` | IMPURE | **ML model inference** |
| — | **`build_pose_sequence` — SEAM 1** | `pose/sequence.py` | PURE | Array assembly + invariants |
| 7a | Gap detection + interpolation | `analysis/smoothing.py` | PURE | |
| 7b | Savitzky-Golay smoothing | `analysis/smoothing.py` | PURE | |
| 7c | Aspect / flip / origin / scale | `analysis/normalize.py` | PURE | |
| 7d | Camera view estimation | `analysis/normalize.py` | PURE | Ratio of body segments |
| 7e | Velocity / acceleration | `analysis/kinematics.py` | PURE | |
| 8 | Handedness detection | `analysis/handedness.py` | PURE | |
| 9 | Contact detection | `analysis/contact.py` | PURE | |
| **10a** | **Ball frame acquisition** | `ball/frames.py` | **IMPURE** | **Second decode pass, filesystem, libav** |
| **10b** | **Calibration mapping + scale** | `ball/geometry.py` | **PURE** | **numpy arithmetic on ints and floats** |
| **10c** | **Player exclusion boxes** | `ball/geometry.py` | **PURE** | **numpy over landmark arrays** |
| **10d** | **Background median** | `ball/detector.build_background` | **PURE-CV** | **Deterministic reduce over a stacked array** |
| **10e** | **Motion + colour masking** | `ball/detector.py` | **PURE-CV** | **Deterministic; cv2 as an array library** |
| **10f** | **Contour features + tier filter** | `ball/detector.py` | **PURE-CV** | **Deterministic geometry on contours** |
| **10g** | **Cross-frame association** | `ball/track.py` | **PURE** | **numpy only; no cv2** |
| — | **`build_ball_track` — SEAM 2** | `ball/sequence.py` | **PURE** | **Array assembly + invariants** |
| **11** | **Speed calculation** | `analysis/speed.py` | **PURE** | **Median, division, one `round()`. numpy only.** |
| 12 | Phase segmentation | `analysis/phases.py` | PURE | |
| 13 | Swing metrics (17 fns) | `analysis/metrics.py` | PURE | |
| 14 | Shot-type inference | `analysis/shot_type.py` | PURE | **Rules, not a model. Does not take a BallTrack.** |
| 15a | Rubric constants | `analysis/rubric.py` | PURE | |
| 15b | Scoring | `analysis/scoring.py` | PURE | |
| 15c | Pure orchestrators | `analysis/pipeline.py` | PURE | `analyze_kinematics`, `analyze_swing` |
| 16a | Gemini payload build | `feedback/payload.py` | PURE | |
| 16b | Prompt rendering | `feedback/prompt.py` | PURE | String templating only |
| 17a | Gemini API call | `feedback/gemini_client.py` | IMPURE | **Network + LLM** |
| 17b | Numeric guard (incl. mph rule) | `feedback/guard.py` | PURE | Regex + set membership |
| 17c | Template fallback | `feedback/template.py` | PURE | |
| 17d | Feedback orchestration | `feedback/service.py` | IMPURE | Calls the client |
| 18a | Response assembly | `services/orchestrator.py` | IMPURE | |
| 18b | Persistence | `services/repository.py` | IMPURE | DB |
| 19 | Poll / fetch | `api/routes_analyses.py` | IMPURE | DB read |

**Impure surface is 12 stages, confined to 9 files:** `api/auth.py`, `api/routes_*.py`, `pose/video_io.py`, `pose/extractor.py`, **`ball/frames.py`**, `feedback/gemini_client.py`, `feedback/service.py`, `services/storage.py`, `services/repository.py`, `services/orchestrator.py`. The ball feature added exactly **one** new impure file.

**PURE-CV surface is exactly one file:** `ball/detector.py`.

**`app/analysis/` still contains zero impure code and imports zero impure modules — and now also imports zero cv2.** `tests/architecture/test_purity_boundary.py` AST-parses every module and enforces, per package:

| Package | Banned imports |
|---|---|
| `app/analysis/**` | `mediapipe`, `av`, **`cv2`**, `httpx`, `requests`, `supabase`, `google.generativeai`, `os` (beyond `os.PathLike` typing), `pathlib`, `open`, `socket`, `time`, `app.services`, `app.pose.extractor`, `app.pose.video_io`, `app.ball.frames`, `app.ball.detector` |
| `app/ball/geometry.py`, `track.py`, `sequence.py`, `params.py` | same list as `app/analysis/**` |
| `app/ball/detector.py` | everything except `numpy`, `cv2`, `dataclasses`, `math`, `app.models`, `app.ball.params` — explicitly banned: `av`, `pathlib`, `open`, `socket`, `httpx`, `supabase`, `mediapipe`, `time`, `app.services` |

Documentation about purity decays; a failing test does not. The `cv2` ban on `app/analysis/**` is the specific thing that stops this feature from eroding the boundary over time — without it, "ball detection needs OpenCV" would eventually become "the metrics module imports cv2 for one convenience function."

### 4.5 Unit tests required with synthetic data

CLAUDE.md: *"Every math function in the analysis pipeline gets a unit test with synthetic keypoint data. No exceptions."* Every function below is covered by programmatic fixtures — no video files, no model, no network. The v1 table is unchanged and carried forward; the v2 additions follow it.

#### 4.5.1 Carried forward from v1 (unchanged)

| Function | Synthetic test approach |
|---|---|
| `interpolate_gaps` | Known linear ramp with punched holes; assert exact recovery; assert gap > 3 rejected |
| `savgol_smooth` | Sine + Gaussian noise; assert amplitude and **peak index** preserved within 1 frame; assert a moving average fails the same test (guards the design choice) |
| `apply_aspect_correction` | 9:16 frame with a known 45° segment; assert corrected angle is 45°, uncorrected is not |
| `torso_scale` | Fixed-length synthetic torso; assert exact; assert per-frame foreshortening does not change the median |
| `to_body_frame` | Translate the whole skeleton by an arbitrary offset; assert output is bit-identical |
| `estimate_camera_view` | Skeleton projected at yaw 0/30/60/90; assert bucket boundaries |
| `central_difference` | Constant-velocity ramp with **non-uniform** timestamps; assert exact velocity (catches assumed-dt bugs) |
| `joint_angle` | Constructed 90°, 180°, 30° arms; assert exact; degenerate zero-length segment returns NaN not a crash |
| `segment_angle_deg`, `signed_angle_between` | Unit vectors at known angles; wraparound at ±180° |
| `path_length` | Unit square traversal → 4.0 |
| `fit_line_angle_deg`, `rms_residual_from_line` | Points on an exact line → residual 0.0, angle exact; add known scatter → known RMS |
| `peak_prominence_ratio` | Two-peak signal with controlled ratio |
| `detect_handedness` | `synthetic_swing(handedness=RIGHT)` → RIGHT with confidence > 0.8; mirrored → LEFT; two-handed → low confidence + hint honored |
| `racket_hand_speed`, `find_deceleration_onset` | Triangular speed profile with a known apex; assert onset lands at the 5 % drop frame |
| `detect_contact_frame` | `contact_at_s=2.5` → frame 75 ± 1 at 30 fps; noise sweep; double-peak clip → prominence drops and confidence falls |
| `segment_phases` | Assert contiguity, ordering, no overlap, coverage == T; degenerate clip → zero-duration phase, no exception |
| `compute_tempo_ratio` | Known phase durations |
| All 17 metric functions | Each gets a purpose-built skeleton with the answer known analytically, plus a `None`-return test with visibility forced below threshold |
| `score_serve`/`score_two_handed`/`score_volley`/`is_dominant_side_contact` | Per-shot-type synthetic swings |
| `infer_shot_type` | Full matrix: all 6 shot types × both handednesses; ambiguous input → `unknown`; assert `class_scores` sums to 1.0 |
| `ramp_score` | Band interior → 100; exactly at `ideal_min` → 100; exactly at `hard_min` → 0; midpoint → 50; beyond hard bound → clamped 0 |
| `verdict_for`, `score_metric` | Boundary values on each side |
| `renormalize_weights` | One metric `None` → remaining weights sum to 1.0 |
| `score_category`, `build_scorecard` | Hand-computed expected overall from a fixed `SwingMetrics` |
| `priority_metric_names` | Ties broken deterministically |
| `analyze_kinematics` / `analyze_swing` | Golden-fixture regression: frozen `PoseSequence` → snapshot `AnalysisCore`; determinism test asserts two runs are bit-identical |
| `build_feedback_payload` | Assert no landmark array, no user id, no email, **no calibration tap coordinates** present anywhere in the dict |
| `numeric_allowlist` | Assert 0- and 1-decimal renderings of every payload float present |
| `extract_numeric_tokens` | Decimals, negatives, percentages, numbers adjacent to punctuation |
| `find_banned_units` | Each banned substring, case-insensitive, inside words |
| `enforce` | Clean draft passes untouched; draft with a number not in payload → discarded; summary violation → full template fallback |
| `render_template_feedback` | Every `ShotType` × every `MetricVerdict` produces non-empty prose with only payload numbers (fed back through `enforce` as a self-check) |

#### 4.5.2 What the synthetic ball fixtures look like

`tests/fixtures/ball_synthetic.py` is the ball-side analogue of `synthetic.py`. It generates **numpy image arrays and `BallTrack` objects programmatically** — no video files, no real footage, nothing checked into the repo but code.

```python
def draw_court_background(size_px: tuple[int, int], surface: str = "hard",
                          line_positions: Sequence[tuple[int, int, int, int]] = (),
                          noise_std: float = 2.0, seed: int = 0) -> np.ndarray: ...
    # A flat surface-coloured field (hard = blue-grey, clay = terracotta, grass = green)
    # with white lines drawn at exact pixel coordinates. Deterministic given `seed`.

def draw_ball(frame_bgr: np.ndarray, centre_xy: tuple[float, float],
              radius_px: float = 3.0, streak_vector_px: tuple[float, float] = (0.0, 0.0),
              hue: int = 33, value: int = 200, blur_sigma: float = 0.8) -> np.ndarray: ...
    # A ROUND ball when streak_vector is (0,0); a CAPSULE (thick line between
    # centre - v/2 and centre + v/2, cap-rounded, then Gaussian-blurred) otherwise.
    # The capsule is the motion-blur model that exercises tier B: a 3 px radius with
    # a 44 px streak vector produces aspect ~8:1 and circularity ~0.24, matching the
    # numbers in Stage 10.2.

def draw_distractor(frame_bgr: np.ndarray, kind: str, centre_xy, size_px) -> np.ndarray: ...
    # kind in {"shoe", "player_torso", "bright_shirt", "line_fragment", "shadow", "glare"}.
    # Each is drawn with the shape/hue/solidity profile that the corresponding row of
    # the Stage 10.4 false-positive table claims it has, so the test asserts the CLAIM,
    # not just the code.

def synthetic_ball_clip(n_frames: int = 16, fps: float = 60.0,
                        size_px: tuple[int, int] = (720, 1280),
                        start_xy: tuple[float, float] = (200.0, 700.0),
                        velocity_px_per_frame: tuple[float, float] = (44.0, -6.0),
                        radius_px: float = 3.0, motion_blur: bool = True,
                        distractors: Sequence[str] = (),
                        drop_frames: Sequence[int] = (),
                        depth_scale_per_frame: float = 1.0,
                        jitter_px: float = 0.0, seed: int = 0,
                        ) -> tuple[list[np.ndarray], np.ndarray, np.ndarray]: ...
    # Returns (frames_bgr, true_centres_xy (n,2), timestamps_s (n,)).
    # GROUND TRUTH IS EXACT BY CONSTRUCTION -- the generator knows the true
    # px-per-frame velocity, so an end-to-end assertion against an exact integer mph
    # is possible with no tolerance fudging.
    # depth_scale_per_frame < 1.0 shrinks the ball each frame, synthesizing the
    # receding-ball case that the blob-drift gate must catch.
    # drop_frames omits the ball entirely, exercising coasting and miss-limit.

def synthetic_track(n: int = 6, step_px: float = 26.0, dt_s: float = 1/60,
                    direction_deg: float = -8.0, minor_axis_px: float = 5.0,
                    minor_axis_drift: float = 1.0,
                    outlier_steps: Mapping[int, float] = (),
                    ) -> BallTrack: ...
    # A BallTrack built directly, bypassing detection entirely. This is what
    # analysis/speed.py is tested against -- it never needs a frame.

def calibration_scale(px_per_m: float = 50.0, segment_px: float = 600.0,
                      reference: CourtReference = CourtReference.SIDELINE_BASELINE_TO_NET
                      ) -> CalibrationScale: ...
```

Two properties make these fixtures worth the code they cost:

1. **Exact ground truth.** `synthetic_ball_clip(step=26.0 px, dt=1/60 s)` with `px_per_m=50.0` gives a true speed of `26 × 60 / 50 = 31.2 m/s = 69.79 mph → 70`. The test asserts `== 70`, not `≈ 70`. Every threshold in Stage 10.4 and 11.4 can be probed at its exact boundary.
2. **The false-positive claims are testable.** The Stage 10.4 table asserts *which criterion* kills each distractor. `draw_distractor` renders each one and the test asserts both that it is rejected **and** which predicate rejected it — so a future parameter change that accidentally makes shoes pass on hue instead of failing on solidity shows up as a failing test rather than as a silent recall/precision shift.

#### 4.5.3 New pure functions requiring unit tests (v2)

| Function | Module | Class | Synthetic test approach |
|---|---|---|---|
| `preview_to_cal_space` | `ball/geometry` | PURE | Point `(0.1, 0.2)` on a 1080×1920 preview, `capture_rotation_deg` swept over 0/90/180/270; assert the mapped `CAL_SPACE` coordinate against hand-computed values; assert the four rotations round-trip to the original |
| `map_calibration_points` | `ball/geometry` | PURE | Portrait and landscape metas; assert both taps land inside `CAL_SPACE` bounds; assert a 90° container rotation composed with a 90° capture rotation is the identity |
| `frame_shape_check` | `ball/geometry` | PURE | Matching aspect → True; 16:9 preview against a 9:16 decoded frame → False; 2 % boundary probed from both sides |
| `scale_from_calibration` | `ball/geometry` | PURE | **The highest-risk arithmetic in the feature.** Two points 600 px apart in `CAL_SPACE` at 11.885 m → `px_per_m == 50.4...` exactly. **Critical negative test:** a diagonal segment on a 9:16 frame must give the same `px_per_m` as an equal-length horizontal one — this fails if anyone computes the segment in normalized units (Stage 11.1). Also: `px_per_m` outside `[8, 300]` → `None`; `segment_px < 60` → `None` |
| `pose_px_to_cal_px` | `ball/geometry` | PURE | 640-space point → 1280-space; assert exact 2× on both axes |
| `player_exclusion_boxes` | `ball/geometry` | PURE | Synthetic skeleton at a known pose; assert the box covers hips/knees/ankles and **excludes** the racket-hand wrist position — a regression here silently deletes every seed detection |
| `exclusion_box_at` | `ball/geometry` | PURE | Native timestamp between two pose samples → nearest one selected; boundary exactly midway → deterministic tie-break |
| `build_background` | `ball/detector` | PURE-CV | Stack of 15 identical frames with a bright blob moved to a different position in each → median recovers the clean background exactly; blob present in 7/15 frames → still removed; present in 8/15 → contaminates (documents the 50 % breakdown explicitly) |
| `motion_mask` | `ball/detector` | PURE-CV | Ball on a static court → mask contains exactly the ball pixels ± 1 px dilation; static court alone → empty mask; a uniform +10-level brightness shift → still empty (threshold 18 has headroom) |
| `morphology` | `ball/detector` | PURE-CV | Salt speckle removed by OPEN; a streak with a 2 px gap reconnected by CLOSE; **assert CLOSE-then-OPEN produces a fake blob from speckle and OPEN-then-CLOSE does not** (guards the ordering decision) |
| `colour_mask` | `ball/detector` | PURE-CV | Yellow ball (H=33) passes; white line fails the yellow branch; red shirt fails both; a blown-out white ball (V=240, S=20) passes only via the achromatic branch; **assert the achromatic branch alone matches every court line**, documenting why it is only ever ANDed with motion |
| `contour_features` | `ball/detector` | PURE-CV | Exact circle r=10 → circularity ≥ 0.90, aspect ≈ 1.0, solidity ≥ 0.97; exact circle r=3 → circularity ≥ 0.70 (documents the discretization floor that justifies the 0.65 threshold); 6×47 capsule → aspect ≈ 7.8, circularity ≈ 0.24, solidity ≥ 0.90; degenerate 1-px contour → no crash |
| `passes_round_tier` | `ball/detector` | PURE-CV | Each of the six criteria swept independently across its boundary; a capsule must fail |
| `passes_streak_tier` | `ball/detector` | PURE-CV | Capsule at the expected direction passes; **same capsule rotated 60° from the track direction fails**; a 4×420 line fragment fails on aspect; a 2 px-thick sliver fails on minor axis; a concave limb fragment fails on solidity; `expected_direction_deg=None` skips only the orientation criterion |
| `in_exclusion_box` | `ball/detector` | PURE-CV | Inside / outside / exactly-on-edge |
| `detect_candidates` | `ball/detector` | PURE-CV | Full frame from `synthetic_ball_clip` with all six distractor kinds present → exactly one candidate returned, and it is the ball; **parametrized over each distractor in isolation, asserting which predicate rejected it** |
| `gate_radius_px` | `ball/track` | PURE | `px_per_m=50`, `dt=1/60` → `71.5 × 50 / 60 = 59.6 px`; assert the 71.5 m/s cap is what bounds it |
| `seed_index` | `ball/track` | PURE | Candidate 0.3 TU from the racket hand seeds; one at 1.2 TU does not; two candidates → nearer wins |
| `step_direction_deg`, `direction_delta_deg` | `ball/track` | PURE | Known vectors; wraparound across ±180 (`170°` vs `−170°` → delta 20, not 340) |
| `associate` | `ball/track` | PURE | Straight synthetic track → all N associated in order; **decoy 3 px off the true path at frame 3 → rejected by the direction gate, and the returned track still has the correct step**; 2 dropped frames → coasted, 3 → `terminated_by == "miss_limit"`; a 90° direction change → `terminated_by == "direction_gate"` at the right index; ball leaving the frame → `terminated_by == "frame_edge"` |
| `build_ball_track`, `validate_track_invariants` | `ball/sequence` | PURE | Non-monotonic timestamps → raises; length mismatch → raises; empty track is legal; `.npz` round-trip is bit-identical |
| `step_displacements_px` | `analysis/speed` | PURE | Known centres → exact Euclidean steps; `skip_first=True` drops exactly one |
| `step_intervals_s` | `analysis/speed` | PURE | **Non-uniform PTS** → exact per-step Δt (catches an assumed-1/fps bug, the ball-side twin of the `central_difference` test) |
| `median_step_speed_px_s` | `analysis/speed` | PURE | **The load-bearing robustness test.** A clean 6-detection track and the same track with step 2 replaced by a 10× value must return the **identical** result. Also: a track with three zero-displacement steps (the duplicate-frame failure Stage 10.2 exists to prevent) must be visibly wrong, documenting why native fps is required |
| `blob_size_drift` | `analysis/speed` | PURE | `minor_axis` linearly 5.0 → 4.0 over 6 detections → ratio 0.80; constant → 1.00; single detection → `None` |
| `px_s_to_mph` | `analysis/speed` | PURE | `1560 px/s ÷ 50 px/m = 31.2 m/s = 69.79 mph`; assert the 2.23694 constant is applied once, not twice |
| `confidence_from_detections` | `analysis/speed` | PURE | Exact boundaries: 2 → `unavailable`, 3 → `low`, 4 → `low`, 5 → `medium`, 8 → `medium`, 9 → `high`, 20 → `high` |
| `apply_confidence_caps` | `analysis/speed` | PURE | Each cap in isolation (oblique view, drift 0.70, `CUSTOM` reference, `segment_px=120`); each in combination; **assert a cap never raises a level** — `apply_confidence_caps(LOW, ...)` can never return `MEDIUM` or `HIGH` under any input |
| `estimate_ball_speed` | `analysis/speed` | PURE | End-to-end on `synthetic_track` constructed to be **exactly 70 mph** → returns `70`; `type(result.ball_speed_mph) is int`, not float; `n=2` → `None` + `too_few_detections`; drift 0.5 → `None` + `depth_drift_exceeded`; a track implying 400 mph → `None` + `implausible_speed`; `scale=None` → `None` + `not_calibrated`; `view=BEHIND` → `None` + `camera_view_unsuitable`; `contact_confidence=0.2` → `None` + `contact_unreliable`; **and in every `None` case, assert the value is `None` and never `0`** |
| `check_mph_binding` | `feedback/guard` | PURE | `ball_speed_mph=None` and text `"about 85 mph"` → violation; `=68` and `"68 mph"` → clean; `"68.0 mph"` → violation; `"about 70 mph"` → violation; `"68 mph"` three times → violation (max 2); `"mph"` with no number → violation; `"68mph"` (no space) → clean; non-breaking space → clean after normalization |
| `validate_text_field`, `enforce` | `feedback/guard` | PURE | Existing v1 cases, plus: a draft mentioning `68 mph` when the payload says 68 passes; the same draft when the payload says `None` → field discarded; a draft converting to `"110 km/h"` → discarded regardless; `mph_rule` recorded correctly in the report in both modes |
| `describe_ball_speed` | `feedback/template` | PURE | `None` → returns `None` (sentence omitted, not "unavailable"); `high`/`medium`/`low` → three distinct hedges; output fed back through `enforce` as a self-check |
| `ball_speed_block`, `hedge_for` | `feedback/prompt` | PURE | Non-null and null payload shapes; assert the null block still carries an explicit `reporting_rule` (an absent key would invite the model to fill it) |
| `MetricUnit` has no `mph` | `models/enums` | PURE | `assert "mph" not in {u.value for u in MetricUnit}` — a one-line test that permanently prevents ball speed from becoming a scored metric |
| `BallSpeedResult` typing | `models/responses` | PURE | `68` valid; `68.0` → `ValidationError` (StrictInt); `68.4` → `ValidationError`; `12` and `200` → `ValidationError` (bounds); `None` valid; `ball_speed_mph is None` with `unavailable_reason is None` → invariant violation |

The `median_step_speed_px_s` and `apply_confidence_caps` tests deserve emphasis: they assert *design properties*, not just outputs. "The median survives one bad association" and "a cap can only lower confidence" are the two claims this feature's honesty rests on, and both are directly executable.

---

## 5. The Single Riskiest Technical Assumption

Re-evaluated in light of the ball-speed addition. **It has not changed.**

**The assumption: that torso-normalized 2D landmarks from a single uncontrolled phone camera produce swing metrics stable enough to score against a fixed rubric — that is, that the same swing filmed from a somewhat different angle yields substantially the same numbers.**

This is still the riskiest because everything downstream is *conditionally* correct. The pure math is testable and will be correct. The rubric is a defensible table. The Gemini guard is airtight. None of that matters if `shoulder_hip_separation_deg` reads 38° from one camera position and 57° from another for the identical swing, because then the rubric verdict flips from "low, work on your coil" to "ideal, nice coil," and the app confidently tells the user to fix something that isn't broken. That is worse than no product: it is a coaching app that produces authoritative-sounding, camera-dependent noise, and users will detect the inconsistency the first time they film from the other side of the court.

**Why it is nonetheless workable for an MVP.** Three pieces of evidence. First, markerless single-camera pose estimation has been repeatedly validated against marker-based motion capture, and the consistent finding is that **sagittal-plane joint angles are recovered to within roughly high-single-digit degrees when the camera is approximately perpendicular to the plane of motion, and degrade sharply off-plane.** The error is not random — it is a systematic function of view angle, which means it can be *gated* rather than merely tolerated. Second, the design already front-loads the mitigation: the metric set was deliberately weighted toward **ratio and relative quantities** — shoulder-hip separation (a difference of two co-projected lines, so first-order projection effects cancel), contact height ratio (normalized against the player's own shoulder height), tempo ratio (pure timing, projection-invariant), balance sway and head stillness (torso-normalized displacement), and the *sign* of swing path angle. These are structurally more view-stable than absolute joint angles, and every one of the fragile absolute angles is already tagged `view_sensitive: true` in the schema. Third, MediaPipe's `world_landmarks` output gives a metric 3D estimate that, while too noisy to report, is a usable second opinion for detecting when the 2D projection has gone bad.

**Concrete fallback.** Enforce the camera view instead of tolerating it. `estimate_camera_view()` already runs in Stage 7 and classifies view from the ratio of projected shoulder width to torso length (near 0.9–1.1 facing the camera, collapsing below ~0.5 side-on). If the detected view falls outside the tolerance band for the rubric, the response degrades to `status: "partial"`, **all `view_sensitive` metrics are set to `None`**, their weights are renormalized away, and the client shows a "reshoot from the side" prompt with a framing overlay. The user gets four to six view-invariant metrics and honest, narrower feedback rather than fifteen metrics of which several are fiction. The `view_sensitive` flag and the `None`-propagation-with-weight-renormalization machinery are in the schema and the scoring functions specifically so this fallback is a configuration change, not a rewrite.

**Cheap early validation — do this before writing any metric code.** One player, one phone, half a day. Record the same three shot types from five camera positions (side-on, 30° off, 45° off, behind, front), and record three repetitions at each position so swing-to-swing variance can be separated from camera-induced variance. Run Stages 5–6 once offline, freeze the fifteen resulting `PoseSequence` objects to `.npz` in `tests/fixtures/golden/`, then compute, for each candidate metric, the coefficient of variation across camera positions for the *same* swing. **Ship only the metrics whose across-camera CV is under 10 % and whose across-camera variance is smaller than their across-repetition variance** — the second condition is the one that matters, because a metric that varies more with the camera than with the actual swing is measuring the camera. This costs no backend, no deployment, and no Gemini calls, and it produces the golden fixtures the regression suite needs anyway. In parallel, `synthetic_swing(camera_yaw_deg=...)` lets the same sensitivity be probed analytically in CI on day one, with no camera at all.

**In v2 this validation shoot gets one addition at zero marginal cost:** hit a ball in every take, tap the sideline calibration before each camera position, and freeze the resulting `BallTrack` objects to `tests/fixtures/golden_ball/`. That converts the same half-day into the ball-speed validation described below, and produces the ball-side golden fixtures for free.

### 5.1 Why ball-speed calibration validity ranks second, not first

This was seriously reconsidered, because on one axis ball speed is clearly *worse*: its error magnitude is larger. A crosscourt ball that slips past the geometry gates reads up to 30 % low, whereas a view-sensitive joint angle typically shifts by 10–20 % across a moderate view change. If magnitude were the only criterion, ball speed would win.

It ranks second on four other criteria, and they dominate:

1. **Blast radius.** The view assumption contaminates *every* swing metric, therefore the scorecard, therefore `overall_score`, therefore the shot-type inference, therefore every sentence of coaching text — that is 100 % of the product's value. Ball speed is **one nullable field**, excluded from the rubric by design (Stage 15), excluded from `MetricUnit` by construction (§2.1), and not an input to shot-type inference (Stage 14). It can be `null` on every single clip and the product still does its entire job.

2. **Detectability of the failure.** Ball speed fails **loudly and locally**: no detections → `null` + a machine-readable reason; bad geometry → `null`; implausible result → `null`. There are eleven distinct `BallSpeedUnavailableReason` values and every one of them is visible in the stored row, so production health is a single SQL query. The view assumption fails **silently and plausibly** — it emits a number that looks entirely reasonable and is wrong, with no signal anywhere that it is wrong. A silent-wrong failure outranks a loud-absent one, because only the first one reaches the user as false authority.

3. **Reversibility.** Ball speed is a strict, additive change behind a nullable field. Killing it is a config flag (`ball_detection_enabled = False`) and one release; clients already handle `null`, because `null` is the common case from day one. The view assumption is load-bearing for the entire design and cannot be removed without redesigning the product.

4. **They share a root cause, and ball speed partially *mitigates* it.** Both risks reduce to the same underlying fact: **we have one uncalibrated monocular sensor and everything is a projection.** The tapped calibration is the only place in this entire system where an external, real-world metric reference enters at all. It is a narrow, gated, error-prone anchor — but it is an anchor, and it is net-additive to the thing §5 is worried about, not additive to the risk.

**The runner-up, stated so it is not lost:** *that a two-point court calibration on the court plane yields a scale factor valid for a ball travelling above and away from that plane.* Its mitigations are the five measures in Stage 10.6 — constrained constant-depth reference segments, a required perpendicular level camera view, a 0.25 s window that bounds depth travel, a measured blob-drift gate that returns `null` past ±40 %, and an explicitly refused depth correction. Its residual is ±10–15 % on a compliant capture and up to 30 % low on a non-compliant one that still passes the gates. Its own cheap validation is the same half-day shoot: hit ten balls past a radar gun or a phone running a commercial speed app, compare, and **if the median absolute percentage error exceeds 20 %, ship the feature with `confidence` forced to `low` on every clip and the number rendered as a secondary, clearly-hedged line rather than a headline figure.** That fallback is a rendering change and a one-line cap, which is exactly why this risk ranks second: it has a cheap, non-structural escape hatch, and the §5 assumption does not.

---

## Appendix A — Frontend touchpoints

Not specified in detail (this document is backend-scoped), but the following Flutter files are implied by the contract above and will need to exist:

- `frontend/lib/services/api_client.dart` — ticket → direct Storage upload → `POST /v1/analyses` → poll loop (2 s interval, 120 s cap).
- `frontend/lib/services/supabase_storage.dart` — signed upload execution.
- `frontend/lib/models/analysis_response.dart` — generated from the Pydantic schema; must honor `view_sensitive`, render `null` metrics as "not measurable in this clip" (never as `0`), and handle `v1` rows that have **no `ball_speed` block at all**.
- `frontend/lib/models/ball_speed_calibration.dart` — the two tapped points, the chosen `CourtReference`, and the preview surface dimensions. Must send the preview size and the rotation it applied, not the recorded frame's — the backend inverts that transform and will reject a mismatched aspect.
- `frontend/lib/screens/capture_screen.dart` — camera framing overlay enforcing the side-on view (the §5 fallback lives here as much as in the backend). **Records at the device default frame rate; it does not force, request, or default to 60 fps** (§11.5). A 60 fps mention belongs only in the setup hint text as an optional aside, never as a prompt, a toggle the user must clear, or a warning.
- `frontend/lib/screens/calibration_screen.dart` — **new.** Pre-recording tap flow: pick a `CourtReference` from a short illustrated list (default `SIDELINE_BASELINE_TO_NET`), tap its two endpoints on the live preview, show the drawn segment for confirmation. Must state that calibration is invalidated by moving the camera, and must offer "skip" as a first-class, unpenalized option — the majority of sessions will skip it and the app must not feel broken when they do.
- `frontend/lib/screens/analysis_result_screen.dart` — scorecard + feedback rendering, with a visible `partial` state; ball speed rendered as a whole number with its confidence badge and the `measurement_definition` text ("average over the first 0.25 s after contact") adjacent to it, never as a standalone hero number. `medium` is the expected badge on 30 fps footage (§11.5) and must be styled as a normal, healthy result — not in a cautionary colour and not with copy implying the user should have filmed differently. It is and **omitted entirely rather than shown as "0 mph" or "—" when `null`** unless the user explicitly calibrated, in which case the `unavailable_reason` is explained in plain language.

## Appendix B — Change log against `v1`

| Area | Change |
|---|---|
| §0 | Rewritten. Ball speed is in scope as a justified, fenced exception; spin and rallies remain out. |
| Stage 3 | `ball_speed_calibration` accepted and validated synchronously; `CALIBRATION_INVALID` added. |
| Stage 4 | Temp-file deletion moved to after Stage 11. |
| Stage 5 | Intake cap 12 s → **60 s**; keyframe motion scan added for clips > 10 s. |
| Stage 6 | Extractor close is now a memory-ordering requirement, not just hygiene. |
| Stage 9 | `absolute_time_s` added to `ContactDetection`. |
| **Stage 10** | **New — ball detection (classical CV, native fps, `CAL_SPACE`).** |
| **Stage 11** | **New — speed calculation (median step, calibrated scale).** §11.5 sets capture-rate policy: 30 fps supported and expected, `medium` is the normal ceiling, 60 fps never forced. |
| Stages 10–17 (v1) | Renumbered to 12–19. |
| §2 | `CourtReference`, `BallSpeedConfidence`, `BallSpeedUnavailableReason`, `BallDetectionTier`, `NormalizedPoint`, `BallSpeedCalibration`, `CalibrationEcho`, `BallDetectionSummary`, `BallSpeedResult` added; `MetricUnit` deliberately unchanged. |
| §3 | `app/ball/` package added; `analysis/speed.py` added; `analyze()` split into `analyze_kinematics()` + `analyze_swing()`. |
| §4 | Second seam `BallTrack` defined; `PURE-CV` class defined; purity test extended, `cv2` banned from `app/analysis/**`. |
| §5 | Re-evaluated; unchanged, with the ball-speed risk named explicitly as runner-up. |
| §1.20 | Rebudgeted; baseline RSS up ~60–75 MB from OpenCV; peak added RSS unchanged at ~110 MB. |
| §17 | Numeric guard extended with the conditional `mph` binding rule. |
| Rubric | **Unchanged.** `rubric_version` stays `rubric_v1`. |
