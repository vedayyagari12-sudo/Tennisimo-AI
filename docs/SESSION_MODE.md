# Session Mode: design for Tennisimo

Status: proposal, designed against `main` at `cf7185a`. Pure-core implementation has started; see **Build status** below. Estimates are labelled as estimates, with the arithmetic shown.

Citations use the file name and line. `PIPELINE.md` means `docs/PIPELINE.md`. `camera_controller.dart`, `platform_file.dart` and `camera.dart` are in the Dart pub cache, not this repo.

**Could not be verified from the repo**
- Deployed Cloud Run settings, and whether CPU throttling is on. The verification command is in section 2.3.
- Live Supabase plan, bucket `file_size_limit`, storage RLS policies and indexes. The verification SQL is in section 2.4.
- Supabase, Cloud Run Jobs, Cloud Tasks and Gemini limits and prices that come from memory. These are tagged **[verify]**.
- Real per-frame pose time on Cloud Run. `analyses.payload->timings` can answer this today; the query is in section 2.3.

**Where the repo differs from what was assumed**
- `baseUrl` is not purely hardcoded. `api_client.dart:32-35` has an `API_BASE_URL` `--dart-define` override, which contradicts CLAUDE.md.
- The "50 MB" limit is 50 MiB (52,428,800 bytes).
- There is also a 15 s recording cap (`record_screen.dart:19`).
- `Settings.max_clip_seconds` is dead config.
- There are no Supabase migration files; DDL lives in `docs/DATABASE_SETUP.md`.

## Build status (2026-11-07)

Branch `worktree-phase-2-ball-court`. Everything below is pure or offline code with synthetic tests. **Nothing is wired to a route, the database, a Cloud Run Job or a screen**, and no accuracy claim is made: every constant is a placeholder until the section 1 experiment and the Phase 2 owner footage (both baselines in view, at least 8 tapped court points) calibrate it.

**Built**

| Piece | Files | Notes |
|---|---|---|
| Phase 1 slice 1, segmentation | `backend/app/analysis/segmentation.py`, `backend/app/models/session.py` | Section 4.2 steps 1-6, `seg_v1`. `contact.py`, `phases.py`, `metrics.py` unchanged. |
| Phase 1 slice 1, statistics | `backend/app/analysis/session_stats.py` | Section 10.1-10.3. |
| Phase 2 batches F/G | `rally.py`, `placement.py`, `match_score.py`, `point_inference.py` | As in `SESSION_MODE_PHASE2.md`. |
| Phase 2 step 16 | `analysis/court_session.py`, `services/court_offline.py`, `scripts/court_eval.py --mode events` | Vendored clip: refused `camera_moving`, no calls. |
| Phase 2 step 20 | `feedback/court_summary.py`, keyword-only `banned_finder` on `gemini.enforce` | Single-swing guard tests unmodified. |
| Phase 2 steps 21-22 | `lib/models/court.dart`, `court_map.dart`, `line_call_card.dart`, `rally_timeline.dart`, `score_panel.dart` | No screen imports them. |
| Step 24 regression | `tests/integration/test_single_swing_unchanged.py` (`slow`) | Matches the d0e4df5 golden. |

**Deviations from this design, each documented in code**
- Section 4.2 step 3: two accepted peaks are split at the minimum-speed frame **among the frames that leave both windows their minimum length**. The plain minimum is the second swing's takeback turn, which left it no build-up and rejected it as `window_too_short` at one-second spacing.
- Section 4.2 step 5, `contact_off_peak`: compares the slice's **winning speed peak** with the candidate peak, not the contact frame, because Stage 9's plateau walk legitimately places contact past a hesitating drive.
- Section 4.2 step 6: `< 0.35` is rejected with a new reason, `confidence_below_floor`, which the step 5 list omitted.
- Phase 2 step 16: a bounce on the hitter's own side with no net contact between drops the hitter (a missed opponent hit) and is called `half_only`. Before this, the far-half region (which has no net edge) called such a ball IN. With net contact between, the landing carries no call.

**Known gaps found while building**
- `rally.py` produced a range of 6-7 hits for one synthetic rally whose true length is 5 (seed 20261102). Not investigated.
- The numeric guard checks numbers, not meaning: a payload number reused with another meaning (median rally length 5 written as "5 rallies") passes.

**Still to do, blocked on owner decisions or footage**
- Slice 0 experiment and Phase 2 footage (accuracy, `SIGMA_INFLATION`, Decision 2).
- Slices 2-8: Part 9 SQL, routes, Job, upload, screens. Needs Decision 1 (paid Supabase plan) and Decision 2 (launch limit).
- Phase 2 batches I (T2 pixel e2e) and J (real-clip smoke).

---

## 1. Summary and the riskiest assumption

Session Mode takes one player's long video, finds every swing, runs the existing per-swing analysis on each, computes session statistics in pure Python, and asks Gemini for one summary built from aggregated numbers only.

The recommended shape:

| Decision | Recommendation |
|---|---|
| Segmentation | One new pure module finds swings and slices the keypoints. Existing Stages 7-9 and 12-15 run unchanged on each slice. |
| Compute | One Cloud Run Job per session, separate from the API service. Scale to zero, about $0.06-0.11 per 20-minute session (estimate, section 6). |
| Storage | New `sessions` and `session_swings` tables. Swings never enter `analyses`, so older app builds keep working. |
| Upload | TUS resumable upload direct to Supabase Storage. Requires a paid Supabase plan and a new client path, because the current path reads the whole file into RAM. |
| Limits | Launch at 10 minutes and 1 GiB, raise to 20 minutes once measured. Single-swing limits do not change. |
| UI | New screens using the existing navigation, widgets and palette: stat wrap-up, video with synced shot list, filter chips, dead-time skipping. `ResultsScreen` is reused for swing detail. |

**Plainly weaker than SwingVision in Phase 1:**
- There is no ball tracking, so a shadow swing counts as a shot.
- There is no shot map, depth, placement, in/out or rally data.
- Forehand versus backhand is unreliable by this codebase's own admission (`shot_type.py:26-29`).
- Processing is slower than real time on the planned compute (estimate, section 6).

### THE RISKIEST ASSUMPTION

> **Racket-wrist speed peaks from single-person MediaPipe tracking at 30 fps, on unedited session footage, identify the intended player's real swings accurately enough to be the only segmentation signal.**

**Why this is the riskiest.** Upload size, compute time and cost can all be solved with money. This one cannot, because there is no fallback signal. The pipeline has no racket tracker, and contact is deliberately pose-only (`contact.py:3-6`). Everything downstream inherits the error: a wrong contact frame "does not stay local" (`contact.py:765-769`).

The evidence so far is thin and comes from a different kind of footage:
- Generated keypoint data "always contains exactly one swing" (`PIPELINE.md:921`).
- Contact timing was validated on 5 hand-labelled events (`PIPELINE.md:927`).
- The corpus is 16 short clips, with 1 failing Stage 6 and 13 of the remaining 15 usable (`PIPELINE.md:770-773`).
- Two studio serves were detected wrongly (`contact.py:136-137`).
- Handedness is wrong on 6 of 16 clips, and its highest-confidence answer is the wrong one (`PIPELINE.md:830-841`).
- `backend/tests/fixtures/clips/README.md:5` records that every Stage 9 bug was found on real footage and none reproduced on synthetic data.

**How it could fail.**
- A player far from the camera gets poor wrist landmarks. Frames are downscaled to 640 px on the long edge before pose (`video_io.py:61`).
- Fast wrists blur at 30 fps.
- Ball pickups, serve bounces, walking and shadow swings produce peaks that look like swings.
- `num_poses=1` locks onto a coach or opponent (`extractor.py:96,162`).
- Any of these gives a confident-looking session of wrong numbers.

**Cheap experiment (about 2 days, before any product code).**
1. Film 5 sessions of at least 8 minutes each, following the existing setup card (`record_screen.dart:842-849`). Include one with a second person on court and one with deliberate shadow swings and ball pickups.
2. Label every event with a contact frame and a class: `hit`, `shadow`, `pickup`, `bounce`, `walk`, `other_person`. Allow about 1 hour per session (estimate).
3. Run a throwaway script using the existing `PoseExtractor` with an enlarged `max_frames`, plus the section 4 algorithm. Dev-box pose cost is 19.5 ms/frame (`PIPELINE.md:1910`), so 8 min × 60 × 30 = 14,400 frames × 19.5 ms is about 4.7 minutes per session.
4. Tune on 3 sessions and score on the 2 held out. Tuned constants stay frozen for scoring.

| Measure, on held-out sessions | Pass | Fail: stop and redesign |
|---|---|---|
| Swing precision (`hit` plus `shadow` count as positive, match within ±5 frames) | ≥ 0.85 | < 0.70 |
| Swing recall (same matching) | ≥ 0.80 | < 0.65 |
| Contact within ±3 frames of label, among matched swings | ≥ 90 % | < 75 % |
| Accepted swings belonging to another person | ≤ 3 % | > 10 % |

Results in between mean building with mandatory swing review and a beta label, and running the play-area and `num_poses=2` escalation in section 11.

Failure means fully automatic Session Mode is not viable on pose alone. The alternatives are user-marked swings, or ball-evidence gating, which needs an owner decision because it breaks the one-way dependency in `contact.py:5`.

Report shadow swings separately. Pose cannot tell them from hits, so the number shows how misleading "shots hit" would be.

### End-to-end data flow and files touched

```
Flutter setup -> POST /v1/sessions -> TUS upload to Supabase -> POST /complete
  -> Cloud Run Job execution -> download -> whole-file 30 fps pose (streaming)
  -> segmentation.py -> per-window Stages 7-9, 12-15 -> optional ball speed
  -> session_stats.py -> session_summary.py (numbers -> Gemini -> numeric guard)
  -> session_swings + sessions rows -> Flutter polls -> overview, review, detail
```

**New backend files**
- `backend/app/analysis/segmentation.py`
- `backend/app/analysis/session_stats.py`
- `backend/app/models/session.py`
- `backend/app/api/routes_sessions.py`
- `backend/app/services/session_pipeline.py`
- `backend/app/services/job_launcher.py`
- `backend/app/worker/session_worker.py`
- `backend/app/feedback/session_summary.py`
- Tests: `backend/tests/unit/analysis/test_segmentation.py`, `test_session_stats.py`, `synthetic_session.py`, `backend/tests/unit/feedback/test_session_summary.py`, `backend/tests/integration/test_api_sessions.py`, `backend/tests/eval/session_eval.py`

**Changed backend files**

| File | Change |
|---|---|
| `backend/app/pose/video_io.py` | Whole-file stream, duration parameter, play-area mask |
| `backend/app/models/enums.py`, `backend/app/api/errors.py` | New codes and statuses |
| `backend/app/config.py` | Session settings |
| `backend/app/main.py` | Include router |
| `backend/app/services/repository.py` | Session reads and writes, account deletion |
| `backend/app/services/storage.py` | Signed download, delete object, byte-limit parameter |
| `backend/app/api/routes_account.py` | Cancel running sessions |
| `backend/tests/unit/models/test_status_enums.py` | New enum members |

**New frontend files**
- `frontend/lib/screens/session_setup_screen.dart`
- `frontend/lib/screens/session_record_screen.dart`
- `frontend/lib/screens/session_processing_screen.dart`
- `frontend/lib/screens/session_overview_screen.dart`
- `frontend/lib/screens/session_review_screen.dart`
- `frontend/lib/services/session_api.dart`
- `frontend/lib/services/tus_upload.dart`
- `frontend/lib/services/session_video_source.dart`
- `frontend/lib/models/session.dart`
- `frontend/lib/widgets/swing_timeline.dart`
- `frontend/lib/widgets/session_filter_chips.dart`

**Changed frontend files**
- `frontend/lib/screens/record_screen.dart`
- `frontend/lib/screens/history_screen.dart`
- `frontend/lib/screens/results_screen.dart`
- `frontend/lib/models/enums.dart`
- `frontend/pubspec.yaml`
- `web_dist/` (rebuild)

**Docs**
- `docs/SESSION_MODE.md`
- `docs/DATABASE_SETUP.md` (new Part 9)
- `docs/PIPELINE.md` (new session section and deploy commands)
- `CLAUDE.md` (repo layout, known decisions)

---

## 2. Current state, cited

### 2.1 What runs today

