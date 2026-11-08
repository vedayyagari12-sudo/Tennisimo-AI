# Session Mode Phase 2: court engine design

Status: design, written against branch `worktree-session-mode-plan` at `c935137`, before any Phase 2 code existed. Nothing in the design sections has been measured; any measured result is recorded where the build order asks for it. Geometry and cost figures are hand arithmetic with stated assumptions, labelled **estimate**. External facts from memory are tagged **[verify]**. Clip facts come from the owner's contact-sheet review and `tennis_clips_v2/SOURCES.txt`. I opened no video.

Citations use file name and line. `PIPELINE.md` is `docs/PIPELINE.md`. `SESSION_MODE.md` is `docs/SESSION_MODE.md`.

**Scope authority.** The owner moved shot maps, depth, in/out calls, rally length and scoring into Phase 2. That answers Decision 9 and the conflict in Appendix A. Spin stays out. All other CLAUDE.md rules still bind.

---

## 1. Summary

### 1.1 What this is

A pure Python engine turns a fixed-camera video plus user court taps into:
- bounce points in court metres, each with a per-shot uncertainty ellipse
- three-valued line calls (`in`, `out`, `too_close_to_call`)
- depth and width zones, and a filterable shot map
- rally lengths with confidence ranges
- a tennis score marked as an estimate, which the user corrects and which recomputes by event replay

### 1.2 Phase 1 is not built

Confirmed by search of the worktree:
- `backend/app/analysis/` holds only `contact`, `handedness`, `metrics`, `normalize`, `phases`, `rubric`, `scoring`, `shot_type`, `smoothing`, `speed`.
- No backend file name contains `session`, `worker`, `segment` or `job_launcher`.
- No `frontend/lib` file name contains `session`, `court` or `rally`.
- `homography`, `session_swings`, `routes_sessions` and `session_worker` appear only in docs.
- `SESSION_MODE.md:3` calls Phase 1 a proposal.

So Phase 2 cannot assume sessions tables, routes, a Job, TUS upload, or `segmentation.py`.

### 1.3 What ships now and what is blocked

| Item | Status | Why |
|---|---|---|
| Pure engine modules (section 6) | **Build now** | No dependency on Phase 1 |
| Pure-CV and frame streaming under `app/ball/` | **Build now** | Works on a local file |
| Pydantic contract `app/models/court.py` | **Build now** | Frozen first, not routed |
| Synthetic truth renderer (tests only) | **Build now** | Only source of exact truth |
| Offline CLI `backend/scripts/court_eval.py` | **Build now** | Local files only; never deploys |
| Court summary payload and guard, with `FakeGeminiClient` | **Build now** | Pure; no live call |
| Flutter models and presentational widgets, fixture data | **Build now** | Not reachable from any screen |
| Routes, DB, Job stage, upload, calibration screen wiring | **Blocked on Phase 1, spec only** | Sections 7.4, 7.5 |
| Pose hits from a real session | **Blocked on Phase 1** | Needs `segmentation.py`. `contact.py` cannot run on long sequences (`SESSION_MODE.md:286-306`). The engine takes hits as optional input. |
| Claiming any real-world accuracy | **Blocked on the release gate** | Section 11.5 |

Your expectation holds, with two refinements from the code:
1. Pixel work cannot live in `analysis/`, because `cv2` is banned there (`test_analysis_purity.py:18-29`). It goes in `app/ball/`.
2. New modules must be flat files in `analysis/`. The purity test uses a non-recursive `glob("*.py")` (`test_analysis_purity.py:41`), so a sub-package would silently escape it.

No deploys and no live DB changes.

### 1.4 THE SINGLE RISKIEST ASSUMPTION

> **Classical CV, on phone-compressed video from one fixed camera, can detect and link a ball only 2-4 px wide in the far half of the court reliably enough to find its bounces.**

**Why this one.** Everything else is solvable by arithmetic or tests: homography, band logic, score replay. This one is not. The user's own shots land in the far half under the recommended placement, and that is where the ball is smallest.

**Current code cannot do it.** Estimated ball diameter at 1280 px long edge, with camera 3.5 m high and 6 m behind the baseline (section 2.2):

| Position | Slant range | Diameter, 1280 px | Area | Diameter, 1920 px | Area |
|---|---|---|---|---|---|
| Near baseline | 6.95 m | 9.7 px | 74 px² | 14.5 px | 165 px² |
| Near service line | 12.0 m | 5.6 px | 25 px² | 8.4 px | 56 px² |
| Net | 18.2 m | 3.7 px | 11 px² | 5.6 px | 24 px² |
| Far service line | 24.5 m | 2.8 px | 6 px² | 4.1 px | 13 px² |
| Far baseline | 30.0 m | 2.2 px | 4 px² | 3.4 px | 9 px² |

Diameter = f × 0.067 m ÷ range, with f from section 2.2. The existing gates need at least 12 px² (`detector.py:83,92`) and at least a 3 px minor axis for streaks (`detector.py:101`). A 3×3 open removes anything narrower (`detector.py:66,160`). **At 1280 px the existing detector loses the ball at the net.**

**My honest position.** I expect classical CV to work in the near half and be marginal in the far half, especially at the 4 Mbps Phase 1 assumes (`SESSION_MODE.md:435`). A learned detector may turn out to be needed. I cannot show that it is, so Phase 2 stays classical and this experiment decides.

**Cheap experiment: about half a day of filming and labelling, after build step 9.**
1. Owner films one 20-minute session at section 2.3 placement, adults only, on one court.
2. Record two profiles of similar play: 1080p60 at about 20 Mbps, and 720p60 at about 10 Mbps. If the phone offers no bitrate control, record what it gives and note the measured file size.
3. Add 20 surveyed tape marks on court: measure each position with a tape, and bounce a ball on each 3 times.
4. Hand-label 300 sampled in-flight far-half frames with a ball position, and at least 60 far-half bounces in the label format of section 11.4.
5. Run `court_eval.py --mode candidates` and `--mode events`. Thresholds stay frozen before scoring.

| Measure, far half, at 1080p60 | Pass | Fail |
|---|---|---|
| Per-frame candidate within 3 px of label | ≥ 0.70 | < 0.40 |
| Bounce recall, ±2 frames | ≥ 0.70 | < 0.45 |
| False bounces per minute of play | ≤ 1.0 | > 3.0 |
| Median along-court error on surveyed marks | ≤ 0.60 m | > 1.20 m |

- **All pass:** continue.
- **Between:** ship near-half calls only, and show far-half landings as "approximate" with no line calls.
- **Any fail:** stop before Flutter wiring. The owner chooses a near-half-only product or a learned detector (Decision 3).

### 1.5 End-to-end data flow

```
video file (local path now; Supabase object once Phase 1 exists)
 -> app/pose/video_io.py    probe_clip, apply_rotation, resize_to_long_edge (reused, unchanged)
 -> app/ball/flight_frames.py    IMPURE PyAV stream: native-fps BGR frames + real PTS
 -> app/ball/camera_motion.py    PURE-CV median feature flow, sampled at 2 Hz
 -> app/ball/flight_detector.py  PURE-CV per-frame candidates as numpy records
 -> app/ball/court_lines.py      PURE-CV optional line segments and tap refinement
 ================== everything below is pure numpy, in app/analysis/ ==================
 -> camera_stability.py   fixed-camera verdict: refuse, or sigma contribution
 -> court_calibration.py  taps -> homography.py + court_model.py -> verdict (court_fit.py optional)
 -> flight_track.py       candidates -> ball flights
 -> ball_events.py        flights + optional pose hits -> bounce / hit / net / unknown
 -> line_calls.py         bounce -> court metres, covariance, three-valued call
 -> placement.py          depth and width zones, shot-map aggregation
 -> rally.py              events -> rallies, length range and confidence
 -> point_inference.py    rally -> suggested point outcome, or none
 -> match_score.py        event log -> replayed score timeline
 -> court_session.py      pure orchestrator -> CourtSessionResult
 ================== pure ends ==================
 -> app/services/court_offline.py  impure glue and wire projection to app/models/court.py
 -> app/feedback/court_summary.py  numbers -> payload -> Gemini text -> guard (not called live here)
 -> backend/scripts/court_eval.py  JSON report under backend/build/
 -> Flutter fixture data -> frontend/lib/widgets/court_*.dart, rally_timeline.dart, score_panel.dart
```

**The contract comes first.** The exact JSON and models are in section 7, and build step 1 freezes them before any math.

**Files touched.** Every path, with its test, is in section 12.

**New backend**
- `app/models/court.py`
- `app/analysis/`: `court_model`, `homography`, `court_calibration`, `court_fit`, `camera_stability`, `flight_track`, `ball_events`, `line_calls`, `placement`, `rally`, `point_inference`, `match_score`, `court_session` (each `.py`)
- `app/ball/`: `flight_frames`, `flight_detector`, `camera_motion`, `court_lines` (each `.py`)
- `app/services/court_offline.py`
- `app/feedback/court_summary.py`
- `scripts/court_eval.py`
- test files
- `tests/unit/court_synth/`
- `tests/eval/real_clip_manifest.json`

**Changed backend**
- `tests/unit/analysis/test_analysis_purity.py`
- `app/feedback/gemini.py` (optional keyword argument only)
- `pyproject.toml` (one marker)

**New frontend**
- `lib/models/court.dart`
- `lib/widgets/court_map.dart`, `line_call_card.dart`, `rally_timeline.dart`, `score_panel.dart`
- `test/support/court_fixtures.dart`, `test/court_widgets_test.dart`, `test/court_models_test.dart`

**Docs**
- `CLAUDE.md`, `docs/SESSION_MODE.md`, `docs/PIPELINE.md`
- this file, `docs/SESSION_MODE_PHASE2.md`

**Untouched by rule:** `contact.py`, `speed.py`, `ball/detector.py`, `ball/track.py`, `ball/frames.py`, `ball/geometry.py`, `pipeline.py`, `app_palette.dart`.

---

## 2. Camera and capture

### 2.1 Decision: two mutually exclusive capture modes

The conflict in Appendix B is real and cannot be tuned away. Current guidance is side-on, level, 5-10 m to the side (`record_screen.dart:842-849`).

Side-on fails for court analysis on coverage alone. With a phone main lens at an assumed 65° horizontal field of view in landscape **[verify per device]**, tan(32.5°) = 0.637:
- Seeing one 11.885 m half from the side needs at least 5.94 ÷ 0.637 = 9.3 m lateral distance from the near sideline.
- Seeing the whole 23.77 m needs at least 18.7 m, normally on the next court.
- The camera's half is the only half visible, and the user's own shots land in the other one.

**Recommended Court mode placement: behind one baseline, centred, 3-4 m high, about 6 m back, landscape, main lens, fixed.**
- At 6 m back, seeing singles corners needs 4.115 ÷ 6 = 0.686, or about 69° horizontal. That is slightly more than the assumed lens, so step back to 6.5 m or accept a clipped near corner. Doubles corners need 8.6 m.
- Ultrawide is not recommended. Lens distortion breaks a single homography, and reprojection gating will usually reject it.
- Portrait is rejected for Court mode. Its short-axis field is about 39° (estimate).

| Mode | Placement | Gives | Loses |
|---|---|---|---|
| **Technique** (Phase 1) | Side-on, level | Pose metrics, ball speed | All court output |
| **Court** (Phase 2) | High, behind baseline | Bounces, calls, depth, rally, score | See below |

**What pose loses in Court mode:**
- All metrics flagged view-sensitive (`metrics.py:89-107`): shoulder turn, hip rotation, elbow angle, wrist lag, contact point forward, peak hand speed, swing plane deviation, knee flexion, weight transfer. Hide them in Court mode.
- The remaining view-stable metrics were never validated from an elevated rear view. Show them labelled "limited view" or not at all until measured.
- Shot-type inference from behind is unvalidated, on top of the existing weakness (`shot_type.py:26-29`). Expect more `unknown`.
- Ball speed: not requested. Rear views are the geometry `PIPELINE.md:1421` calls unmeasurable, and the existing gate is inert (`normalize.py:655`), so Court mode must not rely on it.
- The far player is about 60 px tall at 1280 px (1.8 m × 1005 ÷ 30 m, estimate) and about 30 px in the 640 px pose stream (`video_io.py:61`). Far-player pose is not attempted.

### 2.2 Ground-plane uncertainty per pixel

**Model (estimate).** Pinhole, f = (W ÷ 2) ÷ 0.637. That gives **f = 1005 px at 1280 wide** and **f = 1507 px at 1920 wide**. D is ground distance from the camera foot, H is camera height, R² = D² + H².
- Along the line of sight: **δ_along = R² ÷ (H f)** metres per pixel
- Across the line of sight: **δ_across = R ÷ f** metres per pixel
- Off-axis pixel-angle shrinkage (under 5 %) is ignored here. The code uses the exact Jacobian in section 3.

**Behind baseline, 6 m back, on the centre line.** D is 6.0, 11.485, 17.885, 24.285 and 29.77 m.

| Metres per pixel, along-court | Near baseline | Near service | Net | Far service | Far baseline |
|---|---|---|---|---|---|
| H = 4.0 m, 720p | 0.013 | 0.037 | 0.084 | 0.151 | 0.224 |
| H = 3.5 m, 720p | 0.014 | 0.041 | 0.094 | 0.171 | 0.255 |
| H = 3.0 m, 720p | 0.015 | 0.047 | 0.109 | 0.199 | 0.297 |
| H = 1.5 m tripod, 720p | 0.025 | 0.089 | 0.214 | 0.393 | 0.589 |
| H = 3.5 m, 1080p | 0.009 | 0.027 | 0.063 | 0.114 | 0.170 |

