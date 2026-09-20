# TennisForm AI — Analysis Pipeline Architecture

**Document status:** design contract. No implementation code. Pydantic model definitions and function signatures below are the *contract*, not the implementation.
**Pipeline version:** `v2`
**Rubric version:** `rubric_v1` (unchanged — ball speed is deliberately not scored)
**Detector version:** `ball_cv_v1`
**Revision note:** this document supersedes the `v1` pose-only plan. The only substantive change is the addition of measured ball speed via user calibration (§0.2, Stages 10–11, §2.x, §3 `app/ball/`, §4.4, §1.20 budget, Stage 16 payload). Everything else — async job model, direct-to-Storage upload, the `PoseSequence` seam, the rubric, the Gemini numeric guard — is carried forward unchanged.

**Revision note (Stage 6 rewrite):** Stage 6 has been rewritten against MediaPipe Tasks `PoseLandmarker` as installed (`mediapipe==0.10.35`, Python 3.13.2). The legacy `mediapipe.solutions` API the section was originally specified against **does not exist in the installed package** — `mp.solutions` raises `AttributeError` — and that specification had never been executed. No code was migrated, because none existed: `backend/app/pose/` contained only a 54-byte `__init__.py`. **`Pipeline version` is NOT bumped: the `PoseSequence` seam is unchanged** (same `(T,33,4)`/`(T,33,3)` arrays, channel 4 still `visibility`), no reported quantity's definition changed, and no `ErrorCode` member was added or removed. Two things *did* change substantively and are called out where they land: the Tasks API has **no `smooth_landmarks` option**, so MediaPipe's one-euro filter is now accepted as part of the sensor and documented as such (Stage 6.3, Stage 7 step 7, Stage 9); and `MULTIPLE_SUBJECTS_SUSPECTED` is gone, replaced by a pure `PoseQuality.flags` warning (Stage 6.6, Stage 7 step 1b, §2.1). Knock-on edits landed in: §1.20 (rebudgeted — **the 512 MB container may no longer fit; see the risk there**), §2.1, §2.x `PoseQuality`, §3 (tree, deviations, two new signature blocks, purity classes), §3.5, §4.1, §4.4, §4.5, Stage 5, Stage 7 steps 1 and 7, Stage 9, `requirements.txt`, and `CLAUDE.md`. The riskiest open assumption in the whole pose path is named in Stage 6.11 and is gated by an acceptance test, not by argument.

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