- **Job model:** in-process `ThreadPoolExecutor(max_workers=1)` (`orchestrator.py:167`). The queue counter is in process memory (`orchestrator.py:173-185`). Depth cap is 4 (`config.py:218`, `routes_analyses.py:128`).
- **Upload:** server picks `swing-videos/{uid}/{uuid}.{ext}` (`routes_uploads.py:57`). The client PUTs the entire file as bytes through a signed URL (`supabase_storage.dart:28-33,52-59`).
- **Decode and pose:** frames are streamed one at a time (`video_io.py:718-735`). Landmarks accumulate into preallocated buffers (`extractor.py:203-206`). VIDEO mode, `num_poses=1`, first pose taken (`extractor.py:96,104,162`).
- **Analysis:** one contact per job (`pipeline.py:448-455`). `build_scorecard` and Gemini run once per job (`pipeline.py:485,519-538`).
- **Payload:** finished results are stored whole in `analyses.payload`. It contains no landmarks (`responses.py:453-493`).
- **Video lifetime:** no code deletes a clip after analysis. The only deletion path is account deletion (`routes_account.py:61-62`, `storage.py:191`). No retention policy exists beyond a note (`DATABASE_SETUP.md:1390-1391`).

### 2.2 Where the limits really live

| Limit | Client | Server | Storage / DB |
|---|---|---|---|
| **15 s recording** (not in the brief) | `record_screen.dart:19,325` | none | none |
| **60 s clip** | `video_intake.dart:35,173`; copy at `record_screen.dart:749-751` | Pydantic literal `le=60.0` (`requests.py:96`); probe constant (`video_io.py:72,280-283`) | none |
| **`Settings.max_clip_seconds`** | n/a | **Defined, read by nothing but a test** (`config.py:211`, `test_config.py:22`). Setting `MAX_CLIP_SECONDS` changes nothing. | n/a |
| **2 s minimum** | none | `video_io.py:71,276-279` | none |
| **8 s window / 240 frames** | n/a | `config.py:212,214`; `video_io.py:67,69`; `extractor.py:185,211`; passed at `pipeline.py:384-386,418` | none |
| **50 MiB** | `video_intake.dart:26,166`; `video_file_picker.dart:68`; `analyzing_screen.dart:105-109` | `config.py:45,210`; ticket (`routes_uploads.py:50`); HEAD (`routes_analyses.py:116`); download stream (`storage.py:183`) | Comments claim the Supabase free-tier per-file limit (`config.py:44`). DDL sets no `file_size_limit` (`DATABASE_SETUP.md:316-318`). **Live value unverified.** |
| **Client poll cap** | 210 s (`api_client.dart:69`). `DATABASE_SETUP.md:598` still says 120 s and is stale. | Heartbeat stale at 180 s (`config.py:219`) | none |
| **Accepted containers** | `video_intake.dart:43-47` | `requests.py:65-69` (`mp4`, `mov`, `webm`) | Bucket `allowed_mime_types` unverified |

### 2.3 Cloud Run

Not in the repo as config: only the Dockerfile (`--workers 1`, `Dockerfile:68`) and documented commands.

Documented as deployed (`PIPELINE.md:2046-2059,2333-2337`): gen2, 2 vCPU, 2 GiB, `min-instances=0`, `max-instances=1`, `concurrency=20`, `timeout=300`, default CPU throttling. Part of that doc is explicitly "drafted, not run" (`PIPELINE.md:2075`). **Needs verification:**

```bash
gcloud run services describe tennisform-api --region=us-east1 \
  --format="yaml(spec.template.spec.containerConcurrency,spec.template.spec.timeoutSeconds,spec.template.spec.containers[0].resources,spec.template.metadata.annotations)"
```

The annotations show `run.googleapis.com/cpu-throttling`, `autoscaling.knative.dev/minScale`, `autoscaling.knative.dev/maxScale` and the execution environment.

Timing evidence:

| Figure | Source | Status |
|---|---|---|
| 19.5 ms/frame pose | `PIPELINE.md:1910` | Measured on a dev box, 640×360, VIDEO mode |
| 29-49 ms/frame on 2 vCPU | `PIPELINE.md:1910` | Assumption, not measured on Cloud Run |
| 2-4 s decode per 240 frames | `PIPELINE.md:1893` | Budget |
| 7.8 s to 1.2 s for 350 1080p frames | `video_io.py:743` | Measured on a 12-core host, not on 2 vCPU |
| 6-12 s pipeline import | `main.py:54` | Desktop figure |
| 8-13 s per real clip, decode plus pose, pinned to 2 cores | Commit `d17250d` message | Measured locally. Frame counts are not stated. |
| ~2 min 40 s for a traced production job; MediaPipe started 66 s after submit | Commit `d17250d` message | Measured in production. The commit attributes most of the gap to CPU throttling after the 202 response. |
| RSS figures | `PIPELINE.md:1895,1938` | Marked unverified |

Real measured timing already exists in production rows:

```sql
select percentile_cont(0.5) within group (
         order by (payload->'timings'->>'pose_ms')::float
                  / nullif((payload->'video'->>'frames_sampled')::float, 0)) as median_pose_ms_per_frame,
       count(*)
from public.analyses;
```

If the service runs throttled, that number includes the throttling.

**Note:** `PIPELINE.md:1972` says the free tier is 180,000 vCPU-seconds, while `PIPELINE.md:2360` says 360,000 vCPU-seconds and 180,000 GiB-seconds. They disagree. **[verify]**

### 2.4 Database

There is no migrations directory. Schema changes are SQL run by the owner in the SQL editor and documented in `docs/DATABASE_SETUP.md`, most recently Part 8 (`DATABASE_SETUP.md:2017-2061`). The doc warns that Part 1 is not what built the database and that live uses `_chk` constraint names (`DATABASE_SETUP.md:1982-1998`).

Live `analysis_jobs` has a fixed 20-value error code check and a three-segment path check (`DATABASE_SETUP.md:1485,1498-1502`). Backend writes use the service-role key, which bypasses RLS (`DATABASE_SETUP.md:250-258`).

Verify before migrating:

```sql
select id, public, file_size_limit, allowed_mime_types from storage.buckets;
select policyname, cmd from pg_policies where schemaname='storage' and tablename='objects';
select indexname from pg_indexes where schemaname='public';
```

### 2.5 Frontend

- **Navigation:** imperative `MaterialPageRoute`, no router. Bottom bar is Dashboard, Record, History, with Record as a pushed action (`home_shell.dart:12-18,78-82,105-136`).
- **Theme:** mobile is light only. Dark exists on web only (`main.dart:25-33,63-72`, `app_theme.dart:15`).
- **Video:** `video_player` is used only to read duration today (CLAUDE.md, `video_controller_factory_web.dart:3-11`). No screen plays video.
- **Memory:** `VideoClip` holds the whole file as bytes (`video_clip.dart:24-27`). Both intake paths read everything into RAM (`video_file_picker.dart:74`, `record_screen.dart:377`). This cannot extend to session files.
- **Pagination:** `next_cursor` is never sent, so history stops at 50 rows (`api_client.dart:656-665`).
- **Naming collision:** the History screen already calls single clips "sessions" (`history_screen.dart:17,29,344`). User-facing copy must change to "clips" and "practice sessions".
- **Ball speed on imports:** a picked file gets no ball speed, because calibration needs the live camera (`record_screen.dart:750-751`).

### 2.6 Existing weaknesses Session Mode inherits

- **Every rubric band is a placeholder:** `rubric_v0_placeholder` (`rubric.py:19-22,66`). Scores and any label built on those bands are provisional.
- **Camera view gates are inert:** `estimated_camera_view` is always `UNKNOWN` (`normalize.py:655`).
- **Dead metrics:** `weight_transfer_tu` and `balance_sway_tu` always return `None` (`metrics.py:109-118,582-600`).
- **Documented but not in code:** `PIPELINE.md:1912` describes a rotation retry. `pipeline.py` has none. Session code must not assume one.
- **Non-atomic writes:** `persist_success` is two requests (`repository.py:275-299`).

---

## 3. SwingVision parity and UI-pattern policy

The SwingVision facts below come from public listings and reviews (see Sources). They are indicative: coverage of its UI is thin, and features may have changed. This document makes no claim about whether it processes on device or in the cloud.

| SwingVision feature | Session Mode | Why, tied to what Tennisimo can measure |
|---|---|---|
| Record a practice session | **Phase 1** | One player. In-app recording or import, 10 then 20 minutes. |
| Record a full match | Phase 2 | Needs longer limits and multi-player handling. Compute scales linearly. |
| Shot speed | **Phase 1, limited** | Only with two-tap calibration, in-app recording, side-on camera. The ball window is 0.25 s after contact (`frames.py:57`). Most fence-mounted views will read null. |
| Depth, accuracy, shot map, heat maps | Phase 3 | **Impossible today.** The ball tracker covers 0.25 s after contact, not flight to bounce. Calibration gives one scale factor from two taps (`geometry.py` via `pipeline.py:306-311`). There is no court homography in the repo. |
| Rally length | Phase 2, **needs decision** | Contradicts CLAUDE.md. `rally` is also a banned Gemini token (`gemini.py:81`). |
| Stats overlaid on video | **Phase 1, live UI only** | Current swing's numbers shown beside or over the player, never burned into a file. |
| Scoreboards | Phase 3 | Needs scoring, so needs in/out. |
| Remove dead time | **Phase 1** | Client-side skipping between swing windows. No re-encode. |
| Highlight reels | Phase 2 | Client playlist first. File export has privacy cost (section 11). |
| Share with stats overlaid | Phase 2, off by default | Needs an export pipeline and privacy review. |
| Filter by forehand, backhand and so on | **Phase 1, with caveat** | Filter chips work. Forehand versus backhand is "NOT reliably" separable (`shot_type.py:26-29`), so expect a real `unknown` share. |
| Per-shot analysis by shot type | **Phase 1** | Inferred from technique, as CLAUDE.md requires. Serve and two-handed are the most reliable (`shot_type.py:18-20`). |
| Per-shot spin | **Never under current scope** | CLAUDE.md: the app does not measure spin. `forehand_topspin` and `forehand_slice` are technique labels (`shot_type.py:22-25`). UI copy must say "low-to-high swing", never a spin claim. |
| Per-shot direction and result | Phase 3 | Needs ball flight and court geometry. |
| Session stat wrap-up, graphs | **Phase 1** | Section 10. Pose metrics rather than ball outcomes. |
| Post-session coaching | **Phase 1** | One Gemini summary from aggregated numbers. |
| Real-time line calling, auto scoring | Phase 3 at best | Appendix B. Possibly never on this architecture. |
| Apple Watch control | Never planned | No measurement value and no Watch target in the repo. Revisit only on user demand. |

**What this means for accuracy claims.** The reviewer figure of 85-90 % on shots is a different measure from swing precision and recall here. Use it only as a rough bar, and never publish a comparison.

**UI-pattern policy.** Tennisimo adopts general patterns:
- video with a synced shot list
- filter chips
- a stat wrap-up
- tap a shot to jump to it
- dead-time skipping

These are built in Tennisimo's own Material 3 system with `AppPalette` tokens. We do **not** copy SwingVision's name, branding, icons, screen layouts or marketing copy, and the app does not mention SwingVision.

---

## 4. Swing segmentation

### 4.1 What breaks if the contact detector simply runs over a long sequence

`detect_contact_frame` assumes one swing in its input.

| Mechanism | Citation | What goes wrong on a long sequence |
|---|---|---|
| Global argmax | `contact.py:218-232,810` | Returns one swing, the fastest. All others are lost. |
| Forward-swing search start | `contact.py:390-402,817`, then `:629` | Candidates only exist after the argmax's takeback minimum, so earlier swings are never offered. |
| Candidate cap, first plausible wins | `contact.py:66,736-737` | Five candidates, one winner. |
| Prominence over the whole sequence | `contact.py:454-507` | Every other swing is a "rival", so the ratio sits near 1. Confidence falls to the 0.35 floor (`contact.py:510-515`). Multiplied by observed fraction (`:854`) it drops below 0.35 whenever any frame is unobserved, so **ball speed is refused as `contact_unreliable`** (`speed.py:263-267`) and the swing degrades to partial (`pipeline.py:456-462`). |
| Arm extension relative to clip range | `contact.py:518-586` | One serve or a tracking jump stretches the range, so normal forehands fall below 60 % and are filtered as `arm_not_extended`. |
| Global torso scale | `normalize.py:167-181,569` | One median for the whole video. A player walking toward the camera changes apparent size, so TU values drift with court position. |
| One swing direction sign | `normalize.py:265-280,613-618` | Computed once from one peak. Opposite-direction swings have `forward` flipped, so `wrist_behind_mid_hip` can reject them (`contact.py:683-685`). |
| Identity check on any jump | `normalize.py:184-215,573` | A single jump flags the whole sequence, and every swing gets the 0.7 penalty (`contact.py:122`). In a long session it will always fire. |
| One usability verdict | `normalize.py:422-475,590-596` | One 2 s core decides the fate of the whole video. |
| Whole-sequence handedness | `handedness.py:90-181` | Detection can override the hint at confidence ≥ 0.5 (`handedness.py:163`), which already fails on serves (`PIPELINE.md:832-841`). Re-detecting per swing could flip hands between swings. |
| Phases use their own argmax | `phases.py:342` | If a neighbour swing in the window is faster, phase thresholds key off the wrong swing. |
| Ready exit from frame 0 | `phases.py:351-352,364-366` | A previous swing's follow-through triggers the ready exit, and the backward extreme can land in the wrong swing. |
| Follow-through maximum after contact | `metrics.py:603-623` | Includes the next swing if the window is not bounded. |
| Volley duration, path length | `shot_type.py:241-244,649-664` | Inflated by neighbouring motion. |
| Savitzky-Golay smoothing | `smoothing.py:14-15,65-85` | Local, so harmless, but it must not run across long gaps. The edge-hold step (`normalize.py:329-331`) creates a jump where the hold changes sides. |