| Metres per pixel, across-court (H = 3-4 m) | Near baseline | Near service | Net | Far service | Far baseline |
|---|---|---|---|---|---|
| 720p | 0.007 | 0.012 | 0.018 | 0.024 | 0.030 |
| 1080p | 0.005 | 0.008 | 0.012 | 0.016 | 0.020 |

Worked example, H = 3.5 m, far baseline, 720p: R² = 886.3 + 12.25 = 898.5, and 898.5 ÷ (3.5 × 1005) = 0.255 m per pixel. Court corners are worse than centre-line values by roughly 5-15 % (estimate).

**A normal 1.5 m tripod is not acceptable.** It costs 2.3× along-court at the far baseline.

**Side fence, on the net line, 3.5 m beyond the doubles sideline (9.0 m from centre), aimed at one half.** Point on the centre line, 720p, metres per pixel. The first number is error along the court (baseline calls), the second is error across it (sideline calls).

| Height | Net | Service line | Baseline of that half | Other half |
|---|---|---|---|---|
| 1.5 m | 0.009 / 0.055 | 0.049 / 0.068 | 0.119 / 0.090 | **out of frame** |
| 3.0 m | 0.009 / 0.030 | 0.027 / 0.036 | 0.062 / 0.048 | **out of frame** |

Good pixels, wrong half, and the near sideline is also clipped. This placement stays a technique-only setup.

### 2.3 Capture profile

- **Frame rate:** 60 fps recommended. 30 fps is accepted with wider bands. Below 24 fps is refused.
- **Resolution:** 1080p recommended, 720p accepted. A new `TRACK_SPACE` means rotated native frames capped at 1920 long edge, not `CAL_SPACE` at 1280 (`detector.py:54`). The existing `CAL_SPACE` stays as is for single-swing.
- **Bitrate:** about 20 Mbps at 1080p60, which is 0.16 bits per pixel (20e6 ÷ 124.4e6). The Phase 1 target of 720p30 at 4 Mbps (`SESSION_MODE.md:435`) is 0.145 bits per pixel, but at half the temporal rate and a smaller ball. H.264 can erase a 2 px moving dot **[untested]**. The experiment measures this.
- **Size conflict with Phase 1:** 20 Mbps × 600 s ÷ 8 = 1,500 MB, over the 1 GiB cap (`SESSION_MODE.md:423`). At 1 GiB, 20 Mbps caps recording at 1,073.7 × 8 ÷ 20 = 429 s. Court mode needs its own limit or a lower profile. See Decision 2.
- **Plugin:** whether `camera` honours 60 fps and a bitrate is **[verify on device]** (`SESSION_MODE.md:441`).

### 2.4 Fixed camera is required: detect and refuse

Many corpus clips move, and one pixel of drift is 17-25 cm at the far baseline. A stability check runs before any court output.

**Measurement** (`ball/camera_motion.py`, cv2 allowed there). Grayscale at 640 px long edge, sampled at 2 Hz. `cv2.goodFeaturesToTrack` is taken once on the reference frame, followed by `cv2.calcOpticalFlowPyrLK` to each sample. The per-sample shift is the **median** feature displacement, which tolerates moving players while more than half the features are static. Values are scaled to `TRACK_SPACE`. Estimated cost is 3-5 ms per sample, or 1,200 samples × 4 ms ≈ 5 s for 10 minutes.

**Decision** (`analysis/camera_stability.py`, pure), as a fraction of the long edge so it is resolution-independent:

| Condition | Starting value | Verdict |
|---|---|---|
| P95 shift ≤ | 0.1 % (about 1.9 px at 1920) | `stable` |
| P95 shift ≤ | 0.3 % (about 5.8 px) | `shaky`: `rms_shift_px` joins the sigma, so bands grow |
| P95 shift > | 0.3 % | `moving`: **refuse**, no court output |
| Sustained jump ≥ 0.2 % lasting ≥ 1 s | | `bumped_at_s`: output stops at that time; earlier results stand |
| Tracked-feature count < 30 | | `unknown`: treated as `moving` |

These values are starting estimates. They may be adjusted once only, after measuring the 16 clips (step 18), then frozen. This check cannot judge the accuracy of a call; it only decides whether to refuse.

---

## 3. Court calibration

### 3.1 Coordinates

Origin at court centre on the ground. +x is right, seen from the near baseline looking at the far baseline. +y runs toward the far baseline. z is up. "Near" is the end nearer the camera.

### 3.2 Primary route: user taps

The client shows a frame from the video itself, not a live preview. Coordinates are normalised to the video frame. That avoids the crop and rotation trap in `geometry.py:13-19` and needs no `capture_rotation_deg`. The client must display the whole frame uncropped. The aspect check follows `frame_shape_matches` (`geometry.py:141-159`).

Each tap names a keypoint. There are 14 legal keypoints, all on the ground and on visible line intersections:
- baseline with doubles sideline (4)
- baseline with singles sideline (4)
- service line with singles sideline (4)
- service line with centre line, the "T" (2)

Net posts and the net line are excluded because they are not visible ground points.

**Recommended six taps:** four singles corners and both Ts.

| Taps | Allowed for |
|---|---|
| Fewer than 4 | Rejected |
| 4 or 5 | Shot map only, flag `unverified_calibration`. Four points fit exactly, so reprojection error is always 0 and proves nothing. |
| 6 or more | Line calls and scoring suggestions |

A mandatory confirmation step draws the fitted court back onto the frame for the user.

### 3.3 Math

**Pure numpy, normalised DLT.** `cv2.findHomography` is not allowed in `analysis/` (`test_analysis_purity.py:18-29`) and is not needed.

Algorithm (Hartley normalisation):
1. Translate each point set to zero centroid and scale to mean distance √2.
2. Build the 2N×9 matrix A.
3. Take the right singular vector for the smallest singular value from `numpy.linalg.svd`.
4. Denormalise and scale so h33 = 1.
5. Produce court-to-image `H_ci` and its inverse `H_ic`.

**Image-to-court Jacobian (analytic).** With w = h31 u + h32 v + h33, X = (h11 u + h12 v + h13) ÷ w, and Y similarly:

```
J = (1/w) * [[h11 - X h31, h12 - X h32],
             [h21 - Y h31, h22 - Y h32]]
```

**Propagation.** Σ_ground = J Σ_image Jᵀ + Σ_calibration.

**Calibration covariance.** Numeric central difference of the mapped point with respect to each tap coordinate, giving a 2×2N matrix C, then Σ_calibration = σ_tap² C Cᵀ. It is deterministic and keeps correlations. N is at most 14, so this is cheap. Cache C per grid cell if profiling demands it.

**Camera pose.** Assume square pixels and the principal point at frame centre, then H_ci ∝ K[r1 r2 t].
- Solve f² by least squares over both orthogonality and equal-norm constraints. Use both because a centred, unrolled camera makes the orthogonality constraint degenerate on its own, with a vanishing point at infinity.
- Derive camera height C_z and foot (C_x, C_y).
- Plausibility: f in 400-3000 px scaled to 1280, C_z in 0.5-15 m. Failure means `camera_pose_implausible`, and line calls are refused.

### 3.4 Gates

All thresholds are `Final` constants in `court_calibration.py`.

| Check | Rejection reason |
|---|---|
| Duplicate keypoints, or taps within 8 px | `duplicate_taps` |
| Any 3 of the 4 widest taps within 12 px of collinear | `collinear_taps` |
| Hull under 5 % of frame area | `taps_too_small` |
| Tap order contradicts court orientation (near above far, or left-right mirrored) | `taps_mirrored` |
| Condition: σ7 ÷ σ8 of normalised A under 1e-3 | `ill_conditioned` |
| N ≥ 6, reprojection RMS > 2.0 px or max > 4.0 px at 1280-equivalent | `reprojection_error_high` |
| Aspect mismatch > 2 % | `frame_shape_mismatch` |
| Implausible pose | `camera_pose_implausible` |

Default `σ_tap` is 1.5 px (estimate, with a magnifier loupe). It is 0.5 px if refinement succeeds.

### 3.5 Optional assist: line candidates

Never primary and never auto-accepted. Built last, and it can be cut without harming anything.
- `ball/court_lines.py`: white top-hat plus `cv2.HoughLinesP` at 1280, returning segments. `refine_tap_to_intersection` fits two local line edges within ±20 px of a tap.
- `analysis/court_fit.py`: pure. Group segments into two direction families, enumerate at most 225 line-pair hypotheses, and score each by model line length supported within 3 px. Accept if support ≥ 0.6, at least 6 model lines are visible, and the pose is plausible. Return at most 3 candidates for the user to confirm.

**Validation on real clips:** the padel clip and courtless clips must return zero fits. Partly-lined clips may return none, and that is acceptable. None of this tests call accuracy.

---

## 4. Ball tracking

### 4.1 Decision: do not extend the existing tracker; build beside it and reuse its pieces

| Existing behaviour | Citation | Why it blocks flight tracking |
|---|---|---|
| Seeds only in frame 0, by racket wrist | `track.py:258`, `geometry.py:295` | Rallies have no such seed |
| Terminates on a turn over 35° | `track.py:60-61,310-316` | A bounce is the event we need |
| One track; ends after 3 misses | `track.py:64-65` | Multiple balls; far-half gaps |
| Fixed background from a pre-roll | `detector.py:137-148` | Lighting drifts over minutes |
| Stacks all frames in RAM | `frames.py:188-192` | 36,000 × 6.2 MB ≈ 223 GB |
| 12 px² floor and 3×3 open | `detector.py:66,83,92` | Kills the far-half ball |
| Fixed 0.25 s window, wall-clock deadline | `frames.py:57,66` | Wrong shape of problem |

Changing these would alter ball speed, and single-swing output must stay identical.

**Reuse without modification:** `contour_features` and `colour_mask` from `detector.py`; `axis_difference_deg` and `direction_difference_deg` from `track.py`; `V_MAX_M_PER_S` from `track.py:56`; `apply_rotation` and `resize_to_long_edge` from `video_io.py`.

### 4.2 Pure split

| Layer | File | I/O |
|---|---|---|
| Decode | `app/ball/flight_frames.py` | Impure. PyAV generator `(pts_s, bgr)`. Streaming, one frame at a time. Thread type `AUTO`, as `video_io.py:746`. Real PTS, no resampling (`frames.py:25-28`). |
| Per-frame CV | `app/ball/flight_detector.py` | Pure-CV. Arrays in, numpy records out. Never opens anything. |
| Linking | `app/analysis/flight_track.py` | Pure numpy. No cv2. |

### 4.3 Per-frame candidates

**Motion: double difference.** Mask = (|I_t − I_t−1| > τ) AND (|I_t+1 − I_t| > τ). It needs no background model, tolerates lighting drift, and removes the trailing ghost. Cost: one frame of latency.

Threshold: τ = max(8, 4 × MAD of the frame difference). The existing threshold of 18 (`detector.py:62`) is too high for a 2 px ball: estimated peak contrast after pixel mixing and H.264 is only 20-40 levels (estimate, untested).

**No open step.** A 2×2 close only.

**Colour.** Reuse the HSV branches via `colour_mask`, evaluated only inside connected-component boxes, not the whole frame. Keep colour as a score, not a hard gate, below 4 px expected diameter, because at that size colour is mostly lost to chroma subsampling **[verify on real footage]**.

**Shape.** Min area 2 px². Max 1500 px² (`detector.py:93`). Above 6 px expected diameter, use the existing round/streak logic. Below it, only reject blobs far larger than expected.

**Expected size.** d_expected = f × 0.067 ÷ range at the row's ground point, from camera pose. Reject if the minor axis is greater than 2.5 × d_expected + 2 px. A ball in the air is nearer than the ground point behind it, hence the loose factor.

**ROI.** The calibrated court polygon plus 4 m run-off, extended upward in the image by the projection of a 7 m height. This removes most adjacent-court activity. It cannot remove balls from a court behind the far baseline that appear inside the ROI.

**Record dtype, 64 bytes:** `frame_index`, `time_s`, `x`, `y`, `area`, `minor`, `major`, `orientation_deg`, `colour_score`, `contrast`. **Do not retain `ContourFeatures` objects:** at 5 per frame × 36,000 frames × about 600 B (estimate) that is over 100 MB.

### 4.4 Link logic

Deterministic: every sort has explicit tie-breaks of time, then distance, then candidate index.

1. **Scale.** Air-plane px per metre at row: f ÷ range. Maximum step per frame: V_MAX × Δt × scale × 1.5, using real PTS deltas.
2. **Seed.** Any triplet across consecutive frames with both steps inside the gate and an acceleration under limit.
3. **Extend.** Predict with a quadratic through the last 5 points. Gate = max(4 px, 0.35 × max step). Nearest wins. Streak orientation must agree within 25° (`track.py:51`) when the candidate is a streak.
4. **Gaps.** Coast up to 0.1 s (6 frames at 60 fps, 3 at 30). Mark samples missing, never interpolated as detections.
5. **Reacquire.** After a kink, one wide-gate step at maximum step size. This is what lets a track survive a bounce.
6. **Close and stitch.** Join tracklets if the gap is ≤ 0.2 s and the start falls within max step × gap. Greedy, one-to-one.
7. **Reject** a flight with under 6 detections (under 4 at 30 fps), net displacement under 30 px, or a quadratic fit RMS over 3 px across any 0.25 s stretch without a kink.
8. **Multiple balls** are allowed. Flights can overlap in time.

### 4.5 Travel per frame

| Launch speed | m/s | 30 fps | 60 fps |
|---|---|---|---|
| 60 mph | 26.8 | 0.89 m | 0.45 m |
| 80 mph | 35.8 | 1.19 m | 0.60 m |
| 100 mph | 44.7 | 1.49 m | 0.75 m |
| 120 mph | 53.6 | 1.79 m | 0.89 m |

