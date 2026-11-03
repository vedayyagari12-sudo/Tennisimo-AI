# Session Mode Phase 2: implementation brief

Companion to `docs/SESSION_MODE_PHASE2.md` (the "design"). Written by the architect agent on 2026-11-02 after auditing the design against `main` at f985ff1 (pipeline v4). This brief corrects drift, fills gaps, orders the build, and scopes real-clip testing. Where it conflicts with the design, **this brief wins**. It does not redesign anything.

Tags: [V] verified by reading code; [I] inferred, not executed.

## Binding rules

- Do NOT edit `CLAUDE.md`. The step 23 CLAUDE.md text needs the owner's own message (Decision 6).
- No routes, DB, Jobs, deploys, or screen wiring (blocked on Phase 1).
- Gemini formats text only. All math goes in flat, pure files under `backend/app/analysis/`.
- No new packages, including `hypothesis`. Property tests are seeded numpy sweeps.
- Owner decisions taken: 1, 3, 4, 7 follow the design's recommendations. Decision 2 waits on the experiment.

## 1. Drift audit

Everything else the design cites still matches [V].

- **D1.** `metrics.py:89-107` is stale. `METRIC_VIEW_SENSITIVE` is now at `backend/app/analysis/metrics.py:114-130`. The set is unchanged. `PIPELINE_VERSION` is "v4" at `backend/app/services/pipeline.py:131`.
- **D2.** "pipeline.py" in the untouched list means `backend/app/services/pipeline.py`.
- **D3.** Step 6 adds two markers (`court_pixels`, `real_clips`). No `--strict-markers` is set.
- **D4.** `pyproject.toml:9` declares `opencv-python>=4.9`, while `requirements.txt:77` pins the headless build. This is pre-existing; leave it and install from `requirements.txt` only.
- **D5.** The Dart unknown-enum mapping is `parseWireEnum` in `frontend/lib/models/enums.dart:14-20,70-87`, with the sentinel `'__unrecognized__'`. `parseWireEnumOrNull` maps unknown to null. See G20.
- **D6.** Line drift: `shot_mix_chart.dart` `sliceAt` is at 309 (`DonutPainter` at 285); `app_palette.dart` `secondaryFill` is at 155 and `chartSeries` at 217.
- **D7.** There is no venv. Python is the global 3.13. Step 0 confirms `python --version` and `python -c "import cv2, av, scipy, numpy, mediapipe"`.
- **D8.** `backend/app/ball/__init__.py` and `backend/app/models/__init__.py` are empty. `analysis/` may import `app.ball.track` (numpy only), as `speed.py` already does.

## 2. Gaps and decisions

### Correctness fixes to the design

- **G1. DLT condition.** `numpy` returns singular values in descending order, so σ7/σ8 ≥ 1 and the design's gate can never fire. Use `condition = s[7] / s[0]` on the Hartley-normalised A and reject below 1e-3. Use `np.linalg.svd(A, full_matrices=True)`, because with 4 taps A is 8×9. Test: six good taps pass; near-collinear taps fail.
- **G2. Tiny-blob features.** `contour_features` returns None when `cv2.contourArea` is 0 (`detector.py:209-212`), which drops 2-3 px balls. Below a 6 px expected diameter, or when the size is unknown and the component has ≤ 30 px, take these from `cv2.connectedComponentsWithStats`:
  - area = pixel count
  - centroid from the stats
  - major/minor/orientation from the second moments of the pixel coordinates

  Apply "min area 2 px²" to the pixel count. Use `contour_features` and the round/streak logic only above 6 px. Step 7 tests a 2×2 and a 2×1 blob.
- **G3. Court guard regex.**
  - Use `\d\s?(?:m|cm|mm|ft)\b`, plus `\binch(?:es)?\b`, `\bmet(?:er|re)s?\b`, `\bfeet\b`, `\byards?\b`, `\bspin\b`, `\bkm/h\b`, `\bm/s\b`, `\bkph\b`, `\brpm\b`, `\bspin rate\b`, `\brevolutions\b`.
  - Test that "151 in, 18 out" passes the court guard.
  - "rallies" already passes the single-swing guard, so don't write a test claiming it fails. The split test uses "Your rally length".
- **G4. `FLIGHT_CANDIDATE_DTYPE`** lives in `app/analysis/flight_track.py`; `flight_detector` imports and re-exports it.
  - Fields: frame_index i8, time_s f8, x f8, y f8, area f4, minor f4, major f4, orientation_deg f4, colour_score f4, contrast f4 (56 bytes packed).
  - Do not assert 64 bytes.