**Conclusion.** Do not modify `contact.py`, `phases.py` or `metrics.py`. Give them inputs that look like the clips they were built for: **one swing per slice**. Single-swing mode is then untouched by construction.

### 4.2 Algorithm

New pure module `backend/app/analysis/segmentation.py`. No I/O, no clock, no model call. Input is one `PoseSequence` for the whole video plus a required handedness hint.

**Step 1: session speed signal**
1. Apply `valid_frames` (`normalize.py:90-98`), then aspect correction and y-flip with existing helpers.
2. Compute a **rolling** torso scale: median of valid `torso_lengths` over ±5 s in time. Time-based, as the code already insists (`normalize.py:393-398`).
3. Express the racket wrist relative to mid-hip, in rolling TU.
4. Split into tracking segments at gaps over `MAX_GAP_FRAMES` (`smoothing.py:16`) and at identity breaks using the existing thresholds (`normalize.py:53-54`).
5. Within each segment only, run `savgol_smooth` and `central_difference` on actual timestamps.
6. Build a `NormalizedSequence` from this and reuse `racket_hand_speed` and `racket_wrist_reliable` (`contact.py:165-200`) unchanged.

**Step 2: candidates and peak picking**

Call the existing `candidate_peak_indices` with `window_start=0`, `separation=REFRACTORY_FRAMES` and an uncapped `max_candidates` (`contact.py:589-648`). It already does greedy non-maximum suppression, highest speed first, ties broken by index. That is the refractory period, and it keeps the frame-separation logic in one place.

Then filter by floor and prominence:

| Constant | Starting value | Basis | Rejection reason |
|---|---|---|---|
| `SWING_FLOOR_TU_S` | 3.0 | Half the 6.0 lower edge of the forehand topspin band (`rubric.py:305`), below the slice band's 4.0 (`rubric.py:323`). **Placeholder.** | Not a candidate; counted only |
| Relative floor | 0.35 × P90 of candidate peaks | Keeps walking noise out without needing an absolute scale. Uncalibrated. | `below_relative_floor` |
| `REFRACTORY_S` | 0.6 s (18 frames) | Forward swing plus follow-through; a swing is ~0.6-1.2 s (`normalize.py:59`). Fast volley exchanges are the known cost. | Suppressed |
| `MIN_PEAK_VALLEY_RATIO` | 2.0 | Peak must be at least twice the higher adjacent valley. | `not_prominent` |

All values are calibrated on labelled training sessions, then frozen and versioned as `SEGMENTATION_VERSION = "seg_v1"`.

**Step 3: windows and overlap**
- Default window is contact peak −2.0 s to +1.0 s, enough for a serve build-up and follow-through.
- Between adjacent accepted peaks, split at the **minimum-speed frame** between them. Each frame belongs to exactly one swing, so windows can never overlap and a neighbour's faster peak cannot enter the slice.
- Windows also stop at tracking-segment boundaries.
- Minimum usable window: 0.5 s before and 0.2 s after the peak, else `window_too_short`. Fast exchanges will lose some swings. This is a known weakness, and the count is reported.
- **Unexercised path:** a slice under 2 s is legal for Stage 7, because the core window then becomes the whole sequence (`normalize.py:398`). But the 2 s clip minimum (`video_io.py:71`) means Stages 7-15 have never run on input that short, and Stage 9's arm-extension gate is relative to the clip's range. The evaluation must report accuracy separately for slices under 2 s, and `test_two_swings_at_one_second_get_disjoint_windows` must run the full stack on them.

**Step 4: re-run the existing per-swing stack on each slice**
1. `slice_pose_sequence` produces a `PoseSequence` for the window with timestamps intact.
2. `normalize_sequence(slice)` (`normalize.py:528`). A slice gets its own torso scale, sign and core verdict.
3. Handedness is locked at session level: call `detect_handedness` for diagnostics, then overwrite with the hint and `HandednessSource.USER_HINT`. Hint-required matches existing behaviour (`record_screen.dart:449-454`, `PIPELINE.md:931`).
4. `detect_contact_frame(..., analysis_window_start_s=slice_start_pts)`. Absolute time arithmetic already works (`contact.py:795-800,890-895`).
5. `segment_swing_phases`, `compute_swing_metrics`, `infer_shot_type`, `build_scorecard`, exactly as in `pipeline.py:464-495`.

**Step 5: rejection reasons** (closed enum, counted per session in `rejection_counts`)

`below_relative_floor`, `not_prominent`, `wrist_unobserved`, `window_too_short`, `pose_unusable`, `identity_break_in_window`, `contact_not_found`, `sequence_unusable`, `contact_off_peak` (more than 6 frames from the candidate peak), `no_motion_evidence` (`MOTION_NOT_SUSTAINED`, `contact.py:161`).

**Step 6: confidence**
- `swing_confidence` is `contact.confidence` computed on the slice. The slice restores the meaning of prominence and the existing penalties apply.
- `≥ 0.45` means `accepted` (matches `gemini.py:55`).
- `0.35-0.45` means `low_confidence`: stored and shown, excluded from statistics.
- `< 0.35` means rejected.
- **Confidence is uncalibrated.** Past measurements show confidence can anti-correlate with correctness (`contact.py:136-137`, `PIPELINE.md:839-841`). The evaluation must plot precision by confidence bin. If that curve is not monotonic, these thresholds do not ship.

### 4.3 What the algorithm does with non-swing motion

| Event | Expected handling | Honest status |
|---|---|---|
| Dead time, player still | Below floor | Should work |
| Walking | Body-frame removes translation, arm swing below floor | Likely, unmeasured |
| Bouncing the ball | Off-hand mostly; racket-hand low amplitude, below floor | Likely, unmeasured |
| Ball pickup | Bending shortens the torso, so the rolling median resists. `motion_evidence_absent`, arm gates | Uncertain, measured in the experiment |
| Takeback peak | Inside refractory, or fails prominence or `contact_off_peak` | Mostly |
| Practice and shadow swings | **Cannot be distinguished by pose. They count.** | Known limit. Copy says so, and the user can exclude them. |
| Other people | Section 11 | Depends on the experiment |

### 4.4 Determinism

No randomness and no wall clock in the module. Sorts have explicit tie-breaks (`contact.py:640`). All thresholds are named `Final` constants.

**Given the same landmarks, the result is identical.** MediaPipe itself is not assumed bit-reproducible across thread counts or CPU types, so a retried job could differ slightly. Record `pipeline_version`, `segmentation_version` and a SHA-256 of rounded landmarks for diagnosis, then discard the landmarks.

### 4.5 Required synthetic-keypoint tests

Build on `tests/unit/analysis/synthetic_swing.py` by concatenating `canonical_wrist_path` swings with idle stretches in `synthetic_session.py`.

- `test_ten_swings_yield_ten_windows`
- `test_faster_swing_does_not_hide_slower_neighbours`
- `test_refractory_merges_double_peak_within_one_swing`
- `test_two_swings_at_one_second_get_disjoint_windows`
- `test_windows_never_overlap` (property test over random spacings from a fixed list)
- `test_walking_arm_motion_produces_no_candidate`
- `test_low_amplitude_bounce_produces_no_candidate`
- `test_occlusion_spike_is_not_a_candidate`
- `test_gap_longer_than_three_frames_splits_segments`
- `test_identity_jump_rejects_only_the_affected_window`
- `test_player_size_doubling_does_not_change_swing_count`
- `test_mixed_swing_directions_both_detected`
- `test_handedness_is_locked_to_hint_across_windows`
- `test_slice_prominence_recovers_above_floor` (the 0.35 floor regression)
- `test_contact_absolute_time_uses_source_pts`
- `test_same_input_gives_identical_output`
- `test_module_has_no_io_imports` (extends `test_analysis_purity.py`)
- `test_single_swing_pipeline_outputs_unchanged` (`contact.py`, `normalize.py` regression against the vendored clip)

Synthetic tests prove logic, not accuracy. Real footage does that.

### 4.6 Measuring accuracy

- **Labelled set:** at least 10 real sessions and 600 labelled events before general release. Mix feeding, rallying and serve practice; near and far camera; alone and with others. Labels are stored privately, not committed, because they contain people. Only the vendored Pexels clip stays in the repo.
- **Matching:** one-to-one greedy by time. Detection tolerance ±5 frames at 30 fps (±167 ms). Contact-accuracy tolerance ±3 frames. Human label precision is ±1 frame and Stage 9 carries an accepted +1 residual (`PIPELINE.md:927-929`), so tighter than ±3 would measure noise.
- **Split:** by session, never by swing. Tune on train and report on held-out.
- **Reported:** precision, recall, F1, contact error distribution, false positives per class, per-session worst case, precision by confidence bin, shot-type confusion matrix, `unknown` share, and precision with shadows counted as hits and as errors.
- **Release targets:** precision ≥ 0.90, recall ≥ 0.85, contact within ±3 frames on ≥ 90 %, `unknown` ≤ 30 %. Any change to `SEGMENTATION_VERSION` re-runs the harness (`backend/tests/eval/session_eval.py`, run manually since it needs private footage).

---

## 5. Limit changes

**Single-swing limits stay exactly as they are.** All the values in section 2.2 remain, including the defaults on the functions they live in. New session limits are separate settings used only by new code, so old clients and tests see no change.

### 5.1 Proposed session values

| Setting | Launch | After measurement | Basis |
|---|---|---|---|
| `SESSION_MIN_SECONDS` | 60 | 60 | Below this the single-swing flow is better |
| `SESSION_MAX_SECONDS` | 600 | 1200 | Compute (section 6) |
| `SESSION_MAX_BYTES` | 1 GiB (1,073,741,824) | 1 GiB | Fits in 4 GiB in-memory filesystem with margin |
| `SESSION_MAX_SWINGS` | 600 | 600 | Bounds rows and payload |
| Pose rate | 30 fps | 30 fps | Stage 9 constants assume ~30 fps (`contact.py:33-52`, `video_io.py:57`) |
| Pose resolution | 640 long edge | Raise only if the experiment shows wrist quality needs it | `video_io.py:61` |

### 5.2 Size arithmetic

Size in MB = Mbps × seconds ÷ 8.

| Source | Bitrate | 10 min | 20 min |
|---|---|---|---|
| Vendored clip, for reference | 2.19 Mbps measured (2,853,152 B × 8 ÷ 10.4 s) | 164 MB | 329 MB |
| **In-app target: 720p30** | **4 Mbps, requested** | **300 MB** | **600 MB** |
| Phone default 720p (estimate) | 8 Mbps | 600 MB | 1,200 MB (over limit) |
| Phone 1080p (estimate) | 16 Mbps | 1,200 MB (over limit) | 2,400 MB (over limit) |

The 1 GiB cap at 20 minutes implies at most 1,073.7 MB × 8 ÷ 1,200 s = 7.2 Mbps average. A 16 Mbps import hits the cap at 1,073.7 × 8 ÷ 16 = 537 s, about 9 minutes.

**The in-app recorder can request this with no new dependency.** `camera` 0.11.4's `CameraController` accepts `fps` and `videoBitrate` (pub cache `camera_controller.dart:246-256`). The current code passes neither (`record_screen.dart:250-254`). Whether each platform honours the values must be tested on real devices.

`ResolutionPreset.high` is commonly 720p. **[verify on device]**

### 5.3 Upload approach

**Recommendation: TUS resumable upload directly to Supabase Storage, with a server-chosen path.**