Speed at bounce is lower. With v = v0 × e^(−k s) and k = 0.0204 per metre (constants from `PIPELINE.md:1477-1481`, no spin), a 100 mph serve over 18 m lands at about 69 mph (31 m/s), and a 70 mph drive over 20 m lands at about 46 mph (21 m/s). These are estimates.

**Far-baseline image motion at 60 fps.** A ball moving away at 30 m/s travels 0.5 m per frame, or 2.9 px at 1080p and 2.0 px at 720p. Falling at 7 m/s adds 5.9 px at 1080p and 3.9 px at 720p. A 2-4 px ball moving 4-7 px per frame can pass a double difference, but only just.

**Blur.** Streak = v × shutter. At 30 m/s: 3 cm at 1/1000 s, which is under the ball's size; 25 cm at 1/120 s, which is 36 px in the near field and about 1-8 px at the far baseline depending on direction. Peak contrast falls by d ÷ (d + L). Indoors or at dusk, expect far-half loss (estimate).

### 4.6 Cost against the Phase 1 Job plan (estimates)

Per frame at 1080p on 2 vCPU:
- Decode and convert 11-22 ms. Bounds: 22 ms single-threaded, 3.4 ms threaded on 12 cores (`video_io.py:743`).
- CV 11-14 ms: `PIPELINE.md:1908` gives 5-6 ms at 720p, × 2.25.
- Linking under 0.2 ms.
- **Total 22-36 ms.**

| Session | Frames at 60 fps | Ball pass | Pose pass (`SESSION_MODE.md:503-504`) | Total | 2 vCPU, 4 GiB cost |
|---|---|---|---|---|---|
| 10 min | 36,000 | 13-22 min | 13-22 min | **26-44 min** | $0.07-0.12 |
| 20 min | 72,000 | 26-43 min | 24-42 min | **50-85 min** | $0.14-0.23 |

- Cost arithmetic for 44 min: 2,640 s × 2 vCPU × $0.000018 = $0.095, plus 2,640 s × 4 GiB × $0.000002 = $0.021, total $0.116. Rates from `PIPELINE.md:1972`, **[verify]**.
- That is 2.6-4.4× real time in Court mode, slower than Phase 1's 1.2-2.2×. Task timeout of 2 h (`SESSION_MODE.md:540`) holds for 20 minutes, with less margin at the upper bound.
- **Memory:** frame ring about 20 MB; candidates 12-46 MB (5-20 per frame × 64 B × 36,000); temp file 1,500 MB at 20 Mbps; plus 335-470 MB baseline (`PIPELINE.md:1929`) and 100 MB signals. **About 2.0-2.2 GB, which fits the 4 GiB Job.** A 20-minute 3 GB file would reach about 3.6 GB, too thin, so stay at 10 minutes or use 8 GiB.
- **Order:** run the ball pass only after pose closes, per the existing memory rule (`PIPELINE.md:1914`).

---

## 5. Bounce and hit detection

### 5.1 Height ambiguity

**A ground homography is only valid for points on the ground. Bounce points are the only ball positions that may pass through it.**

A ball at height z maps to a point past its true position by Δ = D z ÷ (H − z), along the ray from the camera. With H = 3.5 m:
- at the net with z = 1.5 m: 17.885 × 1.5 ÷ 2.0 = **13.4 m wrong**
- at the far baseline with z = 1 m: 29.77 ÷ 2.5 = **11.9 m wrong**

So:
- No in-flight "court position" exists anywhere in the output.
- No speed is derived from in-flight ground mappings.
- `line_calls.py` accepts only `BOUNCE` events.

**Even the bounce needs a correction.** The ball's centre at contact is about 3.3 cm above the ground. It projects Δ = D × 0.033 ÷ (H − 0.033) past the contact point: about 28 cm at the far baseline, 17 cm at the net, 6 cm at the near baseline.

This bias would push calls toward OUT at the far end. Correct it: X_true = C_g + (X_map − C_g) × (C_z − r) ÷ C_z, using camera pose from section 3.3.

Fitting a full 3D flight with known gravity is **not** in Phase 2. It would be fragile at far-field noise and depends on a focal estimate. It is a Phase 3 candidate.

### 5.2 Break-point fitting

For each flight, at each candidate frame:
1. Fit quadratics to u(t) and v(t) separately on up to 8 samples before and 8 after, within 0.15 s on each side, excluding coasted samples.
2. Compare a two-segment fit against a single-segment fit. Keep breaks where residual improvement exceeds a threshold.
3. Solve the break time t* where the before and after row curves intersect, within ±1 frame of the candidate.
4. Report v⁻ and v⁺ at t*, the jump J = v⁺ − v⁻, the speed ratio ρ = |v⁺| ÷ |v⁻|, image point p, `σ_t` and Σ_image.

**Time-term propagation.** Add Σ_time = v̄ v̄ᵀ σ_t², with v̄ = (v⁻ + v⁺) ÷ 2. Falling and rising row components approximately cancel, which leaves the ground-path motion. If only one side has at least 3 samples, set σ_t to frame interval ÷ √12 and flag `one_sided_bounce`.

### 5.3 Bounce time error and its position effect (estimates)

| Condition | σ_t | × v_h 21-31 m/s (1σ) | 95 % |
|---|---|---|---|
| No fit, 30 fps (uniform within a frame, Δt ÷ √12) | 9.6 ms | 20-30 cm | 40-58 cm |
| No fit, 60 fps | 4.8 ms | 10-15 cm | 20-29 cm |
| Fitted, 30 fps | about 7 ms | 15-22 cm | 29-43 cm |
| Fitted, 60 fps | about 3.5 ms | 7-11 cm | 14-21 cm |

Basis for fitted σ_t at the far baseline: a vertical velocity reversal of about 14 m/s appears as 1005 × 14 ÷ 30 ≈ 470 px/s; a fit-intersection error of 1.7 px gives 3.6 ms. Near-field streaks raise centroid noise, so the same figure is used there. Worst case with one missing side: half a frame, ±52 cm at 30 fps and 31 m/s.

Rolling shutter is a further, unmodelled term **[verify readout time per device]**. The synthetic renderer includes it as an option.

### 5.4 Classification

Rules run in order. Every threshold is a named constant. `J_min` = 2 m/s × f ÷ range, in px/s. Image rows increase downward, so an upward image impulse has J_row < 0.

| Rule | Conditions | Result |
|---|---|---|
| 1 | A pose hit within ±50 ms, and image point near the wrist when given | `HIT`, side from hitter's end |
| 2 | p inside the projected net polygon ±2 px, and (ρ < 0.5, or along-court direction reverses without an upward impulse) | `NET_CONTACT` |
| 3 | J_row < −J_min, ρ ≤ 1.05, horizontal image direction broadly continues, ground map within court + run-off | `BOUNCE` |
| 4 | ρ ≥ 1.2, or direction flips toward the other end | `HIT`, side from position |
| 5 | Otherwise | `UNKNOWN_KINK` |

**Why it mostly works:** gravity aside, a bounce is the only upward impulse without a speed gain. **Where it can fail:**
- A near-side hit without pose can look like a bounce, because it is also an upward impulse. Speed gain (ρ) is the separator.
- A far-side lob's impulse is ambiguous.
- A net cord that clears the net looks like a slight kink and cannot be told from noise.
- Ball pickups, hand bounces and rolling balls: low speed, rejected by minimum-speed and ROI rules, but unmeasured on real footage.

**Grammar pass (`resolve_event_grammar`).** Dynamic programming over candidate labels with fixed costs. It enforces plausible sequences: a hit is followed by a bounce on the other half or a volley hit; two bounces on one half end the point. It only re-labels low-margin events, and never invents events without evidence, though it may record `hits_inferred` in the rally.

**Use of pose.** Pose contact times are optional input. The dependency stays one-way (`contact.py:3-6`): the ball never revises pose contact. Pose may inform rally events, not the reverse. Only the tracked player's hits can appear, and `num_poses=1` can pick the wrong person (`SESSION_MODE.md:1273`), so pose hits are treated as strong evidence, never as proof.

---

## 6. In/out, depth, shot map, rally, score

### 6.1 Module list

All flat files in `backend/app/analysis/`, pure, frozen dataclasses, numpy and stdlib only. `scoring.py` already means rubric scoring, so tennis score is `match_score.py`.

| Module | Responsibility | Version constant |
|---|---|---|
| `court_model.py` | ITF dimensions, keypoints, lines, regions | `COURT_MODEL_VERSION = "court_itf_v1"` |
| `homography.py` | DLT, mapping, Jacobian, covariance, pose | `HOMOGRAPHY_VERSION = "homography_v1"` |
| `court_calibration.py` | Tap validation and gating | `CALIBRATION_VERSION = "court_cal_v1"` |
| `court_fit.py` | Optional line-candidate model fit | `COURT_FIT_VERSION = "court_fit_v1"` |
| `camera_stability.py` | Fixed-camera verdict | `STABILITY_VERSION = "stability_v1"` |
| `flight_track.py` | Candidates to flights | `FLIGHT_TRACK_VERSION = "flight_track_v1"` |
| `ball_events.py` | Bounce, hit, net, unknown | `BALL_EVENTS_VERSION = "ball_events_v1"` |
| `line_calls.py` | Bounce to metres, band, verdict | `LINE_CALLS_VERSION = "line_calls_v1"` |
| `placement.py` | Depth, width, shot-map aggregates | `PLACEMENT_VERSION = "placement_v1"` |
| `rally.py` | Rally segmentation and length | `RALLY_VERSION = "rally_v1"` |
| `point_inference.py` | Suggested point outcome | `POINT_INFERENCE_VERSION = "point_inference_v1"` |
| `match_score.py` | State machine and replay | `SCORE_RULES_VERSION = "score_rules_v1"` |
| `court_session.py` | Pure orchestrator | `COURT_ENGINE_VERSION = "court_engine_v1"` |

`app/ball/flight_detector.py` carries `FLIGHT_DETECTOR_VERSION = "flight_cv_v1"`. `DETECTOR_VERSION = "ball_cv_v1"` (`frames.py:68`) does not change.

### 6.2 Court dimensions and zones

ITF metres **[verify against current Rules of Tennis]**: length 23.77, singles 8.23, doubles 10.97, net to baseline 11.885, net to service line 6.40. Measurements run to the outer edges of lines **[verify]**. Centre service line width 0.05 m **[verify]**.

**Depth zones**, by distance from net in the bounce's half:

| Zone | Range (m from net) | Basis |
|---|---|---|
| `short` | 0 to 6.40 | Inside the service line |
| `mid` | 6.40 to 9.1425 | First half of the 5.485 m behind the service line |
| `deep` | 9.1425 to 11.885 | Second half |
| `beyond` | over 11.885 | Past the baseline |

**Width zones,** singles: `left`, `centre`, `right` in thirds of 8.23 m (2.743 m each). **Serve zones:** `wide`, `body`, `t` in thirds of the 4.115 m box (1.372 m each).

**Zone assignment is honest.** Each bounce gets probabilities from a normal CDF (`math.erf`) on its along-court sigma. `depth_zone` is the point-estimate zone, and `depth_zone_confident` is false if the 95 % range crosses a boundary. Aggregates sum probabilities, so a blurry far-baseline ball does not count as a whole "deep". With zones 2.74 m wide and far-baseline bands of ±0.6-0.9 m, many far-half balls will be not confident (estimate).

### 6.3 Three-valued line calls

For each edge of the target region, with outward normal n:
- `margin = signed distance inside the edge + BALL_CONTACT_PATCH_RADIUS_M` (0.02 m **[estimate, verify]**). A ball touching a line is good, so the patch counts toward IN.
- `σ_n = sqrt(nᵀ Σ_ground n)`, with `r = LINE_CALL_Z × σ_n` and `LINE_CALL_Z = 1.96`.

```
OUT  if any edge has margin <= -r
IN   if every edge has margin >= +r
TOO_CLOSE_TO_CALL otherwise
```

The deciding line is the edge with the smallest |margin| ÷ r.

**Also TOO_CLOSE_TO_CALL regardless of margin, with a flag:** bounce confidence under 0.5 (`weak_bounce`), `one_sided_bounce` on the deciding direction, or `unverified_calibration` is impossible here because calls need N ≥ 6.

**Target region:**
- Rally ball: full singles or doubles half opposite the hitter.
- Serve: diagonal service box from score state. The box includes the centre line, so its inner edge sits at the far edge of the line (x = ∓0.025 m).
- Target unknown (no serve context, hitter unknown): the half that the ball landed in, with `target = half_only`.

No bounce means no call. A missed bounce is reported as an unobserved rally end, never a call.

**Stored for every call:** `verdict`, `deciding_line`, `signed_margin_m`, `normal_sigma_m`, `uncertainty_radius_m` (r), ellipse (major, minor, angle), `flags`, `LINE_CALLS_VERSION`.

**Sigma inflation.** `SIGMA_INFLATION` starts at 1.0 and is set only from coverage measured in section 11.

### 6.4 Expected band size (estimates)

Placement: H = 3.5 m, 6 m back, 60 fps unless stated. Image budget: fit σ 1.2 px, calibration σ 1.2 px (1.5 px × √(8÷12) for six taps), shake σ 0.5 px. Combined √(1.44 + 1.44 + 0.25) = 1.77 px, × 1.96 = **3.5 px**. The time term from section 5.3 is added in quadrature at v_h = 25 m/s along the path (17 cm at 60 fps, 34 cm at 30 fps).

**Band half-width on baselines and service lines (± cm, 95 %):**

| Setup | Near baseline | Near service | Net area | Far service | Far baseline |
|---|---|---|---|---|---|
| 1080p60 | 18 | 20 | 28 | **44** | **62** |
| 720p60 | 18 | 22 | 37 | **62** | **91** |
| 1080p30 | 34 | 36 | 41 | **53** | **69** |
| 720p30 | 35 | 37 | 48 | **69** | **96** |