- **G5. Purity tests.**
  - Leave the existing subprocess test unchanged. Add `test_importing_every_analysis_module_pulls_no_cv2_av_mediapipe`, whose import line is built from the `analysis_modules()` stems.
  - Add `test_app_never_imports_tests`, an AST scan of `app/**/*.py`.
  - Add `app.ball.flight_detector`, `app.ball.flight_frames`, `app.ball.camera_motion` and `app.ball.court_lines` to `BANNED_DOTTED`.
- **G6. 3D projection.** Add `projection_matrix: Float64Array` (3×4, r3 = r1×r2) to `CameraPose`, and `project_points_3d(pose, xyz) -> Float64Array` to `homography.py`. The net polygon and the ROI's upward extension use them.

### Signature and contract fixes

- **G7. `analyze_court_session` signature.**
  - Signature: `analyze_court_session(candidates, frame_times_s, calibration: CourtCalibration | None, stability, pose_hits=(), *, court_format: CourtFormat, frame_size: tuple[int, int]) -> CourtSessionResult`.
  - The result gains `fps: float` (median PTS delta).
  - Refusals: `frame_rate_too_low` (< 24 fps), `camera_moving`, `camera_unknown`, and `not_calibrated` when calibration is None.
  - Truncate at `bumped_at_s` and return `partial`.
  - A refused result carries an empty ShotMapSummary (count 0, `median_sigma_along_m` None).
- **G8. Missing producers.**
  - Add `time_sigma_s` to BouncePoint.
  - Add `target: str` to LineCall: one of `singles_half`, `doubles_half`, `service_box_deuce`, `service_box_ad`, `half_only`.
  - `rally_id` and `shot_index_in_rally` are derived in `app/services/court_offline.py`, and are nullable when a landing is in no rally.
  - `bounce_id` = `BallEvent.event_id`.
- **G9. Ids.**
  - `find_break_points` assigns temporary ids. `detect_ball_events` assigns final ids after the grammar pass, in time order: `{b|h|n|k}_{index:06d}` with one running index.
  - Rally ids are `r_{index:04d}`; inferred score event ids are `se_{index:04d}`.
- **G10. Event grouping.** Add `group_event_runs(events, *, max_quiet_s=2.5) -> tuple[tuple[BallEvent, ...], ...]` to `rally.py`.
  - Within a run, a bounce's hitter_end is the end of the last preceding HIT. If there is none, hitter_end is None and the target is `half_only`.
  - Add `shot_type: str | None = None` to PoseHit. It is the only producer for `is_serve` in practice mode and for `ShotLanding.shot_type`.
  - Then build landings and call `segment_rallies(events, landings)`.
- **G11. Scoring loop.**
  - `events_from_rallies(rallies, config, user_events: Sequence[ScoreEvent] = ())` loops with running state. In match mode the first landing of each rally is the serve, with target `service_box_region(receiver_end, state.serve_side)`, re-called through `call_region`.
  - `infer_point_outcome(..., *, prior_faults: int = 0)`: the first fault emits nothing; the second emits POINT_WON for the receiver with basis `double_fault`.
  - A user event with `supersedes` always wins; replay via `replay_score`.
  - Add a pure `serve_box_calls(rallies, config, user_events) -> Mapping[str, LineCall]`, keyed by bounce id.
  - Rally end reason `timeout` maps to basis `unseen_bounce`, which needs review.
- **G12. Service boxes.** Deuce = the receiver's right.
  - Near deuce: x > 0, y in [-6.40, 0].
  - Far deuce: x < 0, y in [0, 6.40].
  - The box includes the centre line (inner edge at x = ∓0.025).
  - Test all four boxes in step 2.
- **G13. ScoreEvent fields.**
  - ScoreEvent gains `basis: str | None = None` and `needs_review: bool = False`.
  - Wire `status` is `Literal["estimate", "confirmed"]`. "confirmed" only when every effective event has source=user.
  - `needs_review_count` counts effective events with `needs_review`.
- **G14. Score display.** ScoreStateResponse gains `point_labels: [str, str]` (player_a, player_b). `points_display` is player_a first ("30-15", "Deuce", "Ad-40").
- **G15. Rally counts.** Keep the sample verbatim.
  - hits_observed = HIT events.
  - hits_inferred = direction reversals with no HIT event.
  - length_min = observed + inferred.
  - length_max = length_min + lost-track gaps.
  - Invariant: length_min ≤ length_hits ≤ length_max.
  - A lost-track gap = consecutive flights in a run more than 0.2 s apart with no event at the join.
  - Drop the "stitch over 0.2 s → low" clause; confidence comes from range width only.
