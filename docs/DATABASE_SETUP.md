# TennisForm AI — Database and API Contract Setup

Status: reconstruction, 2026-09-19. This file has been lost twice because it was
never committed. **Commit it in the same commit that creates it.**

Authority order when this file and another disagree:
1. Code on disk (`backend/app/models/`, `frontend/lib/`) — what is actually true.
2. `docs/PIPELINE.md` — the master spec, tracked in git.
3. This file — binding only for the parts PIPELINE.md does not cover:
   the HTTP error envelope (Part 3.0), `GET /v1/analyses` (Part 3.4),
   and the DDL/RLS (Parts 1-2).

Anti-fabrication stance, restated because it drives half the nullability
decisions below: **`0.0` is a real measured value and is not the same as
`NULL`.** A column that can be "not measurable" is `NULL`, never `0`, never a
sentinel, never a default. No column in this schema has a `DEFAULT` that stands
in for a measurement.

---

## Part 0 — What exists on disk today (verified 2026-09-19)

| Thing | State |
|---|---|
| `backend/app/models/enums.py` | EXISTS. Contains `MetricVerdict`, `MetricUnit`, `SwingPhaseName`, `FeedbackSource`, `BallSpeedConfidence`, `BallSpeedUnavailableReason`, `Handedness`, `HandednessSource`, `CameraView`. **No `JobStatus`, no `AnalysisStatus`, no `ErrorCode`, no `ShotType`, no `ScoreCategory`, no `CourtReference`.** |
| `backend/app/models/responses.py` | EXISTS. Contains `PoseQuality`, `HandednessResult`, `ContactDetection`, `BallSpeedResult`, `SwingPhase`, `SwingPhases`, `SwingMetrics`. **No `AnalysisResponse`, `AnalysisJobStatusResponse`, `AnalysisError`, `Scorecard`, `MetricScore`, `ShotTypeInference`, `CoachingFeedback`, `StageTimings`.** |
| `backend/app/api/` | **DOES NOT EXIST.** No package, no `__init__.py`, no routes, no `auth.py`. |
| `backend/app/main.py` | **DOES NOT EXIST.** No ASGI app object. |
| `backend/app/config.py` | **DOES NOT EXIST.** `Settings` is specified in PIPELINE.md §3.4 and implemented nowhere. `max_upload_bytes = 52_428_800` is therefore a *spec* constant (PIPELINE.md §0.3, §2.2, §3.4), **not** a confirmed value in code. |
| `backend/app/services/` | DOES NOT EXIST (`orchestrator.py`, `storage.py`, `repository.py` all planned). |
| Supabase schema | Unknown to this document. Treat Part 1 as the authoritative creation script; if tables already exist, diff them against it before running anything. |
| `frontend/lib/services/api_client.dart` | EXISTS, 435 lines, and is the de-facto client contract. Part 4 is the cross-check against it. |

**Consequence:** Part 3 is not documentation of a built system. It is a
specification the backend has to be built against, and it is the thing the rest
of the project is blocked on.

---

## Part 1 — Postgres DDL

Run in the Supabase SQL editor as the project owner. `pgcrypto` (for
`gen_random_uuid()`) is pre-installed on Supabase projects; the `CREATE
EXTENSION` is included for a non-Supabase Postgres.

Columns are exactly those in PIPELINE.md line 1151-1152, plus three additions
that are flagged inline and repeated in Part 6.

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- analysis_jobs : one row per POST /v1/analyses. Source of truth for job state.
-- PIPELINE.md §1 "the source of truth for job state is the Supabase
-- analysis_jobs row, not process memory".
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.analysis_jobs (
    id             uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        uuid        NOT NULL
                               REFERENCES auth.users (id) ON DELETE CASCADE,
    storage_path   text        NOT NULL,

    status         text        NOT NULL DEFAULT 'queued',

    -- NULL unless status = 'failed'. See the CHECK below.
    error_code     text,
    error_message  text,

    -- ADDITION (not in PIPELINE.md line 1151). AnalysisError (§2.3) carries a
    -- `stage` field, and the poll endpoint must be able to rebuild that object
    -- from the row alone. Without this column `stage` would have to be
    -- fabricated at read time, which the anti-fabrication rule forbids.
    error_stage    text,

    -- Liveness. Written by the running job; read by the staleness rule
    -- (Part 3.5). NULL while queued -- a queued job has no worker to beat.
    heartbeat_at   timestamptz,

    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),

    -- JobStatus (PIPELINE.md §2.1) enforced at the database level.
    CONSTRAINT analysis_jobs_status_check
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),

    -- ErrorCode (PIPELINE.md §2.1 + the two members added in Part 3.0).
    -- Deliberately the FULL set, not the job-terminal subset. A too-narrow
    -- CHECK turns a job failure into an INSERT failure, which destroys the
    -- error message and leaves the client polling a row that never moves.
    -- Breadth here is cheap; precision here is dangerous.
    CONSTRAINT analysis_jobs_error_code_check
        CHECK (error_code IS NULL OR error_code IN (
            'auth_invalid_token', 'storage_path_forbidden', 'object_not_found',
            'storage_unavailable', 'file_too_large', 'unsupported_content_type',
            'unsupported_codec', 'decode_failed', 'no_video_stream',
            'video_too_short', 'video_too_long', 'no_pose_detected',
            'pose_quality_too_low', 'contact_not_found', 'calibration_invalid',
            'queue_full', 'worker_lost', 'internal_error',
            'invalid_request', 'analysis_not_found'
        )),

    -- A failed job MUST say why; a non-failed job MUST NOT carry a stale code.
    CONSTRAINT analysis_jobs_failure_shape_check
        CHECK (
            (status = 'failed'  AND error_code IS NOT NULL) OR
            (status <> 'failed' AND error_code IS NULL)
        )
);

-- ---------------------------------------------------------------------------
-- analyses : one row per SUCCEEDED job. id == analysis_jobs.id (PIPELINE.md
-- line 1152: "id (= job id)"), so the client's single analysis_id addresses
-- both the job and the result and the poll endpoint needs no join key.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.analyses (
    id               uuid        PRIMARY KEY
                                 REFERENCES public.analysis_jobs (id)
                                 ON DELETE CASCADE,
    user_id          uuid        NOT NULL
                                 REFERENCES auth.users (id) ON DELETE CASCADE,
    storage_path     text        NOT NULL,

    -- ADDITION (not in PIPELINE.md line 1152). Present so the AnalysisStatus
    -- value set can be enforced by the database, which is what makes the
    -- Part 4.4 discriminator invariant structurally impossible to violate on
    -- the write path rather than merely forbidden in prose.
    status           text        NOT NULL,

    shot_type        text        NOT NULL,

    -- NULL means "no score could be produced". 0 would mean "scored zero".
    -- These are different facts and the schema keeps them different.
    overall_score    numeric(5,2),

    -- integer, NOT numeric: PIPELINE.md §2.5 layer 2 of the whole-number
    -- enforcement. NULL = not measured (uncalibrated clip, or an
    -- unavailable_reason). Never 0.
    ball_speed_mph   integer,

    pipeline_version text        NOT NULL,
    rubric_version   text        NOT NULL,

    -- The full AnalysisResponse. Schema evolution without a backfill.
    payload          jsonb       NOT NULL,

    created_at       timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT analyses_status_check
        CHECK (status IN ('complete', 'partial')),

    CONSTRAINT analyses_shot_type_check
        CHECK (shot_type IN (
            'forehand_topspin', 'forehand_slice', 'backhand_one_handed',
            'backhand_two_handed', 'serve', 'volley', 'unknown'
        )),

    CONSTRAINT analyses_overall_score_range_check
        CHECK (overall_score IS NULL OR (overall_score >= 0 AND overall_score <= 100)),

    -- Stage 11.4 plausibility bound restated at the storage boundary, matching
    -- the StrictInt ge=15 le=160 in responses.py.
    CONSTRAINT analyses_ball_speed_range_check
        CHECK (ball_speed_mph IS NULL OR (ball_speed_mph >= 15 AND ball_speed_mph <= 160))
);
```

### 1.1 Indexes

```sql
-- THE KEYSET INDEX. This is the one that matters.
-- ORDER BY created_at DESC, id DESC with a WHERE user_id = $1 predicate walks
-- this index in a single forward scan. Column order and the DESC on BOTH sort
-- columns are load-bearing: with (user_id, created_at DESC) alone, the id
-- tie-break forces a Sort node back in.
CREATE INDEX IF NOT EXISTS idx_analyses_user_created_id
    ON public.analyses (user_id, created_at DESC, id DESC);

-- Poll-adjacent: the job lookup is by primary key, so no index is needed for
-- GET /v1/analyses/{id}. This one serves the queue-position calculation and
-- any per-user job audit.
CREATE INDEX IF NOT EXISTS idx_analysis_jobs_user_created
    ON public.analysis_jobs (user_id, created_at DESC);

-- Staleness sweep + queue-depth admission check. PARTIAL: succeeded and failed
-- jobs are the overwhelming majority of the table within a week and are never
-- scanned by either query.
CREATE INDEX IF NOT EXISTS idx_analysis_jobs_active
    ON public.analysis_jobs (status, heartbeat_at)
    WHERE status IN ('queued', 'running');

-- "Personal best" and the production-health query in PIPELINE.md line 1154.
-- NULLS LAST so an uncalibrated clip never sorts to the top of a best-speed
-- list.
CREATE INDEX IF NOT EXISTS idx_analyses_user_ball_speed
    ON public.analyses (user_id, ball_speed_mph DESC NULLS LAST);
```

**Verification, not assumption.** After seeding, confirm the keyset query plans
as an index scan and not a sort:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, created_at, shot_type, overall_score, ball_speed_mph
FROM public.analyses
WHERE user_id = '00000000-0000-0000-0000-000000000000'
  AND (created_at, id) < ('2026-09-01T00:00:00Z', '00000000-0000-0000-0000-000000000000')
ORDER BY created_at DESC, id DESC
LIMIT 50;
```

Expected: `Index Scan using idx_analyses_user_created_id`. **A `Sort` node in
that plan means the index is wrong, not that the query is slow** — fix the index
before shipping, because the cost is invisible at 10 rows and quadratic in the
user's history length.

Row-comparison note: `(created_at, id) < (ts, id)` is Postgres's row-wise
comparison, which is exactly the lexicographic semantics keyset pagination
needs. Do **not** rewrite it as
`created_at < ts OR (created_at = ts AND id < cid)` — it is the same result but
the planner handles the row-comparison form better against a multi-column index.

### 1.2 `updated_at` maintenance

```sql
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_analysis_jobs_updated_at ON public.analysis_jobs;
CREATE TRIGGER trg_analysis_jobs_updated_at
    BEFORE UPDATE ON public.analysis_jobs
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
```

`analyses` has no `updated_at`: a stored analysis is immutable. A re-analysis is
a new job and a new row, so the history list is an append-only log.

---

## Part 2 — RLS policies

### 2.1 What RLS does and does not protect here — read this before writing a policy

**The backend holds the Supabase service-role key. The service-role key bypasses
RLS entirely.** Every query the FastAPI service makes against `analysis_jobs`
and `analyses` runs with RLS switched off. This is not a misconfiguration to be
fixed; it is required, because the backend writes rows on behalf of a user
inside a background job where no user JWT is in scope.

Therefore, stated plainly and without hedging:

- **RLS does not protect the backend path.** It cannot. It is not consulted.
- **The primary access control for every API route is the backend's own
  ownership check**, and there are exactly two of them:
  1. Stage 1 chooses the storage path (`swing-videos/{user_id}/{uuid4}.{ext}`),
     and Stage 3 verifies the submitted `storage_path` starts with
     `swing-videos/{jwt.sub}/` **before any download**. A prefix string
     comparison, no round trip.
  2. Every read of `analysis_jobs` / `analyses` filters on
     `user_id = jwt.sub`, and a row belonging to another user is reported as
     `404 analysis_not_found` — not `403`, so the endpoint does not confirm the
     id exists.
- **RLS is defense-in-depth for the direct-client path.** The Flutter app holds
  a Supabase anon key and a user JWT and can talk to PostgREST directly. It does
  so today for auth and will do so for Storage uploads. RLS is what stops that
  key from reading another user's rows, and what limits the blast radius if a
  future screen adds a direct table read.