**Band half-width on sidelines, ball travelling mostly along the court (± cm, 95 %). Time term × 0.3:**

| Setup | Near baseline | Near service | Net area | Far service | Far baseline |
|---|---|---|---|---|---|
| 1080p60 | 5 | 6 | 7 | 8 | 9 |
| 720p60 | 6 | 7 | 8 | 10 | 12 |

Example, 1080p60 far baseline: 0.170 m × 3.5 = 59.5 cm; √(59.5² + 17.2²) = 61.9 cm. Tap refinement at σ_tap 0.5 px reduces the budget to about 2.8 px, so 1080p60 far baseline falls to about 50 cm.

**Where calls are useless.**
- **Far baseline and far service line:** ±44-96 cm. Any ball within about half a metre to a metre of these lines is `too_close_to_call`. That includes many deep drives and many serves near the service line. This is the half the user's own shots land in.
- **Close calls anywhere:** a ball within 5-10 cm of a baseline or service line is too close at any setup. **This system does not settle close-line disputes.** It calls clear balls and says when it can't.
- **Sidelines are the strong case:** ±5-12 cm.
- **A 1.5 m tripod:** about 2.3× worse along-court. Far baseline over ±1.3 m.
- **30 fps:** the time term dominates even in the near field.

### 6.5 Signatures

```python
# court_model.py
class CourtEnd(StrEnum): NEAR = "near"; FAR = "far"
class CourtFormat(StrEnum): SINGLES = "singles"; DOUBLES = "doubles"
class CourtKeypoint(StrEnum): ...  # the 14 keypoints
class CourtLine(StrEnum): ...      # baselines, service lines, sidelines, centre line
@dataclass(frozen=True)
class CourtRegion:
    name: str; x_min: float; x_max: float; y_min: float; y_max: float
    edge_lines: Mapping[str, CourtLine]
def keypoint_xy(keypoint: CourtKeypoint) -> tuple[float, float]: ...
def half_at(y_m: float) -> CourtEnd: ...
def half_region(end: CourtEnd, fmt: CourtFormat) -> CourtRegion: ...
def service_box_region(end: CourtEnd, side: Literal["deuce", "ad"]) -> CourtRegion: ...

# homography.py
Float64Array = npt.NDArray[np.float64]
def normalization_transform(points: Float64Array) -> Float64Array: ...
def fit_homography_dlt(src: Float64Array, dst: Float64Array) -> tuple[Float64Array, float]: ...  # H, condition
def apply_homography(h: Float64Array, points: Float64Array) -> Float64Array: ...
def invert_homography(h: Float64Array) -> Float64Array: ...
def image_to_court_jacobian(h_ic: Float64Array, uv: tuple[float, float]) -> Float64Array: ...
def reprojection_errors_px(h_ci: Float64Array, court_xy: Float64Array, image_uv: Float64Array) -> Float64Array: ...
def calibration_covariance(court_xy: Float64Array, image_uv: Float64Array, uv: tuple[float, float],
                           sigma_tap_px: float) -> Float64Array: ...
@dataclass(frozen=True)
class CameraPose:
    focal_px: float; height_m: float; foot_xy_m: tuple[float, float]; position_m: tuple[float, float, float]
def decompose_camera_pose(h_ci: Float64Array, frame_size: tuple[int, int]) -> CameraPose | None: ...
def correct_ball_centre_parallax(court_xy: tuple[float, float], pose: CameraPose,
                                 ball_radius_m: float = 0.0335) -> tuple[float, float]: ...
def image_scale_px_per_m(pose: CameraPose, h_ic: Float64Array, uv: tuple[float, float]) -> float: ...

# court_calibration.py
@dataclass(frozen=True)
class CourtTap: keypoint: CourtKeypoint; u_px: float; v_px: float
@dataclass(frozen=True)
class CourtCalibration:
    h_court_to_image: Float64Array; h_image_to_court: Float64Array; pose: CameraPose
    frame_size: tuple[int, int]; taps: tuple[CourtTap, ...]
    reprojection_rms_px: float; reprojection_max_px: float
    sigma_tap_px: float; calls_allowed: bool; flags: tuple[str, ...]; version: str
@dataclass(frozen=True)
class CalibrationVerdict: calibration: CourtCalibration | None; rejection: str | None
def validate_taps(taps: Sequence[CourtTap], frame_size: tuple[int, int]) -> str | None: ...
def calibrate_court(taps: Sequence[CourtTap], frame_size: tuple[int, int],
                    *, sigma_tap_px: float = 1.5) -> CalibrationVerdict: ...

# court_fit.py
@dataclass(frozen=True)
class CourtFitCandidate: taps: tuple[CourtTap, ...]; support: float; lines_visible: int
def fit_court_candidates(segments: Float64Array, frame_size: tuple[int, int],
                         *, max_candidates: int = 3) -> tuple[CourtFitCandidate, ...]: ...

# camera_stability.py
class StabilityVerdict(StrEnum): STABLE="stable"; SHAKY="shaky"; MOVING="moving"; UNKNOWN="unknown"
@dataclass(frozen=True)
class CameraStability:
    verdict: StabilityVerdict; p95_shift_fraction: float; rms_shift_px: float
    bumped_at_s: float | None; samples: int
def assess_camera_stability(times_s: Float64Array, shifts_px: Float64Array, feature_counts: npt.NDArray[np.int32],
                            long_edge_px: int) -> CameraStability: ...

# flight_track.py
@dataclass(frozen=True)
class BallFlight:
    flight_id: int; times_s: Float64Array; xy_px: Float64Array
    detected: npt.NDArray[np.bool_]; minor_axis_px: Float64Array; confidence: float
def link_flights(candidates: npt.NDArray[np.void], frame_times_s: Float64Array,
                 scale_px_per_m: Callable[[float, float], float],
                 *, max_gap_s: float = 0.1) -> tuple[BallFlight, ...]: ...
def stitch_tracklets(tracklets: Sequence[BallFlight], *, max_gap_s: float = 0.2) -> tuple[BallFlight, ...]: ...

# ball_events.py
class BallEventKind(StrEnum): BOUNCE="bounce"; HIT="hit"; NET_CONTACT="net_contact"; UNKNOWN_KINK="unknown_kink"
@dataclass(frozen=True)
class PoseHit: time_s: float; wrist_xy_px: tuple[float, float] | None; end: CourtEnd | None; confidence: float
@dataclass(frozen=True)
class BallEvent:
    event_id: str; flight_id: int; kind: BallEventKind; time_s: float; time_sigma_s: float
    image_xy_px: tuple[float, float]; image_cov_px2: Float64Array
    velocity_before_px_s: tuple[float, float]; velocity_after_px_s: tuple[float, float]
    confidence: float; flags: tuple[str, ...]
def find_break_points(flight: BallFlight) -> tuple[BallEvent, ...]: ...
def classify_event(event: BallEvent, calibration: CourtCalibration, pose_hits: Sequence[PoseHit]) -> BallEvent: ...
def resolve_event_grammar(events: Sequence[BallEvent], calibration: CourtCalibration) -> tuple[BallEvent, ...]: ...
def detect_ball_events(flights: Sequence[BallFlight], calibration: CourtCalibration,
                       pose_hits: Sequence[PoseHit]) -> tuple[BallEvent, ...]: ...

# line_calls.py
class LineVerdict(StrEnum): IN="in"; OUT="out"; TOO_CLOSE_TO_CALL="too_close_to_call"
@dataclass(frozen=True)
class BouncePoint:
    event_id: str; time_s: float; court_xy_m: tuple[float, float]; cov_m2: Float64Array
    half: CourtEnd; confidence: float; flags: tuple[str, ...]
@dataclass(frozen=True)
class LineCall:
    verdict: LineVerdict; deciding_line: CourtLine; signed_margin_m: float
    normal_sigma_m: float; uncertainty_radius_m: float; flags: tuple[str, ...]; version: str
def bounce_to_court(event: BallEvent, calibration: CourtCalibration, stability: CameraStability) -> BouncePoint: ...
def uncertainty_ellipse(cov_m2: Float64Array, z: float = 1.96) -> tuple[float, float, float]: ...
def call_region(bounce: BouncePoint, region: CourtRegion, *, z: float = 1.96) -> LineCall: ...

# placement.py
class DepthZone(StrEnum): SHORT="short"; MID="mid"; DEEP="deep"; BEYOND="beyond"
class WidthZone(StrEnum): LEFT="left"; CENTRE="centre"; RIGHT="right"
class ServeZone(StrEnum): WIDE="wide"; BODY="body"; T="t"
@dataclass(frozen=True)
class ShotLanding:
    bounce: BouncePoint; call: LineCall; hitter_end: CourtEnd | None
    shot_type: str | None; is_serve: bool; depth_zone: DepthZone; depth_confident: bool
    depth_probabilities: Mapping[DepthZone, float]; width_zone: WidthZone
@dataclass(frozen=True)
class ShotMapFilter:
    hitter_end: CourtEnd | None = None; shot_type: str | None = None
    verdict: LineVerdict | None = None; serves_only: bool | None = None
@dataclass(frozen=True)
class ShotMapSummary:
    count: int; verdict_counts: Mapping[LineVerdict, int]
    depth_expected_counts: Mapping[DepthZone, float]; width_counts: Mapping[WidthZone, int]
    median_sigma_along_m: float | None; landings: tuple[ShotLanding, ...]
def depth_zone_probabilities(y_from_net_m: float, sigma_along_m: float) -> Mapping[DepthZone, float]: ...
def classify_landing(bounce: BouncePoint, call: LineCall, hitter_end: CourtEnd | None,
                     shot_type: str | None, is_serve: bool) -> ShotLanding: ...
def filter_landings(landings: Sequence[ShotLanding], flt: ShotMapFilter) -> tuple[ShotLanding, ...]: ...
def summarize_shot_map(landings: Sequence[ShotLanding]) -> ShotMapSummary: ...

# rally.py
class RallyEndReason(StrEnum): BALL_OUT="ball_out"; DOUBLE_BOUNCE="double_bounce"; NET="net"
    # plus LOST_TRACK="lost_track", TIMEOUT="timeout"
class LengthConfidence(StrEnum): HIGH="high"; MEDIUM="medium"; LOW="low"
@dataclass(frozen=True)
class Rally:
    rally_id: str; start_s: float; end_s: float; length_hits: int
    length_min: int; length_max: int; confidence: LengthConfidence
    hits_observed: int; hits_inferred: int; end_reason: RallyEndReason
    event_ids: tuple[str, ...]; landings: tuple[ShotLanding, ...]; flags: tuple[str, ...]
def segment_rallies(events: Sequence[BallEvent], landings: Sequence[ShotLanding],
                    *, max_quiet_s: float = 2.5) -> tuple[Rally, ...]: ...
def rally_length(events: Sequence[BallEvent]) -> tuple[int, int, int, LengthConfidence]: ...

# match_score.py
class PlayerId(StrEnum): A="player_a"; B="player_b"
class FinalSetRule(StrEnum): TIEBREAK="tiebreak"; MATCH_TIEBREAK_10="match_tiebreak_10"; ADVANTAGE="advantage"
class ScoreEventKind(StrEnum):
    POINT_WON="point_won"; POINT_VOID="point_void"; POINT_UNRESOLVED="point_unresolved"
class ScoreSource(StrEnum): INFERRED="inferred"; USER="user"
@dataclass(frozen=True)
class ScoreConfig:
    best_of_sets: Literal[1, 3, 5]; ad_scoring: bool; games_per_set: int = 6; tiebreak_at_games: int = 6
    final_set: FinalSetRule = FinalSetRule.TIEBREAK; first_server: PlayerId = PlayerId.A
    player_a_end_at_start: CourtEnd = CourtEnd.NEAR
@dataclass(frozen=True)
class ScoreEvent:
    event_id: str; sequence: int; kind: ScoreEventKind; winner: PlayerId | None
    source: ScoreSource; rally_id: str | None; supersedes: str | None
@dataclass(frozen=True)
class ScoreState:
    sets: tuple[tuple[int, int], ...]; games: tuple[int, int]; points: tuple[int, int]
    in_tiebreak: bool; server: PlayerId; serve_side: Literal["deuce", "ad"]
    player_a_end: CourtEnd; match_winner: PlayerId | None; unresolved_before: int
@dataclass(frozen=True)
class ScoreTimelineEntry: event_id: str; state: ScoreState; points_display: str
def initial_state(config: ScoreConfig) -> ScoreState: ...
def apply_point(state: ScoreState, winner: PlayerId, config: ScoreConfig) -> ScoreState: ...
def points_display(state: ScoreState, config: ScoreConfig) -> str: ...
def effective_events(events: Sequence[ScoreEvent]) -> tuple[ScoreEvent, ...]: ...
def replay_score(config: ScoreConfig, events: Sequence[ScoreEvent]) -> tuple[ScoreTimelineEntry, ...]: ...

# point_inference.py
class InferenceBasis(StrEnum): ...  # section 6.8
@dataclass(frozen=True)
class PointInference: winner: PlayerId | None; basis: InferenceBasis; needs_review: bool
def infer_point_outcome(rally: Rally, state: ScoreState) -> PointInference: ...
def events_from_rallies(rallies: Sequence[Rally], config: ScoreConfig) -> tuple[ScoreEvent, ...]: ...

# court_session.py
@dataclass(frozen=True)
class CourtSessionResult:
    status: Literal["complete", "partial", "refused"]; refusal_reason: str | None
    calibration: CourtCalibration | None; stability: CameraStability
    landings: tuple[ShotLanding, ...]; rallies: tuple[Rally, ...]
    summary: ShotMapSummary; events: tuple[BallEvent, ...]; versions: Mapping[str, str]
def analyze_court_session(candidates: npt.NDArray[np.void], frame_times_s: Float64Array,
                          calibration: CourtCalibration, stability: CameraStability,
                          pose_hits: Sequence[PoseHit] = ()) -> CourtSessionResult: ...
```