- A 600 MB single PUT fails on any network blip and, on this code path, requires the whole file in RAM.
- Supabase's `storage/v1/upload/resumable` endpoint uses 6 MB chunks and URLs valid for 24 hours **[verify]**. Auth is the user JWT, governed by the existing insert policy `swing_videos_insert_own` (`DATABASE_SETUP.md:322-327`). **That policy is not confirmed live.** Today's upload uses a signed token and does not depend on it. Whether TUS accepts a signed upload token instead is **[verify]**.
- `storage_client` 2.8.0 has no resumable API (no match in `storage_file_api.dart` in the pub cache). Write a small client, `tus_upload.dart`, using the existing `http` package: POST to create, PATCH chunks, HEAD to resume. About 200 lines of Dart (estimate). Evaluate a third-party package only if this proves hard on web.
- Files read through `PlatformFile.readAsByteStream()` (`file_picker_platform_interface` 4.0.0 `platform_file.dart:56`), sequentially, never `readAsBytes`. Resume skips already-sent bytes.
- A 1-hour JWT (`DATABASE_SETUP.md:988`) can expire during a long upload, so read the current session token per PATCH.

Upload time arithmetic for 600 MB: at 20 Mbps, 4 minutes; 10 Mbps, 8 minutes; 5 Mbps, 16 minutes; 1 Mbps, 80 minutes.

**Plan limit:**

| Plan | Limit | Result |
|---|---|---|
| Free | 50 MB per file, 1 GB total project storage **[verify]** | 2 sessions at most. Session Mode cannot run on this plan. |
| Pro | Roughly $25/month with 100 GB storage and 250 GB egress; per-file limit raised through a global setting **[verify]** | Raises the bucket cap, which is why the API checks must remain. |

The global setting applies to the single-swing path too. Its API and client caps still hold at 50 MiB, so behaviour there stays the same, but direct uploads under a user's own prefix could now be larger. They are harmless: nothing analyses them without passing `routes_analyses.py:116`, though they cost storage. The retention sweep should remove orphaned objects.

**Path:** `swing-videos/{user_id}/sessions/{session_id}.{ext}`. Same bucket, so storage RLS stays unchanged and account deletion already recurses into sub-folders (`storage.py:245-259`).

### 5.4 Client-side compression

**Phase 1: none for imports.** The pubspec has no transcoder (`pubspec.yaml:10-21`). A transcoder is a large native dependency, slow and battery-heavy on the phone, and hard on web. In-app recording controls bitrate at the source. An oversize import gets a clear rejection: "Record in the app, or trim it." This is a real limitation for 1080p camera-roll footage, and it is a known gap against any app that accepts full matches.

### 5.5 Mobile failure modes

| Failure | Handling |
|---|---|
| App backgrounded during upload | Dart stops. No background upload in Phase 1 (no foreground service, and adding one changes the permission set CLAUDE.md records). TUS resumes on return. Copy says so. |
| Process killed | Recorded file lives in app documents with `{session_id, offset}` persisted, not in `getCacheDir()`, which the OS can purge. A picked file may need re-picking on Android if the URI grant is gone. |
| Screen sleep during 20-minute recording | Keep-awake. `WAKE_LOCK` is already in the shipped set (CLAUDE.md). Check the merged manifest as CLAUDE.md requires. |
| Heat, battery | Warn before starting. Stop cleanly if the camera throws, keeping what was recorded. |
| Low disk | Check free space ≥ expected size × 1.2 before recording; 600 MB needs 720 MB. |
| Mobile data | Before upload: "About 600 MB. Wi-Fi recommended." No connectivity detection in Phase 1. |
| Phone moved mid-session | Calibration invalid, play area wrong. Setup copy says tripod. Not detected in Phase 1. |

### 5.6 Web

`web_dist/` is the built site. Hosting config and `CORS_ALLOWED_ORIGINS` are not in the repo.

- **No live session recording on web in Phase 1.** `camera_web` accumulates `Blob`s in memory until stop (`camera_web` `camera.dart:135`), and a 20-minute browser recording risks killing the tab.
- **Import works**, via byte stream.
- **Browser-recorded WebM is weak for sessions.** MediaRecorder writes no Duration or Cues (`video_io.py:324-326`). Duration falls back to a packet scan (`video_io.py:273,321-357`), which is fine. Seeking is not: without Cues, tap-to-jump in `<video>` and ball-stage seeks may be slow or inaccurate. Accept WebM, but warn that playback jumps may be slow.
- **CORS:** TUS PATCH and headers cross to Supabase. Existing comments only confirm PUT (`supabase_storage.dart:23-27`). **[verify from a browser before committing to web upload]**

---

## 6. Cloud Run

### 6.1 Compute arithmetic (estimate)

Per frame on 2 vCPU: pose 29-49 ms (assumed, `PIPELINE.md:1910`) + decode 8.3-16.7 ms (2-4 s ÷ 240, `PIPELINE.md:1893`) = **37-66 ms**.

| Session | Frames (duration × 30) | Pose + decode | Pure analysis (0.2 s × 200 swings, `PIPELINE.md:1897,1902`) | Download, Gemini, persist | Total wall |
|---|---|---|---|---|---|
| 10 min | 18,000 | 11-20 min | 40 s | ~1 min | **13-22 min** |
| 20 min | 36,000 | 22-40 min | 40 s | ~1.5 min | **24-42 min** |

That is **1.2 to 2.2 times real time**. Slower than the player might expect, and the single largest user-visible weakness. There is weak support for the per-frame range: if the 8-13 s clips in commit `d17250d` were a full 240 frames, that is 33-54 ms/frame on 2 pinned cores. The commit does not state frame counts. A Job is not throttled while running, so the 2 min 40 s production trace should not apply to it. Replace these numbers with the query in section 2.3 and one real Job run. XNNPACK scaling to 4 vCPU is unmeasured (`PIPELINE.md:1989`), so measure 2 and 4.

### 6.2 Memory

Current code does **not** hold frames in RAM. Decode is a generator, one frame deep (`video_io.py:727-731`). Landmarks are the part that grows, and they stay small:

- Raw buffers per frame: 528 B (landmarks) + 396 B (world) + 8 + 1 = 933 B. × 36,000 = **33.6 MB**.
- The session signal is of similar size, tens of MB.
- Slices are small and short-lived.

**The real memory problem is the video file.** The container filesystem is in-memory, so the temp file counts against the limit (`PIPELINE.md:1930`).

| Component | 600 MB file | 1 GiB file |
|---|---|---|
| Idle + peak job, excluding clip (385-520 MB minus 50 MiB, unverified) | 335-470 MB | 335-470 MB |
| Temp file | 600 MB | 1,024 MB |
| Keypoints and signals (estimate) | 100 MB | 100 MB |
| **Total** | **~1.0-1.2 GB** | **~1.5-1.6 GB** |

2 GiB is too thin on unverified numbers. **Use 4 GiB** on the Job (2 vCPU allows up to 8 GiB, `PIPELINE.md:1985`). Log RSS at each stage from day one. A long-lived landmarker has never been run for 36,000 frames.

### 6.3 Options

| Option | Timeout behaviour | Throttling | Effect on single-swing | Cost | Verdict |
|---|---|---|---|---|---|
| Synchronous request | Max 3,600 s, service set to 300 s (`PIPELINE.md:1991-1993`). Mobile client must hold a connection for 40 minutes. | None during the request | Blocks the single instance | Low | **No** |
| Background thread in API | No request limit, but instance can be scaled down. Needs `--no-cpu-throttling` and `min-instances=1`. | Under default throttling the job freezes after the response (`PIPELINE.md:1970`) | **Severe:** `max_workers=1`, so one session blocks every clip for 40 minutes. Deploys kill jobs. | ~$104/month (`PIPELINE.md:1972`), ~$114 with 4 GiB (+2 GiB × 2,592,000 s × $0.000002 = $10.37) | **No** |
| **Cloud Run Job** | Task timeout hours **[verify limit]** | CPU always allocated while running | **None.** Separate container. | **$0.06-0.11 per 20-minute session** | **Recommended** |
| Cloud Tasks + worker service | HTTP dispatch deadline about 30 minutes **[verify]**, shorter than the upper estimate | Needs a tuned service | None | Similar compute plus another moving part | No |
| Chunked fan-out | Each chunk short | Fine | None | Same CPU, faster wall | Later. Tracking state and swings at chunk seams add real complexity. Cloud Run Job parallel tasks are the natural upgrade. |

### 6.4 Recommendation: one Cloud Run Job execution per session

- Same image as the API (`Dockerfile`), command override `python -m app.worker.session_worker`, `PYTHONPATH` already set (`Dockerfile:67`).
- Flags: gen2, `--cpu=2 --memory=4Gi --tasks=1 --max-retries=1 --task-timeout=7200`.
- The API starts an execution through `POST run.googleapis.com/v2/.../jobs/{job}:run` with `SESSION_ID` as an env override, using a metadata-server token over the existing `httpx`. The service account needs permission to run with overrides **[verify role]**.
- The worker builds its own `httpx` client and event loop. It does not import FastAPI.
- `max_workers=1` and `--max-instances=1` remain untouched, because the worker is not the API.

**Cost arithmetic (estimate),** using `PIPELINE.md:1972` rates of $0.000018 per vCPU-second and $0.000002 per GiB-second:

| 20-minute session, wall time | vCPU-s × rate | GiB-s × rate | Total |
|---|---|---|---|
| 1,440 s (24 min) | 2,880 × 0.000018 = $0.052 | 5,760 × 0.000002 = $0.012 | **$0.064** |
| 2,520 s (42 min) | 5,040 × 0.000018 = $0.091 | 10,080 × 0.000002 = $0.020 | **$0.111** |

Other per-session costs, all **[verify prices]**:
- Gemini: about $0.003 (3,000 tokens in, 1,000 out).
- Storage: 0.6 GB for 30 days at roughly $0.021 per GB-month is $0.013.
- Egress: one full replay at roughly $0.09 per GB is $0.05 beyond the plan allowance.

**Total about $0.10-0.20 per session, plus the fixed Supabase plan.** At the proposed quota (20 per user per 30 days), a heavy user costs at most about $4/month (estimate). Whether the Cloud Run free tier covers Jobs, and which free-tier figures are correct, is **[verify]**.

Note the API tier is a separate issue. `PIPELINE.md:2347-2353` says any real beta user triggers the ~$100/month production tier, because single-swing jobs run as post-202 background threads under throttling. Session Mode's Job does not need that, and the same pattern could later remove that cost for single-swing work.

### 6.5 Idempotency, retry, recovery, progress, quotas

| Concern | Design |
|---|---|
| Create | `client_request_id` unique per user; a repeat returns the existing row. |
| Complete | No effect unless `awaiting_upload`; otherwise returns current state. |
| Claim | Conditional PATCH `status=eq.queued` to `running`, with `Prefer: return=representation`. Zero rows returned means another worker has it, so exit 0. A single PostgREST statement is atomic. |
| Retry | Cloud Run restarts the task once. The worker sees `running` with a stale heartbeat and `attempts < 2`, and reclaims. Otherwise it fails with `worker_lost`, using the staleness approach already used on the read path (`orchestrator.py:128-146`). |
| Crash mid-pose | Starts over. **No keypoint checkpoint in Phase 1:** it would mean persisting a body-landmark time series, and crashes should be rare. Revisit if the failure rate is above 2 %. |
| Swing writes | All rows in one PostgREST POST array, a single atomic statement (batched at 100 if request size requires). A retry first deletes the session's swing rows. The session's terminal PATCH is last, so failure leaves `running`, then stale. Same benign ordering as `repository.py:281-286`. |
| Lost queue | Read-path reconciliation: polling a `queued` session older than 60 s with capacity free triggers a new execution. Same reasoning as `orchestrator.py:138-142`. |
| Queue drain | A finishing worker claims the next `queued` row before exiting. |
| Progress | Time-based heartbeat every 30 s: stage, fraction, `frames_done`, `swings_found`. Stale at 600 s on the read path. Cost is 2 PATCH per minute. The client polls every 5 s. **Polling does not keep the job alive,** unlike `api_client.dart:39-40`. |
| Cancel | Sets `cancel_requested_at`. Worker checks at heartbeat. Best-effort `executions:cancel`. |
| Account deleted mid-job | A heartbeat PATCH matching zero rows aborts the worker, and final writes tolerate it. The deletion route also cancels active executions. |
| Quotas | `UNIQUE (user_id) WHERE status IN ('queued','running')`: one active per user, structurally. 3 per day and 20 per 30 days, checked at create. Global `SESSION_MAX_RUNNING = 3`. |
| Cost bound | Task timeout (2 h) caps any one run. Quotas cap repetition. |

### 6.6 Two-pass pose gating: useful later, not in Phase 1

A coarse 10 fps pass plus a fine 30 fps pass around swings, with 2.5 s per swing:

- Dense pose frames: 30 × D (D in seconds).
- Two-pass: 10 × D + 75 × S (S swings).
- Break-even: 20D = 75S, so one swing every **3.75 s, or 16 swings per minute**.