If both controls are removed, the backend's checks are the ones whose absence is
exploitable. Review them accordingly.

### 2.2 Policies

```sql
ALTER TABLE public.analysis_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.analyses      ENABLE ROW LEVEL SECURITY;

-- Belt and braces: FORCE makes RLS apply even to the table owner. It does NOT
-- apply to service_role, which holds BYPASSRLS.
ALTER TABLE public.analysis_jobs FORCE ROW LEVEL SECURITY;
ALTER TABLE public.analyses      FORCE ROW LEVEL SECURITY;

-- Read-only for the signed-in user. There is deliberately NO insert, update or
-- delete policy for `authenticated`: all writes go through the backend, which
-- is service_role. A client that can INSERT into analyses can fabricate a
-- coaching report, which is the exact failure mode this project's
-- anti-fabrication stance exists to prevent.
CREATE POLICY analysis_jobs_select_own
    ON public.analysis_jobs
    FOR SELECT
    TO authenticated
    USING (user_id = auth.uid());

CREATE POLICY analyses_select_own
    ON public.analyses
    FOR SELECT
    TO authenticated
    USING (user_id = auth.uid());

-- anon has no business here at all.
REVOKE ALL ON public.analysis_jobs FROM anon;
REVOKE ALL ON public.analyses      FROM anon;
```

### 2.3 Storage bucket

```sql
-- Private bucket. Public = true would make every swing video world-readable by
-- URL, which defeats every policy above.
INSERT INTO storage.buckets (id, name, public)
VALUES ('swing-videos', 'swing-videos', false)
ON CONFLICT (id) DO NOTHING;

-- The first path segment is the owner's uuid, which is exactly what Stage 1
-- guarantees and Stage 3 re-checks.
CREATE POLICY swing_videos_insert_own
    ON storage.objects FOR INSERT TO authenticated
    WITH CHECK (
        bucket_id = 'swing-videos'
        AND (storage.foldername(name))[1] = auth.uid()::text
    );

CREATE POLICY swing_videos_select_own
    ON storage.objects FOR SELECT TO authenticated
    USING (
        bucket_id = 'swing-videos'
        AND (storage.foldername(name))[1] = auth.uid()::text
    );
```

The backend downloads the object with the service-role key (Stage 4), so these
policies govern the phone's direct upload only.

---

## Part 3 — The API contract

Base URL: the deployed service. All paths are absolute and versioned under
`/v1`. All bodies are `application/json; charset=utf-8`. All timestamps are
RFC 3339 / ISO 8601 with an explicit UTC offset. All ids are UUIDv4 strings.

Every route requires `Authorization: Bearer <supabase_jwt>`, verified per
PIPELINE.md Stage 2 (ES256, `aud=authenticated`, `iss={SUPABASE_URL}/auth/v1`,
`PyJWKClient` built once at startup on `app.state`). A missing or bad token is
`401 auth_invalid_token`.

### 3.0 The error envelope — flat, not FastAPI's default

**Every non-2xx response from every route has this body and no other shape.**

```json
{
  "error_code": "storage_path_forbidden",
  "message": "That video does not belong to this account.",
  "retryable": false,
  "request_id": "0f1c9e2a7b4d4c8e9a3f5b6c7d8e9f01"
}
```

| Field | Type | Meaning |
|---|---|---|
| `error_code` | string, an `ErrorCode` member | Machine-readable. The client maps it to plain language via `errorCodeToPlainLanguage` in `frontend/lib/models/enums.dart`. |
| `message` | string | Human-readable, English, safe to show. Never contains a stack trace, a storage path, or another user's id. |
| `retryable` | bool | Whether repeating the identical request could plausibly succeed. See the table below; it is a per-code constant, not a runtime guess. |
| `request_id` | string | Correlates the response with the server log line. Also emitted as the `X-Request-Id` response header. |

**This is not what FastAPI produces by default, and getting it requires work
that is easy to skip.** FastAPI returns `{"detail": ...}` for `HTTPException`
and a *list* of validation-error dicts under `detail` for
`RequestValidationError`, with status `422`. Both must be overridden:

1. `@app.exception_handler(HTTPException)` — rebuild the flat envelope. Raising
   `HTTPException(status_code=403, detail="...")` from a route is only
   acceptable if the handler can recover the `ErrorCode`; the cleaner path is a
   project-specific `ApiError(error_code, http_status, message)` exception plus a
   handler for it, with the `HTTPException` handler kept as a net for anything
   Starlette raises internally (404 on an unknown path, 405, etc.).
2. `@app.exception_handler(RequestValidationError)` — **this is the one that
   gets forgotten.** A Pydantic failure on `CreateAnalysisRequest` or
   `UploadTicketRequest` must come back as **`400` with
   `error_code: "invalid_request"`**, never as a bare `422` carrying FastAPI's
   default list body. Two independent reasons: `422` is not in the client's
   error taxonomy and would surface as a raw "Server error (422)."; and the
   default body leaks the internal field paths of the request model.
3. `@app.exception_handler(Exception)` — anything unhandled becomes `500`
   `internal_error` with a generic message and the real exception logged
   against `request_id`.

The route decorators should declare `responses={...}` pointing at the envelope
model so the generated OpenAPI does not advertise FastAPI's default error
schema. This is documentation hygiene, not behaviour, but the Flutter models are
read off that schema by a human and a wrong schema costs a day.

`request_id` is generated in an ASGI middleware, stored in a `ContextVar`, put on
the response header for *all* responses including 2xx, and injected into the
envelope by the handlers.

#### 3.0.1 `ErrorCode` — complete, with HTTP status and retryability

PIPELINE.md §2.1 lists 18 members. **Two more are required and must be added to
`backend/app/models/enums.py`:**

- `INVALID_REQUEST = "invalid_request"` — request body or query string failed
  validation. Covers `RequestValidationError` and a malformed pagination cursor.
  Without it, every Pydantic failure has no code at all.
- `ANALYSIS_NOT_FOUND = "analysis_not_found"` — the polled id does not exist
  **or is not owned by the caller**. PIPELINE.md Stage 19 specifies `404` for
  both cases but names no code, so the response would have had to omit
  `error_code`, and the client's `_failureFromResponse` would produce a null
  `errorCode` and fall through to the raw message.

| `ErrorCode` | HTTP | `retryable` | Raised at |
|---|---|---|---|
| `invalid_request` | 400 | false | any route, Pydantic/query validation |
| `auth_invalid_token` | 401 | false | Stage 2, all routes |
| `storage_path_forbidden` | 403 | false | Stage 3 prefix check |
| `analysis_not_found` | 404 | false | Stage 19, history detail |
| `object_not_found` | 404 | false | Stage 3 head / Stage 4 download |
| `file_too_large` | 413 | false | Stage 1 declared size; Stage 4 actual size |
| `unsupported_content_type` | 400 | false | Stage 1 |
| `calibration_invalid` | 400 | false | Stage 3, synchronous |
| `queue_full` | 429 | **true** | Stage 3, depth > 4 |
| `storage_unavailable` | 503 | **true** | Stage 1, Stage 4 |
| `unsupported_codec` | job-only | false | Stage 5 |
| `decode_failed` | job-only | false | Stage 5 |
| `no_video_stream` | job-only | false | Stage 5 |
| `video_too_short` | job-only | false | Stage 5 |
| `video_too_long` | job-only | false | Stage 5 |
| `no_pose_detected` | job-only | false | Stage 6 |
| `pose_quality_too_low` | job-only | false | Stage 7 |
| `contact_not_found` | job-only | false | Stage 9 |
| `worker_lost` | job-only | **true** | staleness rule, Part 3.5 |
| `internal_error` | 500 | **true** | anywhere |

"job-only" means the code never appears in an HTTP error envelope. It appears
inside the `error` object of a `200 OK` poll response whose `status` is
`"failed"`. The poll request itself succeeded; the job did not. Conflating those
two is the most common way this contract gets implemented wrong.

`429 queue_full` should carry a `Retry-After: 30` header alongside the envelope.

---

### 3.1 `POST /v1/uploads/ticket`

PIPELINE.md Stage 1. Issues a signed Supabase Storage upload destination. The
**path is chosen by the server**: `swing-videos/{user_id}/{uuid4}.{ext}`, where
`ext` is derived from `content_type` (`video/mp4` → `mp4`,
`video/quicktime` → `mov`). The client never proposes a path, which is what
makes the Stage 3 ownership check a prefix comparison.

**Request**

```json
{
  "content_type": "video/mp4",
  "size_bytes": 18452113,
  "duration_s": 6.24
}
```

| Field | Type | Rule |
|---|---|---|
| `content_type` | string | must match `^video/(mp4|quicktime)$` |
| `size_bytes` | int | `> 0` and `<= 52428800` (50 MiB) |
| `duration_s` | float | `> 0.0` and `<= 60.0` |

`extra="forbid"` — an unexpected key is `400 invalid_request`, not a silent
ignore.

**The 50 MiB cap is 52_428_800 bytes.** Provenance, stated honestly: it is
specified in `docs/PIPELINE.md` §0.3, §2.2 and §3.4. **It is not confirmed in
`backend/app/config.py`, because that file does not exist yet.** When `Settings`
is written, `max_upload_bytes: int = 52_428_800` must be the single source and
both the Pydantic `le=` and the ticket route must read it from there. The cap
matches the Supabase free-tier per-file limit — a larger API cap would be
accepted here and then rejected by Storage, which is a worse failure because it
happens after the upload.

**Response 200**

```json
{
  "storage_path": "swing-videos/b1d9f0a2-1c4e-4a77-8f21-0f9a2c3d4e55/2f7c9a11-4e3b-4a1d-9c22-77bd0e1f3a90.mp4",
  "upload_url": "https://<project>.supabase.co/storage/v1/object/upload/sign/swing-videos/...",
  "upload_token": "eyJhbGciOi...",
  "expires_at": "2026-09-19T12:34:56Z"
}
```

The client reads `storage_path`, `upload_url`, `upload_token`
(`api_client.dart:191-195`). It does **not** currently read `expires_at`; send it
anyway — PIPELINE.md §2.2 specifies it and it is the only way a client can ever
distinguish an expired ticket from a broken one.

**Errors:** `401 auth_invalid_token`, `400 unsupported_content_type`,
`400 invalid_request`, `413 file_too_large`, `503 storage_unavailable`.

---

### 3.2 `POST /v1/analyses`

PIPELINE.md Stage 3. Creates the job row, submits to the single-worker executor,
returns immediately.

**Request**

```json
{
  "storage_path": "swing-videos/b1d9f0a2-1c4e-4a77-8f21-0f9a2c3d4e55/2f7c9a11-4e3b-4a1d-9c22-77bd0e1f3a90.mp4",
  "handedness_hint": "right",
  "camera_view_hint": "side_on",
  "label_hint": "forehand_topspin",
  "client_capture_fps": 30.0,
  "ball_speed_calibration": {
    "point_a": { "x": 0.182, "y": 0.744 },
    "point_b": { "x": 0.617, "y": 0.488 },
    "reference": "sideline_baseline_to_net",
    "distance_m": 11.885,
    "capture_width_px": 1080,
    "capture_height_px": 1920,
    "capture_rotation_deg": 0,
    "tapped_at": "2026-09-19T12:30:11Z"
  }
}
```

The Flutter client sends `storage_path`, `handedness_hint`, `camera_view_hint`,
`label_hint` and optionally `ball_speed_calibration`
(`api_client.dart:221-228`). It does **not** send `client_capture_fps`; that
field is optional and advisory (real timing comes from PTS) so its absence is
correct, not a gap.

Synchronous validation, in order, each failing before anything is downloaded:

1. **Ownership.** `storage_path` must start with `swing-videos/{jwt.sub}/`.
   Otherwise `403 storage_path_forbidden`. This happens **before** any Storage
   call, so a hostile path is never even dereferenced.
2. **Existence and size.** Storage `HEAD` on the object →
   `404 object_not_found`, `413 file_too_large`, `503 storage_unavailable`.