**Pixel-layer signatures, not in `analysis/`:**

```python
# app/ball/flight_detector.py (pure-CV)
FLIGHT_CANDIDATE_DTYPE: Final[np.dtype]
def double_difference_mask(prev_gray: U8, cur_gray: U8, next_gray: U8) -> U8: ...
def find_flight_candidates(prev_gray: U8, cur_gray: U8, next_gray: U8, cur_bgr: U8, frame_index: int,
                           time_s: float, roi_mask: U8 | None,
                           expected_diameter_px: Callable[[float, float], float] | None) -> npt.NDArray[np.void]: ...
def court_roi_mask(calibration: CourtCalibration, frame_size: tuple[int, int]) -> U8: ...
# app/ball/flight_frames.py (impure)
def iter_track_frames(path: Path, probe: ClipProbe, *, long_edge_px: int = 1920) -> Iterator[tuple[float, U8]]: ...
# app/ball/camera_motion.py (pure-CV)
def median_feature_shift(reference_gray: U8, frame_gray: U8, points: F32) -> tuple[float, float, int]: ...
# app/ball/court_lines.py (pure-CV)
def detect_line_segments(frame_bgr: U8) -> npt.NDArray[np.float64]: ...
def refine_tap_to_intersection(frame_gray: U8, u_px: float, v_px: float, *, window_px: int = 20) -> tuple[float, float] | None: ...
```

### 6.6 Rally definition

A **rally** is a maximal time-ordered run of ball events, starting at the first `HIT` (serve or feed) after quiet, and ending at a terminal outcome or at `max_quiet_s = 2.5` s with no flight (estimate).

**Rally length** is the number of racket hits in the rally, counting the serve or feed as hit 1. A serve fault has length 1, and a let is length 1 (and void for scoring). Bounces do not count.

**Count method.** `length_hits = 1 + direction reversals`. A reversal is a `HIT` event, or a change of travel direction between consecutive segments. Direction comes from **bounce endpoints mapped to the ground, which are valid**, and from the row trend of a whole segment where no bounce exists.

**Range.** `length_min` counts only observed hits. `length_max` adds one possible unseen hit per lost-track gap. `length_hits` is the most likely value given the grammar.

| Confidence | Rule |
|---|---|
| `high` | No gaps, no inferred hit, `length_min == length_max` |
| `medium` | Range width 1 |
| `low` | Range width 2 or more, or any stitch over 0.2 s |

**Failure modes:**
- Far-half loss hides far-side hits, so length reads short or a rally splits in two.
- Two balls in play (feeding, adjacent court) merge or split rallies.
- Serve toss, hand bounces, and ball pickups create phantom starts.
- A slow rally with long lob hangs may exceed the quiet limit.
- Players' bodies occlude near-side contact.
- A net cord or frame shot adds a kink that is miscounted.
- `lost_track` rallies report a range and never claim a winner.

### 6.7 Score state machine

Deterministic and independent of ball math. Covers:
- **Points:** 0, 15, 30, 40, deuce, advantage. No-ad: at 40-40 the next point wins the game. **Display only:** the receiver's side choice on a no-ad point is not tracked.
- **Games and sets:** first to `games_per_set`, win by 2, tiebreak at `tiebreak_at_games` all.
- **Tiebreak:** first to 7, win by 2. First server serves 1 point, then each serves 2.
- **Final set:** `tiebreak`, `match_tiebreak_10` (first to 10, win by 2, played in place of the set) or `advantage` (no tiebreak).
- **Server rotation:** alternates each game. After a tiebreak the player who received first in it serves first next set. Serve side is deuce on an even point count in the game or tiebreak.
- **Ends:** change after odd-numbered games and at set end when the games total is odd; every 6 points in a tiebreak. Tracked because the camera's near and far ends map to players through this.
- **Doubles:** team-level scoring only. Which partner serves is not tracked.

**Event replay.** The log is append-only, never edited. A correction is a new event with `supersedes = <event_id>`. `effective_events` keeps the latest non-superseded version in sequence order, and `replay_score` rebuilds every state from the start. Replay is the only way to compute a score. Unit tests assert that any correction anywhere recomputes the same as a clean log.

**Unresolved points.** `POINT_UNRESOLVED` does not change the score. Later states carry `unresolved_before > 0`, and the score shows **needs review**. Server and side beyond that point are provisional, because an unresolved point can change game state.

**Second serves.** A first-serve fault is not a point; only a double fault is. The serve fault count is held in `point_inference.py`, which emits a `POINT_WON` for the receiver only after two faults. Serve-state inference requires both faults to be seen.

### 6.8 Point inference: an estimate with clear limits

**Scoring is always shown as an estimate.** Automatic inference is a suggestion the user corrects. Match mode requires a user declaration of format, first server and starting ends. With no declaration there is no score.

| Observed | Suggestion | Basis |
|---|---|---|
| Final bounce confidently `out` | Point to the non-hitter | `ball_out` |
| Second bounce confidently on one half | Point to the last hitter | `double_bounce` |
| Serve bounce confidently out of box, first serve | No point; first-fault state | `serve_fault` |
| Second consecutive serve fault | Point to the receiver | `double_fault` |
| Net contact, no later bounce on the far side | Point to the non-hitter | `net` |
| Final bounce `too_close_to_call` | **None: needs review** | `too_close` |
| Track lost before an outcome | **None: needs review** | `lost_track` |
| Far-side bounce unseen | **None: needs review** | `unseen_bounce` |

**Unobservable, so inference cannot know:**
- Lets (a net cord on serve that lands in looks like a good serve or a kink).
- Net cords that clear.
- Player declarations, overrules, concessions, hindrance, foot faults, touch violations, a ball hitting a player.
- Which serve a given serve was, if a fault was unseen.
- Whether a shot was a practice ball.
- Wrong side-of-court starts, or a game the players misscored.
- Doubles partner interference.

**Serve context errors cascade.** A wrong server produces a wrong service box and therefore a wrong call. That is why replay recalculates serve-box calls after every correction: `events_from_rallies` depends on score state, so this replay is a pure loop over rallies. Corrections to user-set outcomes win over inference.

---

## 7. Contracts

**All enum values are lower-case wire strings. Unknown values map to `unrecognized` in Dart (`enums.dart:70-87`).** Court enums live in `app/models/court.py`, not `enums.py`, so the persisted-values rule (`enums.py:95-97`) and existing enum tests are not disturbed. All bodies use `extra="forbid"`. Null means not measured, never 0.

### 7.1 Pydantic models, built now

`app/models/court.py`:

`CourtTapRequest`, `CourtCalibrationRequest`, `CourtCalibrationResponse`, `CameraStabilityReport`, `BounceSigma`, `LineCallResponse`, `ShotLandingResponse`, `ShotMapSummaryResponse`, `RallyResponse`, `CourtAnalysisResponse`, `ScoreConfigRequest`, `ScoreStateResponse`, `ScoreTimelineEntryResponse`, `ScoreResponse`, `ScoreEventRequest`, `CourtFeedbackInput`.

### 7.2 Calibration shapes

**`CourtCalibrationRequest`**

```json
{
  "frame_time_s": 2.0,
  "frame_width_px": 1920,
  "frame_height_px": 1080,
  "court_format": "singles",
  "taps": [
    {"keypoint": "near_left_singles_corner", "x": 0.12, "y": 0.91},
    {"keypoint": "near_right_singles_corner", "x": 0.88, "y": 0.91},
    {"keypoint": "far_left_singles_corner", "x": 0.41, "y": 0.28},
    {"keypoint": "far_right_singles_corner", "x": 0.59, "y": 0.28},
    {"keypoint": "near_service_t", "x": 0.50, "y": 0.58},
    {"keypoint": "far_service_t", "x": 0.50, "y": 0.34}
  ]
}
```

`x` and `y` are fractions of the **video frame**, y down, 0 to 1.

**`CourtCalibrationResponse`**

```json
{
  "accepted": true,
  "rejection_reason": null,
  "calls_allowed": true,
  "reprojection_rms_px": 1.1,
  "reprojection_max_px": 2.3,
  "camera_height_m": 3.4,
  "overlay": {
    "lines": [{"line": "far_baseline", "a": [0.38, 0.27], "b": [0.62, 0.27]}]
  },
  "flags": [],
  "calibration_version": "court_cal_v1"
}
```

### 7.3 Results shapes

**`CourtAnalysisResponse`**

```json
{
  "status": "partial",
  "refusal_reason": null,
  "court_engine_version": "court_engine_v1",
  "is_estimate": true,
  "camera": {
    "verdict": "shaky",
    "p95_shift_fraction": 0.0014,
    "rms_shift_px": 1.2,
    "bumped_at_s": null
  },
  "capture": {"fps": 59.9, "frame_width_px": 1920, "frame_height_px": 1080},
  "summary": {
    "count": 212,
    "verdict_counts": {"in": 151, "out": 18, "too_close_to_call": 43},
    "depth_expected_counts": {"short": 40.2, "mid": 88.1, "deep": 71.4, "beyond": 12.3},
    "width_counts": {"left": 61, "centre": 90, "right": 61},
    "median_sigma_along_m": 0.21
  },
  "landings": [
    {
      "bounce_id": "b_000123",
      "time_s": 212.437,
      "time_sigma_s": 0.004,
      "court_xy_m": [1.82, 10.44],
      "sigma_m": {"major": 0.31, "minor": 0.05, "angle_deg": 88.0},
      "half": "far",
      "hitter_end": "near",
      "shot_type": null,
      "is_serve": false,
      "target": "singles_half",
      "line_call": {
        "verdict": "too_close_to_call",
        "deciding_line": "far_baseline",
        "signed_margin_m": 0.21,
        "normal_sigma_m": 0.30,
        "uncertainty_radius_m": 0.59,
        "flags": []
      },
      "depth_zone": "deep",
      "depth_zone_confident": false,
      "depth_probabilities": {"short": 0.0, "mid": 0.14, "deep": 0.62, "beyond": 0.24},
      "width_zone": "right",
      "confidence": 0.72,
      "rally_id": "r_0017",
      "shot_index_in_rally": 3,
      "flags": []
    }
  ],
  "rallies": [
    {
      "rally_id": "r_0017",
      "start_s": 208.1,
      "end_s": 216.9,
      "length_hits": 5,
      "length_min": 5,
      "length_max": 6,
      "length_confidence": "medium",
      "hits_observed": 4,
      "hits_inferred": 1,
      "end_reason": "lost_track",
      "bounce_ids": ["b_000120", "b_000123"],
      "flags": []
    }
  ]
}
```

`status`: `complete`; `partial` (for example, output stopped at `bumped_at_s`); or `refused`, with `refusal_reason` in `camera_moving`, `camera_unknown`, `not_calibrated`, `frame_rate_too_low`.

**`ScoreConfigRequest`**

```json
{"court_format": "singles", "best_of_sets": 3, "ad_scoring": true, "games_per_set": 6,
 "tiebreak_at_games": 6, "final_set": "tiebreak", "first_server": "player_a",
 "player_a_end_at_start": "near", "opponent_acknowledged": true}
```

**`ScoreResponse`**

```json
{
  "status": "estimate",
  "needs_review_count": 3,
  "score_rules_version": "score_rules_v1",
  "current": {
    "sets": [[6, 4]], "games": [2, 1], "points_display": "30-15",
    "in_tiebreak": false, "server": "player_b", "serve_side": "ad",
    "player_a_end": "far", "match_winner": null, "unresolved_before": 2
  },
  "timeline": [
    {"event_id": "se_0091", "rally_id": "r_0017", "kind": "point_unresolved",
     "winner": null, "source": "inferred", "basis": "lost_track",
     "needs_review": true, "points_display": "30-15"}
  ]
}
```

**`ScoreEventRequest`**

```json
{"client_event_id": "2d2a...", "kind": "point_won", "winner": "player_a",
 "rally_id": "r_0017", "supersedes": "se_0091"}
```

`kind` is `point_won` or `point_void`. Response: updated `ScoreResponse`. Repeating a `client_event_id` returns the existing state.

### 7.4 Planned endpoints (spec only, blocked on Phase 1)

All additive, under `/v1/sessions/{id}`. Same auth and ownership rule as `SESSION_MODE.md:790`. Non-owned returns `404`.

| Method and path | Request | Response | Errors |
|---|---|---|---|
| `POST .../court/calibration` | `CourtCalibrationRequest` | 200 `CourtCalibrationResponse` | 400, 401, 404, 409 |
| `GET .../court/calibration/suggestions` | none | `{"candidates":[...]}` | 401, 404, 409 |
| `GET .../court` | none | 200 `CourtAnalysisResponse` | 401, 404, 409 |
| `GET .../court/shot-map?hitter_end&shot_type&verdict&serves_only` | filters | 200 `ShotMapSummaryResponse` | 400, 401, 404 |
| `GET .../rallies?limit&cursor` | keyset | 200 `{"rallies":[...],"next_cursor":null}` | 400, 401, 404 |
| `POST .../score/config` | `ScoreConfigRequest` | 200 `ScoreResponse` | 400, 401, 404, 409 |
| `GET .../score` | none | 200 `ScoreResponse` | 401, 404, 409 |
| `POST .../score/events` | `ScoreEventRequest` | 200 `ScoreResponse` | 400, 401, 404, 409 |

**Changes to Phase 1 shapes (spec):**
- `CreateSessionRequest` gains `"capture_mode": "technique" | "court"`, default `technique`.
- `SessionCapabilities` gains `court_mode_enabled`, `court_max_size_bytes`, `court_max_duration_s`.
- `stage` gains `ball_tracking`, `court_events`.
- New error codes appended: `court_calibration_invalid` (400), `court_not_calibrated` (409), `score_not_configured` (409), `score_event_conflict` (409).
- Moving camera is not an error code. It is `CourtAnalysisResponse.status = "refused"`, and the session itself can still be `partial`.