| Swing rate | Pose saving |
|---|---|
| 6 per minute (lots of dead time) | (30 − 10 − 7.5) ÷ 30 = **42 %** |
| 16 per minute (ball machine, feed) | **0 %** |

Costs of the option:
- Decode runs twice.
- At 10 fps VIDEO-mode tracking may re-detect more often, and IMAGE-mode cost is 4.11× (`PIPELINE.md:1910`).
- Peak timing aliases at 10 fps, a direct recall risk on the riskiest assumption.
- Each fine window needs a new tracker state or monotonic timestamps (`sequence.py:28-31`).

**Keep single-pass dense pose in Phase 1.** It is simpler and more accurate. If the experiment shows typical duty cycles are low and compute is the blocker, two-pass is the first optimisation.

---

## 7. Database schema

**Conventions kept:** service-role writes, read-only RLS for the owner, `_chk` constraint names as live uses, `NULL` for unmeasured and never `0`, SQL in `docs/DATABASE_SETUP.md` as new **Part 9** with a rollback section, run by the owner.

**Why not reuse `analysis_jobs` and `analyses`:**
- `analyses.id` must be a job id (`DATABASE_SETUP.md:1514`), so each swing would need a fake job row.
- `GET /v1/analyses` has no filter (`repository.py:329-334`). 200 swings would flood the 50-row history of **every old client build** and pollute its trends.
- `analysis_jobs` has a fixed error-code check and a three-segment path check (`DATABASE_SETUP.md:1485,1498`).

**What is reused:** the `AnalysisResponse` sub-models, inside `session_swings.payload`. Per-swing shape stays the same, so `ResultsScreen` can render it.

```sql
-- SKETCH. Part 9. Review against live before running.
create table public.sessions (
  id                   uuid primary key default gen_random_uuid(),
  user_id              uuid not null references auth.users(id) on delete cascade,
  client_request_id    uuid not null,
  storage_path         text not null,
  status               text not null default 'awaiting_upload',
  stage                text,
  progress_fraction    numeric(4,3),
  frames_done          integer,
  frames_total         integer,
  swings_found         integer,
  attempts             integer not null default 0,
  execution_name       text,
  cancel_requested_at  timestamptz,
  heartbeat_at         timestamptz,            -- NULL while queued
  error_code           text,
  error_message        text,
  error_stage          text,
  capture_source       text not null,          -- 'record' | 'import'
  declared_duration_s  double precision not null,
  measured_duration_s  double precision,       -- NULL until probed
  declared_size_bytes  bigint not null,
  content_type         text not null,
  handedness_hint      text not null,
  camera_view_hint     text not null default 'unknown',
  play_area            jsonb,
  ball_speed_calibration jsonb,                -- persisted: job is out of process
  consent_bystanders_at timestamptz not null,
  swing_count          integer,
  counted_swing_count  integer,
  low_confidence_count integer,
  rejection_counts     jsonb,
  stats                jsonb,                  -- SessionStats
  summary              jsonb,                  -- CoachingFeedback
  summary_stale        boolean not null default false,
  pipeline_version     text,
  segmentation_version text,
  rubric_version       text,
  timings              jsonb,
  video_retain_until   timestamptz,
  video_deleted_at     timestamptz,
  started_at           timestamptz,
  finished_at          timestamptz,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  constraint sessions_status_chk check (status in
    ('awaiting_upload','queued','running','complete','partial','failed','cancelled','expired')),
  constraint sessions_error_pairing_chk check
    ((status = 'failed') = (error_code is not null)),
  constraint sessions_path_prefix_chk check
    (storage_path like 'swing-videos/' || user_id::text || '/sessions/%'),
  constraint sessions_capture_source_chk check (capture_source in ('record','import')),
  constraint sessions_size_chk check (declared_size_bytes > 0)
);
-- error_code check: full ErrorCode set plus new members, for the reason given
-- at DATABASE_SETUP.md:86-90 (too narrow turns a failure into a failed write).

create table public.session_swings (
  id                 uuid primary key default gen_random_uuid(),
  session_id         uuid not null references public.sessions(id) on delete cascade,
  user_id            uuid not null references auth.users(id) on delete cascade,
  swing_index        integer not null,
  contact_time_s     double precision not null,
  window_start_s     double precision not null,
  window_end_s       double precision not null,
  status             text not null,
  confidence         real not null,
  shot_type          text not null,
  overall_score      numeric(5,2),
  peak_hand_speed_tu_s real,
  ball_speed_mph     integer,
  ball_speed_unavailable_reason text,
  user_excluded      boolean not null default false,
  flags              text[] not null default '{}',
  pipeline_version   text not null,
  rubric_version     text not null,
  segmentation_version text not null,
  payload            jsonb not null,
  created_at         timestamptz not null default now(),
  constraint session_swings_status_chk check (status in ('accepted','low_confidence')),
  constraint session_swings_shot_type_chk check (shot_type in
    ('forehand_topspin','forehand_slice','backhand_one_handed',
     'backhand_two_handed','serve','volley','unknown')),
  constraint session_swings_score_chk check
    (overall_score is null or overall_score between 0 and 100),
  constraint session_swings_speed_chk check
    (ball_speed_mph is null or ball_speed_mph between 15 and 160),
  constraint session_swings_speed_pairing_chk check
    ((ball_speed_mph is null) <> (ball_speed_unavailable_reason is null)),
  constraint session_swings_payload_chk check (jsonb_typeof(payload) = 'object'),
  constraint session_swings_window_chk check
    (window_start_s <= contact_time_s and contact_time_s <= window_end_s),
  unique (session_id, swing_index)
);

create unique index sessions_user_request_uq on public.sessions (user_id, client_request_id);
create unique index sessions_one_active_uq on public.sessions (user_id)
  where status in ('queued','running');
create index idx_sessions_user_created_id on public.sessions (user_id, created_at desc, id desc);
create index idx_sessions_active on public.sessions (status, heartbeat_at)
  where status in ('queued','running');
create index idx_sessions_retention on public.sessions (video_retain_until)
  where video_deleted_at is null;
create index idx_session_swings_filter on public.session_swings (session_id, shot_type, swing_index);

alter table public.sessions enable row level security;
alter table public.session_swings enable row level security;
alter table public.sessions force row level security;
alter table public.session_swings force row level security;
create policy sessions_select_own on public.sessions
  for select to authenticated using (user_id = auth.uid());
create policy session_swings_select_own on public.session_swings
  for select to authenticated using (user_id = auth.uid());
revoke all on public.sessions from anon;
revoke all on public.session_swings from anon;
create trigger trg_sessions_updated_at before update on public.sessions
  for each row execute function public.set_updated_at();  -- recreate function first; unverified live
```

**Size estimate.** One swing's payload is estimated at 8 KB (17 metrics, phases, evidence strings), so 200 swings is 1.6 MB per session. Measure on real rows.

**No third stats table.** Between-session trends read `stats` from a user's last N `sessions`.

**Retention:**

| Data | Lifetime |
|---|---|
| Raw video | `finished_at + 30 days` by default, or user-deleted earlier |
| `awaiting_upload` and its partial upload | Expires at 48 hours |
| Numeric results | Until the user deletes the session or the account |
| Landmark time series | **Never persisted** |

Enforced by a daily Cloud Scheduler trigger of the worker with a `sweep` argument. It deletes objects and sets `video_deleted_at`. The result screens handle a missing video.

**Account deletion change.** `delete_user_rows` must delete `session_swings`, then `sessions`, before `analyses`, explicitly, keeping the policy of not relying on cascades (`repository.py:342-364`). The storage step already recurses. Update `test_account_deletion_clients.py`.

---

## 8. API contract

All additive, under `/v1`. Existing routes and behaviours are unchanged. Bodies use `extra="forbid"`, errors use the flat envelope (`errors.py`). Models live in `backend/app/models/session.py`.

### 8.1 State machine

```
awaiting_upload --complete--> queued --claim--> running --> complete | partial | failed
       |                        |                  |
       +--> expired (48 h)      +--> cancelled <---+
```

- `partial`: at least one swing, but some degradation (a `low_confidence` share over 25 %, Gemini fell back, or ball stage hit its budget).
- `failed`: no usable result.
- `SessionStatus` is its own enum, separate from `JobStatus` and `AnalysisStatus`.
- `no_swings_found` is `failed`, not `complete` with zeros.

### 8.2 Endpoints

| Method and path | Request | Response | Errors |
|---|---|---|---|
| `GET /v1/sessions/capabilities` | none | `SessionCapabilities` | 401 |
| `POST /v1/sessions` | `CreateSessionRequest` | 201 `CreateSessionResponse` | 400, 401, 404 flag off, 413, 429 |
| `POST /v1/sessions/{id}/complete` | none | 202 `SessionResponse` | 401, 404, 409, 413, 429, 503 |
| `GET /v1/sessions/{id}` | none | 200 `SessionResponse` | 401, 404 |
| `GET /v1/sessions?limit&cursor` | keyset, as `/v1/analyses` | 200 `SessionListResponse` | 400, 401 |
| `GET /v1/sessions/{id}/swings?limit&cursor&shot_type&include_low_confidence` | limit default 100, max 200, cursor on `swing_index` | 200 `SessionSwingListResponse` | 400, 401, 404 |
| `GET /v1/sessions/{id}/swings/{swing_id}` | none | 200 `SessionSwingDetail` | 401, 404 |
| `POST /v1/sessions/{id}/swings/{swing_id}/exclude`, `/include` | none | 200 `SessionStatsResponse` | 401, 404, 409 |
| `GET /v1/sessions/{id}/playback` | none | 200 `PlaybackUrlResponse` | 401, 404, 410 |
| `POST /v1/sessions/{id}/cancel` | none | 202 `SessionResponse` | 401, 404, 409 |
| `DELETE /v1/sessions/{id}/video` | none | 204 | 401, 404, 503 |
| `DELETE /v1/sessions/{id}` | none | 204 | 401, 404, 503 |
| `GET /v1/sessions/trends?shot_type&limit` | none | 200 `SessionTrendsResponse` | 400, 401 |

Declare `capabilities` and `trends` before `{id}`.

CORS already allows GET, POST and DELETE (`config.py:84`). Exclude and include are POST so no PATCH is needed. Ownership: every query filters on `user_id`, and non-owned returns the same `404` as missing, per `DATABASE_SETUP.md:265-268`.

### 8.3 Shapes

**`CreateSessionRequest`**

```json
{
  "client_request_id": "6f1d7f4e-2a0b-4d3f-9c11-1a2b3c4d5e6f",
  "content_type": "video/mp4",
  "size_bytes": 628000000,
  "duration_s": 1184.2,
  "capture_source": "record",
  "handedness_hint": "right",
  "camera_view_hint": "side_on",
  "play_area": {"x_min": 0.05, "y_min": 0.1, "x_max": 0.95, "y_max": 1.0},
  "ball_speed_calibration": null,
  "bystander_acknowledged": true
}
```

- The example shows the 20-minute limit. At the 600 s launch limit it would return `session_limit_exceeded`.
- `handedness_hint` is required and must not be `unknown`.
- `bystander_acknowledged` must be true.
- `ball_speed_calibration` reuses `BallSpeedCalibration` and `validation_error()` (`requests.py:129-194`).

**`CreateSessionResponse`** (201). The path is server-chosen.

```json
{
  "session_id": "9f2c1a44-3b7e-4f0d-9a11-6c5c2e8b71aa",
  "status": "awaiting_upload",
  "storage_path": "swing-videos/b1d9f0a2-.../sessions/9f2c1a44-....mp4",
  "upload": {
    "protocol": "tus",
    "endpoint": "https://<project>.supabase.co/storage/v1/upload/resumable",
    "bucket": "swing-videos",
    "object_name": "b1d9f0a2-.../sessions/9f2c1a44-....mp4",
    "chunk_size_bytes": 6291456,
    "expires_at": "2026-11-04T12:00:00Z"
  }
}
```

**`SessionResponse`.** One shape in every state, unlike the polymorphic poll (`routes_analyses.py:223-243`), so there is no body-shape discrimination. Null blocks are present.

```json
{
  "session_id": "9f2c1a44-...",
  "status": "running",
  "created_at": "2026-11-02T12:00:00Z",
  "measured_duration_s": 1184.2,
  "progress": {
    "stage": "pose",
    "fraction": 0.42,
    "frames_done": 14920,
    "frames_total": 35526,
    "swings_found": null,
    "estimated_seconds_remaining": 1100,
    "estimate_is_rough": true
  },
  "queue_position": null,
  "error": null,
  "swing_count": null,
  "counted_swing_count": null,
  "low_confidence_count": null,
  "rejection_counts": null,
  "stats": null,
  "summary": null,
  "summary_stale": false,
  "video_available": true,
  "video_retain_until": null,
  "pipeline_version": "v3",
  "segmentation_version": "seg_v1"
}
```