3. **Calibration** (only when the block is present), per Stage 3:
   - the two points at least `0.05` apart in normalized units;
   - if `reference != "custom"`, `distance_m` within `0.01 m` of
     `COURT_REFERENCE_METRES[reference]` — a mismatch is
     `400 calibration_invalid`, **never a silent server-side override**, because
     the mismatch means the client build is desynced and silently correcting it
     hides that;
   - `capture_width_px` / `capture_height_px` positive and their aspect ratio in
     `[0.4, 2.5]`.
4. **Queue depth.** If `count(status IN ('queued','running')) >= 4`, respond
   `429 queue_full`, `retryable: true`, `Retry-After: 30`. Depth 4 is
   `job_queue_max_depth` in PIPELINE.md §3.4.

**Absence of `ball_speed_calibration` is not an error and never blocks
anything.** It skips Stages 10 and 11, and the finished analysis carries
`ball_speed.ball_speed_mph = null` with
`unavailable_reason = "not_calibrated"`. The majority of sessions will take this
path and nothing in the API may treat it as degraded.

**Response 202**

```json
{
  "analysis_id": "9f2c1a44-3b7e-4f0d-9a11-6c5c2e8b71aa",
  "status": "queued",
  "poll_url": "/v1/analyses/9f2c1a44-3b7e-4f0d-9a11-6c5c2e8b71aa",
  "estimated_seconds": 25,
  "ball_speed_requested": true
}
```

`status` is always `"queued"` here — the row is written before submission, and
the executor has not necessarily picked it up.

`estimated_seconds` is `25` base (PIPELINE.md §1.20: deliberately padded) plus
roughly 25 s per job already ahead in the queue. It is an estimate, is labelled
as one, and on a cold start (dev tier, `--min-instances=0`) it **will read low** —
a cold job totals ~24-52 s. That is accepted and documented, not hidden.

`ball_speed_requested` echoes whether a calibration block was supplied *and
accepted*, so the client can show the right placeholder while polling.

**Errors:** `400 invalid_request`, `400 calibration_invalid`,
`401 auth_invalid_token`, `403 storage_path_forbidden`, `404 object_not_found`,
`413 file_too_large`, `429 queue_full`, `503 storage_unavailable`.

---

### 3.3 `GET /v1/analyses/{analysis_id}`

PIPELINE.md Stage 19. **Polymorphic**: the body shape depends on job state. The
client polls this every 2 s and gives up at 120 s
(`api_client.dart:15-16`).

Ownership: the row's `user_id` must equal `jwt.sub`. A mismatch and a missing id
both return `404 analysis_not_found` with an identical body — the endpoint does
not reveal that an id exists.

**Body A — job in progress or failed (`status` in `queued`, `running`,
`failed`)**

```json
{
  "analysis_id": "9f2c1a44-3b7e-4f0d-9a11-6c5c2e8b71aa",
  "status": "running",
  "queue_position": null,
  "estimated_seconds_remaining": 14,
  "error": null
}
```

Queued:

```json
{
  "analysis_id": "9f2c1a44-3b7e-4f0d-9a11-6c5c2e8b71aa",
  "status": "queued",
  "queue_position": 2,
  "estimated_seconds_remaining": 50,
  "error": null
}
```

Failed:

```json
{
  "analysis_id": "9f2c1a44-3b7e-4f0d-9a11-6c5c2e8b71aa",
  "status": "failed",
  "queue_position": null,
  "estimated_seconds_remaining": null,
  "error": {
    "code": "contact_not_found",
    "message": "No clear ball-strike was found in the clip.",
    "stage": "contact_detection",
    "retryable": false
  }
}
```

Note the nested key is **`code`**, not `error_code` — this matches
`AnalysisError` in PIPELINE.md §2.3 and is what
`api_client.dart:306` reads. See Part 4.1: this inconsistency with the flat
envelope is a real wart and a decision is recorded there.

HTTP status for Body A is **always `200`**, including for `status: "failed"`.
The poll succeeded.

**Body B — job succeeded**

The full `AnalysisResponse` (PIPELINE.md §2.4), returned verbatim from
`analyses.payload`, `200 OK`. Its `status` is an **`AnalysisStatus`** —
`"complete"` or `"partial"` — and it carries the `scorecard` and `feedback`
blocks. See the invariant in Part 4.4, which is the single most important rule
on this endpoint.

**Errors:** `401 auth_invalid_token`, `404 analysis_not_found`,
`500 internal_error`.

---

### 3.4 `GET /v1/analyses` — history, keyset pagination

**This endpoint does not appear in PIPELINE.md.** It is specified here for the
first time. `frontend/lib/services/api_client.dart:377` already calls it, so the
client is the only existing constraint and the design below is shaped to fit it.

**Query parameters**

| Param | Type | Default | Rule |
|---|---|---|---|
| `limit` | int | `50` | `1..100`. Out of range → `400 invalid_request`. |
| `cursor` | string | absent | Opaque. Echo of a previous `next_cursor`. Undecodable → `400 invalid_request`. |

Ordering is **newest first**, fixed: `ORDER BY created_at DESC, id DESC`. There
is no `sort` parameter; a second ordering would need a second index and the
product has one history view.

**Cursor format.** `base64url("<created_at>|<id>")`, unpadded, where
`<created_at>` is the RFC 3339 UTC timestamp of the last row on the page and
`<id>` is its uuid. Example: the pair
`2026-09-14T09:12:44.118000+00:00|9f2c1a44-3b7e-4f0d-9a11-6c5c2e8b71aa`
encodes to
`MjAyNi0wOS0xNFQwOToxMjo0NC4xMTgwMDArMDA6MDB8OWYyYzFhNDQtM2I3ZS00ZjBkLTlhMTEtNmM1YzJlOGI3MWFh`.

The cursor is **opaque to the client** — it is documented here so the server can
be tested, not so the app can construct one. It is not signed; it leaks only the
caller's own last-seen timestamp and id, and it is scoped by `user_id` on the
server, so a forged cursor can only page through the forger's own rows. **It is
therefore not a security boundary and must never be trusted as one:** the
`user_id = $jwt_sub` predicate is what enforces isolation, and it is applied
regardless of cursor contents.

**Predicate.** With a cursor:

```sql
SELECT id, created_at, shot_type, overall_score, ball_speed_mph, pipeline_version
FROM public.analyses
WHERE user_id = $1
  AND (created_at, id) < ($2::timestamptz, $3::uuid)
ORDER BY created_at DESC, id DESC
LIMIT $4 + 1;
```

Without a cursor, drop the second `AND`. Fetch `limit + 1` rows: if the extra
row comes back, there is another page, so emit `next_cursor` built from the
*last returned* row (the `limit`-th, not the extra one) and discard the extra.
If it does not, `next_cursor` is `null`.

Keyset, not `OFFSET`, for two reasons that both bite in this product: `OFFSET n`
makes the server read and discard `n` rows, so page 20 costs 20 pages of work;
and an analysis finishing between two page requests shifts every offset by one,
which duplicates or skips a row. Keyset is stable under concurrent inserts
because a new row sorts *above* the cursor and is simply not on any later page.

**Response 200**

```json
{
  "items": [
    {
      "analysis_id": "9f2c1a44-3b7e-4f0d-9a11-6c5c2e8b71aa",
      "created_at": "2026-09-14T09:12:44.118Z",
      "shot_type": "forehand_topspin",
      "overall_score": 74.5,
      "ball_speed_mph": 68,
      "pipeline_version": "v3"
    },
    {
      "analysis_id": "3c8a0f61-55d2-4b19-8f70-1ab2c3d4e5f6",
      "created_at": "2026-09-13T17:02:08.900Z",
      "shot_type": "backhand_two_handed",
      "overall_score": null,
      "ball_speed_mph": null,
      "pipeline_version": "v2"
    }
  ],
  "next_cursor": "MjAyNi0wOS0xM1QxNzowMjowOC45MDAwMDArMDA6MDB8M2M4YTBmNjEtNTVkMi00YjE5LThmNzAtMWFiMmMzZDRlNWY2"
}
```

**The rows are FLAT.** Six keys, all top-level, no nesting. `shot_type` is a
bare string (the indexed column), not a `ShotTypeInference` object.
`ball_speed_mph` is a bare integer or `null`, not a `BallSpeedResult` object.
See Part 4.5 for why, and for the explicit prohibition on returning full
`AnalysisResponse` objects here.

`overall_score: null` means no score was produced. `ball_speed_mph: null` means
no speed was measured — because the clip was uncalibrated, or because a
`BallSpeedUnavailableReason` applied. **Neither is ever rendered as `0`, and the
server never substitutes `0` for either.** The list row deliberately does not
carry `unavailable_reason`: explaining *why* there is no speed is the detail
screen's job, and a list row that says "not measurable because depth drift
exceeded" is noise.

**`pipeline_version`** is a string, always present and never `null`, read from
the `analyses.pipeline_version` column (`text NOT NULL`, Part 1 -- so no stored
row can lack it). It names the pipeline that produced the row's score. It exists
for **trend comparisons across a scoring change**: when the measurement itself
changes, `PIPELINE_VERSION` is bumped (`v2` -> `v3` fixed swing-path angle on
leftward-travelling swings, which used to read ~155 deg instead of ~25 deg), and
an old `v2` score and a new `v3` score are not comparable -- plotting them on one
line would show an "improvement" that is really the fix. Clients that trend
scores over time must compare only rows with the same `pipeline_version`.

The envelope key is `items`. The client accepts `items`, `analyses`, `results`,
or a bare list (`api_client.dart:393-400`); `items` is the one to emit, and the
tolerance is not a licence to change it later.

**Errors:** `400 invalid_request` (bad `limit`, undecodable `cursor`),
`401 auth_invalid_token`, `500 internal_error`.

---

### 3.5 Background job lifecycle

**State machine.** Four states, three legal transitions, no others:

```
queued ──> running ──> succeeded
             │
             └───────> failed
```

| Transition | Trigger | Writes |
|---|---|---|
| (insert) → `queued` | `POST /v1/analyses` accepted | row inserted, `heartbeat_at = NULL` |
| `queued` → `running` | executor picks the job up | `status='running'`, `heartbeat_at = now()` |
| `running` → `succeeded` | Stage 18 persists the `analyses` row | `analyses` insert and the `analysis_jobs` update in **one transaction** |
| `running` → `failed` | any `ErrorCode` raised in Stages 4-18 | `status='failed'`, `error_code`, `error_message`, `error_stage` |
| `queued` → `failed` | never, except by the staleness rule below | |

`succeeded` and `failed` are terminal. A job never returns to `queued`. Retry is
a **new** `POST /v1/analyses` producing a **new** `analysis_id`; there is no
retry endpoint and no in-place restart, because a restart would make
`analysis_id` no longer identify one execution.

The `succeeded` transition and the `analyses` insert must be atomic. If they are
not, a crash between them leaves a job reporting `succeeded` with no row to
return, and the poll endpoint 500s forever on an id the client will keep asking
for.

**Heartbeat.** While `running`, the job writes `heartbeat_at = now()` at each
stage boundary (after download, after decode, after pose, after ball detection,
after analysis, after feedback). Stage boundaries rather than a timer because
they need no extra thread and because a job stuck *inside* a stage is exactly
what the rule must catch. The longest gap between two boundaries is Stage 6 at
8-13 s warm, and up to ~16-26 s if the rotation retry fires — an order of
magnitude inside the window below.

**Staleness rule → `WORKER_LOST`.** A job with `status = 'running'` whose
`heartbeat_at` is older than **180 s** (`job_heartbeat_stale_s`) is dead. Its
worker was killed by an instance restart, a revision deploy, or a Cloud Run
scale event. It will never finish and nothing will ever update its row.

Evaluation is on the **read path**, at poll time:

```sql
SELECT *,
       (status = 'running'
        AND heartbeat_at IS NOT NULL
        AND heartbeat_at < now() - interval '180 seconds') AS is_stale
FROM public.analysis_jobs
WHERE id = $1 AND user_id = $2;
```

When `is_stale`, the poll responds as though the job had failed:

```json
{
  "analysis_id": "9f2c1a44-3b7e-4f0d-9a11-6c5c2e8b71aa",
  "status": "failed",
  "queue_position": null,
  "estimated_seconds_remaining": null,
  "error": {
    "code": "worker_lost",
    "message": "The analysis was interrupted. Try again.",
    "stage": "unknown",
    "retryable": true
  }
}
```

Read-path evaluation rather than a background sweeper is the MVP choice, and the
reason is the deployment: with `--max-instances=1` and a dev tier that scales to
zero, a background sweeper may not be running when a job goes stale, and two
sweepers could race during a scale event. The read path is always running when
it matters, because something is polling. **The row should also be written back
to `failed`/`worker_lost` on a best-effort basis** so history and metrics are
right, but the response must not depend on that write succeeding.

`stage: "unknown"` is honest here and is not a fabrication: the process that
knew the stage is gone. It is not backfilled with a guess.

**Queue position.** For a `queued` job, `queue_position` is the count of jobs in
`('queued','running')` created strictly before it. `1` means "next".
For a `running` job it is `null`, not `0` — `0` would imply a measured position.

**Queue depth cap.** Admission control at Stage 3 only, `>= 4` → `429`. With
`ThreadPoolExecutor(max_workers=1)`, depth 4 bounds the worst wait at roughly
four jobs × ~25 s ≈ 100 s, which is inside the client's 120 s poll cap. Raising
the cap without raising the client cap converts a clean `429` into a spinner
timeout, so the two numbers move together or not at all.

**Known limitation, stated rather than hidden.** The depth counter and the
executor live in process memory. `--max-instances=1` is what keeps that
correct. On the deployed dev tier (`--min-instances=0`) a scale-from-zero event
can briefly overlap two instances, in which case the cap admits up to 2x depth
and two read paths could both mark the same job `worker_lost`. Both are benign
at zero real users; the fix (a DB-backed `SELECT count(*)` admission check and
an advisory-locked sweep) is recorded in PIPELINE.md §1.20.1a and is explicitly
not built for the MVP.

---

### 3.6 Pydantic models to add — signatures only

In `backend/app/models/responses.py` (or a new `errors.py`):

```python
class ErrorEnvelope(BaseModel):
    error_code: ErrorCode
    message: str
    retryable: bool
    request_id: str
```

In `backend/app/models/responses.py`, per PIPELINE.md §2.3, still missing:

```python
class AnalysisError(BaseModel): ...            # code / message / stage / retryable
class AnalysisJobStatusResponse(BaseModel): ...
class AnalysisResponse(BaseModel): ...
```

New, for Part 3.4:

```python
class AnalysisListItem(BaseModel):
    analysis_id: UUID
    created_at: datetime
    shot_type: ShotType
    overall_score: float | None
    ball_speed_mph: Annotated[StrictInt, Field(ge=15, le=160)] | None
    pipeline_version: str   # always present; see Part 3.4

class AnalysisListResponse(BaseModel):
    items: list[AnalysisListItem]
    next_cursor: str | None
```

`AnalysisJobStatusResponse` must serialize with its `None` fields **present and
null**, not stripped — the client reads `queue_position` and
`estimated_seconds_remaining` positionally and a missing key and a null key are
the same to it, but an explicit null documents the contract. What it must
**never** do is grow a `feedback` or `scorecard` key. See Part 4.4.

### 3.7 `DELETE /v1/account` — permanent account deletion

Deletes the **caller's** account and every piece of their data. The user is
always `jwt.sub`; there is no request body and no way to name another user.

| | |
|---|---|
| Auth | `Authorization: Bearer <supabase_jwt>` (the standard Stage 2 dependency) |
| Request body | none |
| Success | `204 No Content`, empty body |
| `401 auth_invalid_token` | missing / invalid / expired token — nothing is deleted |
| `503 storage_unavailable` (`retryable: true`) | Storage list or delete failed |
| `500 internal_error` (`retryable: true`) | PostgREST or GoTrue Admin call failed |

Errors use the flat envelope of Part 3.0. No new `ErrorCode` was added.

**Order, and why it matters** (`backend/app/api/routes_account.py`):

1. **Storage.** Every object under `swing-videos/{user_id}/`. Supabase Storage
   has no delete-by-prefix call, so this is list-then-delete:
   `POST /storage/v1/object/list/swing-videos` with
   `{"prefix": "<user_id>", "limit": 100, "offset": n, "sortBy": {...}}` (paged;
   names come back relative to the prefix; a sub-folder is an entry with
   `id: null` and is descended into), then
   `DELETE /storage/v1/object/swing-videos` with
   `{"prefixes": ["<user_id>/<name>", ...]}` in batches of 100. Despite the
   field name, `prefixes` are **exact object names**. Storage caps it at 1000
   per request and rejects an empty list (`minItems: 1`), so an empty folder
   sends no delete. The folder is built from the verified token subject only,
   and an empty folder component is refused outright (it would list the whole
   bucket).
2. **Rows.** `DELETE /rest/v1/analyses?user_id=eq.<id>`, then
   `DELETE /rest/v1/analysis_jobs?user_id=eq.<id>`. Explicit, even though the
   documented `user_id` FKs cascade — see "FK status" below. `analyses` first,
   because `analyses.id` references `analysis_jobs.id`; child-first is correct
   whether or not that FK cascades.
3. **Auth user, LAST.** `DELETE /auth/v1/admin/users/<id>` with body
   `{"should_soft_delete": false}`. That is a **hard delete**: GoTrue's
   `adminUserDelete` runs `tx.Destroy(user)` unless `should_soft_delete` is
   true, in which case it only sets `deleted_at`. `false` is also the server
   default; it is sent explicitly anyway. A hard delete means a later sign-up
   with the same email is a new account with a fresh confirmation email.
   Auth goes last because while the auth user exists the person can still
   sign in and retry; deleting it first would strand any data a later step
   failed to remove.

All three calls use the service-role key (`apikey` + `Authorization: Bearer`),
like the rest of the backend.

**Retry safety.** Every step is idempotent: an empty folder lists `[]` and sends
no delete; a name already gone is simply missing from Storage's delete
response; a filtered PostgREST `DELETE` matching zero rows is `204`; GoTrue's
`404 user_not_found` is treated as done. So after a failure at any step, the
client simply retries the whole request. The access token is still valid after
the auth user is gone (ES256 verification is offline), so a retry after full
success returns `204` too.

**Residual risks, stated rather than hidden:**

- *In-flight analysis.* If a job for this user is running when the account is
  deleted, its Stage 18 `persist_success` can land after step 2. With a
  cascading `user_id` FK, a row written between steps 2 and 3 is removed by
  step 3, and one written after step 3 is rejected. With a non-cascading FK, it
  makes step 3 fail, and a retry clears it. With no FK, the row is orphaned.
- *Still-valid access token.* Until it expires (Supabase default 1 h) the old
  JWT still verifies, so `POST /v1/uploads/ticket` could still issue a signed
  URL under the deleted user's prefix. Closing this needs a server-side session
  check. It is not built.

#### FK status for account deletion — verification pending, OPTIONAL cleanup