**Job (spec):** after pose closes, run the ball pass. Heartbeat and cancel per `SESSION_MODE.md:573-574`. Result persisted in one write. Score replay runs in the API, since it is pure and cheap, and does not need a Job.

### 7.5 Schema additions (spec only, on top of the Phase 1 sketch)

Next Part of `docs/DATABASE_SETUP.md`, run by the owner, after Phase 1's Part 9 exists. Same conventions as `SESSION_MODE.md:604`.

```sql
-- SKETCH. Requires Part 9. Not run in Phase 2.
alter table public.sessions
  add column capture_mode text not null default 'technique',
  add column court_calibration jsonb,
  add column court_engine_version text,
  add column opponent_acknowledged_at timestamptz,
  add constraint sessions_capture_mode_chk check (capture_mode in ('technique','court'));

create table public.session_court_results (
  session_id uuid primary key references public.sessions(id) on delete cascade,
  user_id    uuid not null references auth.users(id) on delete cascade,
  payload    jsonb not null,     -- CourtAnalysisResponse
  court_engine_version text not null,
  created_at timestamptz not null default now(),
  constraint session_court_results_payload_chk check (jsonb_typeof(payload) = 'object')
);

create table public.session_score_config (
  session_id uuid primary key references public.sessions(id) on delete cascade,
  user_id    uuid not null references auth.users(id) on delete cascade,
  config     jsonb not null,
  created_at timestamptz not null default now()
);

create table public.session_score_events (   -- APPEND-ONLY
  id           uuid primary key default gen_random_uuid(),
  session_id   uuid not null references public.sessions(id) on delete cascade,
  user_id      uuid not null references auth.users(id) on delete cascade,
  sequence     integer not null,
  client_event_id uuid,
  kind         text not null,
  winner       text,
  source       text not null,
  rally_id     text,
  supersedes   uuid references public.session_score_events(id),
  created_at   timestamptz not null default now(),
  unique (session_id, sequence),
  unique (session_id, client_event_id),
  constraint session_score_events_kind_chk check (kind in ('point_won','point_void','point_unresolved')),
  constraint session_score_events_source_chk check (source in ('inferred','user')),
  constraint session_score_events_winner_chk check (winner is null or winner in ('player_a','player_b'))
);
-- RLS: enable + force on all three; select-own for authenticated; revoke anon. Service role writes.
```

Size estimate: 500 landings × about 500 B = 250 KB per 10-minute session. Measure. Account deletion must remove these before `sessions` (`SESSION_MODE.md:749`).

---

## 8. Flutter

**Built now:** models, four widgets, fixtures, tests. **Not built:** any screen, navigation entry, route call, or calibration screen. `lib/screens/` does not change.

### 8.1 Wireframes

**Court map, `lib/widgets/court_map.dart`**

```
+----------------------------------+
| Where your shots landed          |
| (All)(Your shots)(Opponent)      |
| (In)(Out)(Too close)(Serves)     |
|  +----------------------------+  |
|  |      far baseline          |  |
|  |   x          o   (o)  .    |  |   . = in, filled dot
|  |--------- service ----------|  |   x = out, cross
|  |     .    .  |   .  .       |  |   (o) = too close, hollow ring
|  |             |              |  |   soft ellipse = uncertainty
|  |=========== net ============|  |
|  |             |              |  |
|  |--------- service ----------|  |
|  |                            |  |
|  |      near baseline         |  |
|  +----------------------------+  |
|  Depth: Short 19% Mid 42%        |
|         Deep 34% Beyond 6%       |
|  Estimates. Far-end landings     |
|  are less certain.               |
+----------------------------------+
```

**Line call card, `lib/widgets/line_call_card.dart`**

```
+----------------------------------+
| Too close to call                |
|  [ zoomed line patch: line band, |
|    point, uncertainty ellipse ]  |
|  Far baseline                    |
|  About 21 cm inside the line,    |
|  uncertainty about 59 cm.        |
|  [ View in video ] (later)       |
+----------------------------------+
```

The patch is drawn in court metres. It is not a replay of frames in this phase. "View in video" needs Phase 1's playback, so it is an optional callback and hidden when null.

**Rally timeline, `lib/widgets/rally_timeline.dart`**

```
+----------------------------------+
| Rallies   Longest 11  Median 4   |
| 0:00 |##|#|###|##|#####|#|##| end|
| 3:28  5 hits (could be 6)    >   |
|       Ended: lost track          |
| 3:41  2 hits                  >  |
|       Ended: out                 |
+----------------------------------+
```

**Score panel, `lib/widgets/score_panel.dart`**

```
+----------------------------------+
| Score (estimate)                 |
| You       6   2   30             |
| Opponent  4   1   15             |
| 3 points need your review        |
| [ Review points ]                |
| ------------------------------   |
| Point 91  Lost track             |
|  Who won?  (You)(Opponent)(Replay)|
| Point 87  Out at far baseline    |
|  Point to you. [ Change ]        |
+----------------------------------+
```

Correcting a point calls `onCorrect(eventId, winner)`. The panel shows the supplied state and never computes tennis logic in Dart: the score comes from backend replay. A fixture swap stands in for it in tests.

### 8.2 Widgets

| Widget | Key API | Notes |
|---|---|---|
| `CourtMap` | `landings`, `filter`, `onSelect` | `CourtMapPainter` is public. Static hit test `CourtMapPainter.landingAt(Offset, Size, landings)`, like `DonutPainter.sliceAt` (`shot_mix_chart.dart:308-326`). |
| `LineCallCard` | `landing`, `onViewVideo` | `too_close_to_call` is a calm neutral state, not a failure |
| `RallyTimeline` | `rallies`, `onSelect` | Shows the range when `length_min != length_max` |
| `ScorePanel` | `score`, `onCorrect` | Header always says "estimate" until the user has confirmed every point |

`lib/models/court.dart`: `fromJson` parity with section 7, with an `unrecognized` enum member, as `enums.dart:70-87`.

### 8.3 Palette and marks

**No new tokens**, so `palette_test.dart` and all four palettes are untouched.
- Court fill `surfaceContainerHigh`, lines `outline`, labels `onSurface` or `onSurfaceVariant`.
- `in`: filled dot in `primaryFill`. `out`: cross in `errorFill`. `too_close_to_call`: hollow ring in `onSurfaceVariant`.
- **Do not use:** `secondary` or `secondaryFill` (ball speed only, `app_palette.dart:150-155`); `chartSeries` for status (`app_palette.dart:199-217`); the score ramp for calls, because a call is not a grade (`app_palette.dart:251-256`).
- Uncertainty ellipse: mark colour at low alpha plus a thin stroke, drawn to scale in court metres. On a phone-width map a far-baseline ellipse will be visibly long. That is correct.

### 8.4 Too close treatment

- Never grouped with in or out in a "made" percentage. Show counts and share separately.
- Copy: "Too close to call. This ball landed within the uncertainty range of the line." Never "probably in".
- Never coloured as an error.
- Line calls use wording "Estimated", and nothing resembling official-call visuals. No claim of ITF standing.

### 8.5 Accessibility and behaviour

- Verdict is by **shape and text**, never colour alone.
- Text never wears a series colour (test helper pattern: `chart_widgets_test.dart:37-53`).
- `CourtMap` has an accessible equivalent: a text list of each landing ("Shot 3, far court, deep, right, too close to call, uncertainty 59 cm"), with `Semantics` on the map summarising counts.
- Tap targets at least 48 dp. Dense points resolve to the nearest, and the list is the fallback.
- Reduced motion through `motionDuration` (`motion.dart:43`).
- Text scaling: no fixed-height rows.
- Both `kAllPalettes` and `kAllDarkPalettes` are tested. Mobile stays light only.
- Null renders "not measured".
- Court metres may appear in UI; Gemini never gets them (section 9).

---

## 9. Gemini

Gemini writes text from computed numbers. It measures nothing, classifies nothing, and gets no video, frames, positions, timestamps or landmarks.

### 9.1 Payload, `app/feedback/court_summary.py`

New model `CourtFeedbackInput`, `extra="forbid"`. No court coordinates, no sigma values in metres, no player names, no video time.

```json
{
  "court_engine_version": "court_engine_v1",
  "is_estimate": true,
  "data_quality": "limited",
  "landings_counted": 212,
  "line_calls": {"in": 151, "out": 18, "too_close": 43,
                 "in_percent": 71, "out_percent": 8, "too_close_percent": 20},
  "depth": [{"zone": "deep", "expected_percent": 34}],
  "width": [{"zone": "right", "percent": 29}],
  "serves": {"in": 30, "out": 12, "too_close": 9},
  "rally": {"rallies": 42, "median_hits": 4, "longest_hits": 11,
            "low_confidence_rallies": 9},
  "score": {"included": false, "reason": "estimate_not_confirmed"},
  "ball_speed": {"mph": null, "confidence": "unavailable", "reason": "not_measured_in_court_mode"}
}
```

`score` is `included: true` with game and set counts only if every point has been user-confirmed. `data_quality` is computed in Python: `limited` if `too_close_percent` over 25, `shaky` camera, or rally low-confidence share over 25 %. Skip Gemini entirely when landings are under 30; use the deterministic template.

### 9.2 Guard change: `rally` at `gemini.py:81`

**Do not remove `rally` from `BANNED_UNIT_SUBSTRINGS`.** That set guards single-swing text (`gemini.py:75-89`), where no rally count exists, so removal would let the model invent one. Its prompt line (`gemini.py:140`) stays too.

**Change:**
1. Add a keyword-only parameter with defaults preserving behaviour exactly: `validate_text_field(..., *, banned_finder: Callable[[str], list[str]] = find_banned_units)` and the same on `enforce` (`gemini.py:455,474`). `test_gemini.py` and `test_guard_mph.py` must pass unmodified.
2. In `court_summary.py`, define `COURT_BANNED_PATTERNS` as **word-boundary regexes**:
   - All existing units except `rally`: `km/h`, `m/s`, `kph`, `rpm`, `spin rate`, `revolutions`, `meters`, `metres`, `feet`, `inches`.
   - New: `\bspin\b`, `\byards?\b`, and a number followed by a distance unit: `\d\s?(m|cm|mm|ft|in)\b`.
   - `rally` and `rallies` are permitted **only here**.
3. `mph` stays banned: call with `ball_speed_mph=None`, using `check_mph_binding` (`gemini.py:442-443`).
4. **Court allowlist drops ordinals.** `ORDINAL_ALLOWLIST` permits 1 to 5 always (`gemini.py:99,332`). Here that would let the model write "5 rallies" with no basis. Court allowlist: only payload numbers.
5. Percent renderings: add `N%` for keys ending `percent` only, per `SESSION_MODE.md:1247`.
6. Prompt: new `COURT_SYSTEM_INSTRUCTION`, not an edit to `SYSTEM_INSTRUCTION`. It adds: state rally numbers only from `rally`; always call line and score numbers estimates; do not explain what a `too_close` ball "really" was; do not state any distance or uncertainty; never present a score unless `score.included`.

**Not changed here:** word-boundary matching for single-swing. "generally" trips the current guard (`SESSION_MODE.md:1248`), and that is a real false positive, but fixing it changes single-swing output. Leave it for a separate, deliberate change.

**Test proving the split:** the same text "Your rally length" passes the court guard and fails the single-swing guard. "generally" passes the court guard.

---

## 10. Privacy additions

*These flag issues for legal review. They are not legal advice.* Everything in `SESSION_MODE.md` section 11 still applies, including the allowlist gate until legal review and a privacy policy exist.

- **More people in frame.** A full-court frame nearly always captures the opponent, and often neighbouring courts, coaches, and children at a club. It is more bystander exposure than a side-on clip.
- **No new person tracking.** Phase 2 does not detect, track or analyse the opponent's body. Only the ball is tracked. `num_poses` stays as is. The opponent's outcomes appear only as ball landings and score.
- **Still no face data,** per `SESSION_MODE.md:1314`.
- **Opponent acknowledgement.** Court mode in match context needs a separate per-session prompt, "The other player knows this match is being filmed and scored," stored as `opponent_acknowledged_at`. It is a prompt, not legal consent.
- **Names.** Phase 2 uses "You" and "Opponent" only. No free-text player name is stored, because that creates a third party's personal data, possibly a child's. See Decision 4.
- **Minors.** Same open legal questions (`SESSION_MODE.md:1324-1330`). A score and a shot map of a named child are more sensitive than a pose number.
- **Repo rule: do not commit any clip showing a minor, or any frame, contact sheet, crop or label image from one.**
  - The real-clip manifest holds file names and expectations only.
  - Eval output goes to `backend/build/`, already ignored (`.gitignore:5`).
  - `--debug-frames` is off by default and writes only there.
  - `tennis_clips/` and `tennis_clips_v2/` stay gitignored (`.gitignore:17-18`).
  - Owner-filmed sessions and labels live outside the repo.
- **Vendored clip.** `tests/fixtures/clips/serve_vertical_10340710.mp4` is committed. I could not view it. Owner to confirm it does not show a minor (Decision 5).
- **Owner filming.** Consenting adults only, no neighbouring players identifiable if avoidable, and deleted when validation ends.
- **Line calls and scores.** A call can start a dispute, and a scorecard is a statement about someone else's match. No sharing, export, public links or leaderboard in Phase 2. Any later share is opt-in per item, shows the estimate label and the band, and warns about others visible.
- **Gemini.** Never receives names, video, coordinates or times (section 9.1).
- **Retention and deletion.** Court results follow numeric results' lifetime. Account deletion covers new tables (section 7.5).

---

## 11. Evaluation