`stage` values: `downloading`, `probing`, `pose`, `analysing`, `ball_speed`, `summarising`, `persisting`.

`error` reuses `AnalysisError`, so the nested key stays `code` (`responses.py:415-428`). `summary` reuses `CoachingFeedback`.

**`SessionSwingListItem`** (flat)

```json
{
  "swing_id": "c1...",
  "swing_index": 14,
  "contact_time_s": 212.43,
  "window_start_s": 210.61,
  "window_end_s": 213.40,
  "shot_type": "serve",
  "shot_type_confidence": 0.71,
  "status": "accepted",
  "confidence": 0.62,
  "overall_score": 68.5,
  "peak_hand_speed_tu_s": 9.4,
  "ball_speed_mph": null,
  "flags": [],
  "user_excluded": false
}
```

**`SessionSwingDetail`** carries the `AnalysisResponse` fields with `analysis_id` set to the swing id, plus `session_id` and `swing_index`. It has **no `feedback`** (no per-swing Gemini) and no `timings`. Flutter's `AnalysisResponse.fromJson` treats missing `feedback` as null (`analysis_response.dart:127,162`). A contract test proves the detail body parses and that `ResultsScreen` renders it.

**`PlaybackUrlResponse`**

```json
{"url": "https://...signed...", "expires_at": "2026-11-02T13:15:00Z", "content_type": "video/mp4"}
```

TTL 15 minutes (a new `create_signed_download_url` in `storage.py`). The client refetches on expiry. Range-request behaviour on signed URLs is **[verify]**.

**`SessionCapabilities`**

```json
{
  "enabled": true,
  "max_duration_s": 600,
  "min_duration_s": 60,
  "max_size_bytes": 1073741824,
  "sessions_remaining_today": 3,
  "sessions_remaining_30d": 20,
  "live_recording_supported": true
}
```

Limits come from this response rather than being mirrored as constants in Dart.

### 8.4 New error codes

Appended, never reordered (`enums.py:95-97`). Each needs entries in `ERROR_RETRYABLE`, `ERROR_MESSAGES` and, where relevant, `ERROR_HTTP_STATUS` (`errors.py:63-128`), and in the client's `errorCodeToPlainLanguage`. Old clients cannot receive them, because they only appear on new routes.

| Code | HTTP | Retryable | Meaning |
|---|---|---|---|
| `session_not_found` | 404 | no | Missing or not owned |
| `session_feature_disabled` | 404 | no | Flag off or user not allowlisted |
| `session_limit_exceeded` | 413 | no | Declared duration or size over limit |
| `session_quota_exceeded` | 429 | yes | With `Retry-After` |
| `session_state_conflict` | 409 | no | E.g. complete on a cancelled session |
| `video_no_longer_available` | 410 | no | Retention or user deleted it |
| `no_swings_found` | job-only | no | Analysed, nothing passed |

Reused: `queue_full`, `object_not_found`, `file_too_large`, `storage_unavailable`, `video_too_long`, `decode_failed`, `no_pose_detected`, `calibration_invalid`, `worker_lost`, `internal_error`. Measured duration over the limit fails the job with `video_too_long`.

### 8.5 Compatibility and versioning

- **Older builds:** `/v1/uploads/ticket`, `/v1/analyses` and history are unchanged, and session rows never appear there.
- **Newer client against older server:** capabilities 404 or network failure hides the entry point.
- **Unknown values:** Dart session models map unknown enum values to `unrecognized`, per the existing discipline (`api_client.dart:414-447`). The client treats an unrecognised status as "update the app".
- **Numeric comparability:** stats are only compared where `pipeline_version` and `segmentation_version` both match, extending `DATABASE_SETUP.md:762-770`.

---

## 9. Flutter presentation

### 9.1 Flow

```
Record tab -> RecordScreen [ Single swing | Practice session ]
                                   |
                         SessionSetupScreen
                          /               \
              SessionRecordScreen      file chooser
                          \               /
                  SessionProcessingScreen
                  (upload, queue, analyse; safe to leave)
                               |
                     SessionOverviewScreen
                       |              |
          SessionReviewScreen    History (trends)
                       |
             ResultsScreen (one swing)
```

Same imperative `MaterialPageRoute` pattern. The bottom bar does not change.

### 9.2 Screens

**Setup.** Handedness required, as today. Setup card reuses the guidance at `record_screen.dart:842-849`, plus tripod. Play-area drag on the preview or first frame. Acknowledgement checkbox. Optional calibration reuses `CalibrationScreen`, but is live camera only.

```
+----------------------------------+
| <  Practice session              |
| Racket hand   ( Right ) ( Left ) |
| +------------------------------+ |
| | Set up the phone             | |
| | Tripod, side-on, 5-10 m...   | |
| +------------------------------+ |
| +------------------------------+ |
| |  [ preview with drag box ]   | |
| |  Only this area is analysed  | |
| +------------------------------+ |
| Ball speed (optional)  Calibrate |
| [x] Anyone else in frame knows   |
|     they are being filmed        |
| Up to 10 min. About 300 MB.      |
| [ Start recording ] [ Choose file]|
+----------------------------------+
```

**Recording.** Elapsed and remaining time, keep-awake, stop button with confirmation.

**Processing.** One screen for upload and analysis, each with real progress and honest ETA. Leaving is safe, and the session appears in History. An upload left behind gets a Resume card.

```
+----------------------------------+
| <  Your session                  |
|  (1) Upload          done        |
|  (2) Waiting         done        |
|  (3) Analysing                   |
|      Tracking your movement      |
|      [#########-------] 42 %     |
|      About 18 minutes left       |
|      (rough estimate)            |
|  You can leave. It keeps running |
|  and will appear in History.     |
|        [ Go to History ]         |
|        [ Cancel session ]        |
+----------------------------------+
```

There are no push notifications in Phase 1, so the user must come back to check. Copy should be straightforward about this.

**Overview.** Wrap-up, built from `AppCard`, `SectionHeader`, `InlineStats`, `ShotMixChart`, `TrendDirectionChip`. The 18-minute example assumes the limit has been raised (slice 8); the launch limit is 10 minutes.

```
+----------------------------------+
| <  Mon 2 Nov, 18 min             |
|  84 swings   71 counted          |
|  13 not counted  (why?)          |
| +------------------------------+ |
| | Shot mix        [ donut ]    | |
| | Forehand 41  Serve 12        | |
| | Backhand 9   Unknown 9       | |
| +------------------------------+ |
| +------------------------------+ |
| | Consistency: Forehand (n=41) | |
| | Contact height     tight     | |
| | Swing path         moderate  | |
| | Tempo              wide      | |
| +------------------------------+ |
| +------------------------------+ |
| | Early vs late                | |
| | Hand speed   falling  -9 %   | |
| +------------------------------+ |
| +------------------------------+ |
| | Coach summary                | |
| | ...                          | |
| +------------------------------+ |
| [ Watch your swings ]            |
+----------------------------------+
```

**Review: video with synced shot list.** `video_player` from the signed URL, or the local file when it still exists (saves egress).

```
+----------------------------------+
| <  Your swings   [Skip gaps: on] |
| +------------------------------+ |
| |            video             | |
| |  Forehand  #14  Score 68     | |
| +------------------------------+ |
| |--|-|--||-|---|--|-|||--|--|  | |  markers: swing_timeline.dart
| (All)(Forehand)(Backhand)(Serve) |
| (Volley)(Unknown)(Low confidence)|
| +------------------------------+ |
| | 03:32 Serve      68  >       | |
| | 03:41 Forehand   74  >       | |
| | 03:49 Forehand   -- check >  | |
| +------------------------------+ |
+----------------------------------+
```

- Tap a row or marker: seek to `window_start_s`. Tap `>`: `ResultsScreen`.
- Current position highlights the current row.
- "Skip gaps" auto-seeks from a window's end to the next window's start, respecting filters. Client-side only.
- A row action excludes a swing ("Not a real swing"), which updates stats.
- Seeks depend on keyframe spacing. Stock clips had 3-4 s GOPs (`PIPELINE.md:184`). Recorder GOP is unmeasured, so exact seeks could lag.

**Web and wide screens.** `ContentWidth` constrains the layout. At 900 px and up, video left, list right.

**Swing detail.** `ResultsScreen(analysis:)` (`results_screen.dart:18-21`). Add an optional "View in video" action; existing callers stay unchanged. With no `feedback`, it still shows deterministic swing notes (`results_screen.dart:29-32`). That is the coaching for each swing, with no per-swing Gemini cost.

**History and trends.** Add a section above clips: "Practice sessions", from `GET /v1/sessions`. Trends use `TrendChart(scores:)` (`trend_chart.dart:14-21`), per shot type, one pipeline and segmentation version at a time. Nothing averages across shot types (`dashboard_insights.dart:15-27`, `chart_data.dart:14-15`).

### 9.3 States

| State | Treatment |
|---|---|
| Loading | `SkeletonBlock` |
| Never used | `EmptyState`: what it does and how to film |
| Processing | Resumable from History at any time |
| `no_swings_found` | "No swings found", with the likely cause by `rejection_counts` |
| Partial | `_PartialBanner`-style notice above numbers |
| Low-confidence swing | Listed with a "check" label, not counted, filterable |
| Small sample | Shows counts only, with "Need 8 forehands for consistency" |
| Gemini fell back | Stats stay; summary is the template, no error |
| Video expired | Stats and swings remain; player replaced with notice |
| Upload failed | Resume, never re-send from zero |
| Failed | Plain-language error and retry if `retryable` |

### 9.4 Theme and accessibility

- No raw colours; `AppPalette` and `context.palette` only. Works in mobile light and web light and dark (`main.dart:25-33`).
- Add new tokens to both brand flavours (`brand.dart:19-25`). Check contrast and colour-vision safety for any new categorical hue, as the project already intends.
- Chips: M3 `FilterChip` with text. **Shot type is never colour alone.**
- Timeline: tap targets at least 48 dp, since markers can be dense. The list below is the accessible equivalent, and the timeline exposes `Semantics` for current position.
- Reduced motion: use `motionDuration` for transitions (`main.dart:84-85`). Auto-seek skipping stays on only by user choice.
- Video has no audio track (`record_screen.dart:253`).
- Text scales: no fixed-height rows.
- Web: keyboard focus and arrow keys step between swings.
- Null renders as "not measured", never `0`.
- Copy: "practice session" for this feature and "clip" for single swings, replacing the existing "session" wording in History.

---

## 10. Session-level statistics and Gemini summary

### 10.1 Rules

- All in `backend/app/analysis/session_stats.py`. Pure, no I/O.
- Input: only swings with `status == accepted` and `user_excluded == false` ("counted").
- `null` is excluded, never treated as `0`.
- **Nothing that is a mean or spread crosses shot types.** Counts may.
- `unknown` swings count in totals and mix, and are excluded from per-shot stats.

### 10.2 Definitions

**Shot mix.** Counts per `ShotType`, percent of counted swings, `unknown` shown as its own slice and never redistributed.

**Why not plain CV.** CV = SD ÷ mean only makes sense on a ratio scale with a meaningful zero. `swing_path_angle_deg` is signed around 0, `follow_through_height_tu` can cross 0, and contact height's zero is the hip. Their CV explodes near zero for no sporting reason. So:

| Metric | Why this one | Spread |
|---|---|---|
| `contact_height_ratio` | View-insensitive (`metrics.py:95`) | Robust SD, ratio units |
| `swing_path_angle_deg` | View-insensitive sign (`metrics.py:98`) | Robust SD, degrees |
| `follow_through_height_tu` | View-insensitive (`metrics.py:102`) | Robust SD, TU |
| `tempo_ratio` | View-insensitive, ratio scale (`metrics.py:105`) | Robust CV |
| `shoulder_hip_separation_deg` | View-insensitive (`metrics.py:90`) | Robust SD, degrees |
| `peak_hand_speed_tu_s` | View-sensitive; acceptable within one fixed camera session | Robust CV, labelled |

Not used: `weight_transfer_tu`, `balance_sway_tu` (always null), `takeback_displacement_tu` (permanently null, `shot_type.py:92-98`).

- Robust SD = 1.4826 × median absolute deviation (MAD).
- Robust CV = robust SD ÷ |median|, on ratio-scale metrics only.
- Label: ratio = robust SD ÷ (`ideal_max − ideal_min` from the rubric band for that shot type). `≤ 0.5` is tight, `≤ 1.0` moderate, `> 1.0` wide.
- No band for that shot and metric: show the number, no label.
- Output records `rubric_version`. **Bands are placeholders (`rubric.py:19-22`), so labels are provisional. Say so in the doc and UI details.**
- **No single composite "consistency score" in Phase 1.** It would average different things across unvalidated bands.