- **The request timeout is no longer the reason, and this was re-derived rather than inherited.** Cloud Run's default request timeout is 300 s (configurable to 3600 s), so the warm-path budget of 14–27 s and even the rotation-retry worst case of ~23–41 s (§1.20) would fit inside a synchronous request with no configuration change. The earlier justification — Render's ~100 s edge timeout — is retired as a reason. Async is kept on the grounds below instead.
- **One instance, one job at a time, is a structural property of this deployment, not a tier limitation.** §1.20.1 pins `--max-instances=1` — on **both** deployment tiers, production and dev — because the job runner and its queue-depth counter live in process memory. (`--min-instances` differs by tier: `1` on the production tier documented in §1.20.1, `0` on the dev/demo tier actually deployed today, §1.20.1a. The *at most one* half is what this bullet rests on, and it is tier-independent.) With `ThreadPoolExecutor(max_workers=1)` inside that single instance, the service can analyze exactly one clip at a time; the second, third, and fourth concurrent uploads *must* wait. A synchronous design expresses that wait as a silent open socket for up to ~2 minutes with no queue position and no way for the client to distinguish "third in line" from "dead". The async job makes the wait a first-class, observable state (`queued`, `estimated_seconds`, `429 QUEUE_FULL` past depth 4).
- **Mobile network flakiness must not destroy completed CPU work.** On this deployment an analysis is the scarcest resource in the system (one at a time, on a single pinned instance — and on the dev tier of §1.20.1a, on an instance that may have had to cold-start to run it). A synchronous design loses the entire result if the phone changes network mid-request; async-plus-poll writes the result to `analysis_jobs` and the client picks it up on the next poll.
- **MediaPipe inference is CPU-bound and the ASGI loop must stay responsive.** Inference runs in a `ctypes`-loaded native library that releases the GIL, so Python can still serve polls — but only if there is a core to serve them on, which is why §1.20.1 allocates 2 vCPU rather than 1. `--cpu=2` is retained on **both** tiers (it is a sizing flag, not a billing-model flag, and costs nothing while scaled to zero), so this reasoning is unaffected by the dev-tier reversal in §1.20.1a.
- **The queue guard is now a latency and fairness guard, not primarily an OOM guard.** On the 2 GiB allocation in §1.20.1 a single job has roughly 4× headroom (§1.20's rebudget), so `429` past a queue depth of 4 exists to bound the worst-case wait a user is asked to accept, not to prevent an OOM. `max_workers` stays at 1 regardless.

We do **not** introduce Celery/Redis/RQ for the MVP. The job runner is an in-process single-worker executor; the **source of truth for job state is the Supabase `analysis_jobs` row**, not process memory. On instance restart, in-flight jobs are orphaned — handled by a staleness rule: any job in `running` whose `heartbeat_at` is older than 180 s is reported to the poller as `failed` with `ErrorCode.WORKER_LOST`, and the client may retry. This costs one timestamp column and removes the entire class of "spinner forever" bugs.

**Single-instance assumption, made explicit because the platform does not give it for free.** Cloud Run autoscales horizontally by default. A second container instance would mean a second independent `ThreadPoolExecutor`, a second in-process queue-depth counter (so `429 QUEUE_FULL` would admit up to 2× the intended depth), and a second heartbeat sweeper racing the first over the same `analysis_jobs` rows. That is a correctness problem, not a scaling inefficiency. It is bounded by configuration, in §1.20.1: `--max-instances=1`, which is set on every tier. **How completely it is bounded depends on the tier actually deployed.** On the production tier (`--min-instances=1`), the instance never leaves existence, so two instances are not reachable at all. On the **dev/demo tier deployed today** (`--min-instances=0`, §1.20.1a), the service scales to zero, and a scale-from-zero transition can in principle briefly overlap a departing instance with an arriving one — `max-instances=1` reduces that to "at most a brief overlap during a scale event", not "impossible". §1.20.1a states why that residual window is accepted at this stage and what triggers upgrading out of it. The known future option, recorded now and deliberately not built for the MVP: move the depth guard and the staleness sweep into the database (a `SELECT count(*) WHERE status IN ('queued','running')` admission check plus an advisory-locked sweeper), which would make `max-instances > 1` safe and would also close the dev tier's scale-event window. Until that exists, `max-instances=1` is load-bearing and must not be raised to "handle more traffic".

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
- **Rotation self-check.** If, after Stage 6, the pose-detection success rate is below 20 %, retry the whole clip once with rotation `+180°`. Upright-person assumption is baked into BlazePose; a wrongly oriented clip produces near-zero detections rather than wrong landmarks, which makes this check unambiguous. One retry only. **The check is cheap; the retry is not.** A retry is a second full VIDEO-mode extraction pass — a further **7–12 s** on the 2 vCPU Cloud Run allocation (§1.20.1) — taking worst-case Stage 6 to ~16–26 s and the worst-case warm job to ~23–41 s (§1.20). That sits inside the 180 s `heartbeat_at` staleness window with wide margin, and also inside Cloud Run's 300 s request timeout — so the timeout is no longer what makes this survivable; the 180 s heartbeat window is. `estimated_seconds` will read low on any clip that takes this path. When the retry fires, append `rotation_retry_applied` to `PoseQuality.flags` so the cost is visible in the response rather than only in the logs.
- **Timestamps come from PTS × time_base, never from `avg_frame_rate`.** Phone video is variable-frame-rate: iOS "Auto FPS" drops 30 → 24 fps in low light mid-clip. Trusting nominal fps corrupts every velocity in the pipeline — and, in v2, corrupts the ball-speed denominator directly.
- **Analysis frame rate: 30 fps, fixed.** For each target time `t_k = t_0 + k/30`, select the decoded frame whose PTS is nearest `t_k`. We record the **actual** PTS of each selected frame in `PoseSequence.timestamps_s` and compute all derivatives with real Δt, not an assumed 1/30. If the source is 24 fps, some target slots select the same frame twice; the recorded timestamps expose this and the velocity math stays correct. *(This duplicate-selection behaviour is exactly why ball detection does **not** use this stream — see Stage 10.2.)*
  - Why 30 and not 60: 60 fps doubles MediaPipe cost, which is ~70 % of our budget. Why not 24: contact is a ~4 ms event; at 30 fps we localize it to ±17 ms, which is inside the coaching-relevant resolution, and peak-hand-speed estimation degrades noticeably below 30.
- **Downscale to longest side = 640 px** before inference. BlazePose resizes internally to 256×256; feeding 1080p burns CPU in the resize for zero accuracy gain.
- **Cap: 240 sampled frames (8.0 s).**
- **Analysis-window location for long clips (new in v2, forced by the 60 s intake cap).** For clips longer than 10 s we cannot afford to decode every frame just to find the swing. Instead we run a **keyframe-only motion scan**: PyAV with `stream.codec_context.skip_frame = "NONKEY"`, decoding only I-frames (phone encoders emit roughly one per 1–2 s, so 30–60 frames for a 60 s clip), downscaled to 160 px long edge, and take the mean absolute difference between consecutive keyframes. The 8 s analysis window is centred on the keyframe with maximum motion energy, clamped to the clip bounds. This locates the swing to roughly ±1 s, which an 8 s window absorbs comfortably. Cost 0.2–0.8 s (§1.20), versus ~14 s to decode a 60 s clip at full rate. Clips ≤ 10 s skip the scan and use the whole clip.
- **Streaming, never buffered.** 240 frames at 640×360×3 bytes is 166 MB held simultaneously. The generator yields one frame at a time; only landmarks are retained. This is a hard requirement, not an optimization.

Budget: 2–4 s (plus 0.2–0.8 s motion scan for long clips).

#### 5.1 DEFECT — the keyframe motion scan mis-centres the analysis window (UNIVERSAL; fix planned, not implemented)

> **Status: diagnosed on real footage, fix specified here, no code written.** `motion_scan_centre_s` in `backend/app/pose/video_io.py` behaves exactly as this document's Stage 5 bullet describes. The bullet is what is wrong.

**Root cause.** The function decodes I-frames only (`skip_frame = "NONKEY"`), takes the mean absolute pixel difference between each consecutive keyframe pair, and returns the PTS of the **later** keyframe of the highest-difference pair. Two things follow from that last clause, and only the second is obvious:

1. The answer's resolution is the GOP interval. Nothing in the function can localise an event to better than the spacing between keyframes.
2. Returning a bucket *endpoint* discards where inside the bucket the motion happened. Even at the function's own resolution it reports the wrong end of the interval it correctly identified.

The Stage 5 bullet above assumes "roughly one keyframe per 1–2 s, so 30–60 frames for a 60 s clip." **Measured across the entire 16-clip corpus: 3–6 keyframes per clip, GOP 3.03–4.17 s. 16 of 16 clips have GOP ≥ 3.0 s.** The assumption is not slightly optimistic; it is wrong by a factor of two to four on every clip measured, and the function degrades silently when it is wrong — there is no signal anywhere in `PoseStream` that distinguishes "located the swing" from "returned a bucket boundary 2.6 s away from it."

**Worked example — `serve_vertical_10340710.mp4`, 10.4 s, 25 fps.** Keyframes at exactly `[0.0, 3.04, 6.08, 9.12]`. Motion energies: `0 → 3.04: 12.64`, `3.04 → 6.08: 15.48`, `6.08 → 9.12: 8.21`. The argmax correctly picks the middle bucket — the swing really is in it — and then returns that bucket's **end**, 6.08 s. True strike is 3.44–3.52 s, only 0.4 s into a 3.04 s bucket. The resulting window is `[2.08, 10.08]`: centre 2.6 s past the swing.

On this clip the strike does survive inside the window, but only because the clip is barely longer than the window and the clamp at the clip end dragged the window start back to 2.08 s. That is luck, not margin. A 2.6 s error against a ±4 s half-window leaves 1.4 s of slack; any clip long enough that the clamp does not bite loses the swing outright, and a swing sitting *late* in its winning bucket rather than early misses in the other direction with the full GOP as the error term.

**Why this is an algorithm change and not a threshold change.** There is no constant here to tune. `MOTION_SCAN_LONG_EDGE_PX` is fine and the > 10 s scan threshold is fine. Widening the analysis window to absorb the error is the only threshold-shaped option and it is unaffordable: Stage 6 is ~70 % of the job budget (§1.20) and scales linearly in sampled frames, so an 8 → 12 s window costs 4–7 s of MediaPipe time, and the 240-frame cap would force either a cap increase or a drop below the 30 fps that Stage 5 argues for on contact-resolution grounds. The defect is that the function's *output resolution* is the GOP and its *reported point* is an interval endpoint. Both are structural.

##### 5.1.1 Proposed change

Both remedies were evaluated. **The recommendation is to implement both, as one bounded primitive invoked over different spans, rather than as two code paths selected by a density test** — two paths with two thresholds is the shape that produced this defect in the first place.

Proposed signatures (no bodies; the algorithms are in prose below):

```python
# backend/app/pose/video_io.py  —  IMPURE (PyAV decode)

@dataclass(frozen=True)
class KeyframeBucket:
    start_s: float
    end_s: float
    energy: float

@dataclass(frozen=True)
class MotionScanResult:
    centre_s: float
    source: Literal["refined", "dense", "keyframe"]
    keyframe_count: int
    observed_gop_s: float
    frames_decoded: int

def keyframe_motion_profile(path: Path, probe: ClipProbe) -> list[KeyframeBucket]: ...

def dense_motion_centre_s(
    path: Path,
    probe: ClipProbe,
    *,
    span_start_s: float,
    span_end_s: float,
    max_frames_decoded: int = MOTION_REFINE_FRAME_BUDGET,
) -> tuple[float, int] | None: ...

def motion_scan_centre_s(path: Path, probe: ClipProbe) -> MotionScanResult | None: ...
```

The return type widens deliberately. The present function returns a bare `float | None` and therefore cannot tell its caller how much to trust it, which is the mechanism by which a 2.6 s error shipped unnoticed.

**Remedy (a) — refine within the winning interval.** Pass 1 is unchanged except that it retains the whole profile rather than only the argmax, and records both endpoints of each bucket. Pass 2 re-opens the container *without* `skip_frame`, seeks to the winning bucket's start, and decodes forward to its end, sampling at a stride chosen so the sampled count stays inside `MOTION_REFINE_FRAME_BUDGET` (proposed **48**). Energy is the same mean-absdiff at the same 160 px long edge, so pass 2 measures the same physical quantity as pass 1, only at finer spacing. The returned centre is the **midpoint of the highest-energy adjacent sampled pair — never an endpoint.** That rule is the structural fix and it applies at every level, including pass 1's degenerate fallback.

The refinement span is the winning bucket widened by **half a GOP on each side**, clamped to the clip. A bucket endpoint carries ±GOP of uncertainty about which side of a boundary the event actually sits on, and the guard band is what stops a swing that straddles a keyframe from being localised into the wrong half. Budget 48 is chosen so that a 4 s bucket plus two ~2 s guard bands — roughly 8 s of span — samples at about 6 Hz, enough to localise a 0.3–0.5 s strike to within ~0.2 s. That is an order of magnitude better than the 3.04 s the function achieves today, and far inside what an 8 s window absorbs.

**Remedy (b) — per-frame fallback when keyframes are too sparse.** "Too sparse" is defined as observed GOP ≥ **2.0 s**, i.e. fewer than one keyframe per 2 s — the density the Stage 5 bullet assumed as its *floor*, so the threshold is not a new invention but the existing assumption made testable. Measured against the corpus it fires on 16 of 16 clips, which is the honest reading of the evidence: on this corpus the sparse path is the normal path, not the exception. Remedy (b) is then not a separate algorithm; it is remedy (a) invoked over a wider span, because the half-GOP guard band already widens the refinement in proportion to the sparsity. The one genuinely distinct branch is **fewer than two keyframes**, where no bucket exists at all.

**Degradation on a single-keyframe clip.** With one keyframe there is no energy pair, and today's function returns `None`; the caller then sets `motion_scan_used = False` and `analysis_window_bounds` falls back to the head of the clip — a guess, dressed as a decision. Planned behaviour: run `dense_motion_centre_s` over the **whole clip** with the same 48-frame budget, giving a stride of `duration_s / 48` (1.25 s on a 60 s clip). That is coarse, and it is still strictly better than the head of the clip: it puts the swing inside the 8 s window rather than inside the first 8 s of the file. `source` reads `"dense"`, `frames_decoded` reads 48, and the coarseness is therefore legible. Only if that decode also fails does the function return `None` and preserve today's honest no-answer. **Surface it:** append `motion_scan_coarse` to `PoseQuality.flags` whenever `source == "dense"` and the stride exceeded 0.5 s, on exactly the principle that governs `rotation_retry_applied` — a degraded path must cost something visible in the response, not only in the logs.

##### 5.1.2 Cost, against the §1.20 budget

Pass 2 decodes non-keyframes, so the decoder runs from the preceding keyframe regardless of stride; the budget bounds *sampled* frames, not *decoded* ones, and the cost must be stated against the latter. Worst case is a full sequential decode of the refinement span at source resolution with downscale after: ~8 s of a 25–30 fps clip is 200–240 full decodes. At the per-frame decode cost implied by the existing "decode + rotate + sample @ 640 px | 2–4 s" row for 240 frames, that is **~0.3–0.9 s added**, taking the motion-scan line item from **0.2–0.8 s to 0.5–1.7 s**. §1.20's table row should be renamed "Motion scan (keyframe pass + bounded refinement)" and rebudgeted accordingly; the warm total moves from ~14–27 s to ~14–28 s, and only on clips > 10 s.

That is affordable, and the comparison that makes it affordable is already in this section: the alternative is ~14 s to decode a 60 s clip at full rate, and the rotation self-check already budgets a discretionary 7–12 s. One second to stop silently analysing the wrong eight seconds of video is the cheapest correctness in the budget. The hard bound is `MOTION_REFINE_FRAME_BUDGET`, not wall time, so the cost cannot grow with clip length.

##### 5.1.3 Corpus caveat — stated plainly rather than overstated

**The 16-clip corpus is all Pexels stock footage and is uniformly encoded.** A 3–4 s GOP is characteristic of stock and web-delivery encodes optimised for compression ratio. Phone video — the actual input this product takes — typically uses much shorter GOPs, often 1–2 s, which is what the original Stage 5 bullet assumed and is very likely correct for a large share of real uploads. **The corpus is therefore unrepresentative in exactly this respect, and "16 of 16" must not be read as "16 of 16 phone clips."**

That does not weaken the case for the fix, for two reasons which are kept separate so that neither is smuggled into the other. First, **the backend cannot control the uploader's encoder**: a clip that has been through a messaging app, a screen recording, an editor export, or any re-encode arrives with whatever GOP that tool chose, and the current function has no way to notice. Second, and decisively, **the failure is silent** — no flag, no confidence term, no error; the job completes and reports a confident analysis of the wrong window. A defect that is uncontrollable and silent is worth a bounded second of decode even at low incidence. The honest summary: *severity on the corpus is certain and severe; incidence on real phone uploads is unknown and probably lower.*

##### 5.1.4 Rejected alternatives

| Alternative | Why rejected |
|---|---|
| Widen the analysis window to 12 s to absorb the error | Stage 6 is ~70 % of budget and scales linearly; costs 4–7 s and forces the 240-frame cap up or the 30 fps rate down. Buys tolerance for a wrong answer instead of buying a right one. |
| Return the **earlier** keyframe of the winning pair | The same class of error with the opposite sign. It happens to give 3.04 s on the worked clip, which is precisely why it is tempting; a swing late in its bucket is then wrong by a full GOP. Endpoint choice is not the fix — discarding intra-bucket location is. |
| Energy-weighted centroid over all buckets | A second mover — spectator, ball boy, passing player — in a different bucket drags the centroid arbitrarily. Argmax is robust; the problem is resolution, not selection. |
| Decode every frame on every clip | ~14 s on a 60 s clip (§1.20 long-clip note). This is the cost the keyframe scan exists to avoid. |
| Locate the window with a coarse pose pass | MediaPipe is the dominant cost. A pose pass to find the window costs more than the window saves, and it inverts the stage order. |
| Raise the > 10 s scan threshold so more clips skip the scan | Clips ≤ 10 s already skip it. Raising it means analysing the first 8 s of a 15 s clip, which is a worse guess than a coarse scan, not a better one. |

##### 5.1.5 Test strategy

The finding that matters most: **no existing test in `backend/tests/unit/pose/test_video_io.py` would have caught this.** The suite covers `analysis_window_bounds` (pure clamping arithmetic — correct, and unaffected by this change), absolute-versus-window-relative PTS, and the frame cap. Nothing asserts anything about *where* the scan points, and nothing controls keyframe spacing. That gap is why a 2.6 s miscentre shipped green.

- **New fixtures.** Synthetic clips written with PyAV at a *forced* `gop_size`, so keyframe spacing is an input to the test rather than an accident of whichever file was to hand. Minimum three: `gop_size` ≈ 1 s (the assumed case), ≈ 3 s (the measured case), and a single-keyframe clip.
- `test_refinement_locates_a_sub_gop_event` — 12 s clip, 3 s GOP, one moving bright region for 0.3 s at t = 3.45 s. Assert `abs(centre_s - 3.45) <= 0.35`. This is the test that fails today and passes after.
- `test_a_bucket_endpoint_is_never_returned` — assert the returned centre is strictly interior to the winning bucket whenever `source == "refined"`. This encodes the structural rule directly, so a regression to endpoint-reporting is caught even if the tolerance test happens to survive it.
- `test_sparse_keyframes_take_the_refined_path` — assert `source` and `observed_gop_s` on the 3 s-GOP clip.
- `test_single_keyframe_clip_degrades_to_dense_not_none` — assert `source == "dense"` and a non-`None` centre.
- `test_decode_budget_is_bounded` — a 60 s clip must report `frames_decoded <= MOTION_REFINE_FRAME_BUDGET`. Assert on the counter, never on wall time; a timing assertion on CI is a flake generator.
- `test_motion_scan_coarse_flag_is_raised_when_the_stride_is_wide` — the degradation must be visible, and the test is what makes that visibility a contract rather than a courtesy.
- **Existing tests that change:** none in the unit suite change semantics — all three current `test_video_io.py` tests stay valid as written. In the integration suite, `backend/tests/integration/test_pipeline_end_to_end.py::test_stage_5_window_is_absolute_and_covers_the_strike` asserts only that the window *covers* the strike, and passes today despite the 2.6 s error. **Tighten it to centring within ±1.0 s.** That is the assertion that would have failed; leaving it at "covers" leaves the hole open.

##### 5.1.6 Re-validation required

Re-run the full 16-clip corpus recording, per clip: keyframe count, observed GOP, `source`, `frames_decoded`, returned centre, resulting window bounds, and — where a strike is hand-labelled — the signed error. **Acceptance: `abs(centre_s − strike_s) ≤ 1.0 s` on every clip carrying a labelled strike, and motion-scan wall time ≤ 2.0 s on every clip.** Then re-measure Defect 2's gap-gate failure rate on correctly-centred windows *before* touching Stage 7 — the §7.1 numbers were taken at stock settings and will move once this lands, even though, as §7.1 establishes with a monkeypatched true strike time, correct centring does **not** by itself fix Stage 7.

**This fix is first in the ordering.** Defects 1 → 2 → 3: the Stage 7 core-window anchor is computed inside a window Stage 5 places, and the Stage 9 candidate list is computed over frames Stage 7 admits. Fixing them in any other order measures each change against an input that is about to change again.

---

### Stage 6 — MediaPipe Pose extraction (IMPURE)

> **Status: this section is a specification for work that has not been started.** `backend/app/pose/` contains exactly one file — a 54-byte `__init__.py` holding the docstring `"""Pose extraction boundary (MediaPipe is impure)."""`. There is no `extractor.py`, no `video_io.py`, no `sequence.py`, and `extract_keypoints()` has never existed. **Nothing below is a migration and there is no code to change.** The previous version of this section was written against `mediapipe.solutions.pose`, a legacy API that is absent from the installed package and was never executed against it. Do not go hunting for an implementation to fix.
>
> **Installed environment (empirically confirmed, 2026-09-10):** Python 3.13.2, `mediapipe==0.10.35`, `opencv 5.0.0`. `import mediapipe` exposes only `Image`, `ImageFormat`, and `tasks` at top level. `mp.solutions` raises `AttributeError: module 'mediapipe' has no attribute 'solutions'`. Every configuration knob in the old spec — `model_complexity`, `static_image_mode`, `smooth_landmarks`, `min_detection_confidence`, `min_tracking_confidence` — is therefore unavailable as written.

| | |
|---|---|
| **Owner** | `backend/app/pose/extractor.py` (IMPURE), with seam assembly in `backend/app/pose/sequence.py` (PURE) |
| **In** | frame generator from Stage 5 — `Iterable[tuple[timestamp_s: float, frame_rgb: np.ndarray]]`, plus a constructed `PoseExtractor` and `VideoMeta.width_px/height_px` |
| **Out** | `RawPoseSequence` — `landmarks: np.ndarray (T, 33, 4)` float32, `world: np.ndarray (T, 33, 3)` float32, `timestamps_s: np.ndarray (T,)` float64, `detected: np.ndarray (T,)` bool |
| **Fails** | `NO_POSE_DETECTED` (pose found in < 40 % of sampled frames); `INTERNAL_ERROR` if the model asset is missing or its SHA-256 does not match the pinned digest |

**`MULTIPLE_SUBJECTS_SUSPECTED` is removed from this table.** It was never a member of `ErrorCode` (§2.1) so the old table was already inconsistent with the schema, and with `num_poses=1` the condition is *undetectable at this stage by construction* — the API returns at most one person and gives no signal that a second was present. It is replaced by a pure, Stage-7-derived warning string; see **6.6**.

---

#### 6.1 Model bundle — choice, provenance, and how it reaches the container

**Chosen bundle: `pose_landmarker_full.task`.**

| Field | Value |
|---|---|
| Filename | `pose_landmarker_full.task` |
| Download URL | `https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task` |
| Size | **9,398,198 bytes (8.96 MiB)** — confirmed by HTTP HEAD, 2026-09-10 |
| Format | float16 PK-zip task bundle; downloaded, opened, and executed successfully on the dev box |
| Repo location | `backend/app/pose/models/pose_landmarker_full.task` |
| Digest | SHA-256 pinned as a module constant in `app/pose/extractor.py`, verified once at application startup |

All three bundles are live at the same URL pattern (`.../pose_landmarker_<name>/float16/latest/<name>.task`) with confirmed sizes: `lite` 5,777,746 B (5.51 MiB), `full` 9,398,198 B (8.96 MiB), `heavy` 30,664,242 B (29.24 MiB).

**Why `full` and not `lite` or `heavy`.** In the Tasks API there is no `model_complexity` parameter — *the bundle you load **is** the complexity setting*. `full` is the direct successor to the old `model_complexity=1`, so this choice preserves the original intent rather than changing it. `heavy` is 29.24 MiB and roughly double the inference cost, which is unaffordable against the timing decision in 6.3 and would also raise the resident footprint that §1.20's peak-RSS claim depends on. `lite` is tempting on cost, but every metric we report is derived from wrist, elbow, shoulder, and hip landmarks, and contact detection is the localisation of a single velocity peak in the wrist channel — the stage least tolerant of landmark noise. `full` is the measured configuration (19.5 ms/frame, 80/80 frames detected); `lite` is not, and substituting it would invalidate the numbers in 6.3.

**Delivery decision: the bundle is vendored into the repository. It is not fetched at build time and it is not fetched at cold start.**

The deciding fact is in the URL itself: the only published path contains **`/latest/`**. That is a mutable pointer, not a versioned artifact. A build-time `curl` would mean Google can replace our pose model, with no commit in our history, on any deploy — silently changing every landmark, every metric, every score, and every frozen golden fixture in `tests/golden/`. That is an unacceptable supply-chain property for a pipeline whose entire testing strategy rests on deterministic reproduction from frozen `PoseSequence` fixtures. Vendoring converts the model from a live dependency into a reviewed, versioned binary.

Weighed against the alternatives:

- **Fetched at cold start — rejected outright, and Cloud Run makes this *worse*, not better.** On the production tier (`min-instances=1`, §1.20.1) cold starts are rare but not rare enough to ignore: every revision deploy and every instance recycle is one. On the **dev/demo tier deployed today** (`min-instances=0`, §1.20.1a) they are *routine* — every idle-then-request transition is a cold start — which strengthens this rejection rather than weakening it. A 9 MB network fetch in the startup path puts Google Storage availability between a new revision and a healthy instance, and Cloud Run's container filesystem is **in-memory** — the fetched 9 MB would be charged against the instance's memory limit on top of the resident copy MediaPipe loads. Rejected.
- **Fetched at build — rejected for the `/latest/` reason above, and the Cloud Run build model sharpens it.** On Cloud Run the container image *is* the deploy artifact, so a build-time `curl` bakes whatever `/latest/` happened to serve that minute into an otherwise immutable, digest-addressed image — an image that then looks reproducible and is not. Filesystem persistence was never the obstacle on either platform; mutability of the source is.
- **Vendored — accepted.** 8.96 MiB is comfortably under GitHub's 50 MB per-file advisory and the 100 MB hard limit, so no Git LFS is required. The cost is one 9 MB blob permanently in git history and a slightly larger deploy artifact. That is the correct trade for an artifact whose bytes determine every number the product reports.

**On secrets and checksums (CLAUDE.md compliance).** The URL is public and carries no credential, so nothing here is a secret and nothing here belongs in an environment variable for secrecy reasons. The path is nonetheless resolved through `POSE_MODEL_PATH` (a `Settings` field with the vendored path as default) purely so tests and local runs can point at a different bundle — a configuration knob, not a secret.

The SHA-256 **is** pinned, and the reason is specific: a truncated or LFS-pointer-substituted checkout does not make MediaPipe fail loudly. It can produce a landmarker that loads and emits plausible-looking but wrong landmarks, which this pipeline would then turn into a confident coaching report. Verifying the digest once at startup (≈30 ms over 9 MB) converts that entire class of failure into a refusal to boot. The digest constant must be regenerated and committed in the same commit as any model change, and a test asserts the on-disk file matches it.

---

#### 6.2 Legacy → Tasks API mapping

For anyone reading the superseded spec, this is the complete translation:

| Old (`mp.solutions.pose.Pose`) | New (`PoseLandmarkerOptions`) | Note |
|---|---|---|
| `model_complexity=1` | *no such option* — `base_options=BaseOptions(model_asset_path=.../pose_landmarker_full.task)` | Complexity is now the bundle choice (6.1) |
| `static_image_mode=False` | `running_mode=VisionTaskRunningMode.VIDEO` | Also changes the call from `detect()` to `detect_for_video(image, timestamp_ms)` |
| `smooth_landmarks=False` | **no equivalent exists, and none can be synthesised** | This is the load-bearing change. See 6.3. |
| `min_detection_confidence=0.5` | `min_pose_detection_confidence=0.5` | Renamed only |
| `min_tracking_confidence=0.5` | `min_tracking_confidence=0.5` | Unchanged |
| *(did not exist)* | `min_pose_presence_confidence=0.5` | New; no legacy counterpart |
| *(did not exist)* | `num_poses=1` | Previously implicit single-person |
| `enable_segmentation=False` | `output_segmentation_masks=False` | Renamed |
| `mp_pose.Pose(...)` | `PoseLandmarker.create_from_options(options)` | Also `create_from_model_path()` for defaults-only construction |
| `results.pose_landmarks.landmark[i]` | `result.pose_landmarks[0][i]` | Outer index is the person. See 6.5. |

---

#### 6.3 THE DECISION — running mode, and the honest correction to the old purity claim

The previous version of this section rested a load-bearing argument on `smooth_landmarks=False`:

> *"MediaPipe's built-in one-euro filter is an uncontrolled, untestable, stateful transform sitting between the sensor and our math. Disabling it moves 100 % of the filtering into `analysis/smoothing.py` where it is deterministic and unit-tested."*

**That sentence is now false and is deleted.** There is no option that disables the filter in `running_mode=VIDEO`, and the only way to avoid it is to abandon VIDEO mode entirely. Measured on this dev box, same 40 frames, same `full` bundle, tracking `right_wrist` (landmark 16), stateless `detect()` per frame versus sequential `detect_for_video()`:

| Measurement | Value |
|---|---|
| Frames where IMAGE and VIDEO agree exactly | **only frame 0 — 0 of 40** |
| mean abs difference | **0.018775** normalised units (≈ 12 px at 640 px width) |
| max abs difference | 0.071383 |
| Trajectory path length, IMAGE | 1.94165 |
| Trajectory path length, VIDEO | 1.88750 (**2.8 % shorter**) |

A shorter path over identical input is the signature of temporal damping. VIDEO mode reintroduces exactly the transform the old spec set out to exclude.

**Cost of avoiding it** (80 frames, 640×360, dev box):

| mode | construct | per frame | detected | extrapolated to 240 frames |
|---|---|---|---|---|
| VIDEO | 111 ms | **19.5 ms** | 80/80 | **4.7 s** |
| IMAGE | 269 ms | **80.3 ms** | 80/80 | **19.3 s** |

IMAGE is **4.11×** the per-frame cost — a ratio measured on one machine, and platform-independent.

**Calibrating to the deployment target (recomputed for Cloud Run, not carried over from Render).** The superseded figure was a ~3–4× penalty for Render's 0.5 *shared* vCPU. That multiplier described a throttled fraction of a core and does not transfer: on Cloud Run, CPU is an explicit allocation, and §1.20.1 chooses **2 dedicated vCPU**. The remaining gap to this dev box is clock and core count, not contention, so the assumed penalty is **1.5–2.5×** — an assumption, stated as one, and one that further assumes XNNPACK uses both vCPUs.

| | Dev box (measured) | Cloud Run, 2 vCPU (1.5–2.5×) |
|---|---|---|
| VIDEO, per frame | 19.5 ms | **29–49 ms** |
| VIDEO, 240 frames | 4.7 s | **7.0–11.8 s** |
| IMAGE, per frame | 80.3 ms | **120–200 ms** |
| IMAGE, 240 frames | 19.3 s | **29–48 s** |

Both columns move, but the ratio that drives the decision below does not.

---

**DECISION: `running_mode=VisionTaskRunningMode.VIDEO`. We accept MediaPipe's internal filtering and reclassify it as part of the sensor.**

The defence, in order of weight:

1. **IMAGE mode does not fit, and the gap is not marginal — this still holds on the faster Cloud Run allocation.** 29–48 s for Stage 6 alone turns the §1.20 total from 14–27 s into roughly **36–63 s**. Cloud Run's 300 s request timeout and the 180 s heartbeat staleness rule both have room for that, so it is survivable in the narrow sense — and note that the argument can no longer lean on a platform timeout at all. What is not survivable is throughput: one worker at `max_workers=1` inside one pinned instance, with `job_queue_max_depth=4`, means the fourth user in the queue waits **~2.5–4 minutes** instead of ~1–2, and `estimated_seconds` would have to be rewritten from 25 to ~50. Paying 4.11× on the stage that is ~60 % of the pipeline, on a continuously billed single instance, to avoid a filter we can characterise, is still not a good trade.

2. **The old purity argument conflated "a filter" with "impurity", and that was a category error.** The seam is `PoseSequence`. Everything upstream of it is the sensor by definition — including a TFLite convolutional network whose weights we cannot inspect, whose behaviour we cannot unit-test, and which is vastly more of a black box than a one-euro filter. Adding a deterministic low-pass stage *inside* that same black box does not cross any boundary this document defends. Concretely, what survives completely intact:
   - **Determinism.** MediaPipe's VIDEO-mode filter is stateful but deterministic: the same frames with the same timestamps in the same order produce the same landmarks. The golden-fixture strategy (§4.1) is therefore untouched — freeze `PoseSequence` to `.npz` once, and Stages 7–15 remain bit-reproducible in CI with no video, no model, and no network.
   - **Unit-testability of our math.** Every function in `app/analysis/**` still takes arrays in and returns numbers out, still has no I/O, and is still tested on synthetic keypoints. CLAUDE.md's requirement is about *our* math being pure and tested. It is.

   What is genuinely lost, named precisely: (a) we no longer know the total filter response of the chain, because our Savitzky-Golay filter now sits *in series* with an unknown-parameter causal filter; and (b) per-frame idempotence — `detect()` on frame *k* in isolation no longer reproduces the sequence result, so any future debugging tool must replay the whole sequence from the window start. Both are real. Neither is a purity violation.

3. **IMAGE mode is not a cleaner sensor — it is a noisier one, and the path-length number is evidence for VIDEO, not against it.** IMAGE mode re-runs full detection independently on every frame with no tracker, so its frame-to-frame error is uncorrelated jitter. Integrated path length is **upward-biased under zero-mean noise**: for true steps Δp and noise ε, `E[Σ‖Δp + ε‖] > Σ‖Δp‖` by Jensen, strictly, for any non-degenerate ε. So IMAGE's 2.8 % longer trajectory is *consistent with* noise inflation and cannot be read as more faithful motion capture. The honest reading of the 2.8 % gap is that it is some unknown mixture of real damping (bad) and removed jitter (good), and the data as collected does not separate them. This is precisely why item 1 of 6.7 exists.

4. **The one-euro filter's lag is smallest exactly where we need it smallest.** A one-euro filter is adaptive by design: its cutoff rises with observed speed, so smoothing is heaviest when the subject is near-stationary and lightest at peak velocity. Contact (Stage 9) is localised at and just after the racket-hand speed maximum — the lowest-lag region of the filter's response. **This is a mechanism argument, not a measurement: peak time-shift was not measured.** It is the reason the risk in 6.11 is plausible to clear, not evidence that it has been cleared.

**Rejected alternatives, with their arithmetic:**

- **IMAGE mode with a shortened analysis window.** To fit IMAGE into the existing 14–22 s budget we would need ~60–90 frames, i.e. a 2–3 s window. Stage 5's keyframe motion scan locates the swing only to roughly ±1 s, and a swing spans ~1.5 s; a 3 s window would clip the takeback or the follow-through on a routine capture. A 4 s / 120-frame compromise still costs 29–38 s, about 2× the current budget, for a window that is now marginal. Rejected on both counts.
- **IMAGE mode at `analysis_fps=20`.** 160 frames × 240–320 ms = 38–51 s, still 2–3×, and it directly contradicts Stage 5's justification for 30 fps (peak-speed estimation degrades noticeably below 30, and contact resolution drops from ±17 ms to ±25 ms). Rejected.
- **Hybrid: VIDEO for the clip, IMAGE for a narrow band around a coarse contact estimate.** Rejected, and this is the most important rejection because it looks clever. It would splice two different noise regimes into one time series; the central-difference velocity at each splice boundary would be computed across a discontinuity of ~12 px, producing two spurious acceleration spikes flanking exactly the region where Stage 9 hunts for a velocity peak. It would manufacture the artefact it was meant to avoid.
- **VIDEO mode with our SG window left at 7.** Rejected — see the knock-on edit to Stage 7. Two low-pass stages in series over-smooth, and the stage that pays is peak sharpness in the contact channel.

---

#### 6.4 Configuration, fixed

Every field below is a real member of `PoseLandmarkerOptions` in `mediapipe==0.10.35`, confirmed via `dataclasses.fields`. There are no others.

| Option | Value | One-line defence |
|---|---|---|
| `base_options` | `BaseOptions(model_asset_path=<vendored pose_landmarker_full.task>)` | This field *is* the old `model_complexity`; `full` is the measured configuration (6.1). Pass the path, never the bytes — passing bytes would hold a second 9 MB copy resident for the life of the job. |
| `running_mode` | `VisionTaskRunningMode.VIDEO` | Decided in 6.3: 4.11× cheaper than IMAGE and the only mode that fits §1.20. Requires `detect_for_video(image, timestamp_ms)`. |
| `num_poses` | `1` | Raising it runs the landmark model once per detected person, roughly doubling per-frame cost on any frame containing a bystander, and it buys nothing: we would still need a subject-selection rule, which is a new untested classifier in the impure layer. Declined; the multi-subject condition becomes a warning instead (6.6). |
| `min_pose_detection_confidence` | `0.5` | Direct rename of the old `min_detection_confidence`; value carried forward unchanged so the 40 % `NO_POSE_DETECTED` threshold keeps its original meaning. |
| `min_pose_presence_confidence` | `0.5` | New field with no legacy counterpart. Left at the library default deliberately — we have no data on which to tune it, and inventing a value would be a silent, untestable change to detection rate. Revisit only with measurements from the golden clips. |
| `min_tracking_confidence` | `0.5` | Unchanged from the legacy spec. In VIDEO mode this governs when the tracker gives up and re-runs full detection; lowering it would let the tracker coast on a lost subject, which is worse than a gap (Stage 7 interpolates gaps ≤ 3 frames but cannot detect a confidently-wrong track). |
| `output_segmentation_masks` | `False` | We never use a mask. Enabling it allocates a full-resolution float mask per frame — pure waste against a 512 MB container. |
| `result_callback` | `None` (must be) | `LIVE_STREAM` only; setting it with `running_mode=VIDEO` is a configuration error. Our work is batch and synchronous behind a single-worker executor. |

`smooth_landmarks` is absent from this table because **the option does not exist**. See 6.3.

---

#### 6.5 Result parsing

`detect_for_video()` returns a `PoseLandmarkerResult` with three fields: `pose_landmarks: list[list[NormalizedLandmark]]`, `pose_world_landmarks: list[list[Landmark]]`, and `segmentation_masks: Optional[...]` (always `None` for us).

**The outer index is the person, not the landmark.** Access is `result.pose_landmarks[0][i]` for `i` in `range(33)`. The legacy `results.pose_landmarks.landmark[i]` form does not exist and will `AttributeError`.

Non-detection is signalled by an **empty outer list**, not by `None`. The detection test is therefore `len(result.pose_landmarks) > 0` — and it must be applied to `pose_landmarks` and `pose_world_landmarks` together, since a frame is only usable if both are populated.

Both `NormalizedLandmark` and `Landmark` carry `x, y, z, visibility, presence, name`.

**`visibility` survives and remains our channel 4.** **`presence` is new and is deliberately ignored.** Three reasons, and they are all about not changing the seam for free: (a) Stage 7 step 1's validity gate is specified as mean `visibility` ≥ 0.5 over 12 core landmarks, and every threshold downstream of it is calibrated to that quantity; (b) `presence` answers a different question (is this landmark inside the image at all) from `visibility` (is this landmark unoccluded), and we have no threshold for it and no data to derive one; (c) carrying it would require a fifth channel, which is a seam change. If it is ever adopted it must be a `(T, 33, 5)` seam with an explicit pipeline-version bump and regenerated golden fixtures — not an in-place addition.

**Timestamp handling (new requirement, no legacy analogue).** `detect_for_video()` takes an integer millisecond timestamp and requires it to be **strictly increasing** across the sequence. Our timestamps come from real PTS (Stage 5), so the conversion rule is fixed and explicit:

- `timestamp_ms = int(round((timestamp_s - window_start_s) * 1000.0))`, then clamped to `max(previous_ms + 1, timestamp_ms)` to guarantee strict monotonicity. At a 30 fps target the natural spacing is ~33 ms so the clamp should never fire, but Stage 5's duplicate-frame selection on 24 fps sources makes the guarantee worth enforcing rather than assuming.
- **The clamped millisecond value is fed to MediaPipe and then discarded.** `PoseSequence.timestamps_s` stores the original full-precision float PTS, unmodified. Every derivative in Stage 7 step 8 continues to use real Δt. Storing the rounded value would inject up to 0.5 ms of quantisation into every velocity.
- Both the rounding and the clamp are pure functions and live in `app/pose/sequence.py`, not `extractor.py`, so each gets a unit test per CLAUDE.md.

---

#### 6.6 Multiple subjects — resolved as a warning, not an error

With `num_poses=1` the API returns one person and offers no indication that another was present, so `MULTIPLE_SUBJECTS_SUSPECTED` cannot be raised here. It was also never a member of `ErrorCode` (§2.1), so the old Fails row referenced a code that does not exist. **Decision: it is not an `ErrorCode`, it is a warning string, and it is derived in the pure layer.**

The detectable proxy is the symptom that actually matters — MediaPipe silently re-anchoring onto a different person mid-clip, which appears as a discontinuity in the subject's geometry. Stage 7 emits `PoseQuality.flags += ["subject_identity_unstable"]` when either the mid-hip centroid jumps by more than 0.25 TU or the per-frame torso length changes by more than 35 % between consecutive detected frames. This is a pure function of the landmark array, costs nothing, is unit-testable on synthetic keypoints with an injected swap, and fails soft: the analysis still runs, the flag rides along in `PoseQuality`, and the flag depresses Stage 9 confidence rather than killing the job.

---

#### 6.7 Lifecycle, and the memory-ordering requirement (carried forward, confirmed)

The landmarker is created **once per job and closed in a `finally`**, exactly as before. `PoseLandmarker` exposes `close()`, confirmed present on the installed class alongside `detect`, `detect_for_video`, `detect_async`, `create_from_options`, and `create_from_model_path`. `close()` tears down the underlying graph and releases the native TFLite arena, so **the v2 memory-ordering requirement is preserved unchanged**: the extractor must be closed *before* Stage 10 opens its decode pass, so that the graph + arena is released before the ball stage allocates its ~25 MB working set. Peak RSS depends on this ordering (§1.20), and the orchestrator contract (`_collect_pose` fully exits its `with PoseExtractor(...)` block before `_collect_ball` is called, §3) is the mechanism that enforces it.

One deliberate wrapper decision: **`PoseExtractor` owns the context-manager protocol, not MediaPipe's object.** Whether `PoseLandmarker` itself implements `__enter__`/`__exit__` in 0.10.35 was not verified and is irrelevant — our wrapper's `__exit__` calls `PoseLandmarker.close()` unconditionally, which keeps the orchestrator's `with` contract true regardless of what the library offers.

---

#### 6.8 Function signatures — `backend/app/pose/extractor.py`

Contract only; no bodies. **`extract_keypoints` is kept as the public entry point's name**, but its signature changes in one important way: it no longer owns the landmarker's lifecycle. A free function that constructs and closes the landmarker internally would either rebuild the ~100 MB graph per call or hide a process-global singleton, both of which break the once-per-job create/close rule in 6.7 and the memory ordering §1.20 depends on. The lifecycle moves to a class; the function takes the live extractor.

```python
# backend/app/pose/extractor.py  —  IMPURE (ML model inference, filesystem)
from collections.abc import Iterable, Iterator
from pathlib import Path
from types import TracebackType
import numpy as np

POSE_MODEL_FILENAME: str = "pose_landmarker_full.task"
POSE_MODEL_SHA256: str           # pinned digest of the vendored bundle (6.1)
POSE_MODEL_SIZE_BYTES: int = 9_398_198
NUM_LANDMARKS: int = 33

def resolve_model_path(configured: Path | None = None) -> Path: ...
def verify_model_asset(path: Path) -> None: ...
    # Raises RuntimeError on missing file, size mismatch, or digest mismatch.
    # Called once at application startup, never per job.

class PoseExtractor:
    def __init__(
        self,
        model_path: Path,
        *,
        num_poses: int = 1,
        min_pose_detection_confidence: float = 0.5,
        min_pose_presence_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
    ) -> None: ...
    def __enter__(self) -> "PoseExtractor": ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    def close(self) -> None: ...
    def detect_frame(
        self, frame_rgb: np.ndarray, timestamp_ms: int
    ) -> tuple[np.ndarray, np.ndarray, bool]: ...
        # Returns (landmarks (33,4) float32, world (33,3) float32, detected).
        # On non-detection returns zero-filled arrays and False.

def extract_keypoints(
    extractor: PoseExtractor,
    frames: Iterable[tuple[float, np.ndarray]],
    *,
    window_start_s: float,
    width_px: int,
    height_px: int,
    max_frames: int = 240,
) -> RawPoseSequence: ...
    # Drives the generator once, streaming. Accumulates per-frame (33,4)/(33,3)
    # arrays into preallocated (max_frames, ...) buffers, trimmed on exit.
    # Does NOT construct or close the extractor. Does NOT raise NO_POSE_DETECTED —
    # it reports `detected`, and the orchestrator applies the 40 % gate.
```

Pure helpers that belong in `backend/app/pose/sequence.py`, not here, so that each gets its own unit test with synthetic inputs:

```python
# backend/app/pose/sequence.py  —  PURE (numpy only; mediapipe must not be importable)
def frame_timestamps_ms(timestamps_s: np.ndarray, window_start_s: float) -> np.ndarray: ...
    # Rounded, strictly-monotonic int ms for detect_for_video. Never stored on the seam.
def detection_rate(detected: np.ndarray) -> float: ...
def build_pose_sequence(raw: RawPoseSequence, width_px: int, height_px: int) -> PoseSequence: ...
def validate_sequence_invariants(seq: PoseSequence) -> None: ...
```

---

#### 6.9 THE SEAM IS UNCHANGED — verified channel by channel

**`PoseSequence` is bit-for-bit the same contract as in v1 and v2. Nothing about the seam changes.** This was checked explicitly against the installed API rather than assumed:

| `PoseSequence` field | Shape / dtype | Populated from |
|---|---|---|
| `landmarks[t, i, 0]` | float32 | `result.pose_landmarks[0][i].x` — normalized `[0,1]` of image **width** |
| `landmarks[t, i, 1]` | float32 | `result.pose_landmarks[0][i].y` — normalized `[0,1]` of image **height, y grows downward** |
| `landmarks[t, i, 2]` | float32 | `result.pose_landmarks[0][i].z` — relative depth, same scale as `x`, origin at hip midpoint |
| **`landmarks[t, i, 3]`** | float32 | **`result.pose_landmarks[0][i].visibility`** — `[0,1]`. **Channel 4 is `visibility`, confirmed present on `NormalizedLandmark` in 0.10.35.** `presence` is ignored (6.5). |
| `world[t, i, 0:3]` | float32 | `result.pose_world_landmarks[0][i].x/.y/.z` — metres relative to hip midpoint |
| `timestamps_s[t]` | float64 | Stage 5's actual PTS, full precision, **unmodified** — not the millisecond value handed to MediaPipe |
| `detected[t]` | bool | `len(result.pose_landmarks) > 0 and len(result.pose_world_landmarks) > 0` |
| `width_px`, `height_px` | int | `VideoMeta`, post-rotation, post-downscale (640 px long edge) |

Still `(T, 33, 4)` float32, still `(T, 33, 3)` float32, still `(T,)` float64 timestamps, still `(T,)` bool, still 33 landmarks in BlazePose index order, still frozen, still one-line `.npz`-serializable, still no MediaPipe objects on the far side. `world` landmarks remain a cross-check signal only and are never a reported measurement (§5). The golden-fixture strategy in §4.1 needs no change.

The only thing that changes behind the seam is the *numeric content* of `landmarks` — VIDEO mode's filtering is now baked in. That is a measurement change, not a contract change, and it means **existing golden fixtures must be regenerated** once the extractor exists. There were never any to begin with, so nothing is invalidated today.

---

#### 6.10 Budget

| | Dev box (measured) | Cloud Run 2 vCPU (at the assumed 1.5–2.5× factor, §1.20.1) |
|---|---|---|
| Landmarker construction (graph + 9 MB float16 load) | 111 ms | ~0.2–0.3 s, once per job |
| Per frame, VIDEO mode, 640×360, `full` | **19.5 ms** | **~29–49 ms** |
| 240 frames | 4.7 s | **~7.0–11.8 s** |
| Startup digest verification | ~30 ms | ~0.05–0.08 s, once per process (not per job) |

**Stage 6 wall time: 8–13 s**, revised down from 14–22 s — not because anything was re-measured, but because the deployment target changed from a 0.5 shared vCPU to an explicitly allocated 2 vCPU and the scaling assumption was re-derived (6.3). This stage remains the largest single line item at roughly 60 % of total wall time, and §1.20's total becomes **14–27 s**.

**Worst case worth naming:** Stage 5's rotation self-check retries the whole clip at +180° if the detection rate falls below 20 %. That retry is a second full VIDEO pass, so the worst-case Stage 6 is **~16–26 s** and the worst-case job is ~23–41 s. Comfortably inside both the 180 s heartbeat window and Cloud Run's 300 s request timeout, and still reported honestly to the poller, but it should be in §1.20's table rather than implicit.

**Memory — flagged as unverified.** The `~100 MB (graph + TFLite arena)` figure in §1.20 was a v1 estimate for the legacy `solutions` API and has **not** been measured for `PoseLandmarker` with the `full` float16 bundle. A reasoned range is 90–130 MB (9 MB of float16 weights, likely dequantised to float32 working buffers, plus the arena and the graph's intermediate tensors). §1.20's memory-*ordering* requirement is unaffected either way — it is a statement about sequencing, not magnitude — but the absolute number should be replaced with a measured RSS delta as the first thing done once the extractor runs, because the 512 MB headroom calculation depends on it.

---

#### 6.11 The single riskiest assumption in this stage

**Named explicitly: that MediaPipe's VIDEO-mode one-euro filter does not displace the racket-hand velocity peak in time by more than one frame (33 ms).**

Everything downstream leans on this. Stage 9 localises contact as the first frame at or after the speed maximum where speed has dropped ≥ 5 % from peak; Stage 7 step 7 chose Savitzky-Golay specifically because it does not time-shift peaks; Stage 9's `contact_absolute_time_s` feeds the ball measurement window, so a shifted peak mislocates the ball measurement as well as the swing metrics. We have now accepted a causal, adaptive filter upstream of all of it, and **we measured its amplitude effect (2.8 % path-length damping) but not its phase effect.** The mechanism argument in 6.3 item 4 — that one-euro lag is minimised at high speed, which is where contact lives — makes this plausible but does not establish it.

The risk is taken knowingly, and it is bounded by the fact that it is **cheaply falsifiable with the assets we already need**. The acceptance test, which must run before this stage is considered done:

1. On each of the 15 golden clips, extract twice — once in VIDEO mode, once in IMAGE mode — and freeze both `PoseSequence` objects.
2. Run the full pure pipeline on both and compare `ContactDetection.frame_index`. **Acceptance: agreement within ±1 frame on at least 14 of 15 clips.**
3. Also compare `peak_hand_speed_tu_s`. VIDEO is expected to read slightly lower (damping); **acceptance: within 5 %.** A larger gap means the SG window retune (Stage 7) was insufficient and the window must drop to 5 or the filter chain must be reconsidered.
4. Record both results in `tests/golden/` so the comparison is a standing regression, not a one-off.

If step 2 fails, the decision in 6.3 must be reopened — and the fallback is not IMAGE mode at 240 frames (which does not fit), but IMAGE mode at a reduced window with the Stage 5 motion scan tightened to compensate. That is a significantly larger change, which is exactly why this test runs first and runs early.

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

1. **Gate on visibility.** A frame is valid if the mean `visibility` of the 12 core landmarks (shoulders, elbows, wrists, hips, knees, ankles) ≥ 0.5. Invalid frames become gaps. `visibility` is MediaPipe Tasks `NormalizedLandmark.visibility`, channel 4 of the seam; `presence` is deliberately not used (§4.1, Stage 6.5).
1b. **Subject-continuity check — emits `PoseQuality.flags += ["subject_identity_unstable"]`.** Stage 6 runs with `num_poses=1`, so the API returns one person and gives no signal that a second was present; the old `MULTIPLE_SUBJECTS_SUSPECTED` failure is undetectable at the sensor by construction and has been removed from Stage 6's Fails row (it was never an `ErrorCode` member either — §2.1). The detectable symptom is the one that actually matters: MediaPipe silently re-anchoring onto a **different** person mid-clip, which appears as a geometric discontinuity. Flag the clip when, between consecutive *detected* frames, either the mid-hip centroid jumps by more than **0.25 TU** or the per-frame torso length changes by more than **35 %**. This is a pure function of the landmark array, costs nothing, is unit-testable on synthetic keypoints with an injected subject swap, and **fails soft**: the analysis still runs, the flag rides along in `PoseQuality`, and it depresses Stage 9 confidence rather than killing the job.
2. **Interpolate gaps ≤ 3 frames** (100 ms) linearly per coordinate. Longest gap > 3 → `usable=False`.
3. **Aspect correction.** Multiply `x` by `width/height`. Skipping this is the classic silent bug: on a 9:16 portrait clip, one unit of normalized x is 0.56 the pixel distance of one unit of y, and every computed angle is wrong by a view-dependent amount. *(The same trap applies to the calibration points — see Stage 11.1, which is why `scale_from_calibration` operates on true pixels, never on normalized coordinates.)*
4. **Flip y** so up is positive.
5. **Origin shift** to mid-hip: `origin_t = (L_HIP + R_HIP)/2` per frame. All coordinates become body-relative, killing camera pan/translation.
6. **Scale normalization — torso units.** `torso_px = median_t( |mid_shoulder_t − mid_hip_t| )`. Divide all coordinates by it. One TU = one shoulder-to-hip torso length.
   - Why torso length and not shoulder width or height: shoulder width foreshortens catastrophically as the player turns side-on (it can collapse by 60 % during a takeback), and full height requires reliable ankle and head landmarks simultaneously. Torso length is the most view-stable body segment through a tennis swing and stays visible in every phase.
   - The **median over frames**, not per-frame, so the scale is a single constant per clip — a per-frame scale would inject the torso's own foreshortening into every derived velocity.
7. **Smoothing — Savitzky-Golay, `window_length=5`, `polyorder=2`,** applied independently per coordinate along the time axis, with edge handling by polynomial fit rather than reflection.
   - **Why the window dropped from 7 to 5 (changed with the Tasks API migration).** Our SG filter is no longer the only low-pass stage in the chain. MediaPipe's VIDEO-mode one-euro filter now sits ahead of it and **cannot be disabled** — there is no `smooth_landmarks` option in the Tasks API (Stage 6.2, 6.3). Two low-pass stages in series at the original settings over-smooth, and the channel that pays is racket-hand speed, whose peak sharpness *is* the signal Stage 9 consumes. Measured evidence that the upstream filter is real and is damping: identical input, IMAGE vs VIDEO mode, gave a mean landmark difference of 0.018775 normalised units (≈12 px at 640 px) and a **2.8 % shorter wrist trajectory** in VIDEO mode. Window 5 keeps the combined response near the original target instead of stacking on top of it.
   - Why SG over a moving average: a moving average attenuates and *time-shifts* peaks. Contact detection is fundamentally the localization of a velocity peak; an algorithm that moves peaks is disqualified. In v2 a shifted contact frame would also mislocate the ball measurement window, so this choice protects two stages. **This bullet must no longer be read as a claim that the pipeline contains no peak-shifting filter — as of the Tasks API migration it does, upstream, in the sensor, by necessity (Stage 6.3).** SG remains the right choice for the stage we control, and the combined chain's effect on peak *timing* is the explicit open risk, bounded by the ±1-frame IMAGE-vs-VIDEO acceptance test in Stage 6.11.
   - Why SG over zero-phase Butterworth (`filtfilt`), the biomechanics standard: SG is a single scipy call with no filter-design step, no phase concerns by construction, and its window is directly interpretable in frames. Window 5 at 30 fps is a **167 ms support, roughly a 9 Hz corner** — still above the 3–5 Hz fundamental of a swing, and now sitting downstream of MediaPipe's own filtering rather than carrying the whole noise-rejection burden alone.
8. **Derivatives.** Velocity by central difference on smoothed coordinates using **actual Δt** from `timestamps_s`. Acceleration by central difference on velocity. Units: TU/s and TU/s².
9. **Swing direction sign.** `swing_direction_sign ∈ {−1,+1}` = sign of racket-hand x-displacement from takeback-end to contact. Every signed-x metric is multiplied by it, so "forward" means "toward the target" regardless of which way the player faces the camera.

#### 7.1 DEFECT — the gap gate rejects ordinary footage, and the statistic it uses is the wrong statistic (UNIVERSAL; root cause established, fix planned)

> **Status: root-caused on real footage, fix specified here, no code written.** `normalize_sequence` in `backend/app/analysis/normalize.py`, constant `MAX_GAP_FRAMES = 3`.

**The evidence that determines the shape of the fix.** It is tempting to read this as downstream of Defect 1 — a badly centred window would naturally contain more dead time. **It is not.** With `motion_scan_centre_s` monkeypatched to return the true strike time (3.48 s) so that the window correctly becomes `[0.0, 8.0]`, Stage 7 **still fails**: `longest_gap_frames = 98` against a bound of 3. At stock settings, with the miscentred window, it is 156. Fixing Defect 1 moves the number and does not change the verdict.

Failure rate across the corpus: **3 of 6 forehands (gaps 110, 122, 18) and 2 of 7 serves (gaps 156, 88)** — 5 of 13 clips that reach Stage 7 are rejected as unusable before any analysis happens.

**Cause, as observed.** On the ground-truth clip, valid pose exists only over roughly **2.2–4.7 s**. Before that the player is small in frame or turned away during the pre-toss routine; after it there are ~5 s of post-swing recede and off-frame walking. The 8 s analysis window is sized to guarantee *swing context* — enough lead-in for a takeback and enough tail for a follow-through — and nothing about that sizing obliges the clip to hold a detectable pose across all of it. A whole-window rule of "no gap longer than 3 frames" has essentially **zero tolerance for the dead time that ordinary single-camera footage contains by construction**.

**The root cause is that one constant is doing two incompatible jobs.**

- As an **interpolation limit**, `MAX_GAP_FRAMES = 3` is correct and must not be relaxed. Linear interpolation across more than ~100 ms of a swing fabricates a trajectory, and the fabricated trajectory then flows into every velocity, every phase boundary and every metric the product sells. Step 2 of Stage 7 is right to refuse it.
- As a **usability verdict**, it is measuring the wrong thing. `longest_gap` is a global worst-case over a window most of which is irrelevant to the analysis. A 6 s hole in the tail of the window, after the player has walked out of frame, says nothing whatever about whether the swing is measurable.

**Is `longest_gap` even the right statistic?** No — and this is the substantive architectural finding, not the threshold. What Stages 9, 12 and 13 actually consume is *a contiguous, densely-tracked run of frames that contains the swing*. The question Stage 7 should answer is therefore **"is there such a run, and does it cover the contact region?"** — a question about **coverage near contact**, not about the worst gap anywhere in the window. A clip can have a perfect 2.5 s run through the strike and a catastrophic `longest_gap`, and that clip is fully analysable. The inverse is also true and matters more: a clip with a good-looking `longest_gap` of 3 but valid frames scattered in alternating holes across the window is *not* analysable for velocity, and the current gate passes it.

**Why this is not a threshold change.** The distance between 98 and 3 is two orders of magnitude. That gap is the tell: no single value of `MAX_GAP_FRAMES` satisfies both roles, because the constant is serving two roles. Raising it to 99 to pass this clip would (a) authorise linear interpolation across 3.3 s of missing swing — fabricating precisely the trajectory the pipeline exists to measure, in direct violation of CLAUDE.md's anti-fabrication stance — and (b) still be a whole-window worst-case statistic, which the next clip with a 6 s dead stretch defeats anyway. **Retuning the constant to pass one clip is the explicit anti-pattern here**, and §9.1 round 3 is this document's standing precedent for what retuning a threshold on this data buys: byte-identical results across a sweep.

##### 7.1.1 The ordering problem, stated before the fix

Anchoring usability to "where the swing is" requires knowing where the swing is. Swing location is Stage 9's output; Stage 9 consumes a `NormalizedSequence`, which is Stage 7's output, which is what the verdict gates. That is a genuine circular dependency and it has to be broken explicitly rather than stepped around. Three ways out were considered:

1. **Run Stage 9 first, on raw landmarks.** Rejected. Stage 9 consumes normalised, origin-shifted, torso-scaled, smoothed coordinates and a `swing_direction_sign`, all of which are Stage 7 products. Running it on raw data is not "Stage 9 early," it is a different algorithm with no validation behind it.
2. **Emit the sequence unconditionally and move the verdict to the orchestrator, after Stage 9.** Architecturally honest, and it is the runner-up. Rejected as primary for two reasons: it moves a `POSE_QUALITY_TOO_LOW` decision out of the stage that owns `PoseQuality`, and it requires Stage 9 to run over a sequence containing 98-frame holes, where `forward_swing_window_start` and the plateau walk of step 4 traverse absent or fabricated data. Letting the detector run on data the quality stage has already judged unusable inverts the purpose of having a quality stage.
3. **Anchor on a cheap signal Stage 7 can compute itself. — RECOMMENDED.** Stage 7 already computes per-frame validity in step 1. The anchor needs to be good to ±0.5 s, not to ±1 frame, and a signal that coarse does not need contact detection.

##### 7.1.2 Proposed change — a two-tier verdict

**Tier 1 — interpolation (unchanged).** Gaps of ≤ 3 frames are interpolated linearly, exactly as today. Gaps longer than 3 frames are **not** interpolated; those frames stay marked invalid and are carried forward as a mask rather than being filled. This is a correctness rule about fabrication and it is not being relaxed — only *decoupled* from the verdict.

**Tier 2 — usability, judged over a core sub-window anchored to the swing.**

The anchor is computed without Stage 9:

- `anchor_index` = the centre of the **longest contiguous run of valid frames** in the window. If that run is longer than the core window, refine within it using the frame of maximum summed two-wrist displacement from mid-hip — a scale-free proxy that needs neither handedness nor smoothing. **This is a coarse anchor and must never be described or reused as contact detection.** It exists to place a 2 s box, and it is allowed to be half a second wrong.

Proposed signatures:

```python
# backend/app/analysis/normalize.py  —  PURE

def longest_valid_run(valid: np.ndarray) -> tuple[int, int]: ...

def core_window_indices(
    valid: np.ndarray,
    timestamps_s: np.ndarray,
    xy: np.ndarray,
    *,
    core_window_s: float = CORE_WINDOW_S,
) -> tuple[int, int]: ...

def core_coverage(valid: np.ndarray, core: tuple[int, int]) -> float: ...
```

Proposed constants, each with its justification rather than its provenance:

| Constant | Proposed | Justification |
|---|---|---|
| `CORE_WINDOW_S` | **2.0 s** (±1.0 s about the anchor) | A swing from takeback-end through follow-through runs ~0.6–1.2 s, and Stage 12 needs all four phases plus margin. 2.0 s is 60 frames at 30 fps — comfortably more than the longest phase structure observed, with room for the accepted Stage 9 `+1` residual. It is also shorter than the ~2.5 s of valid pose measured on the failing ground-truth clip, so that clip passes; **that is a consequence of the sizing, not the reason for it.** |
| `MIN_CORE_COVERAGE` | **0.90** | 10 % of 60 frames is 6 frames. Because Tier 1 still refuses any single gap over 3, those 6 lost frames must arrive as at least two separate short gaps to get this far; a single 6-frame hole is already caught by `MAX_CORE_GAP_FRAMES` below. The two rules are deliberately redundant in opposite directions — one bounds total loss, the other bounds concentrated loss. |
| `MAX_CORE_GAP_FRAMES` | **3** | Unchanged bound, newly *scoped*. Inside the core, a gap over 3 frames is still disqualifying, for exactly the fabrication reason Tier 1 gives. Outside the core it is recorded, flagged, and tolerated. |

**Whole-window statistics survive as diagnostics, not as the verdict.** `PoseQuality.longest_gap_frames` keeps its present meaning and stays in the response — it is genuinely informative and existing consumers read it. Add alongside it: `core_window_start_s`, `core_window_end_s`, `core_coverage_fraction`, `longest_core_gap_frames`, and a `dead_time_outside_core` flag raised when the whole-window gap exceeds the bound but the core passes. That flag is what makes the new tolerance auditable: a reviewer can see that the clip was admitted *despite* a 98-frame hole, and where the hole was.

**Contract consequence for downstream stages.** Frames outside the core, and unfilled gaps inside the window, are not trustworthy and must be marked as such rather than silently looking like data. Stage 7 already has the `valid` array; it should be carried on `NormalizedSequence` so Stages 9, 12 and 13 can compose it. Stage 9 already does exactly this kind of composition — `racket_wrist_reliable` ANDs per-landmark visibility across `i-1, i, i+1` — so the mask fits the existing discipline rather than introducing a new one. See `docs/PIPELINE_STAGES_12_14_15.md` §A.5 and §A.6, which already specify how Stage 12 degrades when the data is too thin to segment; **those rules should key off this mask rather than re-deriving validity**, and this section deliberately does not restate them.

**One thing this fix explicitly does not do:** it does not fix Defect 3. On the ground-truth clip the wrongly selected source frame 71 sits at ~2.84 s, inside the 2.2–4.7 s valid run and inside any core window anchored near the strike. Restricting Stage 9's peak search to the core would not have excluded it. The two defects are independent and must be validated independently.

##### 7.1.3 Rejected alternatives

| Alternative | Why rejected |
|---|---|
| Raise `MAX_GAP_FRAMES` to ~120 | Authorises interpolation across seconds of absent swing — fabrication, and the exact thing CLAUDE.md forbids. Still a whole-window worst-case statistic, so the next clip with a longer dead stretch fails anyway. Passes one clip; fixes nothing. |
| Keep a whole-window gate but switch it to a **valid-frame fraction** (e.g. ≥ 60 % valid) | Coverage without contiguity is not the signal. A clip 60 % valid in alternating holes is unusable for central-difference velocity; the failing ground-truth clip is ~30 % valid, entirely contiguous, and perfectly analysable. This inverts the correct verdict on both. |
| Shrink the analysis window to 4 s to reduce dead time | Does not remove dead time, only re-centres it — and it makes the verdict depend on Defect 1's centring being correct, trading one defect's fix for exposure to another's. Also costs Stage 12 its pre-takeback context. |
| Trim the window down to the longest valid run and analyse only that | Close to the recommendation, and the considered runner-up. Rejected as primary because it makes the analysed window length clip-dependent, which changes the frame counts that every downstream metric, phase boundary and test currently assumes. Anchoring the *verdict* is far cheaper than re-cutting the *window*. Worth revisiting if core-window tuning proves fragile. |
| Move the verdict to the orchestrator after Stage 9 | Option (2) in §7.1.1 — takes the `PoseQuality` decision away from the stage that owns it and runs the detector over holes. |

##### 7.1.4 Test strategy

**Why the unit suite is green against a defect that rejects 5 of 13 real clips:** the synthetic fixtures in `backend/tests/unit/analysis/synthetic.py` and `synthetic_swing.py` produce sequences that are valid end to end. Real footage is valid in the middle and absent at both ends. The synthetic data cannot express the shape of the failure, so no amount of unit testing was ever going to find it. **Fixing the fixture generator is part of this change, not a follow-up.**

- **New fixture builder** parameterised by `(clip_len_s, valid_run_start_s, valid_run_len_s)`, emitting a `PoseSequence` with invalid frames outside the run. Every test below is built on it.
- **`backend/tests/unit/analysis/test_normalize.py::test_normalize_sequence_interpolates_short_gaps_and_rejects_long_ones` MUST CHANGE.** It encodes the whole-window rule directly and will fail by design. Split it in two, so that the surviving half is not weakened by the new half: `test_interpolation_still_refuses_gaps_longer_than_three_frames` (Tier 1, semantics unchanged, asserts that long gaps are left unfilled rather than interpolated) and `test_a_long_gap_outside_the_core_window_does_not_make_the_clip_unusable` (Tier 2, new).
- `test_the_anchor_picks_the_longest_valid_run_when_there_are_two` — two runs of different lengths, assert the core lands on the longer.
- `test_the_core_window_clamps_at_the_sequence_ends` — anchor near frame 0; the core must not run off the array and must not silently shrink below a floor without saying so.
- `test_a_four_frame_hole_inside_the_core_makes_the_clip_unusable` — the rule that must still bite.
- `test_the_same_hole_three_seconds_outside_the_core_is_tolerated_and_flagged` — assert `usable is True` **and** `dead_time_outside_core` in `flags`. Tolerating silently would be a new silent failure replacing an old loud one.
- `test_core_coverage_below_the_minimum_is_unusable` — scattered short gaps inside the core, each individually legal under Tier 1.
- `backend/tests/unit/analysis/test_normalize.py::test_normalize_sequence_flags_a_subject_swap_but_stays_usable` is **unaffected** and must stay passing — it is the existing precedent for "flag, do not fail," and this change extends that pattern rather than replacing it.
- **Purity:** `normalize_sequence` and all new helpers stay pure and I/O-free. `backend/tests/unit/analysis/test_analysis_purity.py` covers this and needs no change, but the new helpers must be inside its scope.
- **Integration:** `backend/tests/integration/test_pipeline_end_to_end.py::test_default_settings_fail_on_pose_quality` asserts today's failure at stock settings and **must change** — after this fix the ground-truth clip should reach Stage 9. Re-point it at a fixture that genuinely *is* unusable (a hole through the contact region) rather than deleting it; the "clip is honestly rejected" path still needs a test.

##### 7.1.5 Re-validation required

**Land Defect 1 first.** The core anchor is computed inside a window Stage 5 places, so measuring this change against miscentred windows measures the wrong thing.

Re-run the full 16-clip corpus recording, per clip: window bounds, valid-run extents, anchor time, core window, `core_coverage_fraction`, `longest_core_gap_frames`, whole-window `longest_gap_frames`, and the verdict. Acceptance, both halves required:

1. **The 5 currently-failing clips reach Stage 9** (forehands at gaps 110, 122, 18; serves at 156, 88).
2. **No clip that currently produces a usable sequence becomes unusable.**

And the criterion that is *not* acceptance: **a higher pass rate is not the goal.** A clip with a genuine 4-frame hole through contact must still be rejected, and the corpus run must include at least one such case — synthetic if the corpus does not contain one — to demonstrate the gate still has teeth. A gate that passes everything is not a fix, it is a deletion.

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

> **VALIDATION GAP -- Stage 8's own discrimination does not work on real footage.** Two-handed backhands are no longer the *only* failure case. On real clips (§9.1) confidence stayed in **0.05-0.36** on every clip tested, never reaching the 0.5 override threshold, and the detected hand was **wrong on more than half of a 7-clip batch** of visually-confirmed right-handed players -- after the visibility fix, not before it. The hint path is confirmed working (it correctly overrode wrong detections on `serve_01` and `serve_04`), so the shipped system is correct in practice; but the hint is currently the **primary** signal, not the fallback, and `source="detected"` with `confidence >= 0.5` is not a state real footage has yet produced. A request with no hint, or with a wrong profile hint, has no safety net and fails **silently** -- see the Stage 13 note on handedness-sensitive metrics.

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
2. Find the global maximum `t_peak` **over visibility-reliable frames only**. A frame is reliable when the racket wrist's own `visibility` channel clears threshold at `i-1`, `i` and `i+1` -- all three, because velocity is a central difference and a single unseen neighbour corrupts it. Stage 7 step 1 gates on the *mean* visibility of 12 landmarks, which one occluded wrist cannot move far, so a frame can pass Stage 7 while that specific wrist is hallucinated; the resulting one-frame position jump is a velocity spike taller than any real swing in the clip and a bare `argmax` picks it every time. If no frame is reliable the search falls back to the unrestricted `argmax` and the motion gates in step 5 are what say so.
3. Restrict to the **forward-swing window**: frames after the last local minimum of forward displacement preceding `t_peak`.
4. **Contact = the last frame of the peak's sustained-speed plateau.** Walk forward from `t_peak` while `s` stays at or above **45 %** of the peak (`SUSTAINED_SPEED_FRACTION`). If that run reaches **2** frames (`MIN_SUSTAINED_RUN_FRAMES`) it is a genuine plateau and its **last** frame is returned; otherwise the peak was a one-frame spike and the peak itself is returned. The walk is bounded at **4** frames (`MAX_SUSTAINED_RUN_FRAMES`). Rationale: in a well-struck ball the hand is still accelerating or at plateau *into* the ball, and the deceleration onset is the frame the hand stops travelling at *swing* speed -- not the first frame it dips at all. Those coincide only on a smooth curve, and the measured racket-hand curve is not smooth: it routinely loses **15-45 %** of its height in the single frame after a real peak, because Stage 7's Savitzky-Golay window of 5 bleeds a sharp spike into its immediate neighbours by construction. A "dropped by >= X %" rule therefore fires at `t_peak + 1` for every X up to ~50 %, which made the old 5 % rule, in practice, `return peak + 1` -- a sweep from 1.5 % to 10 % produced byte-identical detections on all 7 real test clips, and a measured 2-frame late bias (§9.1). The `min_run` fallback is why a one-frame spike no longer overshoots; the `max_run` bound is why a broad, slowly-decaying peak cannot drag the answer into the follow-through (at 25-30 fps the whole impact-and-brake event is over inside ~150 ms). `max_run` was inert on every real clip measured -- longest observed plateau: 3. If the clip ends before the hand decelerates the last frame is returned, which then trips `contact_near_clip_end` and depresses confidence rather than pretending to know.
5. Sanity gates, each of which lowers confidence rather than rejecting. The full flag vocabulary is exactly: `wrist_behind_mid_hip` (wrist must be forward of mid-hip, `x·swing_direction_sign > 0`; ×0.6), `arm_not_extended` (arm-to-shoulder reach below 60 % of this clip's own observed range; ×0.8), `contact_near_clip_end` (within 4 frames of either clip end; ×0.7), `subject_identity_unstable` (propagated from `PoseQuality.flags`; ×0.7), `motion_not_sustained` (×0.3, `MOTION_ABSENT_PENALTY`), and `racket_wrist_unobserved` (raised when the reliable-frame share is 0). **`sequence_unusable`** is the single flag on the degenerate return (fewer than 3 frames, or any internal failure -- Stage 9 cannot raise).
5b. **Motion presence.** The four gates above test static *geometry* only -- where the wrist is, how extended the arm is -- so a motionless player holding a ball satisfies every one of them; this was confirmed on real footage, where a stationary subject scored the highest confidence in its batch (0.766) with zero gates firing. `motion_not_sustained` demands positive, *local* evidence that a swing happened, evaluated at `t_peak` (the leading edge of the event, since a hard genuine swing may legitimately halve in the next frame). Three independent ways to fail, because a fabricated speed event can look healthy on any one alone: (a) the peak does not belong to a run of >= **2** frames (`MIN_MOTION_RUN_FRAMES`) that are both reliable and at >= **50 %** of peak (`MOTION_SPEED_FRACTION`); (b) fewer than **50 %** (`MIN_APPROACH_COVERAGE`) of the **6** frames before the peak (`APPROACH_WINDOW_FRAMES`) were already reliable and above **15 %** of peak (`APPROACH_SPEED_FRACTION`) -- a real swing ramps in, a tracking snap out of a static trophy-hold does not; (c) more than **10 %** (`MAX_HELD_FRACTION`) of the reliable frames within **40** frames (`MOTION_NEIGHBOURHOOD_FRAMES`) have *exactly* zero speed, meaning the coordinate was held (an interpolated gap or a carried-forward detection) rather than tracked. Test (a) alone is self-referential -- it measures the peak against a fraction of itself -- and on the measured batch it is anti-correlated with correctness, which is why (b) and (c) exist and why `MIN_MOTION_RUN_FRAMES` was not simply raised. All comparisons are deliberately biased toward silence: a gate that fires on a good swing costs more than one that stays quiet on a bad one, because below 0.35 the orchestrator downgrades to `partial` and refuses ball speed.
6. `prominence_ratio` = peak speed ÷ second-highest well-separated peak (separation >= 5 frames; capped at 10.0; rivals are likewise restricted to visibility-reliable frames, since an occlusion spike is not a rival swing). Confidence is a monotone ramp in this ratio between 1.1 and 2.5 into `[0.35, 1.0]`, then **multiplied by the observed fraction** (the share of frames in which the racket-hand velocity was trustworthy at all -- every unobserved frame is a frame that could have held the real contact), then multiplied by one penalty per sanity gate that fired.

`confidence` is surfaced in the response and gates the feedback tone (Stage 16).

**New field: `contact_absolute_time_s`.** `time_s` is measured from `analysis_window_start_s`; Stage 10 seeks into the *original* file and therefore needs an absolute PTS. `contact_absolute_time_s = analysis_window_start_s + time_s` is computed once, here, and carried explicitly. On a 60 s clip with an 8 s window this is the difference between measuring the right 250 ms and measuring nothing — an off-by-a-window bug that would be invisible on the short test clips a developer would naturally use.

#### 9.1 Real-footage validation -- first pass, and what it cost

**This is the first time Stage 9 has been run against real (non-synthetic) footage.** Everything above this subsection had previously been validated only against generated keypoint data, which is smooth, fully visible, and always contains exactly one swing. Three rounds against real clips each found and fixed a genuine bug. Each round's fix was confirmed by **direct visual inspection of the video frames** -- racket-at-ball-strings versus follow-through position, on multiple clips -- not merely by numeric agreement with a reported index.

**Round 1 -- the `visibility` channel was never read.** Neither Stage 8 nor Stage 9 consulted the per-landmark `visibility` that `NormalizedSequence` already carried. Stage 7's frame-level gate only checks *mean* visibility over 12 core landmarks, so a frame passes overall while one specific wrist is individually occluded, and that hallucinated position then received full weight in Stage 8's path-length integration and Stage 9's central-difference velocity. Two distinct failures followed: Stage 8's path-length/radius comparison could be *inverted* by an occluded wrist's spurious displacement, and Stage 9's `argmax` preferred a tracking-glitch spike over the real, lower-speed swing peak. Fixed by introducing per-frame racket-wrist reliability (visible at `i-1`, `i`, `i+1`), restricting the peak search and the prominence rivals to reliable frames, scaling confidence by the observed fraction, and adding optional visibility masking to Stage 8's `path_length` and `peak_radial_distance` with confidence scaled by the worse-observed wrist's coverage.

**Round 2 -- every sanity gate was static geometry.** Wrist-forward-of-hip and arm-extension-relative-to-clip-range are both satisfiable by a motionless player holding a ball. Confirmed on real footage: a stationary subject scored confidence **0.766**, the highest in that batch, with **zero** gates firing, because a single-frame occlusion-driven velocity spike was read as a swing peak. Fixed by adding the motion-presence check described in step 5b, plus `racket_wrist_unobserved` for the case where nothing was observable at all.

**Round 3 -- the deceleration-onset rule was doing nothing.** With rounds 1-2 in place, testing against **5 hand-labelled ground-truth contact events** (Pexels serve footage, ball-on-strings frame, ±1 frame human labelling precision) showed a systematic bias: the old step 4 landed exactly **2 frames late in 4 of 5 cases and 2 frames early in the 5th**. Root cause, empirically confirmed: because real racket-hand speed loses 15-45 % of its height in the frame after any peak, the ">= 5 % below peak" condition was trivially true at `peak + 1` regardless of the threshold's configured value -- a sweep from 1.5 % to 10 % produced **byte-identical** detections on all 7 test clips. The function was, in practice, `return peak + 1`. Replaced with the sustained-plateau walk in step 4 above.

**Accepted residual -- a consistent +1 frame, cause unresolved.** After round 3 the early case corrected to exactly 0 and all four late cases converged to **+1**: detection is one frame after hand-labelled truth on **4 of 5** real events, exact on the 5th. Whether this is a genuine small algorithmic bias or noise inside the ±1-frame precision of the human labels **is not established**, and was explicitly flagged as unresolved by the engineer who found it. **This residual is accepted and documented, not solved and not silently absorbed.** If a future round establishes tighter ground truth and the +1 persists, the recommended next step is **sub-frame interpolation of the speed peak -- not another threshold retune**; round 3 is the standing evidence that retuning a threshold on this curve changes nothing. Downstream consumers of `frame_index` should treat it as carrying a possible systematic `+1` in addition to ordinary noise (see the Stage 12/13 notes on windows narrower than ~4 frames).

**Still open -- Stage 8's independent discrimination is not validated and is weak on real footage.** Even after the round-1 visibility fix, Stage 8's path-length/radial-distance separation stayed at **confidence 0.05-0.36 on every real clip tested** -- never once reaching the 0.5 hint-override threshold -- and it selected the **wrong hand on more than half** of a 7-clip batch of visually-confirmed right-handed players. The system works in practice **only** because Stage 3/the client always supplies `handedness_hint` and Stage 8's design already gives the hint priority below 0.5 confidence. That fallback path *is* validated as functioning: on `serve_01` and `serve_04` the wrong-handed detection was correctly overridden by the hint. **What is not validated is Stage 8's hint-free discrimination.** A user who omits a hint, or whose profile hint is itself wrong, has **no safety net** -- the wrong wrist is then fed to Stage 9's speed curve, to `swing_direction_sign`, and to every handedness-sensitive Stage 13 metric, all of which will produce normal-looking numbers. Treat a present, correct `handedness_hint` as a **precondition** of trusting Stages 9 and 13, not as a convenience.

#### 9.2 DEFECT — Stage 9 computes a candidate-rejection signal and then ignores it (fix planned; severity is serve-specific)

> **Status: diagnosed against hand-labelled ground truth, fix specified here, no code written.** `backend/app/analysis/contact.py`, the `GATE_PENALTIES` application at the end of `detect_contact_frame`.

**The observed failure.** On the ground-truth clip Stage 9 returns **source frame 71 with confidence 0.043**, carrying the flags `arm_not_extended`, `wrist_behind_mid_hip` and `motion_not_sustained` — and returns it anyway. Frame 71 is visually confirmed as mid-raise, with the racket down near the hip. True contact is **frame 87**, at full extension, striking the ball. The system computed three independent signals saying "this cannot be a contact," recorded all three in the response, and used them only to make the wrong answer's confidence small.

**Root cause, in two layers. Both need stating; neither alone explains the failure.**

**(a) Architectural — the gates are post-hoc discounts on a decision already made.** `peak_speed_index` returns a single `argmax`; `find_deceleration_onset` walks it to a contact index; *then* step 5's gates run, and `confidence *= GATE_PENALTIES[flag]` applies. There is only ever one candidate, so a physically implausible one cannot lose — there is nothing for it to lose to. The gates are, structurally, a commentary track on a fixed decision. This is the layer the fix addresses.

**(b) Kinematic — why the `argmax` is wrong on serves in the first place.** The wrist-speed proxy peaks **during the explosive arm drive, roughly 0.6 s before contact.** The racket head's final acceleration into the ball comes from forearm pronation and wrist snap — rotation *about* the wrist — at a moment when the wrist's own translational speed has already fallen. Measured on this clip: **~9–11 units/s at frame 71 versus ~2.6–3.9 at frames 86–88.** The selected frame is not a tracking glitch and not a noise artefact; it is the true global maximum of the quantity Stage 9 measures. The quantity is a proxy, and on a serve the proxy and the target diverge by design.

On forehands, wrist and racket travel together through the hitting zone, so the proxy lands close to contact and this divergence is small. **Severity is therefore serve-specific — but the supported claim is "serves need distinct handling," not "groundstrokes are fine."** Only 3 forehands reached Stage 9 at all, and only 1 of those was cleanly confirmable against a hand label. That is not enough to clear groundstrokes, and this section does not clear them.

This layer is **not** fixed by the change below. Candidate filtering removes an implausible winner; it does not make the wrist-speed proxy a good estimator of racket-head timing on a serve. A proper fix for (b) is a racket-head kinematic proxy — a wrist-to-elbow segment orientation rate, or an elbow-anchored extension velocity — and that is a separate, larger piece of work with its own validation. **Filtering is the correct first move because it converts a confidently wrong answer into either a right one or an honest failure**, which is the property (b) currently lacks.

##### 9.2.1 Proposed change — promote two gates from post-selection discount to pre-selection filter

Proposed signatures:

```python
# backend/app/analysis/contact.py  —  PURE

#: Gates that EXCLUDE a candidate before selection.
FILTER_GATES: Final[frozenset[str]] = frozenset({"wrist_behind_mid_hip", "arm_not_extended"})

def candidate_peak_indices(
    speed: np.ndarray,
    reliable: np.ndarray,
    *,
    window_start: int,
    separation: int = PEAK_SEPARATION_FRAMES,
    max_candidates: int = MAX_CANDIDATES,
) -> list[int]: ...

def candidate_rejection_flags(
    seq: NormalizedSequence,
    handedness: Handedness,
    index: int,
) -> list[str]: ...

def select_contact_index(
    seq: NormalizedSequence,
    handedness: Handedness,
    speed: np.ndarray,
    reliable: np.ndarray,
    *,
    window_start: int,
) -> tuple[int | None, list[str], list[tuple[int, list[str]]]]: ...
```

The third element of `select_contact_index`'s return is the **audit trail**: every candidate considered and why it was rejected. It is not decoration — the re-validation in §9.2.5 cannot be performed without it, and a filter whose rejections are invisible is how a rejection-all rate becomes a mystery instead of a measurement.

Algorithm:

1. **Enumerate candidates instead of taking one `argmax`.** All local maxima of `s(t)` over visibility-reliable frames within the forward-swing window, separated by at least `PEAK_SEPARATION_FRAMES` frames, ordered by descending speed, capped at `MAX_CANDIDATES` (proposed **5**). **Reuse the separation constant that step 6's prominence calculation already defines (≥ 5 frames)** — one constant, not two with the same meaning drifting apart.
2. **Walk each candidate through the existing plateau rule of step 4** to get the index that candidate would actually return. The filter is applied to the *returned* frame, not the peak. Applying it to the peak would check a frame the function is not going to return, and the plateau walk moves the answer by up to 4 frames.
3. **Apply the filter set.** The first candidate passing every gate in `FILTER_GATES` wins. Deduplicate: two candidates whose plateau walks converge on the same index are one candidate.
4. **The remaining gates continue to discount confidence exactly as today**, applied to the winner. No change to `GATE_PENALTIES` values.
5. **If no candidate passes**, return the degenerate detection described in §9.2.3.

##### 9.2.2 Which gates filter, which only penalise — and why

| Flag | Role | Reasoning |
|---|---|---|
| `wrist_behind_mid_hip` | **FILTER** | `x · swing_direction_sign > 0` is a statement about physical possibility, not about quality: a ball cannot be struck forward with the hand behind the hips. It is also the gate with the least threshold content in it — it compares against zero, so there is no tuned constant to be wrong about. |
| `arm_not_extended` | **FILTER, with a guard** | Clip-relative (60 % of the clip's own observed reach range), which makes it robust to body size and camera distance but *dependent on the clip containing a genuine extension*. On a mishit or a practice swing the range collapses and "60 % of nothing" is meaningless. **Keep `ARM_EXTENSION_MIN_RATIO = 0.60` for filtering, but require the clip's observed reach range to exceed an absolute floor (`ARM_RANGE_MIN_TU`, order 0.15 TU — roughly a tenth of an arm length, to be SET FROM CORPUS MEASUREMENT, not guessed) before the filter is permitted to reject.** Below that floor it degrades to a penalty. A filter that rejects on a degenerate denominator converts a low-confidence answer into a job failure for zero information gain. |
| `contact_near_clip_end` | **PENALISE only** | A real contact genuinely can occur within 4 frames of a clip edge — the user trimmed the clip, or started recording late. Filtering it would reject *correct* answers, which is the one failure mode a filter must not have. Its ×0.7 discount already says the right thing. |
| `motion_not_sustained` | **PENALISE only, for now** | The most powerful gate (×0.3) and the most composite — three independent sub-tests (step 5b a/b/c), of which **(a) is self-referential and was measured anti-correlated with correctness** on the real batch. Promoting a composite whose strongest component is known to be anti-correlated is how you build a filter that rejects good swings. Revisit only after each sub-test's precision has been measured *separately* against labelled contacts. Noted honestly: this flag fired on frame 71 and would have helped here. That is one clip, and it is not enough to license it. |
| `subject_identity_unstable` | **PENALISE** | A property of the *clip*, not of the *candidate*. It takes the same value for every candidate and therefore has no discriminating power by construction — filtering on it would reject all candidates or none. |
| `racket_wrist_unobserved` | **PENALISE** | Already the terminal case; nothing was observable, so there is nothing to filter between. |

##### 9.2.3 What happens when the filter rejects every candidate

**The recommendation is to return nothing honestly.** `CONTACT_NOT_FOUND` already exists as an `ErrorCode` member (`backend/app/models/enums.py:188`) and is currently unreachable; this change is what reaches it.

**Say the cost plainly: this raises the job-failure rate.** On the current corpus it will convert some silently-wrong analyses into visible failures, and a user who today gets a plausible-looking scorecard will instead get an error. That is the correct trade, and the reason is specific rather than a general preference for honesty: a wrong contact frame does not stay local. It sets Stage 12's phase boundaries, therefore every Stage 13 metric, therefore Stage 14's shot type, Stage 15's score and Stage 16's coaching text — all of which come out looking entirely normal and are entirely wrong. A `contact_not_found` is locally diagnosable and locally honest. A wrong frame is neither.

Two constraints on how it is returned:

1. **Stage 9 still cannot raise.** That is its contract (the Fails row above) and it is not being changed. The degenerate return is a `ContactDetection` with `confidence = 0.0` and a new sanity flag `contact_not_found`, distinct from the existing `sequence_unusable` (which means "fewer than 3 frames or internal failure," a different condition). **The orchestrator** maps that flag to `ErrorCode.CONTACT_NOT_FOUND`. That mapping belongs in `docs/PIPELINE_STAGES_12_14_15.md` §E.2 (`ErrorCode` per stage) and interacts with §E.3 (skippable stages) and §E.4 (`complete` vs `partial`); **it must be amended there, not duplicated here.**
2. **Pre-commit to a rate before measuring it.** Run the filter over the 16-clip corpus and record the all-rejected rate. **If it exceeds ~20 %, the filter is too strict and `ARM_RANGE_MIN_TU` is the first thing to revisit, not the last.** Writing the number down in advance is what stops it being rationalised afterwards.

##### 9.2.4 Interaction with the accepted `+1` frame residual — the binding design constraint

§9.1 records an accepted, unresolved **`+1` frame** residual: detection lands one frame after hand-labelled truth on 4 of 5 real events. The filter runs on the plateau-walked index, so that residual sits **inside** the quantity being filtered. A filter that evaluates the true contact frame one frame late, at a moment when the arm has begun to fold or the wrist has begun to cross back, would reject the correct answer — and a filter that rejects correct answers is strictly worse than the defect it replaces.

**Therefore: a candidate is rejected only if it fails the gate at `i-1`, `i` **and** `i+1` — all three.** This mirrors the three-frame rule already used by `racket_wrist_reliable` in step 2, so it is the existing discipline rather than a new one, and it makes the filter tolerant of exactly the ±1 uncertainty the document already admits to carrying. A one-frame dip below the extension threshold at the true contact frame cannot reject it.

**This is the single most important constraint on the change** and the test named for it in §9.2.5 is the one that must not be allowed to go soft.

##### 9.2.5 Why this is an algorithm change and not a threshold change

No value of any existing constant produces the right answer. Confidence on frame 71 was already **0.043** — well below the 0.35 threshold at which the orchestrator downgrades to `partial` and refuses ball speed — and the pipeline returned frame 71 regardless, because confidence gates the *tone* (Stage 16) and *ball speed* (Stage 11), never the *selection*. Driving every penalty to 0.0 would change `confidence` to 0.0 and leave `frame_index = 71` untouched. **The thing that is missing is a second candidate, and no constant creates one.** This is the same lesson as §9.1 round 3, arriving from a different direction.

##### 9.2.6 Rejected alternatives

| Alternative | Why rejected |
|---|---|
| Make low confidence fail the job instead of downgrading it | Frame 71 scored 0.043 and was returned; a confidence floor would turn it into a failure without ever finding frame 87. It converts wrong answers into failures but never into right ones. |
| Search for the *second* peak when gates fire on the first | A special case of candidate enumeration with a hard-coded depth of 2 and no ordering rule. The general form costs no more and is testable. |
| Replace the wrist-speed proxy with a racket-head proxy now | The correct long-term fix for root cause (b), and out of scope here. It is a new signal requiring its own ground-truth validation, and it does not remove the need for plausibility filtering. Sequence it after this change, with §9.2.7's extended ground truth in place to measure it against. |
| Filter on all six flags | Rejects correct answers via `contact_near_clip_end`, and filters on `subject_identity_unstable`, which cannot discriminate between candidates at all. |
| Restrict the peak search to Stage 7's new core window (Defect 2) | Would not have helped: frame 71 sits at ~2.84 s, inside the 2.2–4.7 s valid run and inside any core window anchored near the strike. Stated here because the two defects look related and are not. |

##### 9.2.7 Test strategy

**Unit — `backend/tests/unit/analysis/test_contact.py`:**

- **MUST CHANGE — `test_gate_wrist_behind_mid_hip_lowers_confidence` and `test_gate_arm_not_extended_lowers_confidence`.** Both assert the contract this change reverses, and both will fail. Replace with `test_a_candidate_with_the_wrist_behind_mid_hip_is_not_selected` and `test_a_candidate_with_an_unextended_arm_is_not_selected`, each built on a synthetic sequence carrying **two** speed peaks where the taller one is implausible — the shape the current fixtures cannot express.
- **MUST NOT CHANGE — `test_gate_contact_near_clip_end_lowers_confidence` and `test_gate_subject_identity_unstable_lowers_confidence`.** These two staying green is the deliberate asymmetry of §9.2.2, and the tests are the durable record of it. If a later change makes them fail, the asymmetry has been lost.
- `test_the_correct_frame_is_not_rejected_by_a_one_frame_dip` — the §9.2.4 constraint, expressed directly: a sequence whose true contact frame momentarily dips below the extension threshold must still be selected.
- `test_all_candidates_rejected_returns_contact_not_found` — assert `confidence == 0.0`, the `contact_not_found` flag present, and **that Stage 9 did not raise**.
- `test_candidate_enumeration_respects_separation_and_cap`.
- `test_a_degenerate_arm_range_demotes_the_filter_to_a_penalty` — the `ARM_RANGE_MIN_TU` guard.
- `test_the_audit_trail_records_every_rejected_candidate`.

**Unit — `backend/tests/unit/analysis/test_defect_regressions.py`** is the right home for a **serve-shaped regression fixture**: a synthetic serve whose wrist-speed peak precedes true contact by ~0.6 s, where the earlier peak fails arm extension and the later, slower peak passes. This fixture does not exist today, and `synthetic_swing.py` produces a single peak that *coincides* with contact — which is precisely why the unit suite is green against a defect visible on every serve. **Building the fixture is part of the change.**

**Integration — `backend/tests/integration/test_pipeline_end_to_end.py`:**

- **`test_contact_matches_the_ground_truth_band` is currently marked `@pytest.mark.xfail(strict=True)`** (line 275), with a reason naming this defect. When this fix lands, that test **passes** — and under `strict=True` a passing xfail is an `XPASS`, which **FAILS the suite**. **Removing the `xfail` marker is a required step of this change, in the same commit.** It is not a follow-up, not a cleanup, and not optional: leaving it in place turns a successful fix into a red build, and the natural reaction to a red build is to doubt the fix.
- `test_contact_is_reported_with_its_confidence_and_flags` (line 263) currently asserts `"sequence_unusable" not in contact.sanity_flags`. It should gain `"contact_not_found" not in contact.sanity_flags`, so that a regression into blanket rejection is caught by the same assertion that catches degenerate returns.

##### 9.2.8 Re-validation required — §9.1's validation does not transfer

Stage 9 was declared stable in §9.1 after three rounds against real footage. **That validation does not carry over to this change, and the reasons are specific:**

- It used **hand-centred windows** — the analysis window was placed by hand, not by Stage 5's motion scan. Defect 1 (§5.1) is the record of what Stage 5 actually produces, so §9.1's Stage 9 was validated on inputs the shipped system does not generate.
- It was **serve-light in the sense that matters**: 5 hand-labelled ground-truth events, and only 3 forehands ever reached Stage 9 with a single one cleanly confirmable. The validation set cannot distinguish "works on groundstrokes" from "was never meaningfully tested on groundstrokes."
- Candidate enumeration changes the *shape* of the output, not just its value. A stage validated as a one-candidate `argmax` has not been validated as a filtered ranked selection.

Required, in this order:

1. **Re-run the 5 existing hand-labelled ground-truth events.** The `+1` residual must be **unchanged**. This change must not move answers that are already correct, and this is the regression gate for that.
2. **Extend ground truth to ≥ 8 serves and ≥ 8 forehands**, ball-on-strings labelled at ±1 frame, **on Stage-5-placed windows** — after Defects 1 and 2 land, since both change which frames Stage 9 is given.
3. **Record per clip:** the full candidate list, which candidates were filtered and on which flags, the selected index, and the signed distance to truth. Acceptance, all three required: **no correct answer newly rejected (hard requirement, no exceptions);** the serve error band narrowed from ~15 frames to within ±2; and the all-rejected rate below the ~20 % pre-committed in §9.2.3.
4. **Re-run Stage 8's handedness measurement alongside.** The candidate list is computed on the racket-hand speed curve, so a wrong hand changes every candidate. §9.1's still-open finding — Stage 8 confidence 0.05–0.36 on real footage, wrong hand on more than half a 7-clip batch — is not a new risk introduced here, but **this change sharpens the dependency**: previously a wrong hand produced a wrong frame, and now it can additionally produce a spurious `contact_not_found`. Treat a present, correct `handedness_hint` as a precondition of interpreting these results.

---

### Stage 10 — Ball detection (IMPURE frame acquisition + PURE-CV detection) **[NEW]**

| | |
|---|---|
| **Owner** | `ball/frames.py` (impure), `ball/detector.py` (pure-cv), `ball/geometry.py` (pure), `ball/track.py` (pure) |
| **In** | temp video `Path`, `ContactDetection.contact_absolute_time_s`, `BallSpeedCalibration`, `NormalizedSequence` (for player exclusion boxes), `VideoMeta` |
| **Out** | `BallTrack \| None` + `BallDetectionSummary` |
| **Fails** | Never fails the job. Every failure path yields `BallTrack = None` and a `BallSpeedUnavailableReason`. Hard wall-clock deadline of 6 s → `detection_timeout`. |

Skipped entirely when `ball_speed_calibration` is absent, when `contact.confidence < 0.35`, or when `PoseQuality.estimated_camera_view ∈ {front, behind}` (§10.5).

> **KNOWN GAP — the camera-view gate is not yet functional.** Camera-view estimation fell outside the nine ordered steps of Stage 7 as specified, so `estimate_camera_view()` was never built; the implemented Stage 7 (`backend/app/analysis/normalize.py`) hardcodes `PoseQuality.estimated_camera_view = CameraView.UNKNOWN`. `UNKNOWN` matches none of `front`, `behind`, or `oblique`, so **every decision gated on this field is currently inert**: this skip condition, §10.6 mitigation 2, and both camera-view rows of the §11.4 cap table never fire.
>
> **What this means for trusting ball speed.** §10.6's residual error of ±10–15 % is stated *for a compliant capture*, and it assumes the non-compliant captures were rejected by this gate. With the gate inert they are not rejected: a `front` or `behind` capture — the `θ ≈ 90°` geometry §10.6 itself calls unmeasurable — passes straight through Stages 10–11 and produces a confidently wrong mph value instead of `null` + `camera_view_unsuitable`. Ball speed therefore currently fails *silently and plausibly* rather than *loudly and locally*, which is precisely the failure mode §5.1 relied on to rank it second.
>
> **Closing the gap** requires implementing camera-view estimation in Stage 7 to the definition already given in §5's concrete-fallback discussion: classify from the ratio of projected shoulder width to torso length — near 0.9–1.1 facing the camera, collapsing below ~0.5 side-on. The unit test for it is already specified in the §4 purity/test table (`estimate_camera_view`, skeleton projected at yaw 0/30/60/90, assert bucket boundaries). **Until that lands, Stage 10/11 ball-speed gating cannot be trusted and a speed figure must not be surfaced as authoritative.** No threshold or decision in §10/§11 changes when it lands; the gates simply begin to fire.

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

2. **Require a roughly perpendicular camera view.** `PoseQuality.estimated_camera_view` exists as a schema field but is **hardcoded to `UNKNOWN` in the implemented Stage 7, so this mitigation is not currently in force** — see the KNOWN GAP note under Stage 10's skip conditions. As designed (and as it will behave once Stage 7's camera-view estimation is built), ball speed is **refused outright** (`camera_view_unsuitable`) for `front` and `behind`, and **capped at `low` confidence** for `oblique`. The capture UI additionally instructs: phone level (not tilted down more than ~10°), 5–10 m to the side, and *hit down the line* — i.e. parallel to the near sideline, which is the `θ ≈ 0` condition.

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
| `estimated_camera_view ∈ {front, behind}` | `null` — `camera_view_unsuitable` — **currently unreachable: field hardcoded to `UNKNOWN`, see KNOWN GAP under Stage 10's skip conditions** |
| `estimated_camera_view == oblique` | cap `low` — **currently unreachable, same gap** |
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


> **KNOWN GAP — `UNKNOWN` handedness zeroes all 18 metrics, including the four
> that do not need a racket hand.** When Stage 8 returns
> `Handedness.UNKNOWN`, `racket_wrist_index()` raises `UnknownHandednessError`
> (correctly — it refuses to default to the right wrist), Stage 13's
> never-raises wrapper catches it, and the return is a bare `SwingMetrics()`
> with every field `None`. Stage 15 then produces a scorecard with no
> measurable metric and `overall_score = None`, and Stage 17 has nothing to
> narrate. Verified behaviour, not inference: the warning attached is the
> INTERNAL-FAILURE one (`"swing metric computation failed internally
> (UnknownHandednessError: ...)"`), so an unresolved racket hand is currently
> reported to operators as a bug rather than as an unmeasurable clip.
>
> Four of the eighteen do not reference a racket hand at all and could still be
> measured: `shoulder_turn_deg`, `hip_rotation_deg`, `head_stillness_tu` and
> `wrist_separation_at_contact_tu` — they are computed from the shoulder line,
> the hip line, the nose and BOTH wrists symmetrically
> (`backend/app/analysis/metrics.py`). A clip that cannot be attributed to a
> hand is therefore reported as entirely unmeasurable when roughly a quarter of
> the metric set is in fact available, and the user is shown nothing rather
> than a narrower, honest analysis.
>
> **Tracked, NOT fixed.** The fix is a handedness-independent metric subset
> computed before the racket-hand branch, plus rubric bands for it; it is a
> Stage 13/15 change with its own unit tests, not a loosened guard, and it must
> not be closed by defaulting `UNKNOWN` to the right hand — which is precisely
> the silent behaviour `racket_wrist_index()` already exhibits and which Stage
> 14 goes out of its way to refuse.


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

> **KNOWN GAP — topspin and slice cannot be separated when handedness is weak.**
> As implemented (`backend/app/analysis/shot_type.py`), the spin axis (rule 4) is
> gated on the forehand/backhand axis (rule 3) having fired: only the forehand
> classes carry a spin qualifier, so if no side was established, neither
> `forehand_topspin` nor `forehand_slice` receives any spin evidence. Rule 3 in
> turn contributes **nothing** when `handedness.confidence < 0.5` with
> `source == detected`, or when handedness is `unknown` — the deliberate
> suppression described in the Stage 14 design (Part B.2), added because
> `racket_wrist_index()` silently maps `UNKNOWN` to the RIGHT wrist.
>
> **Why this matters more than it reads:** weak handedness is the COMMON case,
> not the edge case. Stage 8 measures 0.05–0.36 confidence on real footage
> (§9.1 / Stage 8 validation), so on an ordinary uncalibrated upload the
> pipeline cannot tell a topspin drive from a slice at all — and, because both
> forehand classes then tie at the same score, the 0.15 margin gate sends the
> result to `unknown` rather than to a spin-free forehand. Observed on the
> vendored clip: `shot_type = unknown`, `confidence = 0.20`, with the evidence
> strings `"handedness confidence 0.43 < 0.5 with no user hint; the
> forehand/backhand axis contributed nothing"` and `"the shot was not
> identified as a forehand; the topspin/slice axis contributed nothing"`.
>
> **Tracked, NOT fixed.** Closing it means either a handedness signal strong
> enough to trust without a hint, or a spin axis that does not depend on which
> side the shot came from. Do not close it by ungating rule 4: the spin bands
> in the rubric are per-shot-type, and a spin call on an unidentified side has
> nothing to be scored against.

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
| Model asset digest verification (startup, once per process — **not** per job) | ~0.1 s | ~0 MB (streamed hash) |
| Storage download (streamed to a temp file) | 1–3 s | ~2 MB of Python heap — **plus up to 50 MiB of the temp file itself**, because Cloud Run's container filesystem is in-memory (§1.20.1) |
| Keyframe motion scan (clips > 10 s only) | 0.2–0.8 s | ~3 MB |
| Decode + rotate + sample @ 640 px | 2–4 s | ~5 MB (one frame at a time) |
| `PoseLandmarker` construction (graph + 9 MB float16 bundle load) | ~0.35–0.5 s | included in the row below |
| MediaPipe extraction (VIDEO mode, 240 frames) | **8–13 s** | **~90–130 MB — UNVERIFIED** (graph + TFLite arena) |
| — extractor closed; the 90–130 MB released — | | |
| Pure kinematics (Stages 7–9) | < 80 ms | < 1 MB (`(240,33,4)` f32 ≈ 127 KB) |
| Ball frame pass: seek + decode ~0.9 s of video @ 1280 px | 0.5–1.1 s | ~5.5 MB (2 working frames) |
| Background median (15 × 1280×720 grayscale) | 0.20–0.30 s | ~14 MB (ring, transient) + 0.9 MB (result) |
| Ball detection, per-frame CV (~16–45 processed frames) | 0.3–0.8 s | ~6 MB (masks, contours) |
| Speed calculation | < 5 ms | negligible |
| Pure analysis (Stages 12–15) | < 120 ms | < 1 MB |
| Gemini | 1.5–4 s | negligible |
| Persist | 0.2–0.5 s | negligible |
| **Total warm, calibrated** | **~14–27 s** | **~110 MB above baseline, + up to 50 MiB in-memory temp file** |
| **Total warm, uncalibrated** | **~13–25 s** | **~110 MB above baseline, + up to 50 MiB in-memory temp file** |

**Per-frame CV cost breakdown** at 1280×720, **2 dedicated vCPU** (§1.20.1; the superseded figures were for Render's 0.5 shared vCPU and were scaled down by the same 1.5–2.5× re-derivation as Stage 6): absdiff + threshold ~1 ms, morphology open+close ~1.5 ms, HSV convert + two threshold branches ~2 ms, `findContours` + feature extraction ~1 ms ≈ **5–6 ms/frame**. At 60 fps the 0.25 s window plus coasting margin is ~18 processed frames ≈ 0.1 s; the table's band (now **0.3–0.8 s**) is dominated by the keyframe seek and 1280×960 portrait framing rather than by the per-frame arithmetic. The 6 s hard deadline below is unchanged and is now a very wide guard.

**Basis of the MediaPipe wall-time figure.** 8–13 s is not a guess, but it rests on one platform assumption that changed. Measured on a dev box (Python 3.13.2, `mediapipe==0.10.35`, `pose_landmarker_full.task`, VIDEO mode, 640×360): **19.5 ms/frame**, 111 ms construction, 80/80 frames detected. The superseded figure scaled that by an assumed 3–4× penalty for Render's 0.5 *shared* vCPU, giving ~60–80 ms/frame and 14–19 s. **That multiplier is retired with the platform**: it described a throttled fraction of a core, and §1.20.1 deploys on an explicit **2 dedicated vCPU** allocation where the remaining gap to the dev box is clock and core count, not contention. The re-derived penalty is **1.5–2.5×** — an assumption, stated as one, which additionally assumes XNNPACK uses both vCPUs — giving ~29–49 ms/frame and **~7.0–11.8 s for 240 frames**. The same measurement in IMAGE mode was 80.3 ms/frame (4.11×, a platform-independent ratio), which would put this row at ~29–48 s; Stage 6.3 records why VIDEO mode was chosen and what was given up. **The first measurement taken on a real Cloud Run revision replaces the 1.5–2.5× factor with a measured one**; it is the same discipline the RSS table below demands.

**Worst case: the rotation self-check doubles this stage.** Stage 5 retries the whole clip at +180° if the pose detection rate falls below 20 %. That retry is a **second full VIDEO-mode extraction pass**, so worst-case Stage 6 is **~16–26 s** and the worst-case warm job is **~23–41 s**. That fits inside the 180 s `heartbeat_at` staleness window with wide margin and also inside Cloud Run's 300 s request timeout. **The timeout observation is now informational, not load-bearing**: the async job model is justified by single-instance serialization and client-network decoupling (Stage 1), not by a platform timeout, so this worst case is bounded by the heartbeat window alone. `estimated_seconds` will read low on a clip that takes this path, and that is accepted rather than hidden.

**The peak-memory claim, and the ordering requirement it depends on.** The ball stage's ~25 MB working set is allocated **strictly after** the pose graph is released. If an implementer runs ball detection before closing the extractor — or holds the extractor open "in case of a retry" — the two add instead of overlapping and the OOM headroom narrows by the full ~25 MB. This is why Stage 6.7 specifies the close as a memory-ordering requirement and why the orchestrator's structure (`_collect_pose` fully exits its `with PoseExtractor(...)` block before `_collect_ball` is called) is part of the contract, not an implementation detail. **The ordering requirement is a statement about sequencing and holds regardless of the absolute magnitudes below.**

**Baseline RSS — rebudgeted, and the numbers are worse than v2 assumed. Every figure in this table is an ESTIMATE; none has been measured.**

The v2 table claimed `Python + FastAPI + numpy + PyAV + MediaPipe | ~150 MB`. That figure was never measurable, because **MediaPipe was never installed** — it was not in `requirements.txt` and `app/pose/` was empty. Installing it brings a transitive dependency set that v2 did not budget for at all (`matplotlib`, `pillow`, `sounddevice`, and friends are pulled in by `mediapipe==0.10.35` and are all imported eagerly by `mediapipe.tasks.python.vision`, so they are resident, not merely on disk):

| Component | Estimated resident | Note |
|---|---|---|
| Python 3.13 + FastAPI + pydantic + numpy + PyAV | ~110–140 MB | unchanged in kind from v1; re-measure |
| `opencv-python-headless` | +55–75 MB | see the wheel-variant decision below |
| `mediapipe` native graph libs + protobuf + flatbuffers + absl-py, at import | +40–60 MB | before any landmarker is constructed |
| `matplotlib` + `pillow` + `contourpy`/`fonttools`/`kiwisolver`, at import | **+35–55 MB** | **pure dead weight — we never plot anything** |
| `sounddevice` + `cffi` (+ bundled PortAudio) | +5–10 MB | **pure dead weight — we never touch audio** |
| **Idle total (estimate)** | **~245–340 MB** | |
| Peak added by a job (pose graph dominates) | +90–130 MB | ball stage's 25 MB overlaps, not adds |
| **Idle + peak job (estimate)** | **~335–470 MB** | |
| Stage 4 temp video file | **+ up to 50 MiB** | **New on Cloud Run:** the container filesystem is in-memory, so the downloaded clip counts against the memory limit for the whole job (it must survive until after Stage 11 — see the temp-file lifetime decision in Stage 1). Did not apply on Render's disk-backed ephemeral filesystem. |
| **Idle + peak job + temp file (estimate)** | **~385–520 MB** | |
| **Headroom on the chosen 2 GiB allocation (§1.20.1)** | **~1.5–1.7 GB** | |

**This risk is resolved by platform choice, and the resolution should be stated plainly rather than left as a standing worry.** The superseded text called a 512 MB container "a live risk" with ~40 MB of pessimistic headroom. That framing was correct for Render, where 512 MB was a *tier* and escaping it meant a paid plan migration. On Cloud Run, memory is an explicit per-revision argument (default 512 MiB, maximum 32 GiB, coupled to the CPU count — at `--cpu=2` anything up to 8 GiB is legal). §1.20.1 allocates **2 GiB**, roughly 4× the pessimistic ~520 MB figure above including the in-memory temp file. **The OOM risk is therefore closed for the MVP**, and "the next tier up" is a one-flag change costing single-digit dollars per month, not a migration.

Three things still follow, and only the third changed in force:

1. **Measure before trusting this table.** The first task after `extractor.py` exists is to record actual RSS at three points — idle after import, peak during extraction, and after `close()` — and replace the estimates with measured numbers. This is now about knowing the system, not about surviving it.
2. **The memory-ordering requirement stands regardless of headroom.** Closing the extractor before the ball stage allocates is still part of the contract (Stage 6.7). A 2 GiB allocation makes violating it survivable, which is exactly why it must stay enforced by structure rather than by luck.
3. `max_workers` stays at **1** and `job_queue_max_depth` stays at **4** — but the *reason* has changed. These are no longer an OOM guard. `max_workers=1` is now about CPU serialization on a 2 vCPU instance and about the single-instance pinning in §1.20.1; the depth cap is about bounding the wait a queued user is asked to accept. Queue depth was never a memory lever (queued jobs hold only a request body) and is even less of one now.

**On the OpenCV wheel variant.** v2 asserted that `opencv-python-headless` was "a hard requirement in `requirements.txt`, not a preference." That assertion was never true of the file — `requirements.txt` pinned the non-headless `opencv-python>=4.9` — and it cannot be satisfied by pinning alone, because `mediapipe==0.10.35` hard-declares a dependency on the **differently-named** distribution `opencv-contrib-python`. Verified by `pip install --dry-run --report`: pip sees no conflict between the two distribution names and installs **both**, each shipping a `cv2/` package to the same path, leaving which one wins install-order dependent and effectively unspecified. The reasoning for wanting headless still stands (the GUI variants map GTK/Qt/X11 shared objects we never call), so the fix is a build-step substitution rather than a pin; it is specified in `backend/requirements.txt` and summarised in §3's deviations list.

**Wall-clock guard.** Ball detection carries a hard 6 s deadline enforced in the orchestrator. On exceed, the stage abandons, returns `detection_timeout`, and the job continues to Gemini normally. A pathological clip cannot turn a 25 s job into a 90 s job.

**Long-clip note.** A 60 s intake clip does not change the pose or ball budgets — the analysis window is still 8 s and the ball window is still 0.25 s. It adds only the keyframe motion scan (0.2–0.8 s) and a larger download (up to ~2.5 s at the 50 MB cap). The 60 s cap costs roughly one second of wall time, which is why it is affordable.

`estimated_seconds` in the 202 response is `25 + (3 if calibrated else 0) + 2 × queue_depth`, rounded up. **The 25 s base is deliberately left unchanged even though the warm budget fell to 14–27 s**, and that is a decision, not an oversight: 25 s now sits at the top of the warm band rather than in its middle, so the estimate absorbs a cold start, a slow Supabase download, or a Gemini call at the top of its 1.5–4 s range without ever reading low on the common path. An estimate that over-promises is a worse bug than one that pads. Cold start adds roughly 10–25 s (container start, imports, and the one-time 9 MB digest verification), and **the padding must now carry that cost as the common case, not the exception.** On the production tier `min-instances=1` (§1.20.1) would make cold starts rare — revision deploys and instance recycles only. **That flag is not deployed.** The tier actually running is `--min-instances=0` (§1.20.1a), so the service scales to zero whenever idle and **every idle-then-request transition is a cold start: routine, not rare, and accepted at this stage as a known UX cost rather than treated as a bug.** The 10–25 s estimate itself is unchanged — only its expected frequency is — which is precisely the scenario the 25 s base was padded for: on a cold-start job the honest total is ~24–52 s, `estimated_seconds` will read low, and that is accepted and visible rather than hidden. Re-tightening this base becomes appropriate only if and when the production tier is adopted (trigger conditions in §1.20.1a). The base is the **VIDEO-mode** figure; had Stage 6.3 chosen IMAGE mode it would have to read ~50, which is a large part of why it did not.

#### §1.20.1 Deployment: container on Google Cloud Run

**Decided: the service deploys to Google Cloud Run as a container, from source via Cloud Build, as a single pinned instance.** Render is superseded entirely; a future reader should treat every Render-specific figure in this document's history as dead, not renamed. There is exactly one deploy path: the Dockerfile and the `gcloud run deploy` invocation below.

> **READ §1.20.1a BEFORE TRUSTING ANY FLAG LIST IN THIS SUBSECTION.** This subsection documents **two** configurations of the same service, and only one of them is deployed:
> - **Production tier (~$100/month, the target, NOT currently deployed):** adds `--no-cpu-throttling` and `--min-instances=1`. Decisions 1 and 2 below derive *why those flags are required for full correctness* and that derivation is still valid and deliberately retained — it is the target to revisit, not a mistake to erase.
> - **Dev/demo tier ($0/month, CURRENTLY DEPLOYED):** default CPU throttling, `--min-instances=0`. This is what the `gcloud run deploy` invocation and flag table below actually show, because they must match reality. §1.20.1a states plainly what is given up, exactly what breaks under real concurrent traffic, and the explicit trigger conditions for upgrading to the production tier.
>
> The relationship is: **dev tier now, production tier later, same document.** Neither supersedes the other unconditionally. Where a decision below says a flag is "not optional", read it as "not optional for the production tier / once there is real traffic" — §1.20.1a is the record of why running without it is tolerable *only* in the pre-user stage.

**Why a container — and why the `libportaudio2` reasoning transfers unchanged.** This is worth saying explicitly rather than assuming, because the superseded text justified Docker partly by Render's fixed native-runtime toolset, and that argument is gone (Cloud Run has no native runtime; a container image is the only deploy artifact, so Docker is mandatory by platform rather than chosen). **The `libportaudio2` requirement, however, was never platform-derived — it is host-OS-derived, and that is why it survives the move.** `mediapipe.tasks.python.vision` imports `sounddevice` eagerly at import time (a transitive `mediapipe==0.10.35` dependency; see the eager-import note in the baseline-RSS rebudget above). Per `sounddevice`'s own documentation, PortAudio ships inside the wheel **only on Windows and macOS**; on Linux the wheel dynamically loads the system `libportaudio2`. The deciding fact is therefore "the runtime is Linux", not "the host is Render" — so it holds identically inside a `python:3.13-slim` image on Cloud Run, on Render, on a local Docker run, or on any other Linux target. Without the library, `import mediapipe.tasks.python.vision` raises `OSError` **at the first job, not at build time**: a green deploy, a passing startup probe, and a failure on first real traffic.

**The two-step `--no-deps` install is likewise platform-independent and unchanged.** Its reason is a pip dependency-resolution fact about `mediapipe==0.10.35` hard-declaring `opencv-contrib-python` while we require `opencv-python-headless` (full derivation in `backend/requirements.txt` and `backend/requirements-mediapipe.txt`). Nothing about that depends on the host. What changes is only *where the two steps run*: they were a Render Build Command, then `RUN` layers, and they remain `RUN` layers. **Neither requirements file needs any edit for this migration.**

**Not a Python-version problem.** Verified separately by pip dry-run and by inspecting the wheel: `mediapipe==0.10.35` ships **no CPython-ABI extension module**; its native layer is a single C shared library (`mediapipe/tasks/c/libmediapipe.so`) loaded through `ctypes.CDLL`. Python 3.13 compatibility is not at risk and is not the reason for any decision here. (That `ctypes` boundary has a second consequence used in Stage 1: the GIL is released during inference, so the ASGI loop can serve polls concurrently — which is what makes the second vCPU below useful rather than decorative.)

##### The four Cloud Run decisions

**1. CPU allocation mode: `--no-cpu-throttling` (CPU always allocated). Not optional on the production tier — and deliberately absent on the dev tier deployed today (§1.20.1a).**

Cloud Run's **default** is "CPU is only allocated during request processing": between requests, the instance's CPU is throttled to near zero. Google's own documentation is explicit that background threads, async tasks, and timers essentially freeze between requests — a worker pulling from an in-process queue stops making progress whenever no HTTP request is in flight. **That default would silently break this pipeline's core design.** The job runner is an in-process `ThreadPoolExecutor(max_workers=1)` that starts work *after* the `202` has already been returned (Stage 1, Stage 3), plus a periodic `heartbeat_at` sweep. Under the default, the analysis would freeze the instant the triggering request completed and resume only when some unrelated request happened to arrive — producing jobs that appear to run for minutes, heartbeats that stall and trip the 180 s staleness rule, and `WORKER_LOST` failures with no bug in our code to find. This is a correctness requirement, not a performance tuning knob.

**Cost consequence, stated plainly — and this cost is exactly why the flag is not deployed yet.** `--no-cpu-throttling` bills for the instance's entire lifetime rather than only during request handling, at instance-based rates (~25 % lower per vCPU-second and ~20 % lower per GiB-second than request-based rates). Combined with `--min-instances=1` below, this is a **fixed monthly cost independent of traffic**: at tier-1 pricing ($0.000018/vCPU-s, $0.000002/GiB-s), 2 vCPU + 2 GiB running continuously for a 30-day month is 2 × 2,592,000 × 0.000018 + 2 × 2,592,000 × 0.000002 ≈ **$104/month**, about **$100** net of the monthly free allowance. The free tier does not meaningfully apply to an always-on instance — 180,000 vCPU-seconds is consumed in roughly a day. This is the price of the async job model on this platform at production quality. **It is not accepted for a service with zero users: ~$100/month buys correctness guarantees that nothing is currently exercising, so both billing-model flags are reverted for now and this paragraph describes the tier to return to, not the tier running (§1.20.1a).** The one lever, recorded so it is not rediscovered as an open question: `--cpu=1` costs ~$57/month, and is rejected below.

**2. Instance count: `--max-instances=1` always; `--min-instances=1` on the production tier only (`--min-instances=0` is what is deployed today — §1.20.1a).**

Cloud Run autoscales horizontally by default, and the default maximum should be treated as effectively unbounded. Render's single fixed instance gave this design an invariant for free that Cloud Run does not: **that one process is the sole source of truth for in-flight work.** A second instance would mean two independent `ThreadPoolExecutor`s, two independent queue-depth counters (so `429 QUEUE_FULL` would admit up to 2× `job_queue_max_depth`), and two heartbeat sweepers racing over the same `analysis_jobs` rows. That is a correctness defect, not a scaling inefficiency.

- `--max-instances=1` restores the invariant. It is load-bearing and must not be raised to "handle more traffic" without first moving the admission guard and the staleness sweep into the database.
- `--min-instances=1` is the separate and equally necessary half **for the production tier, and it is the flag deliberately reverted on the dev tier.** With `min-instances=0`, Cloud Run judges an instance idle on *request* activity, not on background work, so an instance running a post-`202` analysis with no open request is a scale-down candidate. `--no-cpu-throttling` keeps the CPU alive for the instance's lifetime; it does not extend that lifetime. `min-instances=1` guarantees one instance is always kept running, which is what actually protects an in-flight job; it is also the only thing that fully removes the scale-from-zero transition in which a brief two-instance overlap is possible at all. It also removes cold starts from the common path (see `estimated_seconds` above). **Deployed today: `--min-instances=0`. The consequences — routine cold starts, the throttling freeze window, and the residual scale-event overlap — are enumerated and accepted in §1.20.1a, together with the conditions that make this revert stop being acceptable.**

**The better long-term resolution, named but deliberately not built for the MVP:** make the guard cross-instance — replace the in-process depth counter with `SELECT count(*) FROM analysis_jobs WHERE status IN ('queued','running')` as an admission check, and give the staleness sweep a database advisory lock so exactly one sweeper runs. That would make `max-instances > 1` safe and turn this from a pinned single instance into a real horizontally scaled service. It is the correct next step after the MVP ships; it is not a prerequisite for it.

**3. CPU and memory: `--cpu=2 --memory=2Gi`.**

Cloud Run couples the two — at 2 vCPU, memory may be anything up to 8 GiB; 1 vCPU caps at 4 GiB; 0.5 vCPU caps at 1 GiB. The allocation is chosen against the figures in the rebudget table above, not by eyeballing.

*Memory — why 2 GiB and why that is not waste.* Pessimistic idle + peak job is ~470 MB, plus **up to 50 MiB for the Stage 4 temp video, because Cloud Run's container filesystem is in-memory** — a constraint Render's disk-backed ephemeral filesystem did not impose, and one that matters here because the temp file must survive until after Stage 11 (Stage 10 performs a second decode pass over it). Call it ~520 MB pessimistic. 2 GiB is ~4× that. The default 512 MiB would not fit at the pessimistic end at all; 1 GiB would leave only ~2× headroom against numbers **none of which has been measured**, which is thin margin for a fragmenting Python heap. 4 GiB would be waste, because nothing in this pipeline can grow past roughly 600 MB by construction: `max_workers=1` caps concurrent jobs at one, Stage 5's generator holds one frame at a time rather than 166 MB of buffered frames, Stage 4 never `.read()`s the file into memory, and the 50 MiB upload cap bounds the temp file. The delta from 512 MiB to 2 GiB is 1.5 GiB × 2,592,000 s × $0.000002 ≈ **$7.80/month** — cheap insurance on unmeasured estimates, and the single flag to change once real RSS numbers exist.

*CPU — why 2 and not 1.* 2 vCPU is the more expensive half of the bill (~$93 of the ~$104) so it needs a real defence. Three reasons, in order: (a) `max-instances=1` means this one instance serves both the inference worker and *every* poll, JWT verification, and health request from *every* concurrent user — with the GIL released inside the native inference call, a second vCPU gives the ASGI loop an actual core to answer 2-second polls from up to five queued users instead of contending with a saturated one; (b) MediaPipe's XNNPACK delegate can use both vCPUs, which is where the 1.5–2.5× factor (and therefore the 8–13 s Stage 6 figure) comes from — `--cpu=1` would not merely be slower to serve, it would roughly double per-frame inference time and push Stage 6 back toward the superseded 14–22 s; (c) the ~$47/month saved by `--cpu=1` buys a service that is slower on its single most expensive stage while one pinned instance absorbs all traffic. Rejected. If real measurements show XNNPACK does not scale to the second thread, `--cpu=1` becomes correct and the Stage 6 figures revert — that is the one thing the first production measurement should check.

**4. Request timeout: `--timeout=300` (the default, set explicitly).**

Cloud Run's default is 300 s, configurable to 3600 s. **This retires Render's ~100 s edge timeout as an architectural constraint**, and Stage 1's execution-model decision has been re-derived accordingly — async-job-plus-polling stays, but on single-instance serialization and client-network-decoupling grounds, not because a synchronous request would be cut off. It no longer needs to be large: with the job running in the background, the `POST /v1/analyses` request returns its `202` in well under a second, and `GET /v1/analyses/{id}` returns immediately. 300 s is therefore generous headroom for a path that should never approach it, and it is written into the flags explicitly so that a future reader sees a decision rather than a default. Raising it would be a symptom of having accidentally reintroduced synchronous analysis.

##### Dockerfile

Deploy configuration, not application logic:

```dockerfile
FROM python:3.13-slim

# mediapipe.tasks.python.vision -> sounddevice -> libportaudio2.
# Linux wheels do not bundle PortAudio (Windows/macOS wheels do). Host-OS fact,
# not a platform fact: required on any Linux runtime, Cloud Run included.
# --no-install-recommends because libportaudio2's recommends pull ALSA tooling
# we never call; the apt lists are dead weight in the final layer.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libportaudio2 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt backend/requirements-mediapipe.txt ./backend/

# Two steps, in this order. Reasoning lives in the requirements files, not here:
#   backend/requirements.txt            (why mediapipe is absent; deps hand-enumerated)
#   backend/requirements-mediapipe.txt  (why --no-deps and why an exact pin)
# Platform-independent: this is a pip resolution fact, unchanged by the move
# from Render to Cloud Run.
RUN pip install --no-cache-dir -r backend/requirements.txt
RUN pip install --no-deps --no-cache-dir -r backend/requirements-mediapipe.txt

COPY backend/ ./backend/

# Cloud Run injects $PORT (8080). Shell form so $PORT expands.
# --workers 1 IS LOAD-BEARING: a second uvicorn worker is a second process with
# its own ThreadPoolExecutor and its own queue-depth counter inside one
# container -- the identical correctness defect that --max-instances=1 exists
# to prevent. Concurrency comes from the async event loop, never from workers.
ENV PYTHONUNBUFFERED=1
CMD exec uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1
```

**Flagged gap, not fixed here:** `backend/requirements.txt` currently lists `pydantic` but neither `fastapi` nor `uvicorn`, so the image above would not start as written. That is a pre-existing omission in the requirements file, independent of this migration, and it needs its own edit.

##### Deploy invocation

`gcloud run deploy --source` is chosen over a hand-built image deliberately: Cloud Run requires `linux/amd64`, and building locally on a Windows or Apple-Silicon dev box is a well-known way to ship an unrunnable image. Cloud Build produces amd64 natively, which removes the trap rather than documenting it.

```bash
# DEV/DEMO TIER -- this is what is actually deployed today ($0/month, see §1.20.1a).
# Note what is ABSENT and absent on purpose: --no-cpu-throttling and --min-instances=1.
# Default CPU throttling applies (CPU allocated only during request processing), and
# --min-instances=0 lets the service scale to zero when idle.
gcloud run deploy tennisform-api \
  --source=. \
  --region=us-central1 \
  --execution-environment=gen2 \
  --cpu=2 \
  --memory=2Gi \
  --min-instances=0 \
  --max-instances=1 \
  --concurrency=20 \
  --timeout=300 \
  --port=8080 \
  --allow-unauthenticated \
  --set-env-vars=SUPABASE_URL=https://<project>.supabase.co,POSE_MODEL_PATH=/app/backend/app/pose/models/pose_landmarker_full.task \
  --set-secrets=SUPABASE_SERVICE_ROLE_KEY=supabase-service-role-key:latest,GEMINI_API_KEY=gemini-api-key:latest
```

Flag-by-flag, for the non-obvious ones:

| Flag | Why |
|---|---|
| `--execution-environment=gen2` | Full Linux compatibility for MediaPipe's `ctypes`-loaded `.so` and PyAV's native decoders. Gen1's restricted syscall surface is not worth debugging. Gen2 also sets the 512 MiB memory floor, which is below our 2 GiB anyway. |
| *(`--no-cpu-throttling` — **deliberately absent, dev tier**)* | Decision 1 explains why the production tier requires it: without it the post-`202` background job freezes between requests. **Not set today** because it is a fixed ~$100/month billing-model change on a service with no users. Default CPU throttling is in effect. The failure it prevents requires a job to sit mid-analysis with no request in flight and no traffic to revive it — see §1.20.1a for why that is a tolerable testing-stage annoyance rather than a silent production failure, and for the trigger to add the flag back. |
| `--min-instances=0 --max-instances=1` | Decision 2, dev tier. `--max-instances=1` is unchanged and still load-bearing for the single-worker invariant — it must not be raised. **`--min-instances=1` is deliberately absent**: `0` scales to zero when idle, which is what makes this tier $0/month, at the cost of routine cold starts (§1.20 `estimated_seconds`) and a residual brief two-instance overlap during a scale event (§1.20.1a). Production tier restores `--min-instances=1`. |
| `--concurrency=20` | Bounds simultaneous work on the ASGI loop. Five job slots (1 running + 4 queued) polling every 2 s generate nowhere near 20 in-flight requests, so this never rejects legitimate traffic; it caps how many ES256 JWT verifications can pile onto the serving core at once while inference runs. The real admission control is `429 QUEUE_FULL` at depth 4, in application code. |
| `--allow-unauthenticated` | Authentication is application-level (Supabase ES256 JWT, Stage 2). Cloud Run IAM would reject the mobile client's tokens, which are not Google identities. Every route is guarded by Stage 2; there is no unauthenticated surface other than health. |
| `--set-secrets` | Secret Manager, per CLAUDE.md's no-hardcoded-secrets rule. `SUPABASE_URL` and `POSE_MODEL_PATH` are non-secret configuration and stay as plain env vars; the model path is a test/local-override knob (Stage 6.1), not a secret. |

##### What this does not change

The container boundary affects what is present on the filesystem, not Python-level resident memory: the RSS estimates and the measure-before-committing requirement above stand. The install *order* is unchanged. The vendored model bundle decision (Stage 6.1) is unchanged and its reasoning is actually strengthened here, since the image is the deploy artifact. `max_workers=1` and `job_queue_max_depth=4` are unchanged in value, though their justification moved from OOM protection to CPU serialization and queue-wait bounding.

##### §1.20.1a Current deployment tier: development/demo ($0/month) — deliberately inferior, explicitly temporary

**This is the configuration actually deployed. It is deliberately, knowingly inferior to the ~$100/month production configuration documented in Decisions 1–4 above, and it is temporary.** Nothing above is retracted or wrong; the production tier remains the target and its derivation is retained in full precisely so that re-adopting it is a two-flag change with the reasoning already written. A future reader must not read §1.20.1 as "the config" and this as a footnote: **§1.20.1a supersedes §1.20.1's flag list for as long as the trigger conditions below remain unmet, and §1.20.1 resumes authority the moment any of them is met.**

The reverted flags, exactly:

| Flag | Production tier (§1.20.1, target) | Dev tier (deployed today) |
|---|---|---|
| CPU allocation | `--no-cpu-throttling` (CPU always allocated) | **omitted** → Cloud Run default: CPU allocated **only during request processing** |
| Minimum instances | `--min-instances=1` | **`--min-instances=0`** → scales to zero when idle |
| `--cpu=2 --memory=2Gi --max-instances=1 --timeout=300` | unchanged | **unchanged — not part of this revert.** Sizing and the single-instance cap cost nothing while scaled to zero, and `--max-instances=1` is needed the moment there is any traffic at all. |

**Why this is defensible now rather than a regression being papered over.** Both correctness problems Decisions 1 and 2 identified require their failure condition to *actually occur* to matter, and neither condition is reachable by a single developer testing serially with no users. This project has no users; it must stay at $0/month until there is something worth demoing.

**What breaks under real concurrent traffic on this tier — named precisely, using the mechanisms already derived above, not restated vaguely:**

1. **The CPU-throttling background-job freeze (Decision 1's mechanism).** A job handed to the in-process `ThreadPoolExecutor(max_workers=1)` *after* the `202` has already been returned (Stage 1, Stage 3) can stall mid-analysis the instant its triggering request completes, because under default throttling the instance's CPU is throttled to near zero between requests. It resumes only when some unrelated request happens to arrive. A long enough stall lets `heartbeat_at` go stale past the **180 s** rule, and the poller is then told `failed` with `ErrorCode.WORKER_LOST` — **a real failure with no bug in the code to find.** Why it is tolerable today: the client polls every 2 s while a job is in flight, and each poll *is* a request, which reallocates CPU; so in the normal single-user path the job is revived long before 180 s elapses. The exposure is a client that stops polling (app backgrounded, phone sleeps, network drops) while a job is mid-analysis and nothing else is hitting the service. During solo/manual testing that either does not happen or is noticed immediately by the person doing the testing. It is an acceptable testing-stage annoyance, not a silent production failure.
2. **The autoscaling race (Decision 2's mechanism).** `--min-instances=0` means the service scales to zero, so an idle-then-suddenly-busy period can in principle start a second instance before scale-down of the first has settled. Two instances means two independent `ThreadPoolExecutor`s, **two independent in-process queue-depth counters** (so `429 QUEUE_FULL` would admit up to 2× `job_queue_max_depth`) and **two heartbeat sweepers** racing over the same `analysis_jobs` rows. **State plainly: `--max-instances=1` bounds this to "at most a brief overlap during a scale event" — it does not make it impossible.** Only pinning `--min-instances=1`, the reverted flag, removed the scale-from-zero transition in which the overlap can occur at all. Why it is tolerable today: the race requires genuine concurrent load to make Cloud Run want a second instance in the first place, and nothing forces a scale-up while the sole developer tests one clip at a time. In practice `min-instances=0, max-instances=1` with no real traffic essentially never runs two instances simultaneously.
3. **Cold starts are now routine, and that is a cost, not a bug.** Every idle-then-request transition is a cold start (~10–25 s: container start, eager MediaPipe imports, one-time 9 MB model digest verification). §1.20's `estimated_seconds` base of 25 s was already padded partly to absorb exactly this, so the number needs no change — only its framing, which is corrected in §1.20. Accepted at this stage.

**Explicit trigger conditions for upgrading to the production tier.** Re-add `--no-cpu-throttling` and `--min-instances=1` (i.e. return to §1.20.1's invocation verbatim) when **any one** of the following becomes true — these are checkable facts, not a mood:

- **T1.** The service is demoed to **more than one person at once**, or to anyone who will use it from their own phone while someone else is also using it. (A single-viewer screen-share demo does not trigger this.)
- **T2.** The service is handed to **any real beta user** — anyone outside the developer, on their own device, using it unsupervised.
- **T3.** **Any** `WORKER_LOST` failure is observed that is not explained by a deliberate restart, a revision deploy, or a known OOM. One unexplained occurrence is the trigger; do not wait for a "rate".
- **T4.** The service is listed publicly, linked in a store listing, or pointed at by anything a stranger can reach.
- **T5.** More than ~500 analyses are run in a calendar month (see the free-tier bound below — this is also the point where the $0 claim stops being safe).

Upgrading is a redeploy with two flags added and no code change. **Do not treat any of these triggers as "probably fine, will do it later": T1–T4 are precisely the conditions under which mechanisms 1 and 2 stop being hypothetical.**

**Cost consequence, stated as concretely as the ~$100/month production figure.** At `--min-instances=0` with default CPU throttling, billing is request-based and only while requests are being processed:

- **Idle cost: ~$0.** Scaled to zero means no instance, therefore no vCPU-second and no GiB-second billing. There is no fixed monthly floor on this tier.
- **Active cost is bounded by Cloud Run's always-free allocation: 2,000,000 requests/month, 360,000 vCPU-seconds/month, 180,000 GiB-seconds/month.**
- **Does solo testing stay inside that? Yes, by a wide margin.** Take a pessimistic analysis: ~50 s of billed instance time (25 s cold start + ~27 s warm job, rounded up), at `--cpu=2 --memory=2Gi` → ~100 vCPU-s and ~100 GiB-s per analysis, plus a few dozen 2-second polls whose cost is already inside that window. **The binding limit is GiB-seconds: 180,000 ÷ 100 ≈ 1,800 analyses/month.** vCPU-seconds allow 360,000 ÷ 100 ≈ 3,600; requests are irrelevant at this scale (a million-fold headroom). A single developer testing heavily runs tens, not thousands, of analyses per month, so the realistic figure is **$0/month**.
- **The condition under which it would not be free:** sustained use past roughly **1,800 analyses/month** (~60/day), or adding a background keep-alive/cron ping that prevents scale-to-zero — a warm-ping every minute would bill ~43,200 instance-seconds/month of otherwise-zero cost and defeats the entire point of this tier. **Do not add a keep-alive ping to "fix" cold starts on this tier; that is a half-priced, half-correct version of the production tier. Either accept the cold starts or upgrade properly.** Past ~1,800 analyses/month, overage is billed at the same tier-1 rates ($0.000018/vCPU-s, $0.000002/GiB-s) and T5 says to upgrade rather than drift.

##### The single riskiest assumption in this subsection

**There are two, one per tier, because two configurations are documented here and only one is deployed. Do not read the production-tier assumption as load-bearing for what is currently running — it is not, because its config is not running.**

**Dev/demo tier (§1.20.1a) — CURRENTLY DEPLOYED. Riskiest assumption: that no real concurrent user reaches this service before the tier is upgraded.** Everything that makes default CPU throttling and `--min-instances=0` survivable reduces to that one bet. Mechanism 1 (the post-`202` freeze tripping the 180 s `heartbeat_at` rule into a spurious `WORKER_LOST`) needs a job to sit mid-analysis with nothing polling it; mechanism 2 (two in-process queue counters and two heartbeat sweepers) needs enough concurrent load for Cloud Run to want a second instance. Both are gated on traffic this service does not have.

Evidence it holds, such as it is: there are **zero** users today, the service is not publicly linked, and all testing is serial and manual by one developer who observes each run. The 2-second client poll cadence additionally keeps CPU reallocated for the duration of any job someone is actually watching, which is the only job that exists at this stage.

**What falsifies it, and it is falsifiable cheaply:** any of triggers **T1–T5** in §1.20.1a. **T3 in particular — a single unexplained `WORKER_LOST` — is the metric that falsifies this assumption**, and the honest response is to add the two flags, not to reason about whether that one occurrence was unlucky. The risk is deliberately accepted because the cost of being wrong is a retryable error with a real error code during a demo (never a spinner that never resolves — `analysis_jobs` is still the source of truth), while the cost of being over-cautious is ~$100/month for a service with no users.

**Production tier (§1.20.1 Decisions 1–2) — NOT CURRENTLY DEPLOYED; this is the assumption to re-validate when it is. Named explicitly: that `--no-cpu-throttling` plus `--min-instances=1 --max-instances=1` keeps one long-lived instance alive long enough for an in-process background job to finish after its triggering request has already returned `202`.**

Evidence it holds: CPU-always-allocated is documented to keep CPU available for the instance's lifetime rather than only during request processing, which is precisely the freeze this design must avoid; and `min-instances` is documented to keep instances running and warm rather than scaling to zero. Together these address the two known mechanisms that would kill a background job. **This reasoning is unchanged and still believed correct — it is simply dormant, because the flags it describes are not set today.**

What is *not* guaranteed, and is accepted: Cloud Run can still replace an instance — revision rollout, infrastructure maintenance, or an instance exceeding its memory limit. Any of those kills an in-flight job. This is already handled and handled honestly: `analysis_jobs` is the source of truth, not process memory, and a `running` job whose `heartbeat_at` is older than 180 s is reported to the poller as `failed` with `ErrorCode.WORKER_LOST` (Stage 1), which the client may retry. So the failure mode is a retryable error with a real error code, never a spinner that never resolves. The assumption being made is about *frequency*, not about correctness: that instance replacement is rare enough that `WORKER_LOST` stays an edge case rather than a routine user experience. **That frequency is the thing to watch in production, and `WORKER_LOST` rate is the metric that falsifies this assumption if it is wrong.**

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

**Resolved discrepancy, recorded so it is not rediscovered: `MULTIPLE_SUBJECTS_SUSPECTED` was never a member of this enum.** The pre-migration Stage 6 Fails row listed it as though it were, so that row referenced a code the schema did not define. It is now gone from Stage 6 entirely, for a second and independent reason: with `num_poses=1` the Tasks API returns at most one person and offers no signal that another was present, making the condition undetectable at the sensor. The replacement is a pure, Stage-7-derived warning string, `subject_identity_unstable`, carried in `PoseQuality.flags` (Stage 6.6, Stage 7 step 1b). **No enum member is added and none is removed by the Tasks API migration.**

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
    flags: list[str] = Field(
        default_factory=list,
        description=(
            "Closed vocabulary, part of the contract -- clients may branch on these. "
            "'ankles_not_visible' | 'wrists_low_visibility' | 'subject_identity_unstable' | "
            "'rotation_retry_applied'. 'subject_identity_unstable' replaces the removed "
            "MULTIPLE_SUBJECTS_SUSPECTED ErrorCode: with num_poses=1 the sensor cannot "
            "report a second person, so the detectable symptom -- a mid-clip re-anchor onto "
            "a different subject -- is derived in the pure layer instead. It never fails "
            "the job; it depresses Stage 9 confidence."
        ),
    )

class HandednessResult(BaseModel):
    handedness: Handedness
    confidence: float = Field(ge=0.0, le=1.0)
    source: HandednessSource
    racket_hand_path_length_tu: float | None = Field(default=None)
    off_hand_path_length_tu: float | None = Field(default=None)
    warnings: list[str] = Field(
        default_factory=list,
        description="Free-text, NOT a closed vocabulary -- do not branch on these. "
                    "Records incomplete wrist visibility and the coverage scaling applied, "
                    "path/radius criterion disagreement, and hint-over-detection override.",
    )

class ContactDetection(BaseModel):
    frame_index: int = Field(description="Index into the 30 fps sampled sequence.")
    time_s: float = Field(description="Seconds from analysis_window_start_s.")
    contact_absolute_time_s: float = Field(
        description="analysis_window_start_s + time_s. ABSOLUTE PTS in the source file. This is "
                    "what the ball-detection seek uses; on a long clip with an 8 s window, "
                    "confusing it with time_s measures the wrong 250 ms.",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    peak_hand_speed_tu_s: float = Field(description="Peak racket-hand speed, torso units per second.")
    peak_frame_index: int
    prominence_ratio: float = Field(description="Primary peak / next distinct peak. Higher = less ambiguous.")
    method: str = Field(default="peak_speed_decel_onset_v1")
    sanity_flags: list[str] = Field(
        default_factory=list,
        description="Closed vocabulary: 'wrist_behind_mid_hip' | 'arm_not_extended' | "
                    "'contact_near_clip_end' | 'subject_identity_unstable' | "
                    "'motion_not_sustained' | 'racket_wrist_unobserved' | 'sequence_unusable'. "
                    "Each lowers confidence; none rejects.",
    )

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
    "handedness": "right", "confidence": 0.31, "source": "user_hint",
    "racket_hand_path_length_tu": 7.83, "off_hand_path_length_tu": 2.41,
    "warnings": ["low separation (confidence 0.31 < 0.5); user hint right applied over detection left"]
  },
  "contact": {
    "frame_index": 96, "time_s": 3.20, "contact_absolute_time_s": 3.20, "confidence": 0.74,
    "peak_hand_speed_tu_s": 11.62, "peak_frame_index": 94,
    "prominence_ratio": 2.34, "method": "peak_speed_decel_onset_v1",
    "sanity_flags": []
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
      models/                 # VENDORED BINARY ASSET — not a Python package, no __init__.py
        pose_landmarker_full.task   # 9,398,198 bytes, SHA-256 pinned in extractor.py
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
- **`app/pose/models/` — added with the Tasks API migration.** A directory holding exactly one vendored binary: `pose_landmarker_full.task`, 9,398,198 bytes, SHA-256 pinned as a constant in `extractor.py` and verified once at startup. It is **not** a Python package and must not contain an `__init__.py`. Vendored rather than downloaded because the only published URL for the bundle contains `/latest/` — a mutable pointer that would let an upstream refresh silently change every landmark, every metric, and every frozen golden fixture with no commit in our history (Stage 6.1).
- **`backend/requirements-mediapipe.txt` — added with the Tasks API migration.** A second requirements file existing solely so `mediapipe==0.10.35` can be installed with `--no-deps`, which a plain `requirements.txt` line cannot express. The reason is a dependency-name collision that no pin can fix: `mediapipe` hard-declares `opencv-contrib-python` while this project requires `opencv-python-headless`, and because those are different distribution names pip installs **both**, leaving which `cv2/` wins unspecified. Full specification in `backend/requirements.txt` and §1.20.

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
| **IMPURE** | Anything touching the filesystem, network, a decoder, a model, or the clock. | anything | `api/**`, `pose/video_io.py`, `pose/extractor.py` (ML inference **+ filesystem, for the vendored model asset**), `ball/frames.py`, `feedback/gemini_client.py`, `feedback/service.py`, `services/**` |

`PURE-CV` is not a loophole. It carries the same testability guarantee that CLAUDE.md actually cares about: **every function in `ball/detector.py` is unit-testable with synthetic, programmatically generated inputs — no video files, no network, no model** (§4.5). What it gives up is only the ability to live under `app/analysis/`, and the purity test is extended, not weakened, to enforce that:

- `app/analysis/**` — banned imports unchanged, **plus `cv2`** explicitly added to the banned list. `analysis/speed.py` is strict-pure numpy; it never sees a frame.
- `app/ball/geometry.py`, `track.py`, `sequence.py`, `params.py` — same banned list as `analysis/`, including `cv2` and `av`.
- `app/ball/detector.py` — banned list is everything except `numpy`, `cv2`, `app.models`, and `app.ball.track`. Specifically banned: `av`, `pathlib`, `open`, `socket`, `httpx`, `supabase`, `mediapipe`, `app.services`, `time`.
- `app/ball/frames.py` — the only file in `app/ball/` permitted to import `av` or touch the filesystem. It is the exact analogue of `pose/video_io.py`.
- `app/pose/sequence.py`, `app/pose/landmarks.py` — listed PURE above, and the banned list must say so explicitly: **`mediapipe` is banned from both**, alongside the same list applied to `app/analysis/**`. This was a real gap — `mediapipe` was banned from `app/analysis/**` but from nothing under `app/pose/`, which is exactly the hole through which someone builds the seam object with `mp.tasks` imported into the pure module. `app/pose/extractor.py` is the only file in the repository permitted to import `mediapipe`.

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

#### `app/pose/extractor.py` — **IMPURE — PLANNED, DOES NOT EXIST** (MediaPipe Tasks + filesystem)

The MediaPipe boundary. `backend/app/pose/` currently contains only a 54-byte `__init__.py`; nothing below is implemented. `extract_keypoints` keeps its name from the original Stage 6 spec, but **no longer owns the landmarker's lifecycle** — a free function that constructed and closed the landmarker internally would either rebuild the ~100 MB graph per call or hide a process-global singleton, both of which break the once-per-job rule in Stage 6.7 and the memory ordering §1.20 depends on. The lifecycle moves to a class; the function takes the live extractor.

```python
POSE_MODEL_FILENAME: str = "pose_landmarker_full.task"
POSE_MODEL_SHA256: str           # pinned digest of the vendored bundle (Stage 6.1)
POSE_MODEL_SIZE_BYTES: int = 9_398_198
NUM_LANDMARKS: int = 33

def resolve_model_path(configured: Path | None = None) -> Path: ...
def verify_model_asset(path: Path) -> None: ...
    # Raises RuntimeError on missing file, size mismatch, or digest mismatch.
    # Called ONCE at application startup, never per job. A truncated or
    # LFS-pointer-substituted checkout does not make MediaPipe fail loudly --
    # it can yield a landmarker that loads and emits plausible but wrong
    # landmarks. This turns that class of failure into a refusal to boot.

class PoseExtractor:
    def __init__(
        self,
        model_path: Path,
        *,
        num_poses: int = 1,
        min_pose_detection_confidence: float = 0.5,
        min_pose_presence_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
    ) -> None: ...
    def __enter__(self) -> "PoseExtractor": ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    def close(self) -> None: ...
    def detect_frame(
        self, frame_rgb: np.ndarray, timestamp_ms: int
    ) -> tuple[np.ndarray, np.ndarray, bool]: ...
        # Returns (landmarks (33,4) float32, world (33,3) float32, detected).
        # On non-detection returns zero-filled arrays and False.

def extract_keypoints(
    extractor: PoseExtractor,
    frames: Iterable[tuple[float, np.ndarray]],
    *,
    window_start_s: float,
    width_px: int,
    height_px: int,
    max_frames: int = 240,
) -> RawPoseSequence: ...
    # Drives the Stage 5 generator exactly once, streaming. Accumulates into
    # preallocated (max_frames, ...) buffers, trimmed on exit. Does NOT
    # construct or close the extractor. Does NOT raise NO_POSE_DETECTED --
    # it reports `detected`, and the orchestrator applies the 40 % gate.
```

`PoseExtractor` owns the context-manager protocol, **not** MediaPipe's object. Whether `PoseLandmarker` itself implements `__enter__`/`__exit__` in 0.10.35 is unverified and irrelevant: our `__exit__` calls `PoseLandmarker.close()` unconditionally, which keeps the orchestrator's `with` contract true regardless of what the library offers.

#### `app/pose/sequence.py` — **PURE — PLANNED, DOES NOT EXIST** (numpy only; `mediapipe` banned)

Seam assembly. The millisecond-timestamp derivation lives here rather than in `extractor.py` specifically so that it is pure and gets its own unit test, per CLAUDE.md.

```python
def frame_timestamps_ms(timestamps_s: np.ndarray, window_start_s: float) -> np.ndarray: ...
    # int ms relative to window start, rounded, then clamped to strictly
    # increasing. `detect_for_video()` REQUIRES strict monotonicity. At a 30 fps
    # target the natural spacing is ~33 ms so the clamp should never fire, but
    # Stage 5's duplicate-frame selection on 24 fps sources makes the guarantee
    # worth enforcing rather than assuming. The returned values are fed to
    # MediaPipe and DISCARDED -- they never reach PoseSequence.timestamps_s.
def detection_rate(detected: np.ndarray) -> float: ...
def build_pose_sequence(raw: RawPoseSequence, width_px: int, height_px: int) -> PoseSequence: ...
def validate_sequence_invariants(seq: PoseSequence) -> None: ...
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
    golden/                       # frozen PoseSequence .npz from 15 real clips.
                                  #   TWO fixtures per clip: <clip>_video.npz and
                                  #   <clip>_image.npz, to support the Stage 6.11
                                  #   IMAGE-vs-VIDEO acceptance test as a STANDING
                                  #   regression rather than a one-off check.
                                  #   All must be regenerated when the model bundle
                                  #   or the running mode changes.
    golden_ball/                  # NEW -- frozen BallTrack .npz from the same clips
  unit/
    pose/
      test_landmarks.py
      test_sequence.py
      test_sequence_timestamps_ms.py   # NEW -- monotonicity clamp, duplicate-PTS input
      test_model_asset_digest.py       # NEW -- vendored .task size + SHA-256 pin
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

**This definition survived the MediaPipe Tasks API migration unchanged** (Stage 6.9, where each channel is walked to the Tasks field that populates it). Two points are now part of the contract rather than left implicit:

- **Channel 4 of `landmarks` is MediaPipe Tasks `NormalizedLandmark.visibility`**, confirmed present on the installed `mediapipe==0.10.35`. `NormalizedLandmark.presence`, which is new in the Tasks API and has no legacy counterpart, is **deliberately excluded**: it answers a different question (is the landmark inside the image at all) from `visibility` (is the landmark unoccluded), we have no threshold for it, and Stage 7 step 1's validity gate is calibrated to `visibility`. Adopting it would mean a `(T, 33, 5)` seam, a pipeline-version bump, and regenerated golden fixtures — never an in-place addition.
- **`timestamps_s` holds full-precision PTS**, float64, as recorded by Stage 5. It is never the rounded integer milliseconds that `detect_for_video()` requires; that value is derived, passed to MediaPipe, and discarded (Stage 6.5). Storing the rounded value would inject up to 0.5 ms of quantisation into every velocity in Stage 7 step 8.

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
| 6 | MediaPipe Tasks `PoseLandmarker` inference (VIDEO mode) | `pose/extractor.py` | IMPURE | **ML model inference + filesystem** (reads the vendored `.task` bundle) |
| 6b | Model asset digest verification | `pose/extractor.verify_model_asset` | IMPURE | Filesystem read; startup only |
| 6c | Millisecond timestamp derivation | `pose/sequence.frame_timestamps_ms` | PURE | Rounding + monotonicity clamp on an array |
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
| **`app/pose/sequence.py`, `app/pose/landmarks.py`** | same list as `app/analysis/**`, and **`mediapipe` in particular**. `app/pose/extractor.py` is the only module in the repository permitted to import `mediapipe`; the seam builder must be testable and runnable with MediaPipe absent from the environment entirely. |
| `app/ball/detector.py` | everything except `numpy`, `cv2`, `dataclasses`, `math`, `app.models`, `app.ball.params` — explicitly banned: `av`, `pathlib`, `open`, `socket`, `httpx`, `supabase`, `mediapipe`, `time`, `app.services` |

Documentation about purity decays; a failing test does not. The `cv2` ban on `app/analysis/**` is the specific thing that stops this feature from eroding the boundary over time — without it, "ball detection needs OpenCV" would eventually become "the metrics module imports cv2 for one convenience function."

### 4.5 Unit tests required with synthetic data

CLAUDE.md: *"Every math function in the analysis pipeline gets a unit test with synthetic keypoint data. No exceptions."* Every function below is covered by programmatic fixtures — no video files, no model, no network. The v1 table is unchanged and carried forward; the v2 additions follow it.

#### 4.5.1 Carried forward from v1 (unchanged)

| Function | Synthetic test approach |
|---|---|
| `interpolate_gaps` | Known linear ramp with punched holes; assert exact recovery; assert gap > 3 rejected |
| `savgol_smooth` | Sine + Gaussian noise; assert amplitude and **peak index** preserved within 1 frame; assert a moving average fails the same test (guards the design choice). **Window is now 5, not 7** (Stage 7 step 7) — the test must pin the window explicitly so a silent revert is caught. |
| `frame_timestamps_ms` | Ascending float PTS → ascending int ms; **duplicate/near-duplicate PTS must still yield strictly increasing ms** (the `detect_for_video` requirement); assert the returned ms are never written back into `PoseSequence.timestamps_s` |
| `detection_rate` | Synthetic boolean arrays at the 40 % boundary; assert the `NO_POSE_DETECTED` gate fires at `< 0.40` and not at `0.40` |
| `subject_identity_unstable` detection | Synthetic two-subject swing with an injected mid-clip swap (centroid jump > 0.25 TU, torso-length change > 35 %); assert the flag is raised, assert `usable` stays `True`, assert Stage 9 confidence is depressed |
| `verify_model_asset` | Vendored `.task` matches `POSE_MODEL_SIZE_BYTES` (9,398,198) and `POSE_MODEL_SHA256`; truncated and zero-byte copies each raise. **No network.** |
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
| `racket_hand_speed`, `find_deceleration_onset` | Triangular speed profile with a known apex. Assert: a plateau >= 2 frames above `SUSTAINED_SPEED_FRACTION` (0.45) of peak returns the plateau's LAST frame; a one-frame spike returns the peak itself, not `peak + 1`; a slow decay is bounded at `MAX_SUSTAINED_RUN_FRAMES` (4); a clip ending mid-plateau returns the last frame. **Pin all three constants explicitly** -- the retired 5 %-drop rule was, on a real speed curve, indistinguishable from `return peak + 1` for every threshold value, so a silent revert would not show up in a fixture that only checks the answer is "near the peak". |
| `racket_wrist_reliable`, `observed_fraction` | Synthetic sequence with one wrist landmark's `visibility` punched below threshold at frame k; assert k-1, k, k+1 are all marked unreliable (central-difference support), assert `peak_speed_index` skips an injected spike at k, assert `observed_fraction` drops proportionally |
| `sustained_motion_frames`, `approach_motion_coverage`, `held_landmark_fraction` | Static-subject sequence with one injected single-frame spike -> `motion_not_sustained` fires and confidence falls below `CONFIDENCE_FLOOR`; a real synthetic swing -> does not fire. Each of the three sub-tests must fail independently on a stream that passes the other two |
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

#### 4.5.4 Contract tests against the installed MediaPipe (new)

These are the only tests that require `mediapipe` to be installed, which is why they live in `tests/contract/` and are skipped when it is absent. They exist because Stage 6 is written against an empirically verified API surface, and the previous version of Stage 6 was written against an API that did not exist in the installed package and was never executed — the failure mode this whole group is designed to prevent.

| Test | What it asserts | Why it must exist |
|---|---|---|
| `test_mediapipe_options_fields` | `dataclasses.fields(PoseLandmarkerOptions)` names exactly `base_options`, `running_mode`, `num_poses`, `min_pose_detection_confidence`, `min_pose_presence_confidence`, `min_tracking_confidence`, `output_segmentation_masks`, `result_callback` | A version bump that adds, removes, or renames a knob invalidates Stage 6.4's config table. Fails loudly at CI instead of quietly at runtime. |
| `test_mediapipe_result_shape` | `result.pose_landmarks` is `list[list[...]]`; non-detection is an **empty outer list**, not `None`; `NormalizedLandmark` still carries `visibility` | `visibility` is channel 4 of the seam (§4.1). If it ever disappears, the seam is broken and every Stage 7 threshold is meaningless. |
| `test_mediapipe_dependency_set` | mediapipe's declared `Requires-Dist` matches the hand-enumerated list in `requirements.txt`, and `opencv-contrib-python` is **not installed** | The `--no-deps` install pins us to a private dependency list. This converts "breaks on upgrade" from a surprise into a CI failure. |
| `test_contact_image_vs_video` | Over 15 golden clips: `ContactDetection.frame_index` agrees within **±1 frame on ≥14 of 15**; `peak_hand_speed_tu_s` agrees within **5 %** | **Stage 6.11's acceptance test — the riskiest assumption in the pose path.** Failure reopens the running-mode decision in Stage 6.3. |

---

## 5. The Single Riskiest Technical Assumption

Re-evaluated in light of the ball-speed addition. **It has not changed.**

**The assumption: that torso-normalized 2D landmarks from a single uncontrolled phone camera produce swing metrics stable enough to score against a fixed rubric — that is, that the same swing filmed from a somewhat different angle yields substantially the same numbers.**

This is still the riskiest because everything downstream is *conditionally* correct. The pure math is testable and will be correct. The rubric is a defensible table. The Gemini guard is airtight. None of that matters if `shoulder_hip_separation_deg` reads 38° from one camera position and 57° from another for the identical swing, because then the rubric verdict flips from "low, work on your coil" to "ideal, nice coil," and the app confidently tells the user to fix something that isn't broken. That is worse than no product: it is a coaching app that produces authoritative-sounding, camera-dependent noise, and users will detect the inconsistency the first time they film from the other side of the court.

**Why it is nonetheless workable for an MVP.** Three pieces of evidence. First, markerless single-camera pose estimation has been repeatedly validated against marker-based motion capture, and the consistent finding is that **sagittal-plane joint angles are recovered to within roughly high-single-digit degrees when the camera is approximately perpendicular to the plane of motion, and degrade sharply off-plane.** The error is not random — it is a systematic function of view angle, which means it can be *gated* rather than merely tolerated. Second, the design already front-loads the mitigation: the metric set was deliberately weighted toward **ratio and relative quantities** — shoulder-hip separation (a difference of two co-projected lines, so first-order projection effects cancel), contact height ratio (normalized against the player's own shoulder height), tempo ratio (pure timing, projection-invariant), balance sway and head stillness (torso-normalized displacement), and the *sign* of swing path angle. These are structurally more view-stable than absolute joint angles, and every one of the fragile absolute angles is already tagged `view_sensitive: true` in the schema. Third, MediaPipe's `world_landmarks` output gives a metric 3D estimate that, while too noisy to report, is a usable second opinion for detecting when the 2D projection has gone bad.

**Concrete fallback.** Enforce the camera view instead of tolerating it. `estimate_camera_view()` is *specified* to run in Stage 7 and to classify view from the ratio of projected shoulder width to torso length (near 0.9–1.1 facing the camera, collapsing below ~0.5 side-on) — **but it is not yet implemented and the field is hardcoded to `UNKNOWN`, so this fallback is currently unavailable; see the KNOWN GAP note under Stage 10's skip conditions.** If the detected view falls outside the tolerance band for the rubric, the response degrades to `status: "partial"`, **all `view_sensitive` metrics are set to `None`**, their weights are renormalized away, and the client shows a "reshoot from the side" prompt with a framing overlay. The user gets four to six view-invariant metrics and honest, narrower feedback rather than fifteen metrics of which several are fiction. The `view_sensitive` flag and the `None`-propagation-with-weight-renormalization machinery are in the schema and the scoring functions specifically so this fallback is a configuration change, not a rewrite.

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
| Stage 9 | `contact_absolute_time_s` added to `ContactDetection`. Briefly documented under two names — `absolute_time_s` in §2.3 and the example response body, `contact_absolute_time_s` in the Stage 9/10 prose. **`contact_absolute_time_s` is the name that shipped**; it is what `backend/app/models/responses.py` and `backend/app/analysis/contact.py` implement, and this document has been aligned to the code rather than the reverse. The reconciliation is internal-only: no Flutter code under `frontend/lib/` parses the `contact` block under either name, so there is no wire-compatibility constraint to honour. |
| **Stage 10** | **New — ball detection (classical CV, native fps, `CAL_SPACE`).** |
| **Stage 11** | **New — speed calculation (median step, calibrated scale).** §11.5 sets capture-rate policy: 30 fps supported and expected, `medium` is the normal ceiling, 60 fps never forced. |
| **Known gap** | `PoseQuality.estimated_camera_view` is hardcoded to `CameraView.UNKNOWN` in the implemented Stage 7 (camera-view estimation was outside Stage 7's nine ordered steps and was not built). Every gate that reads it is therefore inert: Stage 10's skip condition, §10.6 mitigation 2, the two camera-view rows of §11.4, and §5's concrete fallback. Consequence: a `front`/`behind` capture yields a confidently wrong speed instead of `null` + `camera_view_unsuitable`. **Must close before Stage 10/11 ball-speed gating can be trusted** — implement per §5's ratio definition (projected shoulder width ÷ torso length; 0.9–1.1 facing, < ~0.5 side-on). No threshold or decision changes. |
| **Known gap** | **Stage 14's spin axis is unreachable under weak handedness.** Rule 4 (topspin vs slice) only scores the two forehand classes, and rule 3 (forehand vs backhand) contributes nothing when handedness confidence is below 0.5 with no user hint, or is `unknown`. Since Stage 8 measures 0.05–0.36 confidence on real footage, the common uncalibrated upload cannot be classified for spin at all and lands on `unknown`. See the KNOWN GAP note under Stage 14. **Not fixed.** |
| **Known gap** | **`UNKNOWN` handedness makes all 18 Stage 13 metrics `None`.** Four of them — `shoulder_turn_deg`, `hip_rotation_deg`, `head_stillness_tu`, `wrist_separation_at_contact_tu` — need no racket hand and could still be measured, so a clip whose handedness is unresolved is reported as wholly unmeasurable when about a quarter of the metric set is available. See the KNOWN GAP note under Stage 13. **Not fixed.** |
| Stages 10–17 (v1) | Renumbered to 12–19. |
| §2 | `CourtReference`, `BallSpeedConfidence`, `BallSpeedUnavailableReason`, `BallDetectionTier`, `NormalizedPoint`, `BallSpeedCalibration`, `CalibrationEcho`, `BallDetectionSummary`, `BallSpeedResult` added; `MetricUnit` deliberately unchanged. |
| §3 | `app/ball/` package added; `analysis/speed.py` added; `analyze()` split into `analyze_kinematics()` + `analyze_swing()`. |
| §4 | Second seam `BallTrack` defined; `PURE-CV` class defined; purity test extended, `cv2` banned from `app/analysis/**`. |
| §5 | Re-evaluated; unchanged, with the ball-speed risk named explicitly as runner-up. |
| §1.20 | Rebudgeted; baseline RSS up ~60–75 MB from OpenCV; peak added RSS unchanged at ~110 MB. **Re-derived for Cloud Run:** the 3–4× Render-0.5-shared-vCPU penalty is retired for an assumed 1.5–2.5× on 2 dedicated vCPU, so Stage 6 is 8–13 s (was 14–22 s) and the warm total is 14–27 s (was 21–37 s); per-frame CV scaled the same way; a new line item budgets the Stage 4 temp video against memory because Cloud Run's filesystem is in-memory; the "512 MB may not fit" risk is closed by allocating 2 GiB; `estimated_seconds` base deliberately held at 25 as padding. **Cold-start framing corrected for the dev tier (§1.20.1a): with `--min-instances=0` deployed, cold starts are ROUTINE, not rare — every idle-then-request transition is one. The 10–25 s estimate is unchanged; only its expected frequency is. A cold-start job totals ~24–52 s, so `estimated_seconds` will read low on it, accepted and visible.** |
| §1.20.1 | **New, then fully re-derived.** Deploy target is **Google Cloud Run** (container from source via Cloud Build), not Render — Render is superseded, not renamed. Decisions: `--cpu=2 --memory=2Gi`; **`--no-cpu-throttling`**, required for correctness because the default throttles CPU between requests and would freeze the post-`202` background job; **`--min-instances=1 --max-instances=1`**, required because horizontal autoscaling would duplicate the in-process executor, the queue-depth counter, and the heartbeat sweeper (cross-instance DB-backed guards noted as the post-MVP fix); `--timeout=300`. The `libportaudio2` and `--no-deps` mediapipe reasoning is confirmed **host-OS-derived, not platform-derived**, and transfers unchanged — neither requirements file is edited. Cost stated: ~$100/month fixed. **This entire configuration is RETAINED as documented-but-NOT-deployed — it is the production target, see the row below.** |
| **§1.20.1a** | **New — and this is the tier actually deployed.** The two **billing-model** flags are **reverted**: `--no-cpu-throttling` removed (→ Cloud Run default CPU throttling) and `--min-instances=1` → **`--min-instances=0`** (scales to zero). `--cpu=2 --memory=2Gi --max-instances=1 --timeout=300` **unchanged** — sizing and the single-instance cap are not billing-model changes and cost nothing at zero scale. Reason: **no users, $0/month until there is something to demo.** Both correctness problems §1.20.1 identified need their failure condition to actually occur: the post-`202` freeze needs a job un-polled mid-analysis (2 s client polls reallocate CPU), and the autoscaling race needs real concurrent load. `--max-instances=1` bounds the race to **"at most a brief overlap during a scale event", not "impossible"** — only `--min-instances=1` removed the scale-from-zero transition. Cold starts are now **routine and accepted**, absorbed by the already-padded 25 s `estimated_seconds` base. Cost: **~$0 idle; inside Cloud Run's always-free allocation** (2 M requests, 360,000 vCPU-s, 180,000 GiB-s per month) up to **~1,800 analyses/month** (GiB-seconds is the binding limit). Explicit revisit triggers **T1–T5**: >1 concurrent demo viewer, any real beta user, **any** unexplained `WORKER_LOST`, any public listing, or >500 analyses/month. Dev tier's own riskiest assumption named: **that no real concurrent user arrives before the tier is upgraded.** |
| **Stage 1** | **Execution-model decision re-derived for Cloud Run.** Async-job-plus-polling **stays**, but the Render ~100 s edge-timeout justification is retired (Cloud Run's 300 s default would have fit the job synchronously). New grounds: single-pinned-instance serialization must be observable to the client, and a dropped mobile connection must not destroy completed CPU work. The single-instance assumption is now stated explicitly rather than inherited from the platform. **Then tier-qualified for §1.20.1a:** the bullets now rest on `--max-instances=1` (set on **both** tiers) rather than on `min-instances = max-instances = 1`; "continuously billed instance" is corrected to "single pinned instance"; the `--cpu=2` reasoning is noted as tier-independent; and the single-instance-assumption paragraph now states that the bound is **absolute** on the production tier but **"at most a brief overlap during a scale event"** on the deployed dev tier. No bullet still reads as though `--no-cpu-throttling`/`--min-instances=1` are in effect. |
| §17 | Numeric guard extended with the conditional `mph` binding rule. |
| Rubric | **Unchanged.** `rubric_version` stays `rubric_v1`. |
| **DEFECT — Stage 5** | **The keyframe motion scan mis-centres the analysis window. UNIVERSAL, highest priority, fix planned in §5.1, NOT implemented.** `motion_scan_centre_s` returns the PTS of the **later** keyframe of the highest-energy consecutive pair, so its resolution is the GOP interval and it discards where inside the winning bucket the motion occurred. The Stage 5 bullet assumed ~1 keyframe per 1–2 s; **measured across the whole 16-clip corpus: 3–6 keyframes per clip, GOP 3.03–4.17 s, 16 of 16 clips at GOP ≥ 3.0 s.** Worked example (`serve_vertical_10340710.mp4`, 10.4 s, 25 fps): keyframes `[0.0, 3.04, 6.08, 9.12]`, energies `12.64 / 15.48 / 8.21`; the argmax correctly picks the middle bucket and returns its **end** (6.08 s) while the true strike is 3.44–3.52 s, giving window `[2.08, 10.08]` — **centre 2.6 s past the swing**, and surviving only because the clip-end clamp happened to bite. Fix: retain the full keyframe profile, then refine inside the winning bucket (widened by half a GOP each side) with a bounded dense decode of ≤ 48 sampled frames, **returning the midpoint of the highest-energy adjacent pair and never an interval endpoint**; the sparse-keyframe fallback (GOP ≥ 2.0 s) is the same primitive over a wider span, and a single-keyframe clip degrades to a whole-clip dense scan at `duration/48` stride plus a `motion_scan_coarse` flag rather than to today's silent head-of-clip guess. Return type widens to `MotionScanResult` so the caller can tell a located swing from a bucket boundary. Cost: §1.20's motion-scan row **0.2–0.8 s → 0.5–1.7 s**, warm total ~14–27 s → ~14–28 s, clips > 10 s only. **Not a threshold change** — widening the window is the only threshold-shaped option and costs 4–7 s of MediaPipe (~70 % of budget, linear in frames). **Corpus caveat recorded honestly in §5.1.3:** the corpus is all uniformly-encoded Pexels stock footage and phone video typically uses much shorter GOPs, so incidence on real uploads is unknown and probably lower — but the backend cannot control the uploader's encoder and **the failure is silent**, which is what justifies the fix. **Not fixed.** |
| **DEFECT — Stage 7** | **The gap gate rejects ordinary footage, and `longest_gap` is the wrong statistic. UNIVERSAL, root-caused in §7.1, fix planned, NOT implemented.** `normalize_sequence`, `MAX_GAP_FRAMES = 3`. **This is not merely downstream of the Stage 5 defect:** with `motion_scan_centre_s` monkeypatched to the true strike time (3.48 s) the window correctly becomes `[0.0, 8.0]` and Stage 7 **still fails**, `longest_gap_frames = 98` against a bound of 3 (156 at stock settings). Failure rate **3 of 6 forehands (110, 122, 18) and 2 of 7 serves (156, 88)** — 5 of 13 clips rejected before any analysis. Cause: valid pose exists only ~2.2–4.7 s of the clip (player small or turned away pre-toss, ~5 s of post-swing recede after), and a whole-window "no gap > 3 frames" rule has essentially zero tolerance for the dead time ordinary single-camera footage contains by construction. **Root cause is one constant doing two incompatible jobs**: correct as an *interpolation limit* (interpolating across > 100 ms fabricates the trajectory the product measures — CLAUDE.md forbids it) and wrong as a *usability verdict*. **98 vs 3 is two orders of magnitude; retuning the constant is the explicit anti-pattern.** Fix: two tiers — Tier 1 keeps the 3-frame interpolation cap unchanged, leaving longer gaps unfilled as a mask; Tier 2 judges usability over a **2.0 s core sub-window anchored to the centre of the longest contiguous valid run** (refined within it by summed two-wrist displacement — a coarse anchor, never to be reused as contact detection), requiring `MIN_CORE_COVERAGE = 0.90` and `MAX_CORE_GAP_FRAMES = 3` *scoped to the core*. Whole-window `longest_gap_frames` survives as a **diagnostic**, joined by `core_coverage_fraction`, `longest_core_gap_frames` and a `dead_time_outside_core` flag so the new tolerance is auditable. The chicken-and-egg (anchoring needs swing location, which is Stage 9, which needs Stage 7) is broken by anchoring on Stage 7's own per-frame validity rather than by reordering the stages; the runner-up — moving the verdict to the orchestrator after Stage 9 — is rejected in §7.1.1 for taking the `PoseQuality` decision away from the stage that owns it and running the detector over 98-frame holes. Carries the `valid` mask onto `NormalizedSequence`; `docs/PIPELINE_STAGES_12_14_15.md` §A.5/§A.6 should key their thin-data degradation off that mask rather than re-deriving validity. **Explicitly does not fix the Stage 9 defect** — frame 71 sits at ~2.84 s, inside the valid run and inside any core window. **Not fixed.** |
| **DEFECT — Stage 9** | **Candidate-rejection signal is computed and then ignored. Fix planned in §9.2, NOT implemented.** `contact.py` computes `arm_not_extended`, `wrist_behind_mid_hip`, `contact_near_clip_end` and `motion_not_sustained` and uses them **only to discount confidence after a candidate has already won**, via `GATE_PENALTIES`; nothing prevents a physically implausible candidate from being selected, because `peak_speed_index` produces exactly one candidate and there is nothing for it to lose to. Observed: on the ground-truth clip Stage 9 returns **source frame 71 at confidence 0.043** flagged `arm_not_extended` + `wrist_behind_mid_hip` + `motion_not_sustained` — visually confirmed mid-raise with the racket down near the hip — while true contact is **frame 87** at full extension. **Root cause has two layers.** (a) Architectural, and the one this fix addresses: the gates are a commentary track on a fixed decision. (b) Kinematic: on serves the wrist-speed proxy peaks during the explosive arm drive **~0.6 s before contact**, because the racket head's final acceleration comes from forearm pronation and wrist snap while the wrist's own translational speed has already fallen (**~9–11 units/s at frame 71 vs ~2.6–3.9 at frames 86–88**); on forehands wrist and racket travel together so the proxy lands close. **Severity is serve-specific, but the supported claim is "serves need distinct handling", NOT "groundstrokes are fine"** — only 3 forehands reached Stage 9 and only 1 was cleanly confirmable. Layer (b) is not fixed here; a racket-head proxy is separate, larger work. Fix: enumerate candidates (local maxima over reliable frames in the forward-swing window, separation reusing the existing ≥ 5-frame prominence constant, cap 5), plateau-walk each, and **move `wrist_behind_mid_hip` and `arm_not_extended` to PRE-selection filtering** — the first candidate passing both wins. `contact_near_clip_end` stays a penalty only (a real contact can legitimately occur near a clip edge, so filtering it would reject correct answers); `motion_not_sustained` stays a penalty only (composite, ×0.3, and its strongest sub-test was measured **anti-correlated** with correctness); `subject_identity_unstable` cannot discriminate between candidates by construction. `arm_not_extended` gains an `ARM_RANGE_MIN_TU` floor so a degenerate clip-relative range demotes the filter back to a penalty. **The `+1` residual is the binding constraint:** the filter runs on the plateau-walked index, so a candidate is rejected only if it fails at `i-1`, `i` **and** `i+1` — mirroring `racket_wrist_reliable` — so the accepted one-frame bias cannot reject the correct frame. **When every candidate is rejected, return nothing honestly:** `ErrorCode.CONTACT_NOT_FOUND` exists and is currently unreachable; Stage 9 still never raises, returning `confidence = 0.0` plus a new `contact_not_found` flag that the orchestrator maps (amend `docs/PIPELINE_STAGES_12_14_15.md` §E.2/§E.3/§E.4, do not duplicate). **This raises the job-failure rate, stated plainly** — justified because a wrong contact frame propagates silently through Stages 12–16 and comes out looking normal, with a pre-committed revisit trigger if the all-rejected rate exceeds ~20 %. **Not a threshold change:** confidence was already 0.043, below the 0.35 partial threshold, and was returned anyway — confidence gates tone and ball speed, never selection, so driving penalties to 0.0 leaves `frame_index = 71` untouched; what is missing is a second candidate and no constant creates one. **`backend/tests/integration/test_pipeline_end_to_end.py::test_contact_matches_the_ground_truth_band` is `xfail(strict=True)` and will XPASS — i.e. FAIL the suite — the moment this lands; removing the marker is a required step of the same commit.** `test_gate_wrist_behind_mid_hip_lowers_confidence` and `test_gate_arm_not_extended_lowers_confidence` must change; `test_gate_contact_near_clip_end_lowers_confidence` and `test_gate_subject_identity_unstable_lowers_confidence` must NOT. **Re-validation: §9.1's "stable" verdict does not transfer** — it used hand-centred windows (not Stage 5's motion scan) and was serve-light, so §9.2.8 requires re-running the 5 existing labelled events with the `+1` residual unchanged, then extending ground truth to ≥ 8 serves and ≥ 8 forehands on Stage-5-placed windows after Defects 1 and 2 land. **Not fixed.** |
| **Defect ordering** | The three defect fixes above are sequenced **Stage 5 → Stage 7 → Stage 9** and the order is load-bearing, not cosmetic: Stage 7's core-window anchor is computed inside a window Stage 5 places, and Stage 9's candidate list is computed over frames Stage 7 admits. Fixing them in any other order measures each change against an input that is about to change again, and re-validates nothing. |