This is the same gap Part 7.1 records ("`analyses_user_id_fkey` could not be
confirmed") and query 0d in §7.5 partly covers. It is **not a dependency of
`DELETE /v1/account`**, which deletes the rows itself. It matters only for
schema correctness, and in one direction for the endpoint: if a `user_id` FK
exists **without** a cascade, GoTrue's delete fails while rows remain. Step 2
runs first so no rows remain.

The backend cannot check this itself. It reaches Postgres only through
PostgREST, which exposes only `public` / `graphql_public` and not the catalog
(Part 7.1). To check, run this **read-only** query in the Supabase SQL editor:

```sql
-- READ-ONLY. ON DELETE behaviour of every FK on the two tables.
SELECT con.conrelid::regclass            AS table_name,
       con.conname                       AS constraint_name,
       pg_get_constraintdef(con.oid)     AS definition,
       CASE con.confdeltype
            WHEN 'c' THEN 'CASCADE'  WHEN 'a' THEN 'NO ACTION'
            WHEN 'r' THEN 'RESTRICT' WHEN 'n' THEN 'SET NULL'
            WHEN 'd' THEN 'SET DEFAULT' END AS on_delete
FROM pg_constraint con
WHERE con.contype = 'f'
  AND con.conrelid IN ('public.analyses'::regclass, 'public.analysis_jobs'::regclass)
ORDER BY 1, 2;
```

The expected output is three rows, all `CASCADE`:
`analyses_id_fkey` (→ `analysis_jobs(id)`), `analyses_user_id_fkey` and
`analysis_jobs_user_id_fkey` (→ `auth.users(id)`).

**OPTIONAL fix. Run it only if a `user_id` FK is missing or is not
`CASCADE`.** If the live constraint has a different name, put that name in
the `DROP` line. Otherwise `IF EXISTS` does nothing and you end up with two
FKs. First make sure there are no orphans, or the `ADD` will fail:

```sql
-- Orphan check: both counts must be 0 before the ALTERs below.
SELECT (SELECT count(*) FROM public.analyses a
         WHERE NOT EXISTS (SELECT 1 FROM auth.users u WHERE u.id = a.user_id)) AS orphan_analyses,
       (SELECT count(*) FROM public.analysis_jobs j
         WHERE NOT EXISTS (SELECT 1 FROM auth.users u WHERE u.id = j.user_id)) AS orphan_jobs;

BEGIN;
ALTER TABLE public.analyses
    DROP CONSTRAINT IF EXISTS analyses_user_id_fkey,
    ADD  CONSTRAINT analyses_user_id_fkey
         FOREIGN KEY (user_id) REFERENCES auth.users (id) ON DELETE CASCADE;
ALTER TABLE public.analysis_jobs
    DROP CONSTRAINT IF EXISTS analysis_jobs_user_id_fkey,
    ADD  CONSTRAINT analysis_jobs_user_id_fkey
         FOREIGN KEY (user_id) REFERENCES auth.users (id) ON DELETE CASCADE;
COMMIT;
```

---

## Part 4 — Cross-check against `frontend/lib/services/api_client.dart`

This section records decisions, not observations. Each item is a contract
decision with a recommendation.

### 4.1 Key-name inconsistency: `error_code` (flat) vs `code` (nested)

**The wart.** The flat HTTP envelope in Part 3.0 uses `error_code`. The nested
`AnalysisError` inside a poll response uses `code`, because that is what
PIPELINE.md §2.3 specifies and what `api_client.dart:306` reads:

```dart
errorCode: error['code'] is String ? error['code'] as String : null,
```

Meanwhile `_failureFromResponse` at `api_client.dart:139` tolerates both:

```dart
final Object? rawCode = node['code'] ?? node['error_code'];
```

So two different keys carry the same `ErrorCode` string in the same API, and the
client papers over it in one place but not the other. **This is a genuine wart,
not a design.** It exists because the two objects were specified at different
times.

**Decision: keep `code` nested and `error_code` flat for the MVP. Do not
harmonise now.**

**Recommendation and migration cost of each option:**

- *Harmonise on `error_code` everywhere* (rename the nested field). Cost: a
  breaking change to `AnalysisError`, which is embedded in every stored
  `analyses.payload` JSONB for failed-then-retried flows and in
  `AnalysisJobStatusResponse`; `api_client.dart:306` must change or failed jobs
  silently lose their code and show the generic "The analysis failed."; and
  PIPELINE.md §2.3 needs an edit. Cheapest **now**, while zero rows and zero
  users exist. Most correct end state.
- *Harmonise on `code` everywhere* (rename the flat field). Cost: lower on the
  client (`_failureFromResponse` already tries `code` first), but `code` is a
  poor top-level key name in an HTTP error body — it reads as an HTTP status —
  and it diverges from every error-envelope convention the next developer will
  expect. Rejected.
- *Do nothing.* Cost: zero today; a permanent footgun. Every future consumer
  (web dashboard, a second app, a support tool) must learn that the same value
  has two names.

**The recommendation is to harmonise on `error_code` before the first real
user**, because the migration cost is strictly increasing in the number of
stored payloads and installed clients, and it is currently zero. It is deferred
rather than done only because the backend that would emit either key does not
exist yet. **Revisit trigger: the first `analyses` row written in production.**

### 4.2 `next_cursor` is emitted but never consumed

**Observation.** `fetchHistory` (`api_client.dart:377-385`) sends only
`?limit=$limit` and never reads a cursor from the response or sends one back. It
therefore renders **page one, forever**. A user with 200 analyses sees the 50
most recent and has no way to reach the rest.

**Decision: the server emits `next_cursor` anyway, from day one.**

Rationale: the alternative — add pagination later — means changing the response
envelope after clients are installed, and an older client that does not
understand a changed shape is a support problem that a `null` field is not.
`next_cursor` costs one string. Emitting it from the start means the client fix
is a pure client change with no server coordination.

**Recorded as a known client gap, with the exact work:** `fetchHistory` needs a
`String? cursor` parameter, needs to append `&cursor=...` when non-null, needs to
read `next_cursor` off the response map, and needs to return it alongside the
items (a small `HistoryPage` wrapper, since `ApiResult<List<AnalysisSummary>>`
has nowhere to put it). The history screen then needs infinite-scroll or a
"load more" affordance. **Not blocking the MVP** — 50 recent analyses is a
complete experience for a new user — but it must be tracked, because the failure
mode is silent data invisibility rather than an error.

### 4.3 `retryable` and `request_id` are dropped by the client

**Observation.** `ApiFailure` (`api_client.dart:38-54`) has fields for `kind`,
`message`, `errorCode` and `statusCode`. It has **no field for `retryable` and
none for `request_id`**, and `_failureFromResponse` never reads either key. The
poll loop instead infers retryability from the status code
(`api_client.dart:343-344`):

```dart
if (failure.kind == ApiFailureKind.notSignedIn ||
    (failure.statusCode != null && failure.statusCode! < 500)) {
  return ApiResult<AnalysisResponse>.err(failure);
}
```

**Consequence, precisely.** The heuristic "4xx is final, 5xx is transient" is
correct for every code in the Part 3.0.1 table **except `429 queue_full`**,
which is `retryable: true` but is a 4xx and so is treated as final by the poll
loop. In practice `queue_full` is raised by `POST /v1/analyses`, not by the poll
endpoint, so the loop never sees it and the bug is latent rather than live. It
becomes live the moment any 4xx retryable code is returned from a polled route.

**Decision: the server emits `retryable` and `request_id` on every error
response regardless.** They are the correct contract and the client's not
reading them is a client defect, not a reason to stop sending them. Dropping
`request_id` in particular is the expensive one — without it, a user's
"it failed" report cannot be correlated to a log line.

**Recorded as a client gap:** add `retryable` and `requestId` to `ApiFailure`,
populate them in `_failureFromResponse`, prefer `failure.retryable` over the
status-code heuristic in `pollUntilComplete` when it is non-null (keeping the
heuristic as the fallback for bodies that lack it), and surface `requestId` in
the error UI in a copyable form.

### 4.4 REQUIRED INVARIANT — the poll discriminator

`fetchAnalysis` decides whether a `200` body is a finished analysis or a job
status with this test (`api_client.dart:286-290`):

```dart
final bool looksFinished = json['feedback'] is Map ||
    json['scorecard'] is Map ||
    rawStatus == 'complete' ||
    rawStatus == 'partial' ||
    rawStatus == 'succeeded';
```

There is no `type` field, no envelope, no discriminator key. **The shape of the
body is the discriminator.** Two rules follow, and both are REQUIRED
INVARIANTS, not preferences:

**(a) `JobStatus` and `AnalysisStatus` value sets must stay disjoint, and the
server must NEVER emit `succeeded` on this endpoint.**

- `JobStatus` = `{queued, running, succeeded, failed}`
- `AnalysisStatus` = `{complete, partial}`
- Intersection: empty. It must remain empty. Adding `complete` to `JobStatus`,
  or `succeeded` to `AnalysisStatus`, silently breaks the discriminator.
  **This should be asserted by a unit test** —
  `set(JobStatus) & set(AnalysisStatus) == set()` — which is cheap and catches
  the mistake at the moment someone makes it.
- The client tolerates `succeeded` in `looksFinished` as a defensive third
  branch. **Do not rely on it.** If the server emitted
  `{"status": "succeeded"}` with no `scorecard` and no `feedback`, the client
  would take the finished branch and hand the body to
  `AnalysisResponse.fromJson`, which parses tolerantly and would produce an
  analysis with no metrics, no feedback, `shotType: unknown` and a null score —
  a blank result screen presented as a successful analysis. When the job is
  done, return the full `AnalysisResponse` whose `status` is `complete` or
  `partial`. `succeeded` is the *database* value; it is not a wire value on this
  route.

**(b) The in-progress job body must carry no `feedback` key and no `scorecard`
key — not even set to `null`.**

`json['feedback'] is Map` is false for `null`, so a null-valued key is
technically survivable. Do not rely on that either: omit the keys entirely.
`AnalysisJobStatusResponse` in PIPELINE.md §2.3 has exactly five fields and none
of them is `feedback` or `scorecard`. Adding either — for example, to stream
partial feedback while running — breaks the discriminator immediately and
irrecoverably.

**Why this is stated as an invariant rather than a note.** `JobStatus.fromJson`
(`frontend/lib/models/enums.dart:264-265`) is:

```dart
static JobStatus fromJson(Object? raw) =>
    parseWireEnum(values, raw, JobStatus.queued);
```

**An unrecognised status string silently becomes `queued`.** So a contract drift
here does not raise, does not log, and does not show an error. The poll loop sees
`queued`, waits 2 s, polls again, sees `queued` again, and does that for 120 s
before reporting "The analysis is taking longer than expected." **The symptom of
a broken contract is an infinite spinner on a job that finished successfully**,
which is close to the hardest possible bug to diagnose from a user report. That
defaulting is deliberate and correct on the client side — a newer backend must
not blank a screen — and it is exactly what makes the server-side invariant
non-negotiable.

### 4.5 History rows are FLAT

`AnalysisSummary.fromJson` (`frontend/lib/models/analysis_response.dart:113-129`)
reads **flat-first with a nested fallback**:

| Field | Flat (primary) | Nested (fallback) |
|---|---|---|
| id | `analysis_id`, then `id` | — |
| created | `created_at` | — |
| shot type | `shot_type` as a string | `shot_type.shot_type` |
| score | `overall_score` | `scorecard.overall_score` |
| speed | `ball_speed_mph` | `ball_speed.ball_speed_mph` |

The nested fallback exists so that a full `AnalysisResponse` also parses through
`AnalysisSummary`. That is a convenience for the detail-to-list path. **It is not
permission for the list endpoint to return full objects.**

**Decision: `GET /v1/analyses` returns the flat six-key compact row specified in
Part 3.4 and MUST NOT return full `AnalysisResponse` objects.**

Reasons, in order of weight:

1. **Payload size.** A full `AnalysisResponse` includes `video`, `pose_quality`,
   `handedness`, `contact`, `phases`, `metrics` (20+ fields), `ball_speed` with
   its `calibration` and `detection` sub-objects, `scorecard` with every
   `MetricScore`, `feedback` with every `Improvement` and the
   `NumericGuardReport`, `timings`, and `warnings`. That is on the order of
   several KB per row. Fifty of them is a multi-hundred-KB response to render a
   list of five values per row, over a mobile connection, on a free tier.
2. **The query is different.** The flat row is served entirely from indexed
   columns — the index-only scan in Part 1.1 never touches `payload`. Returning
   full objects means reading and deserializing 50 JSONB blobs.
3. **Coupling.** If the list returns `AnalysisResponse`, every change to
   `AnalysisResponse` becomes a change to the list contract.

The six keys are exactly `analysis_id`, `created_at`, `shot_type`,
`overall_score`, `ball_speed_mph`, `pipeline_version`. `shot_type` is the bare
string from the indexed column; `ball_speed_mph` is the bare integer or `null`;
`pipeline_version` is a non-null string (Part 3.4).

---

## Part 5 — Build prerequisites

What does not exist. None of this is a refactor; all of it is new construction.

### 5.1 Missing code

| Missing | Needed for |
|---|---|
| `backend/app/main.py` | The ASGI app. Nothing can be served without it. Must register the three exception handlers from Part 3.0 and the `request_id` middleware. |
| `backend/app/api/` package | Routers. `routes_uploads.py` (Stage 1), `routes_analyses.py` (Stages 3 + 19 + Part 3.4), `auth.py` (Stage 2), `errors.py` (the envelope + handlers). |
| `backend/app/config.py` | `Settings(BaseSettings)` per PIPELINE.md §3.4. **Every constant in Part 3 currently has no home in code.** |
| `backend/app/services/` | `storage.py` (Stage 4), `repository.py` (the DDL's read/write layer), `orchestrator.py` (the job runner and the state machine in Part 3.5). |
| `JobStatus`, `AnalysisStatus`, `ErrorCode`, `ShotType`, `ScoreCategory`, `CourtReference`, `COURT_REFERENCE_METRES`, `SIDE_ON_ALLOWED_REFERENCES`, `BallDetectionTier` in `backend/app/models/enums.py` | Part 1's CHECK constraints, Part 3's every response, Stage 3's calibration validation. The file currently stops at `CameraView`. |
| `AnalysisResponse`, `AnalysisJobStatusResponse`, `AnalysisError`, `Scorecard`, `MetricScore`, `CategoryScore`, `ShotTypeInference`, `CoachingFeedback`, `Improvement`, `NumericGuardReport`, `StageTimings`, `VideoMeta`, `CalibrationEcho`, `BallDetectionSummary` in `backend/app/models/responses.py` | Part 3.3 Body B. |
| `backend/app/models/requests.py` | Does not exist at all. `UploadTicketRequest`, `UploadTicketResponse`, `NormalizedPoint`, `BallSpeedCalibration`, `CreateAnalysisRequest`, `CreateAnalysisResponse`. |

**There is no existing backend auth implementation to copy.** PIPELINE.md
Stage 2 is prose — a description of what `PyJWKClient` should be configured
with. `backend/app/api/auth.py` does not exist and no JWT verification has ever
been written or run in this repo. Every parameter in Stage 2 (`ES256`,
`audience="authenticated"`, `issuer`, `options={"require": [...]}`, `leeway=10`,
`cache_keys=True, lifespan=300`, constructed once on `app.state`) is unexecuted
specification and should be treated as such — in particular, **whether the
Supabase project has actually been migrated to ES256 asymmetric keys has not
been verified from this repo.** Stage 2 says that migration is a prerequisite,
not a fallback. Confirm the JWKS endpoint serves ES256 keys before writing the
verifier.

### 5.2 Missing dependencies

`backend/requirements.txt` currently has: `pydantic`, `fastapi`, `uvicorn[standard]`,
`numpy`, `scipy`, `av`, `opencv-python-headless`, `absl-py`, `certifi`,
`flatbuffers`, `matplotlib`, `sounddevice`, `pytest`. **`PyJWT` is absent and
`supabase` is absent.** Neither auth nor any database or storage call can be
written today.

Required additions:

```
# --- Auth (PIPELINE.md Stage 2) -------------------------------------------
# The [crypto] extra is NOT optional: it pulls `cryptography`, without which
# PyJWT cannot verify ES256 at all -- it raises
# NotImplementedError("Algorithm 'ES256' could not be found") at decode time,
# which looks like a token problem and is not. PyJWKClient also lives here.
PyJWT[crypto]

# --- Supabase (DB + Storage, service-role) --------------------------------
supabase

# --- Settings -------------------------------------------------------------
# Pydantic v2 moved BaseSettings OUT of pydantic into this separate package.
# PIPELINE.md §3.4's `class Settings(BaseSettings)` does not import today.
pydantic-settings
```

**Version pins: deliberately absent above, and that is the point.** These have
not been resolved against PyPI, and inventing a plausible-looking pin is exactly
the fabrication this project forbids. Before committing `requirements.txt`, run
`pip index versions PyJWT supabase pydantic-settings`, pin the resolved versions
with the same `>=x.y,<z` discipline the existing file uses, and confirm three
things:

1. **`supabase` does not drag in a conflicting `httpx` or `pydantic`.** The
   existing file's entire mediapipe section is a case study in a transitive
   dependency quietly installing a second copy of something critical. Verify
   with `pip install --dry-run --report` exactly as that section documents, and
   record the result in a comment.
2. **`PyJWT[crypto]` brings `cryptography`, which is a compiled wheel.** Confirm
   a manylinux wheel exists for Python 3.13 so the Cloud Run build does not fall
   back to compiling from source.
3. **Whether `supabase` is needed at all.** The backend makes a small, fixed set
   of calls: a signed-upload-URL creation, an object HEAD, a streamed object
   download, and four or five SQL statements. All are plain HTTPS against
   PostgREST and the Storage REST API, and `httpx` (already present transitively
   via `fastapi`/`uvicorn[standard]`) can make all of them. The SDK's value is
   convenience; its cost is a dependency tree over the single most
   security-sensitive credential in the system. **Decide this explicitly rather
   than defaulting to the SDK**, and record the decision here.

### 5.3 Suggested build order

1. `config.py` + `enums.py` completion (unblocks the DDL's CHECK lists and every
   model).
2. Run Part 1 and Part 2 against the Supabase project. Verify the `EXPLAIN` in
   Part 1.1.
3. `requests.py` + the remaining `responses.py` models.
4. `errors.py` + `main.py` with the three exception handlers. **Write the
   handlers before the first route**, not after — retrofitting an error envelope
   means auditing every `raise` in the codebase.
5. `auth.py`, verified against a real Supabase token.
6. `routes_uploads.py` → the client's upload path goes green.
7. `routes_analyses.py` POST + the orchestrator skeleton (a job that immediately
   fails with `internal_error` is enough to exercise the whole state machine).
8. `routes_analyses.py` GET (poll), then GET (history).
9. Only then wire the real pipeline into the orchestrator.

Steps 1-8 are testable end to end with no MediaPipe, no Gemini, and no video.

---

## Part 6 — Judgment calls in this reconstruction

Items a reviewer should check rather than trust. The original file is lost and
may have decided any of these differently.

1. **`analysis_jobs.error_stage`** — a column not in PIPELINE.md line 1151,
   added so `AnalysisError.stage` can be reconstructed from the row instead of
   fabricated.
2. **`analyses.status`** — a column not in PIPELINE.md line 1152, added so the
   `complete`/`partial` value set is enforced by a CHECK, which is what makes the
   Part 4.4 invariant structurally enforced on the write path.
3. **`analysis_jobs.error_code` CHECK uses the full 20-member `ErrorCode` set**
   rather than the narrower job-terminal subset. Breadth chosen because a
   too-narrow CHECK converts a job failure into an INSERT failure and loses the
   error entirely.
4. **`overall_score` typed `numeric(5,2)`** — precision not specified anywhere.
   `real` or `double precision` would also work.
5. **`analyses.id` is a FK to `analysis_jobs.id` with `ON DELETE CASCADE`** —
   PIPELINE.md says "`id` (= job id)" but does not specify the constraint or the
   cascade.
6. **Both tables cascade from `auth.users`** — account deletion removes
   analyses. Not specified; a retention or soft-delete policy might be wanted.
7. **`analyses` is immutable and has no `updated_at`.** A re-analysis is a new
   row.
8. **History `limit` bounds `1..100`, default `50`.** The default matches the
   client's `fetchHistory({int limit = 50})`; the max is invented.
9. **Cursor is unsigned and unencrypted.** Argued safe because it is scoped by
   `user_id` server-side, but a reviewer may prefer an HMAC.
10. **Cursor separator is `|`** and the timestamp is the RFC 3339 rendering. Any
    unambiguous encoding works; this one must match between writer and reader.
11. **`items` chosen as the list envelope key** over `analyses` / `results`,
    which the client also accepts.
12. **Staleness evaluated on the read path**, with the row write best-effort,
    rather than by a background sweeper.
13. **`heartbeat_at` written at stage boundaries**, not on a timer.
14. **`queue_position` is `null` for a running job**, not `0`.
15. **Retry is a new `POST /v1/analyses`** — no retry endpoint, no in-place
    restart.
16. **`404 analysis_not_found` for a non-owned id**, not `403` — chosen so the
    endpoint does not confirm existence.
17. **Part 4.1 defers the `code`/`error_code` harmonisation** rather than doing
    it now. The recommendation is to do it before the first production row.
18. **`retryable` is a per-`ErrorCode` constant**, taken from the Part 3.0.1
    table, not computed per occurrence.
19. **`Retry-After: 30` on `429`** — the number is invented; it is roughly one
    job.
20. **Storage bucket policies are included here** even though a storage doc
    might own them.
21. **`FORCE ROW LEVEL SECURITY`** added beyond plain `ENABLE`.
22. **Dependency versions are deliberately unpinned** in Part 5.2, with a
    verification procedure instead. The original may have carried real pins.
23. **Part 5.2 questions whether `supabase-py` is needed at all** and proposes
    plain `httpx` as the alternative. The original may simply have listed the SDK.
24. **`backend/app/config.py` does not exist**, so the 52_428_800 cap is
    documented as a spec constant from PIPELINE.md rather than as confirmed in
    code. Any statement that it is "confirmed in `backend/app/config.py`" is
    currently false.

---

## Part 7 — Live schema reconciliation (2026-09-21)

**Method.** Read-only reconnaissance against the live Supabase project. No row
was inserted, updated or deleted; no DDL was executed; no test user was created.
Two sources were used:

1. **PostgREST OpenAPI introspection** — `GET {SUPABASE_URL}/rest/v1/` with
   `Accept: application/openapi+json`, service-role key. This yields every
   exposed column with its type, nullability and column default.
2. **Non-committing constraint probing.** `information_schema` and `pg_catalog`
   are NOT reachable through PostgREST on this project (`PGRST106 — Only the
   following schemas are exposed: public, graphql_public`), and the only RPC
   exposed is `rls_auto_enable`, so CHECK constraint *bodies* cannot be read.
   They were characterised by probing instead, using a technique that **cannot
   commit**: every probe `INSERT` carries a `user_id` (and, for `analyses`, an
   `id`) that does not exist in the referenced table. Postgres evaluates CHECK
   constraints *before* firing FK triggers, so the request either returns
   `23514` and names the offending CHECK, or returns `23503` on the foreign key
   — meaning "every CHECK passed" — and the statement aborts either way. Both
   tables were confirmed empty (0 rows in `analysis_jobs`, 0 in `analyses`), and
   still are.

**Headline result: a third instance of schema/code drift was found, and this
one is a production-stopper.** See discrepancy **A1** below
(`analyses_ball_speed_reason_pairing_chk`): as the code stands today, *every*
analysis that does not produce a ball speed will fail to persist. Its fix is in
the Python, not in SQL.

### 7.1 Live schema as actually observed

#### `public.analysis_jobs`

| column | type | null | default | in Part 1? |
|---|---|---|---|---|
| `id` | uuid (PK) | NOT NULL | `gen_random_uuid()` | yes |
| `user_id` | uuid | NOT NULL | — | yes (FK `analysis_jobs_user_id_fkey`) |
| `storage_path` | text | NOT NULL | — | yes |
| `status` | text | NOT NULL | `'queued'` | yes |
| `ball_speed_requested` | boolean | NOT NULL | `false` | **NO** |
| `estimated_seconds` | integer | NULL | — | **NO** |
| `error_code` | text | NULL | — | yes |
| `error_message` | text | NULL | — | yes |
| `error_stage` | text | NULL | — | yes (added during the 2026-09-20 fix) |
| `heartbeat_at` | timestamptz | **NOT NULL** | **`now()`** | yes, but doc says NULLABLE |
| `started_at` | timestamptz | NULL | — | **NO** |
| `finished_at` | timestamptz | NULL | — | **NO** |
| `created_at` | timestamptz | NOT NULL | `now()` | yes |
| `updated_at` | timestamptz | NOT NULL | `now()` | yes |

Live CHECK constraints on `analysis_jobs`, all observed by name:

- `analysis_jobs_status_chk` — accepts exactly
  `queued, running, succeeded, failed`. Rejects `pending`, `cancelled`,
  `done`, `QUEUED`, `''`. Semantically identical to the documented
  `analysis_jobs_status_check`; **the documented NAME does not exist.**
- `analysis_jobs_error_code_chk` — all **20** documented `ErrorCode` values
  were probed individually and **all 20 are accepted**; `not_a_code`, `''`,
  `INTERNAL_ERROR`, `timeout` are rejected. Value set matches the doc exactly.
  **The documented NAME `analysis_jobs_error_code_check` does not exist.**
- `analysis_jobs_error_pairing_chk` — `status='failed'` with `error_code IS
  NULL` is rejected; `status IN ('queued','running')` with a non-null
  `error_code` is rejected. Semantically identical to the documented
  `analysis_jobs_failure_shape_check`; **the documented NAME does not exist.**
- `analysis_jobs_path_prefix_chk` — **UNDOCUMENTED.** `storage_path` must begin
  with `swing-videos/` *followed by this row's own `user_id`* followed by `/`.
  Proven: a path built from a *different* random uuid is rejected even when the
  row is otherwise valid, while the same path built from the row's `user_id` is
  accepted. `swing-videos/abc/x.mp4` and a leading-space variant are rejected.
- `analysis_jobs_path_depth_chk` — **UNDOCUMENTED.** Requires exactly three
  non-empty `/`-separated segments (`a/b`, `a/b/c/d`, `a/b/c/`, `/a/b/c`,
  `swing-videos//x.mp4`, `''` all rejected) **and** a total length of **512
  characters or fewer** (512 accepted, 513 rejected). The name understates it:
  it is a depth *and* length cap.
- `analysis_jobs_eta_chk` — **UNDOCUMENTED.** `estimated_seconds >= 0`
  (`-1`, `-5` rejected; `0`, `1`, `86400`, `100000000` accepted). No upper bound.

No constraint was found relating `started_at`/`finished_at` to each other or to
`status`, and none on `error_stage` (arbitrary strings including `''` and a
500-char string are accepted), `error_message`, or `ball_speed_requested`.

#### `public.analyses`

| column | type | null | default | in Part 1? |
|---|---|---|---|---|
| `id` | uuid (PK, FK to `analysis_jobs.id`, `analyses_id_fkey`) | NOT NULL | — | yes |
| `user_id` | uuid | NOT NULL | — | yes |
| `storage_path` | text | NOT NULL | — | yes |
| `status` | text | NOT NULL | — | yes (added during the 2026-09-20 fix) |
| `shot_type` | text | NOT NULL | **`'unknown'`** | yes, but doc declares NO default |
| `overall_score` | numeric(5,2) | NULL | — | yes |
| `ball_speed_mph` | integer | NULL | — | yes |
| `ball_speed_requested` | boolean | NOT NULL | `false` | **NO** |
| `ball_speed_unavailable_reason` | text | NULL | — | **NO** |
| `pipeline_version` | text | NOT NULL | **`'v2'`** | yes, but doc declares NO default |
| `rubric_version` | text | NOT NULL | **`'rubric_v1'`** | yes, but doc declares NO default |
| `payload` | jsonb | NOT NULL | — | yes |
| `created_at` | timestamptz | NOT NULL | `now()` | yes |

`numeric(5,2)` is confirmed by overflow: `999.99` and `1234.5` both return
`22003 numeric field overflow`.

Live CHECK constraints on `analyses`:

- `analyses_status_check` — **the one live constraint that carries the exact
  documented name**, because it was created from this file's DDL during the
  2026-09-20 fix. Accepts `complete`, `partial`; rejects `failed`, `succeeded`,
  `COMPLETE`, `''`.
- `analyses_shot_type_chk` — all 7 documented values accepted, `smash` and `''`
  rejected. Value set matches; **documented NAME `analyses_shot_type_check`
  does not exist.**
- `analyses_ball_speed_range_chk` — `15` and `160` accepted, `14`, `161`, `0`,
  `-5`, `1000` rejected. Bounds match the doc and `responses.py`'s
  `StrictInt ge=15 le=160` exactly; **documented NAME
  `analyses_ball_speed_range_check` does not exist.**
- `analyses_overall_score_chk` — **bounds do NOT match the doc.** `0` and `100`
  accepted, `-1`/`101`/`-0.5`/`100.5`/`-0.49`/`100.49` rejected — but `-0.01`,
  `-0.02`, `-0.03`, `100.01`, `100.02` are **accepted**, and `-0.05`/`100.05`
  are rejected. The documented body is a strict `>= 0 AND <= 100`, which would
  reject `-0.01`. Best-fit hypothesis for the live body is a one-decimal
  rounding tolerance, e.g. `round(overall_score, 1) BETWEEN 0 AND 100`, but
  **this is inferred from boundary behaviour and the actual body could not be
  read — see the confidence flag in §7.4.**
- `analyses_ball_speed_reason_pairing_chk` — **UNDOCUMENTED, AND THE CODE
  VIOLATES IT.** Requires exactly one of `ball_speed_mph` /
  `ball_speed_unavailable_reason` to be non-null. Both-null is rejected;
  both-set is rejected. See A1.
- `analyses_ball_speed_reason_chk` — **UNDOCUMENTED.** All **12** members of
  `BallSpeedUnavailableReason` (`backend/app/models/enums.py:57-71`) were probed
  individually and all 12 are accepted; `bogus_reason` and `''` are rejected.
  The live value set is exactly the code enum.
- `analyses_payload_object_chk` — **UNDOCUMENTED.** `payload` must be a JSON
  *object*: `{}` and `{"a":1}` accepted; `[]`, `"str"`, `5`, `true` rejected
  with this constraint, `null` rejected by NOT NULL.
- `analyses_path_prefix_chk` — **UNDOCUMENTED.** Same `swing-videos/{user_id}/`
  tie as on `analysis_jobs`. **Asymmetry worth noting:** `analyses` has NO depth
  or length counterpart — a 4-segment path, a trailing-slash path and a
  1024-character path are all accepted here but rejected on `analysis_jobs`.

No constraint was found tying `ball_speed_requested` to `ball_speed_mph`, or
`status='partial'` to a null `overall_score`, or bounding `created_at`.

#### The incident-2 constraints are GONE

`analyses_rubric_version_chk` and `analyses_pipeline_version_chk` **no longer
exist.** `rubric_version` was probed with `rubric_v1`,
`rubric_v0_placeholder`, `junk`, `''`; `pipeline_version` with `v1`, `v2`,
`v3`, `v99`, `junk`, `rubric_v1`, `''`. **Every value was accepted.** The
2026-09-20 remediation dropped them rather than widening them. No SQL is needed
here; only the doc needs to record the outcome (see A6 discussion in §7.5).

#### Indexes — NOT enumerable

**Index enumeration was not achievable and is not guessed at below.** PostgREST
exposes only `public` and `graphql_public`; `pg_indexes` / `pg_class` are not
reachable, and no read-only view or RPC over them exists on this project. The
four indexes in Part 1.1 (`idx_analyses_user_created_id`,
`idx_analysis_jobs_user_created`, `idx_analysis_jobs_active`,
`idx_analyses_user_ball_speed`), the `set_updated_at()` function and the
`trg_analysis_jobs_updated_at` trigger are therefore **UNVERIFIED** — they may
be present, absent or differently defined. Verifying them needs one
`SELECT indexname, indexdef FROM pg_indexes WHERE schemaname='public';` and one
`SELECT tgname FROM pg_trigger WHERE tgrelid='public.analysis_jobs'::regclass;`
run by the owner in the SQL editor. Because `CREATE INDEX IF NOT EXISTS` is
idempotent and safe on two empty tables, §7.5 simply re-runs Part 1.1 verbatim.

Similarly, **`analyses_user_id_fkey` could not be confirmed.** `analyses_id_fkey`
fires first on every probe, and with `analysis_jobs` empty there is no valid
`id` to get past it. Confirming it requires a catalog read, not a probe.

### 7.2 Discrepancies — documented but MISSING live (6)

All six are **name-only**: the constraint's *behaviour* is present and correct
in every case, under a `_chk` name instead of the documented `_check` name.

| # | Documented name | Live name | Behaviour |
|---|---|---|---|
| N1 | `analysis_jobs_status_check` | `analysis_jobs_status_chk` | identical |
| N2 | `analysis_jobs_error_code_check` | `analysis_jobs_error_code_chk` | identical, all 20 values |
| N3 | `analysis_jobs_failure_shape_check` | `analysis_jobs_error_pairing_chk` | identical |
| N4 | `analyses_shot_type_check` | `analyses_shot_type_chk` | identical, all 7 values |
| N5 | `analyses_ball_speed_range_check` | `analyses_ball_speed_range_chk` | identical, 15..160 |
| N6 | `analyses_overall_score_range_check` | `analyses_overall_score_chk` | **bounds differ — see M2** |

**Fix direction: align the DOC, not the database** (N1–N5). Renaming five live
constraints buys nothing — no code references a constraint name — while every
rename is a chance to drop a constraint and forget to re-add it on a table that
is about to receive production data. The *doc* is what future readers diff
against, so the doc should carry the real names. N6 is different only because
its body also differs; it is handled as M2.

This naming split is itself the tell that Part 1's DDL was **never the script
that built this database**. Only `analyses_status_check` — the one constraint
created from this file, by hand, on 2026-09-20 — uses the documented spelling.
Everything else came from an unrecorded script using a `_chk` house style. That
is the root cause of all three incidents, and §7.6 records it as such.

### 7.3 Discrepancies — live but UNDOCUMENTED (16)

Counted as: **6 undocumented columns + 7 undocumented constraints + 3
undocumented column defaults.**

**A1 — `analyses_ball_speed_reason_pairing_chk`. SEVERITY: BREAKS PRODUCTION.**

The constraint requires exactly one of `ball_speed_mph` /
`ball_speed_unavailable_reason` to be non-null. The code cannot satisfy it when
no speed is measured:

- `AnalysisRow` (`backend/app/services/repository.py:61-75`) has **no**
  `ball_speed_unavailable_reason` field at all.
- `persist_success` (`repository.py:271-275`) serialises with
  `exclude_none=True`, so a null `ball_speed_mph` is *omitted* from the
  PostgREST body and lands as SQL NULL.
- Stage 18 (`backend/app/services/pipeline.py:565-575`) passes
  `ball_speed_mph=ball_speed.ball_speed_mph` and nothing else from the ball
  speed result.

So every uncalibrated clip — and every clip where detection legitimately
returns one of the 12 `BallSpeedUnavailableReason` values — writes both columns
NULL, gets `23514`, and `_request` turns that into `ApiError(INTERNAL_ERROR)`,
which `pipeline.py:578-582` converts to a `JobFailure` at stage `persist`. The
analysis is lost after the full pipeline has already run. With
`BALL_DETECTION_ENABLED` off, or for any clip submitted without calibration,
this is **100% of analyses**.

**Fix direction: keep the constraint, fix the CODE.** This is a
`rubric_version`-shaped case — the live schema is right and the doc is behind —
and it is not a close call, because the constraint is a *verbatim restatement*
of a model validator the code already enforces on the response side:

> `backend/app/models/responses.py:180-182` —
> `if (self.ball_speed_mph is None) != (self.unavailable_reason is not None):`
> `raise ... "unavailable_reason must be non-null exactly when ball_speed_mph is null"`

Dropping the DB constraint would let the storage layer hold a state the
response model declares impossible — a stored row that cannot be rendered. The
required change is: add `ball_speed_unavailable_reason:
BallSpeedUnavailableReason | None = None` to `AnalysisRow`, and populate it from
`ball_speed.unavailable_reason` at `pipeline.py:566`. **No SQL statement in
§7.5 addresses A1 — it is a Python change and was out of scope for this
read-only pass.** It is the highest-priority item arising from it.

**A2 — `analyses.ball_speed_unavailable_reason` (text NULL) and
`analyses_ball_speed_reason_chk`.** The column A1 needs. Its 12-value CHECK is
exactly `BallSpeedUnavailableReason`. *Fix: document both; the live definition
is correct.* This is one of the "two extra columns" noted in passing by the
earlier report — it is **not** benign, it is half of A1.

**A3 — `analyses.ball_speed_requested` (boolean NOT NULL DEFAULT false).**
The other of the "two extra columns". Never written by `AnalysisRow`; the
default saves the INSERT, so it is benign *today*. But it means every stored
analysis records `false`, including analyses that very much did request a speed
— a silently wrong audit field. *Fix: document it, and flag for a decision —
either populate it in `AnalysisRow` or drop it. Do not leave a column that lies.*

**A4 — `analysis_jobs.ball_speed_requested` (boolean NOT NULL DEFAULT false).**
Same story on the job side. `routes_analyses.py:162` computes
`ball_speed_requested=body.ball_speed_calibration is not None` — but that goes
into the *API response* model (`requests.py:213-214`), never into `JobRow`, so
the DB column stays `false`. *Fix: document, and flag the same
populate-or-drop decision. This one is more clearly wanted — the worker has a
legitimate reason to ask the row whether calibration was requested.*

**A5 — `analysis_jobs.estimated_seconds` (integer NULL).**
`routes_analyses.py:161` computes an ETA into the response model, never into
`JobRow`, so this column is always NULL. *Fix: document; flag populate-or-drop.*

**A6 — `analysis_jobs.started_at` (timestamptz NULL).** Never written by any
code path in `repository.py`. Always NULL. *Fix: document; flag
populate-or-drop.*

**A7 — `analysis_jobs.finished_at` (timestamptz NULL).** Same. *Fix: document;
flag populate-or-drop — this one would make the `worker_lost` post-mortem far
easier to run and is probably worth keeping and populating.*

**A8 — `analysis_jobs_eta_chk`.** `estimated_seconds >= 0`, no upper bound.
*Fix: document; keep.*

**A9 — `analysis_jobs_path_prefix_chk` and A10 — `analyses_path_prefix_chk`.**
`storage_path` must start with `swing-videos/{this row's user_id}/`. *Fix:
document; **keep**.* These are deliberate and valuable: they are the
database-level restatement of the Stage 3 ownership rule this file already
states in prose at Part 3.2 ("`storage_path` must start with
`swing-videos/{jwt.sub}/`. Otherwise `403 storage_path_forbidden`"). Enforcing
it in the schema means a cross-tenant path cannot be stored even if the
application check is ever bypassed or refactored away.

**⚠ One risk to flag:** the bucket segment is a hardcoded literal
`swing-videos`, whereas the application builds the path from
`settings.supabase_storage_bucket` (`backend/app/api/routes_uploads.py:57`, env
var `SUPABASE_STORAGE_BUCKET`). If that environment variable is ever changed,
**every** `INSERT` into both tables starts failing with `23514`, and the failure
will look like an application bug. This coupling was recorded nowhere; it is
recorded here.

**A11 — `analysis_jobs_path_depth_chk`.** Exactly 3 non-empty segments, total
length ≤ 512. *Fix: document; keep.* The name is misleading (it is also a
length cap) but renaming it is not worth the churn — §7.5 documents rather than
renames, and instead closes the `analyses` asymmetry.

**A12 — `analyses_payload_object_chk`.** `jsonb_typeof(payload) = 'object'`.
*Fix: document; keep.* It guarantees the poll endpoint can never read back a
payload that is a bare array or scalar, which would break
`get_analysis_payload`'s contract.

**A13 — `analyses_ball_speed_reason_chk` value set** is counted with A2.

**A14 — `analyses.shot_type DEFAULT 'unknown'`.** Undocumented default.
*Fix direction: **DROP the default** (align live to doc).* `AnalysisRow` always
supplies `shot_type`, so nothing depends on it; meanwhile a default of
`'unknown'` means a future writer that forgets the column silently stores a
classification the pipeline never made. That is precisely the fabrication the
`error_stage` column was added to prevent (Part 1's own comment: "`stage` would
have to be fabricated at read time, which the anti-fabrication rule forbids").
A NOT NULL column with no default fails loudly instead.

**A15 — `analyses.pipeline_version DEFAULT 'v2'`.** Undocumented default.
*Fix direction: **DROP the default**.* Same reasoning; `pipeline.py:121` always
supplies it, and a hardcoded `'v2'` default is a stale contract string waiting
to mislabel a row written by v3.

**A16 — `analyses.rubric_version DEFAULT 'rubric_v1'`.** Undocumented default,
and the most pointed one. *Fix direction: **DROP the default**.* The code
deliberately writes `rubric_v0_placeholder`
(`backend/app/analysis/rubric.py:66`), and that file's own comment explains why
in terms that apply verbatim to the default:

> "Shipping them under `rubric_v1` would assert a validated rubric that does
> not exist."

A column default of `'rubric_v1'` is exactly that assertion, sitting in the
schema, ready to be applied to any row whose writer omits the column. It is the
residue of the dropped `analyses_rubric_version_chk` and should go with it.

### 7.4 Discrepancies — present but MISMATCHED (2)

**M1 — `analysis_jobs.heartbeat_at` is `NOT NULL DEFAULT now()` live; Part 1
declares it nullable with no default.**

*Fix direction: **align LIVE to the doc** — drop the default, drop NOT NULL.*
The doc and the code agree against the database here. Part 1's comment says
"NULL while queued — a queued job has no worker to beat", and the staleness rule
is built on that fact:

> `backend/app/services/orchestrator.py:135` — "`heartbeat_at IS NULL` is NOT
> stale -- a queued job has no worker to beat."
> `orchestrator.py:144` —
> `if job.status is not JobStatus.RUNNING or job.heartbeat_at is None:`

Live, a queued job is stamped `now()` at INSERT, so the NULL that was supposed
to *mean* "no worker yet" never occurs, and the invariant is carried entirely by
the `status is not RUNNING` half of that guard. Nothing breaks today, but the
schema no longer expresses the thing the comment says it expresses, and the
partial index `idx_analysis_jobs_active (status, heartbeat_at) WHERE status IN
('queued','running')` was designed around the null. `JobRow.heartbeat_at` is
typed `datetime | None` (`repository.py:55`), so relaxing live is safe for the
read path, and `create_job` omits the field via `exclude_none=True`, so it is
safe for the write path.

**M2 — `analyses_overall_score_chk` is looser than the documented
`analyses_overall_score_range_check`.**

Doc: `overall_score >= 0 AND overall_score <= 100`. Live: accepts `-0.03` and
`100.02`, rejects `-0.05` and `100.05`.

*Fix direction: **align LIVE to the doc**, replacing the body with the exact
documented bounds* — `scoring.py` produces a weighted mean of 0..100 band
scores, which cannot legitimately land outside 0..100, so the tolerance protects
nothing and only widens what can be stored. **⚠ Confidence flag: the live body
was not read, only inferred from boundary probes.** The replacement statement in
§7.5 is therefore written as an explicit `DROP CONSTRAINT` + `ADD CONSTRAINT`
with the documented body, which is correct regardless of what the old body
actually was — but **do not run it without first reading the real definition**
(§7.5 STEP 0), in case the tolerance was deliberate for a reason not recorded
anywhere in the repo.

### 7.5 Proposed SQL — REVIEW BEFORE RUNNING, NOT APPLIED

Nothing below has been executed. Run it in the Supabase SQL editor as project
owner. Both tables are empty (verified 2026-09-21), so every statement here is
free of backfill risk — **re-verify that before running if time has passed.**

Statements are ordered: catalog reads first, then column-level changes, then
constraints, then the idempotent index/trigger re-assert.

```sql
-- =========================================================================
-- STEP 0 -- READ-ONLY VERIFICATION. Run this block ALONE, first, and read
-- the output before running anything below it. It closes the three gaps
-- this reconciliation could not close through PostgREST (Part 7.1).
-- =========================================================================

-- 0a. The real CHECK bodies. Confirms every inference in Part 7.1/7.4 and,
--     in particular, tells you what analyses_overall_score_chk actually says
--     before M2 replaces it.
SELECT rel.relname AS table_name,
       con.conname  AS constraint_name,
       pg_get_constraintdef(con.oid) AS definition
FROM pg_constraint con
JOIN pg_class rel ON rel.oid = con.conrelid
JOIN pg_namespace ns ON ns.oid = rel.relnamespace
WHERE ns.nspname = 'public'
  AND rel.relname IN ('analyses', 'analysis_jobs')
ORDER BY rel.relname, con.contype, con.conname;

-- 0b. Indexes. Part 1.1 declares four; none could be verified remotely.
SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename IN ('analyses', 'analysis_jobs')
ORDER BY tablename, indexname;

-- 0c. The updated_at trigger (Part 1.2), also unverified.
SELECT tgname, pg_get_triggerdef(oid)
FROM pg_trigger
WHERE tgrelid = 'public.analysis_jobs'::regclass
  AND NOT tgisinternal;

-- 0d. Confirms analyses_user_id_fkey exists (Part 7.1 could not).
SELECT conname, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conrelid = 'public.analyses'::regclass AND contype = 'f';

-- 0e. Emptiness re-check. If either count is non-zero, STOP and re-assess
--     M1 and M2 -- both relax/tighten a live column.
SELECT (SELECT count(*) FROM public.analysis_jobs) AS jobs,
       (SELECT count(*) FROM public.analyses)      AS analyses;


-- =========================================================================
-- STEP 1 -- COLUMN-LEVEL FIXES
-- =========================================================================

-- M1: heartbeat_at must be nullable with no default, because
-- orchestrator.py:135/144 and Part 1 both define "queued, no worker yet" as
-- heartbeat_at IS NULL. Live's DEFAULT now() makes that state unreachable.
ALTER TABLE public.analysis_jobs ALTER COLUMN heartbeat_at DROP DEFAULT;
ALTER TABLE public.analysis_jobs ALTER COLUMN heartbeat_at DROP NOT NULL;

-- A14: shot_type DEFAULT 'unknown' is undocumented and lets a future writer
-- silently store a classification the pipeline never made. AnalysisRow always
-- supplies this column, so nothing depends on the default.
ALTER TABLE public.analyses ALTER COLUMN shot_type DROP DEFAULT;

-- A15: pipeline_version DEFAULT 'v2' is undocumented and hardcodes a contract
-- string that pipeline.py:121 already supplies on every write. Left in place,
-- it mislabels any v3 row whose writer omits the column.
ALTER TABLE public.analyses ALTER COLUMN pipeline_version DROP DEFAULT;

-- A16: rubric_version DEFAULT 'rubric_v1' asserts a validated rubric that
-- does not exist -- the exact claim rubric.py:66's comment refuses to make.
-- The code writes 'rubric_v0_placeholder' on every row; this default is
-- residue from the dropped analyses_rubric_version_chk.
ALTER TABLE public.analyses ALTER COLUMN rubric_version DROP DEFAULT;


-- =========================================================================
-- STEP 2 -- CONSTRAINT FIXES
-- =========================================================================

-- M2: live analyses_overall_score_chk accepts -0.03 and 100.02 (a ~one-decimal
-- rounding tolerance); Part 1 declares a strict 0..100. scoring.py cannot
-- legitimately emit a value outside 0..100, so the tolerance guards nothing.
--
-- !! CONFIDENCE FLAG: the live body was INFERRED from boundary probes, never
-- !! read. Run STEP 0a first. If the real definition shows a deliberate
-- !! tolerance with a rationale, SKIP these two statements and instead update
-- !! Part 1 of this document to declare the tolerant form.
ALTER TABLE public.analyses DROP CONSTRAINT IF EXISTS analyses_overall_score_chk;
ALTER TABLE public.analyses ADD CONSTRAINT analyses_overall_score_chk
    CHECK (overall_score IS NULL OR (overall_score >= 0 AND overall_score <= 100));

-- A11 (asymmetry only): analysis_jobs enforces a 3-segment, <=512-char path
-- via analysis_jobs_path_depth_chk, but analyses has no counterpart -- a
-- 4-segment or 1024-char path is accepted there today. The two tables store
-- the SAME path for the same clip, so they should agree.
--
-- !! OPTIONAL / LOWER CONFIDENCE: this ADDS a constraint that was never
-- !! documented and never live on this table. It is written to match the
-- !! PROBED behaviour of analysis_jobs_path_depth_chk, not a read definition.
-- !! Prefer copying the real body from STEP 0a's output. Skip it entirely if
-- !! you would rather keep the live surface unchanged.
ALTER TABLE public.analyses ADD CONSTRAINT analyses_path_depth_chk
    CHECK (
        length(storage_path) <= 512
        AND array_length(string_to_array(storage_path, '/'), 1) = 3
        AND '' <> ALL (string_to_array(storage_path, '/'))
    );


-- =========================================================================
-- STEP 3 -- IDEMPOTENT RE-ASSERT of Part 1.1 / 1.2
-- Index and trigger presence could NOT be verified remotely (Part 7.1).
-- These are all IF NOT EXISTS / OR REPLACE, so running them is a no-op if
-- the objects are already correct. Compare STEP 0b's output against Part 1.1
-- first: if an index exists with the SAME NAME but a DIFFERENT definition,
-- IF NOT EXISTS will silently keep the wrong one -- drop it by hand.
-- =========================================================================

CREATE INDEX IF NOT EXISTS idx_analyses_user_created_id
    ON public.analyses (user_id, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_analysis_jobs_user_created
    ON public.analysis_jobs (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_analysis_jobs_active
    ON public.analysis_jobs (status, heartbeat_at)
    WHERE status IN ('queued', 'running');

CREATE INDEX IF NOT EXISTS idx_analyses_user_ball_speed
    ON public.analyses (user_id, ball_speed_mph DESC NULLS LAST);

CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $FN$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$FN$;

DROP TRIGGER IF EXISTS trg_analysis_jobs_updated_at ON public.analysis_jobs;
CREATE TRIGGER trg_analysis_jobs_updated_at
    BEFORE UPDATE ON public.analysis_jobs
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
```

**Deliberately NOT in the script, and why:**

- **A1 is not here.** Its fix is Python (`AnalysisRow` +
  `pipeline.py:565-575`), not SQL. The only SQL "fix" would be dropping
  `analyses_ball_speed_reason_pairing_chk`, which would let the DB store a row
  the response model calls impossible. Fix the code.
- **N1–N5 constraint renames are not here.** No code references a constraint
  name; the doc is what needs correcting, and §7.2 corrects it.
- **No `ADD CONSTRAINT` for `rubric_version` / `pipeline_version`.** Their
  incident-2 CHECKs were dropped and should stay dropped: a CHECK on a version
  string converts every future version bump into a production outage, which is
  exactly how incident 2 happened. The version contract is enforced at
  `rubric.py:66` and `pipeline.py:121`, where it is visible to the person
  changing it. **Flagging this as the user's call, not a settled matter** — if
  a CHECK is wanted, it must list `'rubric_v0_placeholder'`, not `'rubric_v1'`.
- **No drop of the undocumented-but-correct constraints** (`path_prefix_chk`
  ×2, `path_depth_chk`, `payload_object_chk`, `eta_chk`,
  `ball_speed_reason_chk`, `ball_speed_reason_pairing_chk`). They are good
  constraints that the doc failed to record; §7.1 and §7.3 record them.
- **No populate-or-drop decision** on `ball_speed_requested` (both tables),
  `estimated_seconds`, `started_at`, `finished_at`. Four columns that are
  always NULL-or-`false` because no code writes them. Dropping them is
  destructive and populating them is a code change; both need a human decision
  that a read-only pass is not entitled to make.

### 7.6 Root cause, and how to stop incident 4

Three incidents, one cause: **Part 1's DDL is not the script that built this
database, and no one has ever diffed the two.** The `_chk`/`_check` naming
split (§7.2) is the fingerprint — every live constraint except the single one
created by hand from this file on 2026-09-20 uses a house style this file never
uses.

Probing one INSERT at a time finds one constraint at a time, which is how
incidents 1 and 2 each cost a production round-trip. The two mechanisms used
here — OpenAPI introspection for columns, FK-guarded non-committing probes for
CHECKs — find all of them in one pass, cannot write a row, and need no test
user. **They belong in CI as a schema-drift test**, run against staging, failing
the build when live and Part 1 disagree. That, not another round of `ALTER`
statements, is what prevents incident 4.

Until then the honest statement is: **Part 1 documents the intended schema, and
§7.1 documents the actual one. Where they disagree, §7.1 is what your INSERT
will hit.**