**Sample sizes.** Approximate relative standard error of a MAD estimate = 1 ÷ sqrt(2 × 0.37 × n), using MAD's 37 % efficiency.

| n | Error | Treatment |
|---|---|---|
| < 5 | n/a | Count only |
| 5-7 | 44-52 % | Show range (min to max), no label |
| 8-19 | 26-41 % | Label, with "n" visible |
| ≥ 20 | ≤ 26 % | Label |

**Within-session trend.** Per shot type and metric, requiring n ≥ 15 and span ≥ 5 minutes:
- Median of the first third minus median of the last third, by time. Plus Theil-Sen slope, per 10 minutes.
- `falling` or `rising` only if |delta| > 0.5 × robust SD **and** the Theil-Sen slope has the same sign.
- Else `steady`.
- **The UI and Gemini never call this fatigue as fact.** "Hand speed fell 9 % from the first third to the last" is measured. Why it fell is not. Peak speed can also change with shot selection and court position.

**Between-session trend.** Per shot type, with matching `pipeline_version` and `segmentation_version`, sessions with n ≥ 8 for that shot, at least 2 sessions:
- Median overall score per session; each metric's median and robust SD.
- Direction uses the existing `kSteadyBand = 2.0` for scores (`dashboard_insights.dart:55`).
- Camera setup is unknown, so view-sensitive metrics are not trended between sessions.

**Ball speed, when present.** Per shot type: median, maximum and n with non-null speed. `null` count stated. Never estimated from hand speed.

### 10.3 Pure-function signatures

```python
@dataclass(frozen=True)
class SwingRecord:
    swing_index: int
    contact_time_s: float
    shot_type: ShotType
    overall_score: float | None
    metrics: Mapping[str, float | None]
    ball_speed_mph: int | None

def robust_spread(values: Sequence[float]) -> RobustSpread | None: ...
def shot_mix(swings: Sequence[SwingRecord]) -> ShotMix: ...
def spread_label(robust_sd: float, band: tuple[float, float] | None) -> ConsistencyLabel | None: ...
def shot_consistency(swings: Sequence[SwingRecord], shot_type: ShotType,
                     bands: Mapping[str, tuple[float, float]]) -> ShotConsistency: ...
def within_session_trend(swings: Sequence[SwingRecord], metric: str,
                         *, min_n: int = 15, min_span_s: float = 300.0) -> WithinSessionTrend | None: ...
def between_session_trend(points: Sequence[SessionShotPoint],
                          *, min_sessions: int = 2) -> BetweenSessionTrend | None: ...
def session_stats(swings: Sequence[SwingRecord], duration_s: float,
                  bands_by_shot: Mapping[ShotType, Mapping[str, tuple[float, float]]],
                  ball_speeds_requested: bool) -> SessionStats: ...
```

### 10.4 Unit tests

- `robust_spread`: known vector; constant values give 0.0; fewer than two values give `None`; outlier barely moves it.
- `shot_mix`: `unknown` never redistributed; percentages sum correctly; excluded swings absent; empty input.
- Per-shot stats: nothing crosses shot types (a fixture where crossing would change the answer).
- `None` metric is dropped and not read as 0, tested with a real 0.0 kept in.
- Boundaries: n of 4, 5, 7, 8, 14, 15.
- CV is not produced for signed metrics.
- Trend: flat, rising, falling, noisy-no-signal, outlier resistance, short span returns `None`.
- Between-session: version mismatch excluded; unbanded metric gets no label.
- Determinism: same input, identical output, input-order independent.
- Excluding a swing and recomputing equals computing without it.

### 10.5 Gemini summary

**One call per session. No per-swing Gemini.** The current runner calls once per job (`pipeline.py:519-538`). Reusing that per swing would mean hundreds of calls. Per-swing text comes from the existing deterministic notes.

New module `backend/app/feedback/session_summary.py`. New input model `SessionFeedbackInput` with `extra="forbid"`, which structurally has no field for video, frames, landmarks, user id, timestamps, file names or calibration taps, matching `models/feedback.py:60-65`.

**Payload (numbers, enums, verdicts only):**

```json
{
  "pipeline_version": "v3",
  "segmentation_version": "seg_v1",
  "rubric_version": "rubric_v0_placeholder",
  "handedness": "right",
  "duration_minutes": 18,
  "counted_swings": 71,
  "low_confidence_swings_not_counted": 13,
  "data_quality": "good",
  "shot_mix": [
    {"shot_type": "forehand_topspin", "count": 41, "percent": 58},
    {"shot_type": "unknown", "count": 9, "percent": 13}
  ],
  "by_shot": [{
    "shot_type": "forehand_topspin",
    "n": 41,
    "median_overall_score": 66.5,
    "consistency": [
      {"metric": "contact_height_ratio", "median": 0.82, "robust_sd": 0.07,
       "unit": "ratio", "label": "tight"}
    ],
    "priority_metric_names": ["swing_path_angle_deg"]
  }],
  "within_session": [
    {"shot_type": "forehand_topspin", "metric": "peak_hand_speed_tu_s",
     "first_third_median": 9.4, "last_third_median": 8.6, "direction": "falling"}
  ],
  "ball_speed": {"mph": null, "confidence": "unavailable", "reason": "not_calibrated"},
  "unit_glossary": {},
  "low_confidence_warnings": []
}
```

**Staying numbers-only and honest:**
- Reuse `SYSTEM_INSTRUCTION` plus session rules: do not diagnose causes such as fatigue, do not add measurements, say "unknown" shots were not classified.
- Reuse `round_payload_numbers`, `numeric_allowlist`, `enforce`, `parse_gemini_response`, `generation_config`, and the `GeminiFeedbackDraft` schema (`gemini.py:241-255,318-333,474-532,552-568,391-402`).
- `mph` is bound to the session's median speed, or banned when null.
- **Needed change:** `NUMERIC_TOKEN_RE` captures `58%` as a token (`gemini.py:94`), but the allowlist only renders plain numbers (`gemini.py:292-300`). Percent values would always be rejected. The session allowlist must add `%` renderings for `percent` keys only. Do not widen the single-swing one.
- **Existing guard false positives:** banned units are plain substring matches (`gemini.py:424`). "gene**rally**" and "natu**rally**" contain `rally`, and "move your **feet**" contains `feet` (`gemini.py:75-89`). A discarded summary falls back to the template (`gemini.py:516`). Longer text raises this risk, so measure the fallback rate, and fix with word-boundary matching for these tokens under a separate change.

**Token budget.**
- Output: ceiling 2,048, thinking 0, as existing. A full single-swing draft measured 260-352 candidate tokens (`gemini.py:166-174`).
- Input target: ≤ 3,000 tokens (estimate: 4 shot types × up to 6 metrics × ~40 tokens ≈ 960, plus mix, trends, glossary and instructions). Cap by limiting `by_shot` to the 4 most frequent and 3 priority metrics. Add a test in the style of `test_gemini_token_budget.py`.
- Timeout: `GoogleGenAIClient` passes no timeout today (`gemini.py:710-715`), and `gemini_timeout_s` (`config.py:208`) is not read in this module. In a billed job, set one (30 s). One retry on transport failure.

**Low confidence.** Python sets `data_quality`:
- `good`: at least 80 % of accepted-or-low swings are accepted.
- `limited`: below that.
- With `limited`, the prompt requires hedged language.
- Skip Gemini entirely (no call) when counted swings < 8, or when no shot type reaches n ≥ 5. Equivalent to `should_skip_gemini` (`gemini.py:576-581`).

**Fallback.** Never fails the session. A deterministic Python template from the same stats, with `source = template`, plus the existing guard report. Numbers stay visible either way.

**Stale summary.** Excluding a swing recomputes stats immediately, sets `summary_stale = true`, and does not call Gemini again in Phase 1. The UI notes "Written before you removed 2 swings."

---

## 11. Other people in frame, and privacy

*The legal points below flag issues for legal review. They are not legal advice.*

### 11.1 Who gets analysed today

`num_poses=1` and `result.pose_landmarks[0]` (`extractor.py:96,162`). There is no target selection. MediaPipe decides who is first, and after losing tracking it can pick up someone else. The only defence is a soft after-the-fact flag on centroid or torso jumps (`normalize.py:184-215`). Over 8 seconds this rarely matters. Over 20 minutes on a shared court it will.

### 11.2 Phase 1 design, in order of cost

1. **Setup copy.** Stand where you are the largest person in frame, with the phone on your side of the court.
2. **Play-area mask.** The user drags a rectangle once. Pixels outside are set to black in the frames given to pose, not in the stored video. This removes opponents, other courts and spectators from what pose ever sees. Coordinates are unchanged, so no maths moves. It requires a fixed camera. Pure helper, unit-tested. **Unverified:** whether black regions upset detection. It is measured in the experiment.
3. **Continuity breaks.** Existing thresholds (`normalize.py:53-54`) split tracking segments. Windows spanning a break are rejected with `identity_break_in_window`. Only those swings are lost, not the session.
4. **User review.** "Not a real swing" removes a stranger's swing.

**Escalation, only if the experiment fails on wrong-person rate:** `num_poses=2`, already a constructor parameter, plus a pure frame-to-frame assignment on mid-hip distance and torso length, starting from a user tap on the player. Each extra tracked person probably costs close to another landmark inference (estimate), up to 2× on crowded frames. Measure before adopting.

| Person | Phase 1 outcome |
|---|---|
| Opponent or other court | Usually masked |
| Coach or feeder standing on the player's side | **Weak.** May be tracked. Their swings can enter. |
| Ball kids walking through | Short occlusion, likely rejected windows |
| Spectators behind | Usually masked |

**Identity switches** undetected between similar-size people are the residual risk, and they silently pollute stats. Face recognition or appearance re-identification is **not** an option (below).

### 11.3 What the repo has today

- **No age gate.** Sign-up is email and password only (`login_screen.dart:43-47`).
- **No terms, privacy policy or consent text** anywhere in the repo. A case-insensitive search for privacy, consent, COPPA, GDPR and similar found only a note that activity data "belongs in the privacy policy" for users "who may be minors" (`DATABASE_SETUP.md:2089-2091`).
- **Minors are plausible users.** Copy is written for "a 15-year-old" and "a student" (`api_client.dart:109,126`), and there is a `school` brand flavour (`brand.dart:23-25`).
- **Deletion exists:** `DELETE /v1/account` (`routes_account.py`). There is no per-clip delete and no retention.

### 11.4 What longer videos change

| Issue | Clip today | Session |
|---|---|---|
| Bystanders captured | Few, seconds | Many, minutes; other courts, children, coaches |
| Retention exposure | Unbounded but small | Must be bounded by default |
| Likelihood someone objects | Low | Real |
| Storage of minors' images | Same legal class | Much more of it |
| Sharing harm | No sharing | Sharing off; Phase 2 export multiplies risk |

### 11.5 Phase 1 privacy design

- **Acknowledgement at each session:** "Anyone else in frame knows they are being filmed." Stored as a timestamp. It is a prompt, not a legal consent mechanism.
- **Retention:** raw video auto-deleted after 30 days by default. "Delete video, keep stats" and "Delete session" are always available.
- **No face data:** no face recognition, no face detection and no biometric templates. Nothing identifies a person across sessions. BlazePose's face landmarks (indices 0-10) are used only as the existing head metrics and are not persisted.
- **No stored landmark time series.** Per-swing payloads hold numbers only (`responses.py:453-493`). Landmarks exist in process memory and die with the job. This is a deliberate constraint, and it is why there is no checkpointing.
- **Gemini gets aggregated numbers only.** Never video, frames, landmarks, user id, timestamps or filenames, enforced by `extra="forbid"`. The Gemini data-use terms for the API tier in use are **[verify]**, though payload content should make this low risk.
- **Minimisation:** the play-area mask means pose never processes people outside it. Audio is not recorded (`record_screen.dart:253`).
- **Access:** the bucket stays private; playback URLs last 15 minutes. Storage paths are not returned in logs.
- **Sharing:** none in Phase 1. Any later export is opt-in per item and warns about others visible in the video. Blurring bystanders automatically would itself require detecting people, so it needs its own review.
- **Deletion:** account deletion covers session rows and videos (section 7), and aborts running jobs.

### 11.6 For legal review before any public release