- **G16. Ellipse.** Wire `sigma_m` is 1σ: the projection calls `uncertainty_ellipse(cov, z=1.0)`. angle_deg is from court +x, counter-clockwise, in [0, 180). Flutter draws at 1.96×.
- **G17. Client event ids.** `ScoreEventRequest.client_event_id` is `str` (min_length 1, max_length 64), not UUID. Future DB note: `event_id text`, `supersedes text`, unique (session_id, event_id).
- **G18. Extra models.**
  - Add `CaptureInfo`, `OverlayLine` and `CalibrationOverlay`.
  - Add the CourtFeedbackInput sub-models: `CourtFeedbackLineCalls`, `CourtFeedbackZone`, `CourtFeedbackServes`, `CourtFeedbackRally`, `CourtFeedbackScore`, `CourtFeedbackBallSpeed`.
  - ShotMapSummaryResponse has no `landings`.
  - `data_quality` is `Literal["good", "limited"]`.
  - `ScoreConfigRequest.opponent_acknowledged: bool` is wire-only.
  - Add the rejection reason `too_few_taps`.
  - Court enums are defined once in `app/models/court.py` and re-exported by `analysis/court_model.py` and other modules.
- **G19. Wire names.**
  - 14 keypoints: `{near|far}_{left|right}_doubles_corner`, `{near|far}_{left|right}_singles_corner`, `{near|far}_{left|right}_service_corner`, and `{near|far}_service_t`.
  - Lines: `near_baseline`, `far_baseline`, `near_service_line`, `far_service_line`, `left_singles_sideline`, `right_singles_sideline`, `left_doubles_sideline`, `right_doubles_sideline`, `centre_service_line`.
  - Keypoints are at outer-edge intersections; the T is at x = 0, y = ±6.40.
  - Add `court_line_endpoints(line)` to `court_model.py`.
- **G20. Dart nullable court enums** (`hitter_end`, `winner`, `match_winner`, `basis`, `deciding_line`): null stays null, and an unknown string becomes `unrecognized`. Do NOT use `parseWireEnumOrNull`.

### Constants

All are `Final`. Tune them on T0/T1 only, then freeze.

- `RUNOFF_M = 4.0`
- `POSE_HIT_WRIST_GATE_M = 1.2` (converted via `image_scale_px_per_m`)
- `BREAK_MIN_RSS_RATIO = 0.5`: keep a break when two-segment RSS ≤ 0.5 × single-segment RSS
- `GRAMMAR_RELABEL_MAX_CONFIDENCE = 0.6`
- `WEAK_BOUNCE_CONFIDENCE = 0.5`
- Event confidence = clip(1 − rss2/rss1, 0, 1) × min(1, min(n_before, n_after)/8)
- **Calibration pixel gates** (8 px, 12 px, RMS 2 / max 4, f 400-3000) are 1280-equivalent: scale by 1280/long_edge. σ_tap is in the frame's own pixels.
- `TRACK_SPACE_LONG_EDGE_PX = 1920`, in `flight_frames.py`. Normalised taps are converted to TRACK_SPACE pixels in `court_offline`.
- **Camera stability:**
  - `shifts_px` is (N, 2) relative to the first sample, in TRACK_SPACE.
  - p95 and rms are computed over pre-bump samples.
  - `bumped_at_s` is the first t where the magnitude is ≥ 0.2 % of the long edge for every sample in [t, t+1 s].
  - A median feature count < 30 gives `unknown`.
  - Always add rms² to Σ_image.

### Other rules

- Dataclasses holding numpy arrays use `@dataclass(frozen=True, eq=False)`. Tests compare fields with `np.array_equal`.
- **Gemini (step 20):**
  - Reuse `GeminiFeedbackDraft`, `response_schema()`, `enforce` and `CoachingFeedback`.
  - The court allowlist is a `court_summary.py` walker over `round_payload_numbers(payload)`, with no ORDINAL_ALLOWLIST, plus `N%` for keys ending in `percent`.
  - The prompt forbids hyphen-joined numbers ("6-4" tokenises as "6" and "-4").
  - Rally `median_hits` uses `statistics.median_low`.
- **No circular imports.** Direction: ball → analysis (court_calibration, flight_track) → app.ball.track / app.models.
- **T2 render cost.** Render the static court once per scene at 4×, then composite only the ball's local patch per frame (8 sub-frames).
- **Decision 1 assumption.** With no score config, "Your shots" means `hitter_end == "near"`. With a score config, the projection fills a new nullable wire field `hitter: "player_a"|"player_b"|null` on ShotLandingResponse.

## 2b. Decisions after batches A-C

- **G21. Line calls need at least 8 taps.** With the 6 recommended taps, OpenCV k1 = −0.25 lens distortion passes the reprojection gates (RMS 1.94 / max 3.93 px at 1280-equivalent) but leaves up to 0.47 m of systematic ground error. That would produce confident wrong calls. With 8 or 14 taps the same distortion is rejected. Decision: `calls_allowed` requires ≥ 8 accepted taps. 4-7 taps give the shot map and zones only; the wire `line_call` is null ("not measured"). The setup copy asks for 8 taps. The strict-xfail test in `test_court_calibration.py` becomes a passing test of this rule. Revisit only if real footage shows 8 taps are impractical.
- **G22. Step 24 compares against the merge base, not `main`.** `main` moves independently (e.g. 9d6bef5 edits `pipeline.py` and `CLAUDE.md`). Use `git diff --stat $(git merge-base HEAD main) HEAD -- <unchanged list>`. The golden reflects the f985ff1 pipeline. If the branch is later rebased onto a newer main, recapture the golden first.