**Passing synthetic tests does not validate real-world accuracy.** The synthetic scene shares this design's assumptions, including a clean model of how a ball looks. `tests/fixtures/clips/README.md:4-6` already records that every Stage 9 bug was found on real footage and none reproduced on synthetic data. Synthetic results prove the logic and propagate known truth. Nothing else.

### 11.1 Synthetic truth renderer

Test-only package `backend/tests/unit/court_synth/`. **Never imported by `app/`.** No video is committed and no file is written. Frames are generated in memory, one at a time. All randomness comes from `np.random.default_rng(seed)` with seeds as constants.

| File | Contents |
|---|---|
| `physics.py` | 3D ball: RK4 at 1/960 s, g = 9.81, quadratic drag k = 0.0204 per m (`PIPELINE.md:1477-1481`). Bounce: restitution e_z = 0.75, horizontal retention 0.65 **[assumptions]**. Net collision at y = 0 below net height. **No Magnus:** spin is out of scope, so real topspin will land differently than these. |
| `camera.py` | Pinhole camera from f, position and look-at. `project`, ground homography, optional radial distortion k1, optional rolling shutter (row-dependent time), optional shake. |
| `scene.py` | Seeded generators: `Rally`, `Match`. Exact truth: hit times and players, bounce times, 3D points, in/out, rally length, point winner. Scenarios: clean rally, serve fault, double fault, net ball, double bounce, far-half occlusion, second ball, adjacent-court decoy, bird-like arc, unseen far bounce. |
| `render.py` | Court (hard blue, hard green, clay) with lines as projected quads, anti-aliased 4× and downsampled. Dark moving capsules for players. Ball drawn by exposure integration over 8 sub-frames for motion blur. Sensor noise σ = 3. Slow illumination drift. In-memory JPEG quality 60-85 via `cv2.imencode`/`imdecode` as a rough compression stand-in **(not H.264; say so in results)**. PTS jitter option. |
| `truth.py` | Matches engine outputs to truth, and computes the thresholds in section 11.2. |

**Test tiers:**
- **T0:** project truth to candidate records with Gaussian noise, no pixels. Fast (under 2 s per test), and the main logic tests.
- **T1:** pixel render, clean.
- **T2:** pixel render with noise, blur, JPEG, drift.
- **Markers:** T1 and T2 use a new `court_pixels` marker in `pyproject.toml`. T2 is one short rally at 720p and 1080p (about 900 frames; about 15 s, estimate).

**No leakage rule:** engine modules take only what real capture provides. A test asserts no `app` module imports `tests`.

### 11.2 Synthetic thresholds

Default scene: H = 3.5 m, 6 m back, 60 fps, at least 100 seeded rallies at T0, a smaller set at T2. These are my targets. Failing means a logic bug or a bad constant.

| Measure | T0 | T2 at 1080p60 |
|---|---|---|
| Homography round trip on exact taps | ≤ 1e-6 m | n/a |
| Ball per-frame recall, near half / far half | n/a | ≥ 0.95 / ≥ 0.80 |
| Bounce recall and precision (±2 frames) | ≥ 0.98 | ≥ 0.90 |
| Median bounce error, near half | ≤ 0.05 m | ≤ 0.10 m |
| Median along-court bounce error, far half | ≤ 0.20 m | ≤ 0.35 m |
| In/out accuracy **outside** the band | ≥ 99.5 % | ≥ 99 % |
| Confident call wrong, per 1,000 bounces | ≤ 5 | ≤ 10 |
| Band coverage: true margin within ±r, nominal 95 % | 0.92-0.98 | 0.90-0.98 |
| Rally length exact / within ±1 | ≥ 0.95 / ≥ 0.99 | ≥ 0.85 / ≥ 0.97 |
| Rally range contains truth | ≥ 0.99 | ≥ 0.97 |
| Point suggestions, wrong where one is made | ≤ 1 % | ≤ 3 % |
| Score with every point known | 100 % exact | n/a |
| Unobservable scenarios | Always `needs_review`, never a silent wrong score | Same |

**Report, not gate:** `too_close_to_call` rate per zone at each resolution and frame rate. It must fall with resolution and rise with distance, and must broadly match the section 6.4 arithmetic. A large mismatch means section 6.4 or the code is wrong, so resolve it and update this document.

**Calibration of `SIGMA_INFLATION`:** set once from T2, to bring coverage into range, recorded and frozen. If it needs to exceed 1.5, treat that as a bug in the covariance.

### 11.2.1 Measured results (2026-11-08)

T0: `tests/unit/analysis/test_court_e2e_t0.py`, 100 distinct seeds over every scenario except double fault (`t0_records` projects only the first ball). T2: `tests/unit/ball/test_court_e2e_pixels.py` (`court_pixels`), clean-rally seeds 20270001-04.

| Measure | T0 | T2 1080p60 | T2 720p60 | Target (T0 / T2) |
|---|---|---|---|---|
| Bounce precision | 0.998 | 1.00 | 0.62 | 0.98 / 0.90 |
| Point-deciding bounce recall | 0.993 | 0.63 | 0.50 | 0.98 / 0.90 |
| All in-frame bounce recall | 0.919 | 0.50 | 0.40 | 0.98 / 0.90 |
| Median bounce error, near | 0.008 m | 0.012 m | 0.018 m | 0.05 / 0.10 m |
| Median along-court error, far | 0.029 m | 0.087 m | 0.027 m | 0.20 / 0.35 m |
| In/out accuracy outside the band | 0.996 | 1.00 | 0.86 | 0.995 / 0.99 |
| Confident wrong per 1000 | 0 | 0 | 0 | 5 / 10 |
| Band coverage | 0.996 | 1.00 (n = 7) | 1.00 | 0.92-0.98 / 0.90-0.98 |
| Rally exact / within one / range | 0.99 / 0.99 / 1.00 | 0.50 / 0.50 / 1.00 | 0.33 / 0.67 / 1.00 | 0.95 / 0.99 / 0.99 |
| Point suggestions wrong | 0 of 51 (38 to review) | n/a | n/a | 1 % |
| `too_close` rate near / far | 0.00 / 0.008 | 0.00 / 0.00 | 0.20 / 0.00 | report |

Read with care:
- **All-bounce recall** misses at T0 are dead-ball bounces after the point. They decide nothing, so the gate uses point-deciding recall.
- **T2 recall and rally exactness are NOT met.** Misses are bounces at flight joins and tracks fragmented near the far player: event-layer work, best driven by real footage rather than this renderer. `test_t2_bounce_recall_meets_the_design_target` is a strict xfail so it flips when fixed.
- **`SIGMA_INFLATION` is frozen at 1.0.** Coverage is above the band at both tiers, so the covariance is conservative; it was not narrowed on synthetic data (`line_calls.py`).
- **Too-close rates do not show the 6.4 trend** (falling with resolution, rising with distance) at these sample sizes; four T2 rallies give 6-8 judged calls per resolution. Not resolved; needs the larger T2 set or real footage.

Bugs this evaluation found and fixed, each with a test: whole flights rejected by a first-to-last displacement check and by one rough window; net balls called IN on the far half; a far player's hit read as an OUT bounce; dead-ball, split, own-side and impossible-crossing hits miscounted in rallies; ~50 noise candidates per T2 frame; airborne balls dropped by a ground-plane size gate; far-player clutter passing because colour was judged only by expected size.

### 11.3 Real-clip smoke run

The 16 clips cannot validate in/out, depth, rally or scoring. None has a rally, a whole-flight ball, or a fixed camera on a full court.

Paths are arguments. Not committed: `C:\Users\shiva\Desktop\TennisApp\tennis_clips` and `tennis_clips_v2`.

Manifest `backend/tests/eval/real_clip_manifest.json`: file name to expectations, **filenames only**. Test `backend/tests/eval/test_real_clips_smoke.py` with marker `real_clips`, skipped unless `TENNIS_CLIPS_DIRS` is set.

| Assertion | Clips |
|---|---|
| No crash, frames decoded, PTS monotonic (includes 25-60 fps and portrait) | All 16 |
| **Zero** auto court fits | `tennis_forehand_34449247` (padel), `tennis_forehand_34449204`, `serve_02_4902143`, `serve_06_vertical_10340710`, `serve_07_10340707`, and the identical `tennis_serve_10340710` |
| No more than 3 candidates, each needing confirmation; zero fits is acceptable | `8224254`, `8224589`, `8224596`, `8224610`, `4902145`, `serve_03`, `serve_05` |
| Stability verdict is `moving` or `unknown`, hence refusal | Each clip the builder tags `camera: moving` |
| With no calibration, no line call and no bounce in metres | All 16 |
| No rally of length ≥ 3 | All 16: no clip contains a rally, so any such rally is a false positive |

**Not asserted:** bounce or call accuracy. Do not add such assertions.

**Clip tags.** The owner's review was not per-clip on motion. In step 18 the builder measures stability on all 16, inspects the sampled frames locally without saving any under the repo, and writes `camera: moving | fixed | unknown` in the manifest. `tennis_forehand_10340703`, `serve_01_baseline_4902773` and `serve_04_practice_4902161` also need a court tag. If a clip tagged `fixed` fails, loosen nothing without recording why.

### 11.4 Footage needed

**Stock (optional; helps detection only).** Pexels with the owner's key, via `PEXELS_API_KEY` env var, never committed.
- **Search terms:** "tennis match wide", "tennis match static camera", "tennis rally full court", "tennis court high angle", "tennis match baseline view", "tennis doubles match", "tennis practice court wide shot", and "aerial tennis court static" (only if truly static).
- **Accept only if:** fixed camera for ≥ 30 s, both baselines visible, ≥ 720p, ≥ 50 fps preferred, at least one exchange over the net, no visible minors.
- **Use:** court fit positive examples, far-half candidate rate. Stock clips have no ground-truth bounces, so hand labels on these can test bounce detection but **cannot** validate in/out.

**Owner-filmed validation session: required, and a release gate.**
- **Rig:** 3-4 m high (fence clamp or tall light stand), about 6 m behind the baseline, centred, landscape, main lens. 1080p60, plus one repeat at 720p60. Lock exposure and focus if the app allows. Measure and record camera height.
- **Conditions:** at least 2 sessions: sun, and overcast or dusk. Note the court surface. At least 2 surfaces if possible.
- **Part A, 30 min:** surveyed marks. 20 tape crosses at tape-measured positions, including within 10-30 cm of baseline, service line and sideline at both ends. Bounce a ball on each 3 times. **This is independent position truth.**
- **Part B, 20 min:** 60 serves and 60 baseline drives each way.
- **Part C, 20 min:** real points, with paper score log by a third person. Adults, consent noted.
- **Part D, independent line truth:** a second phone, low on the line at 120-240 fps, covering 40 or more close bounces. Pixels from the main camera cannot validate themselves.

**Label format (JSON Lines), stored outside the repo:**

```json
{"type":"session","session_id":"s01","video":"s01.mp4","fps":59.94,"width":1920,"height":1080,"camera_height_m":3.5,"court_format":"singles","taps":[{"keypoint":"far_service_t","u_px":960.0,"v_px":366.0}]}
{"type":"surveyed_mark","mark_id":"m07","court_xy_m":[4.0,11.6]}
{"type":"surveyed_bounce","mark_id":"m07","frame":1842}
{"type":"bounce","frame":5120,"u_px":812.0,"v_px":231.5,"end":"far","label_source":"main_frame","visibility":"clear"}
{"type":"line_truth","bounce_frame":5120,"line":"far_baseline","call":"in","margin_cm":3,"source":"second_phone_240fps"}
{"type":"hit","frame":5088,"end":"far","player":"opponent"}
{"type":"ball","frame":5100,"u_px":840.0,"v_px":300.0,"visible":true}
{"type":"rally","start_frame":5000,"end_frame":5600,"length_hits":5,"end":"out"}
{"type":"point","rally_start_frame":5000,"winner":"player_a","reason":"out","note":"","is_let":false}
```

Tune on one session and score on another. Thresholds are frozen before scoring, and labels never go in the repo.

### 11.5 Release gate

These are my proposed targets, not measured values. **No user sees line calls, depth or score until these pass on held-out real footage.** Failure in a zone means disabling that output for that zone (for example, far-half calls), not lowering the number.

| Measure, held-out real sessions | Target |
|---|---|
| Bounce recall (±2 frames), near / far | ≥ 0.80 / ≥ 0.70 |
| False bounces per minute of play | ≤ 1.0 |
| Median error on surveyed marks | Near half ≤ 0.20 m; far half along-court ≤ 0.60 m; far half across-court ≤ 0.20 m |
| Band coverage, surveyed and second-phone truth | 0.90-0.98 |
| Confident call accuracy, second-phone or surveyed truth, n ≥ 200 | ≥ 97 % |
| Share of bounces called `too_close_to_call`, by zone | Reported, compared with section 6.4 |
| Rally length exact / ±1 | ≥ 0.70 / ≥ 0.90 |
| Point suggestions that are correct | ≥ 0.90 |
| Points needing review | Reported, target ≤ 40 % |
| Score after user corrections | Exact, every time |
| Camera `moving` refusal on deliberately bumped footage | 100 % |
| Single-swing regression | Unchanged |

Also required before release: legal items in section 10, and the Phase 1 gates.

---

## 12. Build order

**Setup, Windows or POSIX.** From the worktree root, `cd backend`. `pyproject.toml:17-24` sets test paths and `pythonpath = ["."]`.
- Install, if needed, in the order required by `requirements.txt:3-7`.
- Backend: `python -m pytest -m "not slow and not gemini_live and not court_pixels and not real_clips"`
- Pixels: `python -m pytest -m court_pixels`
- Frontend: `cd frontend` then `flutter test`
- Clips: set `TENNIS_CLIPS_DIRS`, then `python -m pytest -m real_clips`. Separator is the OS path separator.

I did not run these commands; they derive from the config files.