These cannot be settled by engineering:
1. Whether a service plausibly used by children under 13 falls under COPPA, and what parental consent it requires.
2. GDPR-K and national ages of digital consent (13-16) if available in the EU or UK, including video of children as personal data.
3. Whether body-pose measurements could count as biometric data under laws such as BIPA or GDPR Article 9, even without identification.
4. Duties to bystanders filmed in semi-public places, including other people's children at a club.
5. Whether "school" deployment brings FERPA or similar rules.
6. Required privacy policy, terms, processor disclosures (Supabase, Google) and app-store privacy labels.

**Engineering recommendation pending that review:** add an age-declaration step at sign-up and keep Session Mode behind the allowlist until a privacy policy exists.

---

## 12. Deliberately out of scope for Phase 1

- Ball tracking beyond the existing 0.25 s window, ball-based swing gating, bounce detection.
- Court homography, shot maps, heat maps, depth, placement, in/out.
- Rally segmentation or counting (contradicts CLAUDE.md), scoring, line calling.
- Ball spin (contradicts CLAUDE.md).
- Multi-player analysis, doubles, match mode, opponent stats.
- Handheld or moving-camera sessions.
- Highlight export, burned-in overlays, social sharing, public links.
- Client-side transcoding; background upload via a foreground service.
- Push notifications, Apple Watch, real-time or on-device processing.
- Manually adding a missed swing (only exclusion and re-inclusion).
- Regenerating the Gemini summary after exclusions.
- Composite consistency score; comparing sessions across pipeline versions; merging clips with sessions in trends.
- Keypoint checkpointing, fan-out, two-pass gating.
- Fixing Stage 8 hint-free handedness (hint stays required) or the placeholder rubric (labels remain provisional).

---

## 13. Rollout

### 13.1 Slices

Each is independently shippable and hidden behind the flag.

| # | Slice | Delivers | Exit gate |
|---|---|---|---|
| 0 | **Experiment and verification** | Section 1 experiment. `gcloud` and SQL checks. Real ms/frame. | Pass thresholds met |
| 1 | **Contract and pure core** | `session.py` models frozen first. `segmentation.py`, `session_stats.py`, tests, eval harness. No user effect. | Unit tests; held-out eval numbers recorded |
| 2 | **Backend, import only** | Part 9 SQL, routes, Job, worker, retention sweep, account deletion, RSS logging | Integration tests; 10-minute real job on Cloud Run; deletion verified end to end |
| 3 | **Client, import and results** | TUS, setup, processing, overview, swing list, `ResultsScreen` reuse. Allowlist only. | Upload survives airplane-mode test; old-client regression passes |
| 4 | **Video review** | Playback, timeline, chips, dead-time skip, exclude | Seek tests on Android, iOS, web |
| 5 | **Coaching summary** | `session_summary.py` | Guard pass rate ≥ 90 %; payload contract test |
| 6 | **In-app recording** | 10-minute recording with bitrate cap; merged-manifest check | 3 devices, thermal and battery noted |
| 7 | **Ball speed** | Calibration, per-swing ball stage with a session budget. Cost is 1.0-2.2 s per swing (`PIPELINE.md:1898-1901`), so 200 swings is 3.3-7.3 min; 6 s deadline worst case is 20 min, hence a 10-minute session cap. | Detection rate reported honestly |
| 8 | **Trends and limit raise** | Trends endpoint and UI. 20 minutes if real time ≤ 1.5× and failure ≤ 2 %. | Metrics hold |

Legal review, privacy policy and age step must complete before any non-allowlisted user.

### 13.2 Feature flag

Server-side, so no client release is needed to change it:
- `SESSION_MODE_ENABLED`
- `SESSION_MODE_ALLOWLIST` (user ids)
- `SESSION_MAX_SECONDS`, `SESSION_MAX_RUNNING`, and quotas

`GET /v1/sessions/capabilities` drives whether the app shows the entry point. Rollback: turn the flag off. Nothing existing depends on the new code.

### 13.3 Testing

- **Unit:** sections 4.5 and 10.4. Pure code, no network.
- **Contract:** `test_api_openapi.py` includes new routes. Pydantic-to-Dart parity for session models, like `test_dual_model_parity.py`. Detail body parses into `AnalysisResponse`.
- **Compatibility:** existing test suites pass unchanged. `test_single_swing_pipeline_outputs_unchanged` on the vendored clip. Old `GET /v1/analyses` returns no session rows.
- **Integration:** fake storage and repository, like existing tests. The state machine including claim races, retry and cancel.
- **Accuracy:** private eval harness (section 4.6), rerun on any version bump.
- **Device:** long recording and interrupted upload on real Android and iOS, web import in Chrome and Safari.
- **Schema:** apply Part 9 to staging if one exists, then the drift probes in `DATABASE_SETUP.md:1988-1994`.

### 13.4 Metrics

Logged without personal data, per session:

| Metric | Target or alert |
|---|---|
| Upload success, resume count | ≥ 90 % finish |
| Processing time ÷ video duration | Alert > 2.0 |
| Job failure rate by code, `worker_lost` | Alert > 2 % |
| Stuck in `queued` or `running` beyond expected | Zero tolerated. This is the complaint reviewers make about SwingVision. |
| Peak RSS | Alert > 75 % of limit |
| Swings per minute, rejection-reason mix, low-confidence share | Watch for drift |
| `unknown` shot share | Target ≤ 30 % |
| User exclusions ÷ swings | Proxy for false positives; alert > 15 % |
| Gemini source mix and guard rejections | Template ≤ 10 % |
| Cost per session | Compare with estimate |
| Video retention sweep failures | Zero |

### 13.5 Risk table

The riskiest assumption is in section 1 and is not repeated here.

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Processing feels too slow (1.2-2.2× real time, estimate) | High | High | Launch at 10 minutes, true progress, safe to leave, measure 4 vCPU |
| Pose per frame on Cloud Run worse than assumed | Medium | High | Measure in slice 0 |
| Supabase cost or limits differ from memory | Medium | Medium | Verify before slice 2 |
| OOM from in-memory temp file or long-lived graph | Medium | High | 4 GiB, RSS logging, size cap |
| Forehand or backhand mislabels damage trust | High | Medium | Show `unknown`, confidence, user review; measure confusion matrix |
| Shadow swings inflate stats | High | Medium | Setup copy, exclude action, report rate |
| Long-recording failure on phones (thermal, OS kill) | Medium | Medium | Keep what was recorded, device tests, 10-minute launch limit |
| Requested bitrate ignored by device | Medium | Medium | Check real size at stop, enforce cap |
| TUS on web blocked by CORS or memory | Medium | Medium | Verify early; mobile first |
| Seek inaccuracy spoils tap-to-jump | Medium | Medium | Device tests; start slightly before window |
| Numeric guard false positives remove good text | High | Low | Measure; fix substring matching separately |
| Placeholder bands mislead consistency labels | High | Medium | Provisional tag, no composite score |
| Job image out of step with API on deploy | Medium | Medium | Deploy both from one script, versioned payloads |
| Legal or store rejection over minors and bystanders | Medium | High | Section 11, legal review first |
| Quota abuse | Low | Medium | Quotas, unique active index, task timeout |
| Schema drift on migration | Medium | Medium | `DATABASE_SETUP.md:1982-1998` probes |

---

## Decisions for the owner

| # | Decision | Recommendation |
|---|---|---|
| 1 | Move Supabase to a paid plan. Session Mode cannot run on the free per-file and project storage limits. | **Yes,** before slice 2, once pricing is verified. |
| 2 | Launch duration limit. | **10 minutes,** raise to 20 only with measured processing at or below 1.5× real time. |
| 3 | Default raw-video retention. | **30 days,** with delete-video always available. |
| 4 | Age policy and legal review before public release. | **Add an age step and privacy policy, and get legal review.** Keep Session Mode allowlisted until then. Decide explicitly whether under-13s are allowed, and treat the `school` flavour separately. |
| 5 | Shadow swings. Pose cannot tell them from hits. Ball-evidence gating would make detection depend on the ball, against the one-way rule in `contact.py:5`. | **Stay pose-only in Phase 1,** say so plainly, and let users exclude. Revisit with experiment data. |
| 6 | Call the numbers "shots" or "swings"? | **"Swings".** It is what is actually detected, and avoids implying a ball was hit. |
| 7 | Show forehand and backhand filters despite known weakness? | **Show them with an `Unknown` chip,** measure, and hide or merge if accuracy is poor. |
| 8 | Single-swing API production tier (~$100/month) is triggered by any beta user (`PIPELINE.md:2347-2353`), independent of this feature. | **Do not pay yet.** Consider moving single-swing work to Jobs too, after Session Mode proves the pattern. Until then, keep any beta small and accept the documented risk knowingly, or pay. |
| 9 | CLAUDE.md scope on rally counting. | **Keep it unchanged for Phase 1.** Decide after Phase 1 data. |
| 10 | Background upload via a foreground service (new permission, store review implications). | **No for Phase 1.** Resume on return. |

---

## Appendix A: Phase 2 rally segmentation (feasibility notes only)

**Conflict:** CLAUDE.md says the app does not count rallies, and `rally` is a banned token in generated text (`gemini.py:81`). Any rally feature needs an owner decision and a CLAUDE.md change first.

- **Pose-only version:** group swings by gaps, for example under a 6-8 s gap. This produces **hitting bursts, not rallies**. It cannot see a missed ball, a let or a serve fault, and only one player's swings are visible. Calling it a rally would be inaccurate, so it would need different wording ("hitting bursts") or real ball evidence.
- **Reasonable Phase 2 step:** burst grouping for better dead-time skipping and highlight playlists, requiring no CLAUDE.md change if it never claims rally length.
- **Real rally length:** needs both players' hits, or continuous ball tracking. The existing tracker runs only 0.25 s after contact and seeds off the racket wrist (`frames.py:57,338-340`), so continuous tracking is a new system, with new compute cost at full resolution over the whole video.
- **Validation:** would need its own labelled set.

## Appendix B: Phase 3 scoring and line calls (feasibility notes only)

Honest position: a large, separate project and possibly never viable on a single phone at a useful accuracy.

- **Ball tracking:** classical CV covers a short window (`detector.py`, `frames.py`). Full-flight tracking needs a detector robust to small, blurred balls, likely a trained model, likely at least 60 fps. Current pose work is 30 fps and audio-free. Deep-learning ball tracking over a full match on CPU would cost far more than pose.
- **Court homography:** does not exist here. Two-tap calibration gives a scale along one line (`requests.py:129-161`), not a plane mapping. It would need detection of court lines or four-point user taps, plus re-checks if the camera moves.
- **Monocular limits near lines:** a ball's height is ambiguous from one camera, and bounce point must be inferred. At typical phone distance, one pixel on the far side spans several centimetres, and at 30-60 fps a fast ball moves tens of centimetres between frames. Close calls are the ones that matter, and those sit inside this error. Calling them with confidence would be misleading, and showing an uncertainty band is the honest alternative.
- **Camera placement:** high and centred behind a baseline is best for geometry but conflicts with side-on placement that pose metrics and ball speed want (`record_screen.dart:842-849`). One camera can't do both well, and that conflict is a design problem, not a tuning one.
- **Benchmark:** ITF Player Analysis Technology certification is the appropriate external bar for any claim about accuracy. An ITF approval certificate for SwingVision is published (see Sources). Tennisimo should not claim comparable accuracy without independent testing. Do not assume that certification would be achievable.
- **Real-time compute:** real-time calling implies on-device inference. The current architecture is entirely server-side (CLAUDE.md), so this would mean a different product architecture, thermal limits, and model work on each platform. A server-side post-match version is the only plausible start.
- **Scoring:** depends on all of the above, plus serve and point-state logic, voice or manual correction. Contradicts current CLAUDE.md scope boundaries and needs an owner decision.
- **Cheapest honest experiment if ever pursued:** tag bounces by hand in 3 matches, then measure a simple tracker's error against them before building anything else.

## Sources

SwingVision facts, retrieved 2026-11-02. Most are developer-written listings or third-party reviews.

- [App Store listing](https://apps.apple.com/us/app/swingvision-tennis-pickleball/id989461317)
- [tennis.com review](https://prod.tennis.com/news/articles/swingvision-delivers-pro-level-insights-for-recreational-players)
- [Pocket-lint article (Dutch)](https://www.pocket-lint.com/nl-nl/apps/nieuws/apple/161241-wat-is-de-swingvision-ai-app-hoe-werkt-het-hoe-kan-het-je-tennis-verbeteren)
- [ITF PAT approval certificate](https://www.itftennis.com/media/15293/pat-approval-certificate-swingvision.pdf)
- [Tennisnerd review](https://www.tennisnerd.net/tennis-tools/swingvision-review-and-interview/25702)
- [Scandinavian Mind article](https://scandinavianmind.com/?p=26402)
- [User review aggregator](https://justuseapp.com/en/app/989461317/swingvision-a-i-tennis-app/reviews)