## 3. Build batches

Run from `backend/` in PowerShell.
`BASE = python -m pytest -m "not slow and not gemini_live and not court_pixels and not real_clips"`.
Each batch is done when its files pass and BASE is green. Commit each step separately.

- **A. Steps 0-1.**
  - Run BASE and `flutter test`, and record the counts.
  - Capture the golden `backend/tests/fixtures/golden/single_swing_serve_vertical_10340710.json`: the single-swing pipeline output on the vendored clip, numbers only, timings and ids excluded.
  - Build the contract (G7-G20 fields): `tests/unit/models/test_court_models.py`.
- **B. Steps 2-4.** `court_model`, `homography`, `court_calibration` (G1, G6, G12, G19). In the distortion test, k1 = −0.25 is rejected and k1 = 0 accepted.
- **C. Steps 5-6.** Purity (G5), markers, `tests/unit/court_synth/`.
- **D. Steps 7-8.** `flight_frames`, `flight_detector` (G2, G4), `camera_motion`, `camera_stability`.
- **E. Step 9**, plus the minimal `backend/scripts/court_eval.py --mode candidates` (`--video`, `--taps-json`, `--labels-jsonl`, `--out`; output under `backend/build/court_eval/`; within-3-px rate when labels are given). Smoke it on the vendored clip. The section 1.4 experiment needs owner footage; proceed anyway.
- **F. Steps 10-12.** `ball_events`, `line_calls`, `placement`.
- **G. Steps 13-15.** `rally` (G10, G15), `match_score`, `point_inference` (G11, G13).
- **H. Step 16.** `court_session`, `court_offline`, `court_eval --mode events`.
- **I. Step 17.** T0 e2e, T2 pixels, freeze `SIGMA_INFLATION`, paste the tables into the design doc.
- **J. Steps 18-19.** Real-clip smoke with `TENNIS_CLIPS_DIRS="C:\Users\shiva\Desktop\TennisApp\tennis_clips;C:\Users\shiva\Desktop\TennisApp\tennis_clips_v2"`, and confirm it skips when the variable is unset. Step 19 (court_lines, court_fit) is time-boxed; cut it if it fails.
- **K. Step 20.** `court_summary`. `test_gemini.py` and `test_guard_mph.py` stay unmodified.
- **L. Steps 21-22.** Flutter models and widgets. `flutter analyze` must be clean.
- **M. Steps 23-24.** Edit only `docs/SESSION_MODE.md` and `docs/PIPELINE.md`. Add `tests/integration/test_single_swing_unchanged.py` (marked `slow`), which compares against the golden. Then `git diff --stat main -- backend/app/analysis/contact.py backend/app/analysis/speed.py backend/app/ball/detector.py backend/app/ball/track.py backend/app/ball/frames.py backend/app/ball/geometry.py backend/app/services/pipeline.py frontend/lib/theme/app_palette.dart CLAUDE.md` must be empty.

Not completable without owner-filmed footage: the section 1.4 experiment, Decision 2, the full / between / near-half-only choice, the section 11.5 release gate, and any accuracy claim.

## 4. Real clips and Pexels

**The 16 local clips can prove:**
- decode and PTS robustness
- camera-motion classifier behaviour (the single allowed threshold adjustment, then frozen)
- court_fit negatives
- false-candidate density on real H.264
- near-field serve-ball recall against the existing `find_candidates`
- the "no rally ≥ 3" guard
- possibly real tap error on a baseline-view clip

**They cannot prove:** far-half detection, bounce recall, homography accuracy, band coverage, calls, depth, rally length or score.

**Pexels is optional.** It is the only pre-filming way to put early numbers on the section 1.4 assumption: positive court fits, `stable` examples, far-half candidate rate, and false-bounce rate. It cannot validate in/out or metric accuracy.
- **Search:** the section 11.4 terms, plus "tennis tournament wide", "tennis match high angle static", and "tennis court drone hover" (static only).
- **Accept only if all hold:**
  - `stable` for ≥ 30 s
  - both baselines visible, with ≥ 6 keypoints
  - long edge ≥ 1280
  - ≥ 25 fps
  - an exchange over the net
  - no visible minors
- **Target:** 5-10 clips. Store them outside the repo (e.g. `C:\Users\shiva\Desktop\tennis_eval_clips\`) with a SOURCES.txt. `PEXELS_API_KEY` stays env-only.