**Requirements changes: none.** `numpy`, `scipy`, `av` and `opencv-python-headless` already exist (`requirements.txt:60-61,71,77`). `cv2` optical flow and Hough are in headless. Do not add a package; if one seems needed, stop and ask. `requirements-mediapipe.txt` does not change. Mediapipe Tasks only, and no new mediapipe code. No secrets in code.

**Global rules for every step:**
- Type hints on every function. Frozen dataclasses in `analysis/`. Named `Final` constants.
- Every math function gets a synthetic-data unit test. No I/O in `analysis/`. No clock. No RNG in app code.
- Existing tests stay green and unedited, except the purity test additions.
- `contact.py`, `speed.py`, `detector.py`, `track.py`, `frames.py`, `geometry.py`, `pipeline.py` remain byte-identical.
- Commit each step separately.

| # | Build | Test file | Done when |
|---|---|---|---|
| 0 | Baseline only | none | Both suites pass; record counts in the step 1 commit message. If already red, report it before changing anything. |
| 1 | `backend/app/models/court.py`: contract, enums | `backend/tests/unit/models/test_court_models.py` | Every section 7 JSON sample parses verbatim; `extra="forbid"` rejects an extra field; verdict is a closed set of 3; null not coerced to 0 |
| 2 | `analysis/court_model.py` | `tests/unit/analysis/test_court_model.py` | ITF numbers exact; 14 keypoints symmetrical; box regions correct; zone boundaries correct |
| 3 | `analysis/homography.py` | `tests/unit/analysis/test_homography.py` | Exact recovery from a known camera ≤ 1e-6 m. Jacobian matches numeric within 1e-3. Calibration covariance matches Monte Carlo within 15 % in the test. Pose recovered within 2 % at centred and yawed views, including the centred degenerate case. Parallax correction exact on synthetic. |
| 4 | `analysis/court_calibration.py` | `tests/unit/analysis/test_court_calibration.py` | Each rejection reason has its own test. Exactly 4 taps never allow calls. 6 noisy taps yield RMS close to noise. Lens-distorted synthetic is rejected. |
| 5 | Purity update: add `app.ball.flight_detector`, `app.ball.flight_frames`, `app.ball.camera_motion`, `app.ball.court_lines` to `BANNED_DOTTED`, and add new modules to the subprocess import line | `tests/unit/analysis/test_analysis_purity.py` | Test passes, and fails if a new module imports `cv2` (verify temporarily, then revert) |
| 6 | Add `court_pixels` and `real_clips` markers; `tests/unit/court_synth/` package (`__init__.py`, `physics.py`, `camera.py`, `scene.py`, `render.py`, `truth.py`) | `tests/unit/court_synth/test_synth.py` | Same seed gives byte-identical frames. Ball falls with correct g. Bounce energy matches e_z. Projection round-trip correct. No file written. |
| 7 | `app/ball/flight_frames.py`, `app/ball/flight_detector.py` | `tests/unit/ball/test_flight_detector.py`, `test_flight_frames.py` | Finds 2-3 px synthetic balls that existing `find_candidates` misses (assert both). Static lines give no candidates. PTS real. Uses vendored clip for a stream smoke. |
| 8 | `app/ball/camera_motion.py`, `analysis/camera_stability.py` | `tests/unit/ball/test_camera_motion.py`, `tests/unit/analysis/test_camera_stability.py` | Synthetic pan, shake and bump classified correctly; moving players do not trigger `moving` |
| 9 | `analysis/flight_track.py` | `tests/unit/analysis/test_flight_track.py` | Survives a bounce, unlike `associate_candidates` (assert both). Links across 0.1 s gap. Rejects noise. Two simultaneous balls stay separate. Same input identical output. |
| — | **Run the section 1.4 experiment here, using a minimal `court_eval.py --mode candidates`.** Can start once the owner has filmed. Later steps proceed in parallel, but Flutter wiring does not start if it fails. | | Result recorded in this doc |
| 10 | `analysis/ball_events.py` | `tests/unit/analysis/test_ball_events.py` | Bounce vs hit vs net on T0, each with a synthetic case. Near-side hit without pose not called a bounce. In-flight point never yields a ground point. Pose hits are optional. |
| 11 | `analysis/line_calls.py` | `tests/unit/analysis/test_line_calls.py` | **Never confident inside the band** (property test). Boundary cases at exactly ±r. Ball touching a line is in. Far edge of box centre line. Weak bounce forces too close. Covariance grows with distance. |
| 12 | `analysis/placement.py` | `tests/unit/analysis/test_placement.py` | Probabilities sum to 1. Zones exact at boundaries. Filters by side, shot, verdict, serve. Soft counts sum to count. Too-close not added to in. |
| 13 | `analysis/rally.py` | `tests/unit/analysis/test_rally.py` | Length definition tested for serve fault (1), let (1), rally (k). Range contains truth with gaps. Quiet split. Two-ball scene flagged. |
| 14 | `analysis/match_score.py` | `tests/unit/analysis/test_match_score.py` | Deuce and advantage; no-ad; 6-6 tiebreak; 7-6; server rotation in tiebreak; next-set server; ends; final-set rules; unresolved does not advance; correction anywhere equals clean replay; append-only; no Dart or I/O logic. Include a parameterised full-match table. |
| 15 | `analysis/point_inference.py` | `tests/unit/analysis/test_point_inference.py` | Every section 6.8 row. Too close and lost track give no winner. Double fault needs two faults. |
| 16 | `analysis/court_session.py`; `app/services/court_offline.py`; `backend/scripts/court_eval.py` (`--video`, `--taps-json`, `--pose-hits-json`, `--mode`, `--out`; output to `backend/build/court_eval/`) | `tests/unit/analysis/test_court_session.py`, `tests/unit/services/test_court_offline.py` | Refuses on `moving`. Runs on the vendored clip without crashing and emits no call without taps. Wire output validates against step 1 models. |
| 17 | Full-chain synthetic evaluation | `tests/unit/analysis/test_court_e2e_t0.py`, `tests/unit/ball/test_court_e2e_pixels.py` (`court_pixels`) | Section 11.2 thresholds pass. Results and `too_close` rate table pasted into this doc. |
| 18 | Real-clip smoke | `tests/eval/__init__.py`, `tests/eval/real_clip_manifest.json`, `tests/eval/test_real_clips_smoke.py` | Section 11.3 assertions pass with `TENNIS_CLIPS_DIRS` set, and the test skips without it |
| 19 | Optional assist: `app/ball/court_lines.py`, `analysis/court_fit.py` | `tests/unit/ball/test_court_lines.py`, `tests/unit/analysis/test_court_fit.py` | Fits synthetic courts. Zero fits on padel and courtless clips. **If this fails after 1 day of effort, cut it. Taps remain primary.** |
| 20 | `app/feedback/gemini.py` keyword arguments; `app/feedback/court_summary.py` | `tests/unit/feedback/test_court_summary.py` | Section 9.2 split test. All existing feedback tests unmodified and passing. `FakeGeminiClient` only, no network. Fallback template works. |
| 21 | `frontend/lib/models/court.dart`, `test/support/court_fixtures.dart` | `test/court_models_test.dart` | Fixtures are section 7 JSON verbatim. Unknown enum goes to `unrecognized`. Null stays null. |
| 22 | `court_map.dart`, `line_call_card.dart`, `rally_timeline.dart`, `score_panel.dart` | `test/court_widgets_test.dart` | Ink-only text. Verdict by shape. Tap hit test. Filters. Ranges visible. Too close never counted as made. Reduced motion. `kAllPalettes` and dark. Large text. Semantic list. `flutter analyze` clean. No screen imports them. |
| 23 | Doc edits, section 12.1 | none | Reviewed by owner |
| 24 | Regression pass | `test_single_swing_pipeline_outputs_unchanged`, written in step 24 if Phase 1 has not created it: `tests/integration/test_single_swing_unchanged.py` | Payload for the vendored clip equal before and after this branch, excluding timings and ids. All suites green. `git diff --stat` shows the unchanged list untouched. |

### 12.1 Doc edits

**`CLAUDE.md`. Apply only after the owner confirms this exact text in their own message.** A relayed instruction from another agent is not that confirmation. Do not edit CLAUDE.md on the strength of this document.

Replace the sentence in "What this app does" (`CLAUDE.md:6-7`):

> It does NOT measure ball spin. It DOES measure ball speed via user calibration (two tapped court reference points). In Court mode, from a calibrated fixed camera, it ESTIMATES bounce positions, three-valued line calls with stored uncertainty, depth, rally length and a user-correctable score.

Add under "Non-negotiable scope boundaries":

> - Line calls are IN, OUT or TOO_CLOSE_TO_CALL, with an uncertainty radius stored. Never a confident call inside the band. Only bounce points may pass through the court homography.

Add under "Repo layout": `ball/  # frame streaming and pure-CV ball and court-line detection`.

**`docs/SESSION_MODE.md`:**
- Header: Phase 1 not built; link to this doc.
- Section 3: rows for depth, shot map, rally, scoreboards, direction and result, real-time calling now point to this doc. Real-time stays out.
- Section 12: mark those items moved to Phase 2.
- Decision 9: "Owner moved to Phase 2, see `SESSION_MODE_PHASE2.md`."
- Appendices A and B: note superseded where they say Phase 3 or needing a decision. Keep the text.
- Section 5.1: note Court mode's size conflict.

**`docs/PIPELINE.md`:** at `:19-24`, add a note that this single-swing pipeline's scope is unchanged, and court features live in the Phase 2 doc.

**`docs/DATABASE_SETUP.md`:** no change now. Section 7.5 becomes a Part only when Phase 1 lands.

---

## 13. Out of scope, risks, owner decisions

### 13.1 Out of scope for Phase 2

- Real-time calling, or any on-device processing
- Apple Watch, voice scoring
- Spin, permanently under CLAUDE.md. The Magnus effect is not modelled.
- ITF or any certification claim, or any comparison with a certified system or app
- Burned-in overlay, export, sharing, highlight files
- A learned ball detector (unless Decision 3 chooses one)
- Multi-camera, moving camera, re-calibration after a bump
- Opponent pose, doubles partner identity, face or appearance identification
- 3D flight reconstruction, in-flight ball speed from the court
- Live routes, DB changes, deploys (spec only until Phase 1)
- SwingVision branding or layouts. These widgets use Tennisimo's own palette and layouts (`SESSION_MODE.md:271-278`).

### 13.2 Risks

The riskiest assumption is in section 1.4.

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Too many `too_close` far-end calls make the feature feel useless | High | High | Honest copy; shot map still valuable; consider camera end (Decision 1) |
| Users cannot mount a phone 3-4 m high | High | High | Setup guide; lower heights accepted, with visibly larger bands |
| Fence or pole sway | Medium | High | Stability check; shake in sigma |
| H.264 erases the far ball | Medium | High | Experiment; capture profile; Decision 2 |
| Plugin cannot set 60 fps or bitrate | Medium | High | Verify on device before Flutter wiring |
| Synthetic tests pass but real footage fails | High | Medium | Release gate; never quote synthetic figures to users |
| Tap error larger than assumed | Medium | Medium | Loupe; refinement; six taps; overlay confirm |
| Lens distortion or digital stabilisation breaks homography | Medium | High | Reprojection gate; disable stabilisation if possible **[verify]** |
| Adjacent-court balls create false bounces | High | Medium | ROI; trajectory checks; report per scene |
| Hit vs bounce mistakes corrupt rallies | Medium | Medium | Grammar; ranges; review |
| Wrong server cascades to wrong service box | Medium | Medium | Replay; user-declared start; corrections |
| A user trusts an estimated call in a dispute | Medium | Medium | Always "estimate"; band shown; no official styling |
| 2.6-4.4× real time feels too slow | High | Medium | Safe-to-leave flow from Phase 1; 10-minute cap |
| Bigger video files cost more | Medium | Medium | Decision 2; retention |
| Phase 1 slips or changes shape | Medium | Medium | Engine independent; spec sections revisited on Phase 1 landing |
| Pose picks the wrong person | Medium | Medium | Hits optional, treated as evidence only |
| More bystanders, minors | Medium | High | Section 10; allowlist; legal review |

### 13.3 Decisions needing the owner

| # | Decision | Recommendation |
|---|---|---|
| 1 | Which end should the camera sit at? Own end gives usable pose and hit timing, but own shots land where bands are ±44-91 cm along-court. Far end gives about ±18-22 cm on own shots, but pose is useless at about 60 px tall. | **Own end by default,** with the far end as a documented "best for line accuracy" option. |
| 2 | Court mode capture and size. 1080p60 at 20 Mbps is 1.5 GB per 10 minutes, over Phase 1's 1 GiB cap. | **Decide after the experiment.** Expect a 2 GiB Court limit at 10 minutes. If 720p60 at 10 Mbps passes, prefer it. |
| 3 | If the experiment fails: near-half-only product, or a learned detector? A learned detector needs a labelled set, training, a new dependency, and a CPU cost likely well above the current ball pass **[verify; unmeasured]**, and it needs its own exception to the "no model calls in math" rule only at the detection layer, not in `analysis/`. | **Near-half-only first,** reassess on user demand. |
| 4 | Opponent names. | **"You" and "Opponent" only.** No free-text. |
| 5 | Confirm the committed `tests/fixtures/clips/serve_vertical_10340710.mp4` shows no minor. A single mid-clip frame of the same Pexels video (`serve_06_vertical_10340710`) shows what appears to be an adult man; the whole clip was not reviewed. | **Owner to glance at it once.** If it does show a minor, replace it through a separate history-aware change. |
| 6 | Approve the CLAUDE.md text in section 12.1 in your own message. | **Yes,** if the wording matches your decision. |
| 7 | Score display when points are unresolved. | **Show the provisional score with "needs review" prominently,** instead of hiding it. |

