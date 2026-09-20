# Design: Stages 12, 14, 15 (+ the I/O seams they depend on)

**Status:** design only. No implementation code in this document — signatures, data
shapes, algorithms in prose, thresholds with their basis, and a test strategy.

**Scope:** `analysis/phases.py` (Stage 12), `analysis/shot_type.py` (Stage 14),
`analysis/rubric.py` + `analysis/scoring.py` (Stage 15), plus the Stage 5 / Stage 10
seams these stages sit between and the orchestration that calls them.

**Method note.** `docs/PIPELINE.md` and the code in `backend/app/` have drifted. Where
they disagree, **the code wins** and the drift is called out. Every load-bearing claim
carries a `file:line` citation; they are collected in Part G.

---

## Part 0 — The nine findings, re-verified

All nine were re-checked against the working tree. **All nine hold.** Three are worse
than stated; the amendments are marked.

| # | Finding | Verdict | Citation |
|---|---|---|---|
| 1 | `weight_transfer_tu`, `balance_sway_tu`, `takeback_displacement_tu` unconditionally `None` | **CONFIRMED** | `metrics.py:550,559` / `:562,568` / `:682` |
| 2 | `reference_ranges_for(ShotType.FOREHAND_TOPSPIN.value)` returns `{}` | **CONFIRMED — and worse** | `enums.py:128` vs `references.py:69,87,105`; lookup at `references.py:131` |
| 3 | `extract_keypoints` expects **absolute** PTS, subtracts `window_start_s` itself | **CONFIRMED** | `extractor.py:210-228` → `sequence.py:47` |
| 4 | `phase_span` treats `end_frame < start_frame` — and only that — as unusable | **CONFIRMED** | `metrics.py:300` |
| 5 | `detect_ball_track` consumes BGR; MediaPipe consumes RGB | **CONFIRMED** | `detector.py:122,155,166` vs `extractor.py:146-147` |
| 6 | `NormalizedSequence` carries no pre-translation position | **CONFIRMED — recommend adding `origin_px`** | `internal.py:44-70`; discard site `normalize.py:280` |
| 7 | `feedback.MetricScore` sets `extra="forbid"` and renames `score_0_100` → `score` | **CONFIRMED** | `feedback.py:29,35` vs `responses.py:256` |
| 8 | `HandednessSource.HINT_OVERRODE_DETECTION` is never produced | **CONFIRMED** | `enums.py:81`; only `:149` and `:158` assign in `handedness.py` |
| 9 | PIPELINE's Stage 14 slice rule cannot be implemented | **CONFIRMED** | `PIPELINE.md:989` keys on `takeback_displacement_tu`, permanently `None` per (1) |

### Amendments — three findings are worse than the summary said

**(2) is not only a key-space mismatch; it is also a shape mismatch and a coverage
hole.** Three separate defects, and fixing only the first still ships a broken
scorecard:

1. **Key space.** `REFERENCE_RANGES` is keyed `"topspin" | "slice" | "flat"`
   (`references.py:69,87,105`). `ShotType` is keyed `"forehand_topspin" |
   "forehand_slice" | "backhand_one_handed" | "backhand_two_handed" | "serve" |
   "volley" | "unknown"` (`enums.py:128-134`). No key is shared, so
   `REFERENCE_RANGES.get(shot_type, {})` at `references.py:131` returns `{}` for
   **every** `ShotType` member.
2. **Shape.** `ReferenceRange` carries `minimum` and `maximum` only
   (`references.py:41-42`), and is `frozen=True, extra="forbid"`
   (`references.py:39`). Stage 15 needs `hard_min`, `hard_max`, `weight`, `category`
   and `direction` — `PIPELINE.md:1007` already specifies a richer `MetricBand`.
   `ReferenceRange` cannot grow those fields without editing the model.
3. **Coverage.** Even after rekeying, only **three** technique archetypes have
   numbers. `backhand_one_handed`, `backhand_two_handed`, `serve` and `volley` have
   no bands at all. See the product decision in **Part C.7** — this is the one with
   teeth.

**Why this is the most dangerous finding.** The failure is *silent and test-passing*.
A unit test written the obvious way — build a `SwingMetrics` with known values, call
scoring, assert it does not raise, assert `None` metrics are excluded — passes
perfectly against a rubric that returns `{}` for every shot type. `compare_to_reference`
returns `[]` rather than raising (`references.py:145-146`), `reference_ranges_for`
returns `{}` rather than raising (`references.py:131`), and a scorecard in which every
metric is `unavailable` is a *legal* `Scorecard` (`overall_score` is `float | None`,
`responses.py:276`). A builder wiring Stage 15 against `PIPELINE.md` alone would ship
a scorecard that is `None` on every clip ever uploaded, with green tests. **Part C.1
is mandatory, and the test in Part C.9 that pins it is not optional.**

**(1) is worse: two of the three dead metrics also have live reference bands.**
`references.py:81,83` (topspin), `:99,101` (slice), `:117,119` (flat) define bands for
`weight_transfer_tu` and `balance_sway_tu`. Those eleven-odd numbers are unreachable
code today — the metric is always `None` and `compare_to_reference` skips `None`
(`references.py:150-151`). They are a trap: they read as evidence the metrics work.

**(9) is worse: the rule is not merely unimplementable, it is *half*-implementable.**
`PIPELINE.md:989` is a conjunction — `swing_path_angle_deg < -10°` **AND**
`takeback_displacement_tu < 0.6`. An implementer who evaluates a conjunction with one
`None` operand by skipping the `None` term silently converts it to the single-term rule
`swing_path_angle_deg < -10°`, which is a *different and looser* classifier than the
spec, with no warning. See **Part B.5** for the replacement conjunct.

### Two additional defects found during re-verification

**(10) `racket_wrist_index` silently maps `UNKNOWN` to the right wrist**
(`handedness.py:171-173`). With Stage 8 at 0.05–0.36 confidence
(`PIPELINE.md:583`) and no hint, the pipeline does not degrade to "unknown" — it
quietly analyses the *right* wrist and returns normal-looking numbers. Stage 14 must
treat `Handedness.UNKNOWN` as a distinct state, not inherit this default.

**(11) `metrics.py:685-686` swallows every exception and returns a bare
`SwingMetrics()`** — all 18 fields `None`. Correct per the never-raises contract, but
it means "total internal failure" and "clip too occluded to measure" are
indistinguishable downstream, and the `warnings` list is returned *without* an entry
saying the exception path was taken. Stage 15 sees an all-`None` `SwingMetrics` either
way. Recommend Stage 13 append a warning on that path; noted in Part F.

---

## Part A — Stage 12: swing-phase segmentation

### A.1 Contract

```
segment_swing_phases(
    seq: NormalizedSequence,
    handedness: HandednessResult,
    contact: ContactDetection,
) -> tuple[SwingPhases | None, list[str]]
```

- **Module:** `backend/app/analysis/phases.py`. **PURE**: numpy and value objects
  only. No `cv2`, no `av`, no `mediapipe`, no I/O.
- **Never raises.** Wrapped like `compute_swing_metrics` (`metrics.py:685-686`), but
  — fixing defect (11) — the wrapper appends an explicit warning before returning
  `(None, warnings)`, so an internal failure is distinguishable from an honest
  "cannot segment".
- **Returns `None`, not a fabricated `SwingPhases`,** when the boundary signals do not
  support segmentation. The `tuple[..., list[str]]` shape matches
  `compute_swing_metrics` (`metrics.py:620-625`) and exists for the same reason:
  `SwingPhases` has nowhere to hold a warning (`responses.py:163-169`).
- Return type is `SwingPhases | None` and the downstream consumer already accepts it:
  `compute_swing_metrics(..., phases: SwingPhases | None = None)` (`metrics.py:624`),
  and `phase_span` short-circuits on `None` (`metrics.py:296-297`).

**Argument-order note.** `handedness` is a `HandednessResult`, not a `Handedness`,
matching `detect_contact_frame` (`contact.py:519-525`) and `compute_swing_metrics`
(`metrics.py:620-625`). Stage 12 needs `.confidence` as well as `.handedness` — see
A.6.

### A.2 Signals that actually exist

Every boundary below is defined on a signal already computed elsewhere in the pure
layer. Nothing here needs a new sensor.

| Signal | Source | Notes |
|---|---|---|
| `speed[t]` — racket-hand speed, TU/s | `contact.racket_hand_speed(seq, handedness)` (`contact.py:110`) | Same curve Stage 9 peaked on. Reuse, do not recompute. |
| `reliable[t]` — racket-wrist reliability | `contact.racket_wrist_reliable` (`contact.py:119`) | Visible at `i-1, i, i+1`. |
| `forward[t]` — signed forward displacement | `points[:, wrist, 0] * seq.swing_direction_sign` (`contact.py:548`) | Positive = toward target. |
| `timestamps_s` | `seq.timestamps_s` (`internal.py:64`) | Full-precision PTS. Phase times come from here, never from an assumed 1/30. |
| `contact.frame_index`, `contact.confidence` | `responses.py:71,78` | |

**Reusing `contact.racket_hand_speed` and `contact.forward` is a deliberate coupling.**
Stage 12's phases must be consistent with the contact frame Stage 9 chose. Computing a
second, independently-smoothed speed curve would let the two disagree about where the
peak is, and the phases would then bracket a contact frame that is not at their own
peak.

### A.3 Boundary rules

Following `PIPELINE.md:922-928`, with the resolutions the code forces. Let `C =
contact.frame_index`, `T` = frame count, `peak = argmax(speed)` over reliable frames,
`S_peak = speed[peak]`.

| Phase | Start | End | Basis |
|---|---|---|---|
| `ready` | `0` | `r - 1` | `r` = first frame where `speed >= READY_EXIT_FRACTION * S_peak`. `PIPELINE.md:924` (10%). |
| `takeback` | `r` | `b` | `b` = frame of maximum *backward* hand displacement before `C` = `argmin(forward[r : C+1])`. `PIPELINE.md:925`. |
| `forward_swing` | `b + 1` | `C - 2` | Ends where `contact` begins. `PIPELINE.md:926` says `C - 1`; see A.4. |
| `contact` | `C - 1` | `C + 1` | `PIPELINE.md:927`, `C ± 1`. |
| `follow_through` | `C + 2` | `f` | `f` = first frame after `C+1` where `speed < FOLLOW_EXIT_FRACTION * S_peak`, else `T - 1`. `PIPELINE.md:928` (15%). |

**Constants,** all inherited from `PIPELINE.md` and **none empirically validated
against this pipeline's own normalization**:

```
READY_EXIT_FRACTION   = 0.10      # PIPELINE.md:924
FOLLOW_EXIT_FRACTION  = 0.15      # PIPELINE.md:928
CONTACT_HALF_WIDTH    = 1         # PIPELINE.md:927, "contact frame ± 1"
MIN_PHASE_FRAMES      = 2         # PIPELINE.md:930, "< 2 frames" is degenerate
```

**The `C - 1` / `C - 2` discrepancy is resolved toward `C - 2`.**
`PIPELINE.md:926` ends `forward_swing` at `C - 1` and `:927` starts `contact` at
`C - 1`. Taken literally the two phases overlap on frame `C - 1`, which contradicts
`responses.py:166` ("Ordered, contiguous, non-overlapping"). Non-overlap is a
structural invariant a consumer can rely on; the exact `forward_swing` end frame is
not. `forward_swing` therefore ends at `C - 2`. This costs one frame of the
`forward_swing` window, which feeds `knee_flexion_min_deg` and `head_stillness_tu`
(`metrics.py:532,597`) — both are min/std-dev over a window and neither is sensitive
to one frame at the far edge from contact.

**Reliability masking.** `r`, `b` and `f` are searched over **reliable frames only**,
for the reason `PIPELINE.md:560` gives for Stage 9's peak search: an occluded wrist
produces a one-frame position jump whose velocity spike is taller than any real swing.
An unreliable frame crossing the 10% threshold would place `ready`'s exit
arbitrarily early. If no frame is reliable, segmentation returns `None` with a warning
— it does *not* fall back to the unrestricted search. Stage 9 can afford that fallback
because its sanity gates then speak up (`contact.py:561-574`); Stage 12 has no
equivalent gate, and a phase set is consumed as fact by five metrics.

### A.4 Degenerate phases — and how finding (4) forces the encoding

`PIPELINE.md:930`: a phase shorter than 2 frames "collapses to zero duration and adds a
warning". `SwingPhase` has inclusive integer `start_frame` / `end_frame`
(`responses.py:156-157`), and in an inclusive range **there is no way to write an empty
span except `end_frame = start_frame - 1`.**

That is exactly — and *only* — the condition `phase_span` already treats as unusable:

```python
# metrics.py:300
if int(phase.end_frame) < int(phase.start_frame):
    return None
```

**So the encoding is decided for us, and it is the right one.** A collapsed phase is
emitted as `start_frame = b`, `end_frame = b - 1`, `start_time_s = end_time_s =
timestamps_s[b]`, `duration_s = 0.0`. Every Stage 13 metric keyed on that phase then
receives `None` from `phase_span` and returns `None` — no fabricated value, no window
silently widened to the whole clip.

**The trap this avoids.** The tempting encoding is `start_frame == end_frame` for a
collapsed phase. `phase_span` accepts that (`end < start` is false) and returns a
**one-frame window**. `shoulder_hip_separation_deg` over a one-frame takeback returns a
real-looking number from a single frame (`metrics.py:342-355`); `head_stillness_tu`
over a one-frame forward swing returns `None` only because of a separate `< 2` guard
(`metrics.py:599`), and `knee_flexion_min_deg` has no such guard (`metrics.py:531-547`)
— it would happily report the knee angle at one instant as "minimum flexion during the
forward swing". **`end_frame = start_frame - 1` is load-bearing. Do not "fix" it to
`start_frame == end_frame` because it looks wrong.**

Contiguity is preserved under this encoding: define contiguity as
`next.start_frame == prev.end_frame + 1`. A collapsed phase at `b` has
`end_frame = b - 1`, so the next phase starts at `b` — which is where the collapsed
phase also starts. Ordering (`start_frame` non-decreasing) holds.

### A.5 When Stage 12 returns `None` outright

Segmentation returns `(None, warnings)` — never a partial or fabricated `SwingPhases` —
when:

1. `T < 5`. Five phases cannot be ordered across fewer than five frames even with
   collapses.
2. No reliable frame exists (`observed_fraction == 0`, cf. `contact.py:148`). The
   speed curve is not evidence of anything.
3. `S_peak <= 0` or non-finite. Every fraction-of-peak boundary is undefined.
4. `contact.sanity_flags` contains `"sequence_unusable"` — Stage 9's degenerate return
   (`contact.py:515`), which carries `frame_index = 0` (`contact.py:507`). Segmenting
   around frame 0 would produce a `ready` phase of negative length and a `takeback`
   searched over an empty slice. **Check this flag explicitly**; do not infer it from
   `confidence == 0.0`.
5. The ordering invariant cannot be satisfied even after collapsing — i.e. `C + 2 > T -
   1` and `C - 1 < 0` simultaneously, or `b` cannot be located because `r > C`.

Three or more phases collapsing is *not* on this list: that is a legal, honestly-degraded
`SwingPhases` and it should be returned, with a warning naming each collapsed phase.

### A.6 What degrades when `phases is None`

This is the most consequential fact in Part A, and it is worse than "some metrics go
missing".

Of the 18 `SwingMetrics` fields (`responses.py:184-201`):

| Class | Count | Fields |
|---|---|---|
| **Phase-dependent** → `None` without Stage 12 | 6 | `shoulder_hip_separation_deg` (takeback, `metrics.py:342`), `shoulder_turn_deg` + `hip_rotation_deg` (swing span, `metrics.py:365`), `knee_flexion_min_deg` + `head_stillness_tu` (forward swing, `metrics.py:532,597`), `tempo_ratio` (`metrics.py:683`) |
| **Permanently `None`** regardless | 3 | `weight_transfer_tu`, `balance_sway_tu`, `takeback_displacement_tu` (finding 1) |
| **Survives** without phases | 9 | `elbow_angle_at_contact_deg`, `wrist_lag_deg`, `contact_height_ratio`, `contact_point_forward_tu`, `peak_hand_speed_tu_s`, `swing_path_angle_deg`, `swing_plane_deviation_tu`, `follow_through_height_tu`, `wrist_separation_at_contact_tu` |

Cross-referenced against the Part C category map, `phases is None` means:

- **`preparation` has zero available metrics.** All three of its live metrics are
  phase-dependent and its fourth is permanently `None`. The category scores `None`.
- **`balance` has zero available metrics.** Two are permanently `None`; the remaining
  two are phase-dependent. The category scores `None`.
- **`swing_path` loses `tempo_ratio`**, keeping 2 of 3.
- `contact` (5 metrics) and `follow_through` (1 metric) are unaffected.

**So a `None` from Stage 12 silently deletes two of five scoring categories.** Under
the Part C.6 minimum-coverage rule that leaves exactly three categories — the floor —
so `overall_score` is still produced, but it is a score over 40% of the rubric
presented with the same visual weight as a full one. The `Scorecard` records this
honestly (`CategoryScore.score_0_100 = None`, `metrics_available = 0`,
`responses.py:268,271`), and the orchestrator must downgrade the analysis to
`AnalysisStatus.PARTIAL` (Part E.4).

**Stage 12 is therefore not optional in practice.** It is architecturally optional —
every consumer handles `None` — but a pipeline that routinely returns `None` here is a
pipeline whose `preparation` and `balance` scores never exist. Absent segmentation is
*supported*; fabricated segmentation is not; and a *habitually* absent segmentation is
a product failure, not a graceful degradation.

### A.7 Interaction with the Stage 9 `+1` residual

`PIPELINE.md:581` records an accepted, unexplained `+1` frame bias: contact is one frame
late on 4 of 5 hand-labelled events, and `PIPELINE.md:581` explicitly tells downstream
consumers to treat `frame_index` as carrying a possible systematic `+1`, with a warning
about "windows narrower than ~4 frames".

The `contact` phase is **3 frames wide** (`C ± 1`) — narrower than that warning's
threshold. If `C` is one frame late, the true contact sits at the window's leading edge
rather than its centre, and a 2-frame error puts it outside the window entirely.

**Decision: do not widen the contact phase, and do not correct the bias.** Widening to
`C ± 2` is a threshold retune on a curve where `PIPELINE.md:579` is standing evidence
that retuning changes nothing; subtracting 1 would encode an unresolved measurement
artefact as fact and would silently shift every phase boundary on the 1-in-5 clips where
the residual is absent. The bias is recorded, not absorbed
(`PIPELINE.md:581`). What Stage 12 *does* do is emit a warning when
`contact.confidence` is low, so the residual and the ambiguity travel together.

**The compounding risk, stated plainly.** No Stage 13 metric reads the `contact` phase
span (the contact-time metrics index `contact_frame` directly —
`metrics.py:401,418,448,466`), so the 3-frame window's fragility does not propagate
into the metrics. But `swing_path_fit` fits over `[C-6, C+3]` (`metrics.py:64-66,
483-486`), an asymmetric window. **A late `C` shifts that window forward into the
follow-through, where the hand is rising, which biases `swing_path_angle_deg`
positive — the same direction as a topspin prior.** Stage 14's topspin/slice rule
(Part B.5) reads exactly that sign. Two pressures push one way: a late contact and a
topspin-favouring threshold. This is called out again in Part B.5 and Part F.

### A.8 `tempo_ratio`

`tempo_ratio = takeback_duration_s / forward_swing_duration_s`
(`PIPELINE.md:930`), carried on `SwingPhases.tempo_ratio` (`responses.py:167-169`) and
passed straight through by Stage 13 (`metrics.py:683`).

Durations come from `timestamps_s` differences, not from frame counts divided by 30 —
Stage 5 is variable-frame-rate (`PIPELINE.md:165-166`) and `NormalizedSequence`
deliberately keeps full-precision PTS (`internal.py:52`).

`tempo_ratio` is `None` when `forward_swing` collapsed (`duration_s == 0.0`), by the
division-by-zero rule. It is **not** `0.0` and not `inf`. `SwingPhases.tempo_ratio` is
already `float | None` (`responses.py:167`), so this needs no schema change.

### A.9 Test strategy — Stage 12

Per CLAUDE.md ("Every math function in the analysis pipeline gets a unit test with
synthetic keypoint data. No exceptions."):

- **Synthetic canonical swing.** A hand trajectory with an explicit ready-hold, a
  backward excursion, a forward acceleration to a known peak, and a decaying
  follow-through. Assert all five phases, in order, contiguous, non-overlapping,
  covering `[0, T-1]`.
- **Contiguity/ordering invariant, property-style.** Over randomized `T`, `C`, and
  peak position: `phases[i].start_frame == phases[i-1].end_frame + 1` for all `i`;
  `phases[0].start_frame == 0`; `phases[-1].end_frame == T - 1`; `len(phases) == 5`.
- **Degenerate encoding.** Construct a clip where `contact` lands at frame 1 so `ready`
  and `takeback` must collapse. Assert the collapsed phases have
  `end_frame == start_frame - 1` and `duration_s == 0.0`, **and** assert
  `metrics.phase_span(result, THAT_PHASE) is None` — test the contract across the
  seam, not just the field values.
- **The trap test.** Assert explicitly that no emitted phase has
  `start_frame == end_frame` as its collapse representation. This is the regression
  guard for A.4.
- **`None` paths.** One test per A.5 clause, each asserting `result is None` and a
  non-empty warning. Include the `"sequence_unusable"` flag case built from
  `contact._empty_detection`'s actual shape (`contact.py:505-516`).
- **Never raises.** Feed `T=0`, `T=1`, all-NaN points, all-zero visibility,
  `contact.frame_index` out of range (negative, and `>= T`). Assert no exception and
  `result is None`.
- **VFR correctness.** Non-uniform `timestamps_s` (simulating the 30→24 fps drop of
  `PIPELINE.md:165`); assert `tempo_ratio` matches the PTS-derived ratio and **not**
  the frame-count ratio. These differ, which is the point of the test.
- **`tempo_ratio is None`** when `forward_swing` collapses — asserted as `is None`, not
  as `== 0.0`.

---

## Part B — Stage 14: shot-type inference from technique

### B.1 Contract

```
infer_shot_type(
    metrics: SwingMetrics,
    seq: NormalizedSequence,
    handedness: HandednessResult,
    contact: ContactDetection,
    phases: SwingPhases | None,
) -> ShotTypeInference
```

- **Module:** `backend/app/analysis/shot_type.py`. **PURE.**
- **Never raises.** Ambiguous → `ShotType.UNKNOWN` (`PIPELINE.md:978`).
- **No `BallTrack` parameter, and no ball argument of any kind.** CLAUDE.md:8 — "Shot
  type is INFERRED FROM TECHNIQUE, not from the ball." `PIPELINE.md:982` makes this
  structural rather than conventional: the absence of the parameter is the
  enforcement. It is also why Part E orders Stage 14 *before* Stages 10/11 — a
  function that runs before ball detection cannot consult a ball track even by
  accident.
- `phases` is `SwingPhases | None` — Stage 14 must work without segmentation, and
  degrade explicitly where it cannot.

### B.2 What can actually discriminate, and what cannot

The honest summary first, because the rest of Part B depends on it:

| Distinction | Separable from pose alone? | Why |
|---|---|---|
| **Serve vs ground stroke** | **Yes, reliably** | `contact_height_ratio` above shoulder is a large, view-insensitive, unambiguous signal. |
| **Two-handed vs one-handed** | **Yes, reliably** | `wrist_separation_at_contact_tu` is a direct distance between two tracked landmarks, view-insensitive, and **independent of handedness detection** — the one rule Stage 8's weakness cannot corrupt. |
| **Volley vs full swing** | **Probably** | Short path + short duration is a real signal, but it needs `phases` for duration and `HandednessResult.racket_hand_path_length_tu` for path — the latter is handedness-dependent. |
| **Topspin vs slice** | **Weakly** | `swing_path_angle_deg` sign is the intended signal and is view-stable (`metrics.py:98`, `PIPELINE.md:959`), but its window is contaminated by the Stage 9 `+1` residual (A.7). |
| **Forehand vs backhand** | **NO — not reliably** | See below. This is the weakest link in Stage 14. |

**Forehand vs backhand is not reliably separable from this input, and the design must
say so rather than pretend.** `PIPELINE.md:988` proposes "sign of `wrist_x_at_contact`
relative to mid-hip, combined with detected handedness. Dominant side → forehand;
across-body → backhand." Three independent problems:

1. **The available metric is the wrong quantity.** `contact_point_forward_tu` is
   `wrist_x * swing_direction_sign` (`metrics.py:470`) — signed *toward the target*,
   not signed *to the dominant side*. At contact, a forehand and a backhand are both
   forward of the mid-hip. The sign of this metric is near-useless for the
   distinction; it is positive for both, and `wrist_behind_mid_hip` is a Stage 9
   *sanity failure* flag (`contact.py:563-564`), i.e. a negative value indicates a bad
   detection, not a backhand.
2. **The discriminating axis is the one the projection collapsed.** "Dominant side vs
   across body" is a lateral/depth distinction. `NormalizedSequence.points` is 2D
   (`internal.py:61`, `(T, 33, 2)`); the third axis is gone. Recovering the distinction
   from 2D needs to know where the camera is — and
   `PoseQuality.estimated_camera_view` is hardcoded `CameraView.UNKNOWN`
   (`normalize.py:325`), so **every camera-view gate in this pipeline is inert**
   (`PIPELINE.md:598`).
3. **It compounds Stage 8's weakness multiplicatively.** The rule needs handedness,
   and Stage 8 scores 0.05–0.36 independent confidence and picks the wrong hand on
   more than half a 7-clip batch (`PIPELINE.md:583`). `swing_direction_sign` is itself
   derived from the longer-path wrist (`normalize.py:290-293`) — the *same* comparison
   Stage 8 gets wrong. A wrong hand flips the racket wrist *and* can flip the sign,
   and the rule multiplies the two.

**Design response.** Forehand/backhand is scored as **soft evidence with a hard
confidence ceiling**, never as a decisive gate:

- The proxy used is the racket wrist's position at contact **relative to the
  shoulder-line midpoint**, projected onto the shoulder line — i.e. how far along the
  torso's own lateral axis the hand sits. This at least uses a body-intrinsic axis
  rather than the target axis. All four landmarks are already available
  (`normalize.py:29-34`).
- Its contribution to `class_scores` is capped at `FOREHAND_BACKHAND_MAX_EVIDENCE =
  0.25` — it can tip a decision between otherwise-tied classes but can never carry one
  past the 0.45 acceptance gate alone.
- **When `handedness.confidence < HINT_CONFIDENCE_FLOOR` (0.5, `handedness.py:26`)
  *and* `handedness.source == DETECTED`** — i.e. a weak detection with no hint
  rescuing it — the forehand/backhand evidence contributes **zero**, and an evidence
  string says so. Note that the hint *does* rescue this: per `PIPELINE.md:583`, a
  present and correct `handedness_hint` is a **precondition** for trusting anything
  handedness-sensitive, not a convenience.
- **When `handedness.handedness == Handedness.UNKNOWN`**, the evidence contributes
  zero. Per defect (10), `racket_wrist_index` would otherwise silently analyse the
  right wrist (`handedness.py:171-173`) — Stage 14 must not inherit that default as if
  it were a measurement.

**Consequence, stated rather than buried:** on a clip with no handedness hint and a
weak Stage 8 read, Stage 14 can distinguish serve / two-handed / volley / topspin vs
slice, but **cannot distinguish a forehand from a one-handed backhand**, and will
correctly return `unknown` rather than guess. That is the intended behaviour.

### B.3 Evaluation structure

`PIPELINE.md:984` calls for "discriminating features, in evaluation order", but
`ShotTypeInference` requires `class_scores` summing to 1.0 (`responses.py:240`) — a
first-match-wins cascade cannot produce calibrated scores. **Resolution: accumulate
weighted evidence for all six real classes, then apply acceptance gates.** The
"evaluation order" is preserved as *evidence priority* (serve evidence is weighted
highest and suppresses ground-stroke evidence when it fires), not as early exit.

1. Each rule contributes signed evidence to one or more of the six real classes:
   `forehand_topspin`, `forehand_slice`, `backhand_one_handed`, `backhand_two_handed`,
   `serve`, `volley`.
2. A rule whose input metric is `None` contributes **nothing** — it does not
   contribute zero-as-a-value, and it does not silently drop a conjunct (see B.5). It
   appends an evidence string naming the metric as unavailable.
3. Raw evidence is normalized over the six classes to sum to 1.0 → `class_scores`.
   `unknown` is **not** a member of `class_scores`; it is an outcome, not a class.
4. Acceptance gates (`PIPELINE.md:994`): `top_score < TOP_SCORE_MIN (0.45)` **or**
   `top_score - second_score < MARGIN_MIN (0.15)` → `shot_type = UNKNOWN`.
5. `confidence` = `top_score` — the honest number — **even when `shot_type` is
   `UNKNOWN`**. A rejected top score of 0.41 is reported as 0.41, not as 0.0 and not
   as `1 - 0.41`.

**The no-evidence path is distinct.** If *no* rule fired — every discriminating metric
was `None` — normalizing zeros would produce a uniform `1/6 ≈ 0.167` per class, which
reads as a weak measurement when in fact nothing was measured. Instead: `class_scores`
is all-zeros, `confidence = 0.0`, `shot_type = UNKNOWN`, and evidence carries a single
string naming the condition. **This deliberately violates the "sum to 1.0" note in
`responses.py:240`** in the one case where summing to 1.0 would be a fabrication.
Flagged in Part F as a reviewer check; the alternative is to relax that docstring.

### B.4 Rules 1–3

**Rule 1 — Serve.** `PIPELINE.md:986`.
- Primary: `contact_height_ratio > SERVE_HEIGHT_RATIO (1.30)`. From
  `PIPELINE.md:986`. Wrist above the shoulder line; `contact_height_ratio` is
  view-insensitive (`metrics.py:95`) and its axis convention is documented and
  correct (`metrics.py:441-446`).
- Corroborating: racket wrist y above nose y at contact (both landmarks available,
  `normalize.py:28`; y is positive up, `metrics.py:11`).
- **Dropped:** "trunk extended". `PIPELINE.md:986` names it but no `SwingMetrics` field
  measures trunk extension and inventing one here would be a new metric smuggled into
  Stage 14. Recorded in Part F.
- Serve evidence is weighted highest (`SERVE_EVIDENCE_WEIGHT = 1.0`) and, when it
  fires, suppresses volley evidence — a serve satisfies "contact forward of the body"
  trivially.

**Rule 2 — Two-handed.** `PIPELINE.md:987`.
- `wrist_separation_at_contact_tu < TWO_HANDED_SEPARATION_TU (0.35)`, **sustained
  across `contact ± 3`**. The sustain requirement (`PIPELINE.md:987`) rejects
  incidental hand proximity.
- `SwingMetrics` carries only the value *at* contact (`metrics.py:605-614`), so the
  sustain check is computed inside Stage 14 from `seq` directly, reusing
  `metrics.frame_is_visible` and the same two landmark indices. Frames in the window
  where either wrist is not visible are **excluded from the sustain count, not counted
  as failures**; if fewer than `MIN_SUSTAIN_FRAMES (4)` of the 7 are visible, the rule
  contributes nothing and says so.
- **This is the most trustworthy rule in Stage 14.** It uses both wrists symmetrically,
  so it is immune to Stage 8 picking the wrong hand, and it is view-insensitive
  (`metrics.py:106`). Note it discriminates *two-handed vs one-handed*, which combined
  with the weak forehand/backhand axis means `backhand_two_handed` is reachable at
  decent confidence while `backhand_one_handed` largely is not.
- `wrist_separation_at_contact_tu` is deliberately **not** a scored metric
  (`references.py:64-67`) — it is a classification input only. Stage 15 must not score
  it; see C.2.

**Rule 3 — Forehand vs backhand.** Soft evidence only, per B.2. Capped at 0.25,
zeroed on weak-detected or unknown handedness.

### B.5 Rule 4 — Slice vs topspin, and the replacement for the dead conjunct

`PIPELINE.md:989`: `swing_path_angle_deg < -10°` **AND** `takeback_displacement_tu <
0.6`. The second conjunct is permanently `None` (`metrics.py:682`), so **the rule as
specified cannot be evaluated** (finding 9).

**Rejected: drop the conjunct.** Per amendment (9), evaluating the conjunction with one
`None` operand by skipping it silently converts the rule into the looser single-term
classifier `swing_path_angle_deg < -10°`. Given A.7 — the Stage 9 `+1` residual biases
`swing_path_angle_deg` *positive*, away from slice — a single-term rule would
under-detect slice for a reason unrelated to the player's technique.

**Rejected: define `takeback_displacement_tu`.** It could be computed (max backward
racket-wrist excursion during `takeback`), but the Stage 13 metric table
(`PIPELINE.md:949-967`) never defines it, and `metrics.py:636-638` explicitly declines
to guess a definition precisely so that Stage 14's rule is not silently fed an invented
number. Inventing it in Stage 14 would defeat that deliberate refusal. **If it is
wanted, it belongs in Stage 13 with a spec'd definition and its own unit test, not
here.**

**Adopted: `follow_through_height_tu` as the replacement conjunct.**

```
slice evidence  iff  swing_path_angle_deg < SLICE_PATH_ANGLE_DEG (-10.0)
                AND  follow_through_height_tu < SLICE_FOLLOW_THROUGH_TU (0.20)
```

Basis:
- It is a **measurable** metric (`metrics.py:571-591`) — the whole problem with the
  original conjunct.
- It is **view-insensitive** (`metrics.py:102`, `PIPELINE.md:963`), so it does not
  reintroduce a camera-view dependency into a rule whose other term was chosen for
  view-stability.
- It is **mechanically the right signal**: a slice finishes low and forward, a topspin
  finishes high. The existing reference tables already encode exactly this belief —
  slice `[0.0, 0.45]` vs topspin `[0.30, 0.90]` (`references.py:100,82`).
- It is **independent of the contaminated window**: `follow_through_height_tu` is a max
  over all frames after contact (`metrics.py:582-583`), so a one-frame contact error
  barely moves it, whereas `swing_path_angle_deg`'s 10-frame fit window shifts
  wholesale. Pairing a window-sensitive term with a window-insensitive one is the
  point.

**The `0.20` threshold is new and is not grounded in `PIPELINE.md` or in any
real-footage measurement.** It sits below the slice band's midpoint (`[0.0, 0.45]`,
`references.py:100`) and below the topspin band's floor (`0.30`,
`references.py:82`), which is the reasoning — but those bands are themselves marked
`PLACEHOLDER - needs empirical tuning` (`references.py:82,100`). **This is the single
weakest threshold in this design.** Part F.

**Both terms must be present.** If either `swing_path_angle_deg` or
`follow_through_height_tu` is `None`, the topspin/slice axis contributes **nothing** to
either class, and an evidence string names the missing metric. The result is then
typically a forehand/backhand call with no spin qualifier, which fails the 0.15 margin
gate between `forehand_topspin` and `forehand_slice` and lands on `unknown` — the
correct outcome.

**The compounding pressure, restated (A.7).** A late contact shifts the
`[C-6, C+3]` fit window (`metrics.py:64-66`) into the rising follow-through, biasing
`swing_path_angle_deg` positive; the `-10°` threshold already requires a clearly
negative angle. Both pressures push away from `slice`. Expect Stage 14 to
**under-report slice**, and validate in that direction first.

### B.6 Rule 5 — Volley

`PIPELINE.md:990`: total hand path length `< 1.0 TU` AND swing duration `< 0.35 s` AND
contact forward of the body.

All three terms are obtainable, from outside `SwingMetrics`:

- **Path length:** `handedness.racket_hand_path_length_tu` (`responses.py:63`) — a real,
  populated field. Note it is computed over the *whole sequence*
  (`handedness.py:102,165`), not over the swing span, so on a long clip containing
  idle motion it over-reads and volleys are under-detected. Prefer recomputing over
  `swing_span(phases)` when `phases` is available, and fall back to the
  `HandednessResult` value with an evidence string noting the wider window when it is
  not.
- **Swing duration:** `takeback.start_time_s → follow_through.end_time_s` from
  `phases`. **Requires `phases`.** When `phases is None`, this term contributes
  nothing and volley evidence is capped at the remaining two terms — which, being a
  path-length term and a near-universal "contact forward" term, is not enough to clear
  0.45 alone. **So `phases is None` effectively disables volley detection.** Worth
  stating: it is another cost of A.6 that does not show up in the metric count.
- **Contact forward:** `contact_point_forward_tu > 0`. Near-universal at contact (a
  negative value raises Stage 9's `wrist_behind_mid_hip` sanity flag,
  `contact.py:563-564`), so this term is weighted low — it is close to a tautology and
  should not be allowed to carry volley evidence.

Volley evidence is suppressed when serve evidence fires (B.4).

### B.7 Evidence strings

`PIPELINE.md:992`: deterministic, Python-generated, human-readable — e.g. `"wrist
separation 0.21 TU < 0.35 threshold → two-handed"`. These are what Gemini is asked to
**paraphrase**, never to derive (`PIPELINE.md:992`, CLAUDE.md:12-13). They flow to
`FeedbackInput.shot_type_evidence` (`feedback.py:74`).

Requirements:
- One string per rule that **fired**, one per rule that was **skipped for a `None`
  input** (naming the metric), one for each confidence suppression in B.2.
- Numbers formatted at fixed precision from the actual values. Never a number that is
  not in the payload — `NumericGuardReport` (`feedback.py:109-121`) exists to catch
  Gemini inventing numbers, and evidence strings are a channel by which an invented
  number could enter *legitimately-looking* input.
- **Never include a ball-derived quantity.** Structurally guaranteed: no ball input.

### B.8 Test strategy — Stage 14

- **One synthetic clip per class**, hand-built to satisfy exactly that class's rule,
  asserting `shot_type` and `confidence >= 0.45`.
- **`unknown` is first-class and gets more tests than any single class:** ambiguous
  clip (top < 0.45); near-tie clip (margin < 0.15); all-discriminating-metrics-`None`
  clip (asserting `class_scores` all-zero and `confidence == 0.0`, per B.3's
  no-evidence path — **not** uniform 0.167).
- **The `None`-conjunct regression test.** A clip with `swing_path_angle_deg = -25.0`
  and `follow_through_height_tu = None`. Assert the topspin/slice axis contributed
  nothing and the result did **not** classify as slice. This is the guard against
  amendment (9) reappearing with a different conjunct.
- **Ball-independence, structural.** Assert `infer_shot_type` has no parameter whose
  annotation mentions a ball type (introspect `inspect.signature`), and that
  `app.analysis.shot_type` does not import from `app.ball`. A test that reads the
  import graph, not the behaviour — because the behaviour cannot be tested for the
  absence of an influence.
- **Handedness degradation.** Identical clip evaluated at
  `handedness.confidence = 0.9 / source=DETECTED` vs `0.2 / source=DETECTED` vs
  `0.2 / source=USER_HINT` vs `handedness=UNKNOWN`. Assert the forehand/backhand
  evidence is present, zeroed, present, and zeroed respectively.
- **Two-handed sustain.** A clip where wrists are momentarily close at contact but
  separate at `C ± 3` — assert it does **not** classify two-handed. And a clip where
  wrists are close but occluded at 4 of 7 window frames — assert the rule abstains
  rather than firing on 3 frames.
- **Never raises.** Empty `SwingMetrics()`, `phases=None`, `contact` from
  `_empty_detection`, `T=0` sequence.

---

## Part C — Stage 15: the scorecard

### C.1 Resolving finding (2) — the rubric table

**Decision: a new `RUBRIC_V1` in `backend/app/analysis/rubric.py`, keyed by `ShotType`,
with a richer `MetricBand`. `feedback/references.py`'s `REFERENCE_RANGES` becomes a
derived projection of it.**

Rationale, against the alternatives:

- **Rekey `REFERENCE_RANGES` to `ShotType` in place** — fixes defect (2.1) only.
  `ReferenceRange` still carries no `hard_min`/`hard_max`/`weight`/`category`
  (`references.py:41-42`) and is `frozen=True, extra="forbid"` (`references.py:39`), so
  Stage 15's ramp and category grouping have nowhere to live. Rejected.
- **Add a `ShotType → ReferenceShotType` mapping function** — the smallest change, but
  it hard-codes "a one-handed backhand is scored as a topspin forehand", which is a
  coaching claim smuggled in as a lookup table. Rejected as a silent product decision.
- **Put the rubric in `feedback/`** — wrong layer. Stage 15 is `analysis/`
  (`PIPELINE.md:1002`, CLAUDE.md repo layout: `analysis/` is the pure math,
  `feedback/` is Gemini prompt construction). The rubric is coaching *math*, not
  prompt material.

**Shape** (`PIPELINE.md:1007` already specifies it):

```
MetricBand:  ideal_min: float
             ideal_max: float
             hard_min:  float
             hard_max:  float
             weight:    float          # within its category, pre-renormalization
             category:  ScoreCategory
             direction: <see C.4>

RUBRIC_V1: dict[ShotType, dict[str, MetricBand]]
```

**Two tables must not be allowed to drift.** Having both `RUBRIC_V1` and
`REFERENCE_RANGES` recreates finding (2) in a new form one refactor later.
**Recommendation: make `REFERENCE_RANGES` a derived projection** — built at import time
from `RUBRIC_V1` by taking `(ideal_min, ideal_max)` per metric and rekeying to
`ShotType`. `compare_to_reference` and `reference_ranges_for` keep their signatures
(`references.py:126,134`) and their `str` key type, so nothing that calls them changes.
Single source of truth; drift becomes structurally impossible.

If that edit to `references.py` is judged out of scope, the **minimum** acceptable
substitute is a unit test asserting the two tables agree on every shared
`(shot_type, metric)` pair — and that test must be written in the same change, not
deferred.

**Rekeying obsoletes `ReferenceShotType`** (`references.py:23-28`). Its three members
become the archetype vocabulary described in C.7, or are deleted. Either way, the
`"topspin" | "slice" | "flat"` strings must stop being lookup keys.

### C.2 Metric → category mapping

Categories are the five `ScoreCategory` members (`enums.py:137-142`,
`PIPELINE.md:1011`). Weights are within-category, pre-renormalization.

| Category | Metric | Weight | Notes |
|---|---|---|---|
| `preparation` | `shoulder_hip_separation_deg` | 0.40 | View-insensitive (`metrics.py:90`); the strongest preparation signal. Needs `takeback`. |
| | `shoulder_turn_deg` | 0.30 | View-sensitive (`metrics.py:91`). Needs swing span. |
| | `hip_rotation_deg` | 0.30 | View-sensitive (`metrics.py:92`). Needs swing span. |
| | ~~`takeback_displacement_tu`~~ | — | **Excluded from the rubric entirely.** Permanently `None` (finding 1). See C.3. |
| `contact` | `contact_height_ratio` | 0.25 | View-insensitive (`metrics.py:95`). |
| | `contact_point_forward_tu` | 0.25 | View-sensitive (`metrics.py:96`). |
| | `elbow_angle_at_contact_deg` | 0.20 | View-sensitive (`metrics.py:93`). |
| | `wrist_lag_deg` | 0.15 | View-sensitive (`metrics.py:94`); explicitly a proxy (`PIPELINE.md:955`). |
| | `peak_hand_speed_tu_s` | 0.15 | Copied from Stage 9, never recomputed (`metrics.py:671-673`). |
| `swing_path` | `swing_path_angle_deg` | 0.45 | Sign is view-stable (`metrics.py:98`). |
| | `swing_plane_deviation_tu` | 0.35 | View-sensitive (`metrics.py:99`). |
| | `tempo_ratio` | 0.20 | Needs `phases` (`metrics.py:683`). |
| `balance` | `knee_flexion_min_deg` | 0.55 | Needs `forward_swing`. |
| | `head_stillness_tu` | 0.45 | Needs `forward_swing`. |
| | ~~`weight_transfer_tu`~~ | — | **Excluded.** Permanently `None`. |
| | ~~`balance_sway_tu`~~ | — | **Excluded.** Permanently `None`. |
| `follow_through` | `follow_through_height_tu` | 1.00 | **The only metric in its category.** |

**`wrist_separation_at_contact_tu` is not scored** — it is a Stage 14 classification
input, not a coachable band, and `references.py:64-67` already documents the exclusion.
**`ball_speed_mph` is not scored** — `PIPELINE.md:1013`, `references.py:66-67`, and
`MetricUnit` has no `mph` member by design (`enums.py:22-27`).

**Two structural fragilities to state now:**
- **`follow_through` is a single-metric category.** If `follow_through_height_tu` is
  `None`, the entire category is `None` — there is no redundancy. And that same metric
  is a conjunct in Stage 14's slice rule (B.5), so one measurement carries both a
  classification and a whole scoring category.
- **`balance` has two live metrics, both requiring `phases`.** Combined with
  `preparation`'s three, this is the mechanism behind A.6: `phases is None` deletes two
  categories.

### C.3 Permanently-`None` metrics are excluded from the rubric, not banded

`weight_transfer_tu`, `balance_sway_tu` and `takeback_displacement_tu` get **no
`MetricBand` and no `MetricScore` entry** — they do not appear in
`Scorecard.metrics` at all.

Rationale: including them would put three permanent `verdict = unavailable` rows in
every scorecard ever produced, inflate `CategoryScore.metrics_total`
(`responses.py:272`) — making `preparation` read "1 of 4 measured" when the honest
statement is "1 of 3" — and imply to the client that these are measurements that
*could* arrive on a better clip. They cannot, on any clip, by construction
(`metrics.py:109-118`). A metric that is structurally unmeasurable is not an
unavailable measurement; it is not a metric of this pipeline yet.

**Corollary: delete the six dead reference bands** at `references.py:81,83,99,101,117,119`
when rekeying (C.1), or the derived projection will recreate them. Per amendment (1)
they are a trap.

**This is reversible the moment `origin_px` lands** (Part D.4): `weight_transfer_tu` and
`balance_sway_tu` become computable, get bands, and join `preparation` and `balance`
respectively. Note the epistemic caveat in D.4 before doing so.

### C.4 `MetricBand` → `score_0_100`

Piecewise-linear ramp per `PIPELINE.md:1009`:

```
value in [ideal_min, ideal_max]        → 100.0
value in [hard_min, ideal_min)         → linear 0 → 100 across [hard_min, ideal_min]
value in (ideal_max, hard_max]         → linear 100 → 0 across [ideal_max, hard_max]
value <= hard_min or >= hard_max       → 0.0
value is None                          → score_0_100 = None, verdict = unavailable
```

`verdict` (`enums.py:15-19`): `low` below `ideal_min`, `ideal` inside, `high` above
`ideal_max`, `unavailable` when `value is None`.

**`direction`** marks metrics where only one side is a fault —
`swing_plane_deviation_tu` and `head_stillness_tu` have `minimum=0.0` bands
(`references.py:79,84`) and zero is *perfect*, not "low". For these,
`direction = "lower_is_better"`: the ramp is flat 100 below `ideal_max` and only the
upper arm decays, and the verdict is never `low`. Without this, a perfectly still head
scores `low` and generates coaching advice to move it more.

**`hard_min`/`hard_max` are new numbers with no source.** `PIPELINE.md:1009` requires
them; `ReferenceRange` has never carried them. Proposed default derivation for the
initial table — `hard_min = ideal_min - (ideal_max - ideal_min)` and symmetrically for
`hard_max`, clamped at a physical floor where one exists (`0.0` for TU distances,
`0°`/`180°` for joint angles) — i.e. **score reaches 0 one full ideal-band-width outside
the band.** This is a defensible shape, not a measurement. Part F.

**Rounding.** `score_0_100` to one decimal, matching `overall_score`
(`PIPELINE.md:1011`).

### C.5 How `None` propagates — the explicit rules

This is the constraint the whole design turns on: *"Never a default, never a fabricated
number… A category with no measurable metrics must not silently score 0."*

1. **Metric level.** `value is None` → `verdict = unavailable`, `score_0_100 = None`
   (`responses.py:256` documents exactly this: "None iff verdict == unavailable"). The
   metric still appears in `Scorecard.metrics` with its `weight` and `category`, so the
   client can show what was not measurable. Never `0.0` — `0.0` is a legitimate measured
   value for `contact_point_forward_tu`, `swing_path_angle_deg` and `head_stillness_tu`
   (`metrics.py:23-28`).
2. **Within a category.** Unavailable metrics are excluded and their weight is
   **redistributed proportionally across the remaining available metrics in the same
   category** (`PIPELINE.md:1009`, `:943`). Redistribution is within-category only —
   never across categories.
3. **Category with zero available metrics.** `CategoryScore.score_0_100 = None`,
   `metrics_available = 0`. **Explicitly not `0.0`.** The renormalization in (2) has a
   zero denominator here; the code must branch on `metrics_available == 0` *before*
   dividing, not rely on a guarded division returning something plausible. This is the
   single most likely place for a silent `0.0` to be born.
4. **Overall.** `overall_score` = weight-normalized mean over categories **whose score
   is not `None`**, with category weights renormalized over exactly those categories,
   rounded to one decimal (`PIPELINE.md:1011`).
5. **No category has a score** → `overall_score = None` (`PIPELINE.md:1005`), which
   `Scorecard` already permits (`responses.py:276`). The `Scorecard` is still returned
   — populated `categories` and `metrics`, all `None` — never omitted.
6. **Minimum coverage** — see C.6.

### C.6 Minimum coverage: when a score is too thin to report

Rule (4) alone has a failure mode with teeth. A clip where only `contact` is measurable
produces `overall_score` = the `contact` score, renormalized to weight 1.0 — a number
presented to the user as an overall swing assessment that is in fact one category out
of five. The arithmetic is impeccable and the result is misleading.

**Proposed rule:** `overall_score` is emitted only when **at least
`MIN_SCORED_CATEGORIES = 3` of 5 categories have a non-`None` score**. Below that,
`overall_score = None`, every `CategoryScore` is still populated and returned, and the
orchestrator marks the analysis `PARTIAL`.

`3` is chosen because A.6 shows `phases is None` leaves exactly three categories — so
the threshold admits the common degradation while rejecting the pathological one. It is
**a new number with no empirical basis**; Part F.

**The counter-argument, stated fairly:** a user who films a legitimate swing from a bad
angle gets no score at all, which is a worse product experience than a slightly
over-confident number. The counter to *that* is that `overall_score` is persisted
(`AnalysisListItem.overall_score`, `responses.py:459`) and becomes the user's progress
history — a number computed over a varying subset of categories is not comparable
across clips, so the history chart silently compares unlike things. **This is a product
decision, not an engineering one, and it should be made deliberately.**

### C.7 The product decision with teeth: four shot types have no rubric

**Stated plainly, because it is easy to miss and expensive to discover late: with the
reference tables as they exist today, filming a backhand, a serve or a volley produces
an analysis with no score at all.**

Only three technique archetypes have numbers (`references.py:69,87,105`). `ShotType`
has seven members (`enums.py:127-134`). Rekeying (C.1) maps at best:

| `ShotType` | Bands available today |
|---|---|
| `forehand_topspin` | yes — from the `topspin` archetype |
| `forehand_slice` | yes — from the `slice` archetype |
| `backhand_one_handed` | **none** |
| `backhand_two_handed` | **none** |
| `serve` | **none** |
| `volley` | **none** |
| `unknown` | **none** |

With no bands, every metric is `unavailable`, every category is `None`, and
`overall_score` is `None` (C.5 rule 5). A user filming a backhand — a substantial share
of real usage — receives a scorecard that is entirely empty, and a history row with a
null score (`responses.py:459`).

**Recommendation: a shot-type-agnostic `BASE_BANDS` fallback.** `PIPELINE.md:994`
already establishes the principle for `unknown` — "scoring falls back to a
shot-type-agnostic rubric subset". Extend it to any `ShotType` with no table of its
own.

`BASE_BANDS` contains only the metrics whose bands are **genuinely stable across the
three existing archetypes** — i.e. where the tables already agree, so using them for an
unbanded shot type asserts nothing new:

| Metric | topspin | slice | flat | Stable? |
|---|---|---|---|---|
| `shoulder_turn_deg` | 80–110 | 70–100 | 75–105 | yes → base |
| `hip_rotation_deg` | 35–60 | 25–45 | 30–55 | yes → base |
| `knee_flexion_min_deg` | 135–165 | 130–160 | 135–165 | yes → base |
| `swing_plane_deviation_tu` | 0–0.08 | 0–0.07 | 0–0.06 | yes → base |
| `head_stillness_tu` | 0–0.06 | 0–0.05 | 0–0.06 | yes → base |
| `tempo_ratio` | 1.5–3.0 | 1.5–3.0 | 1.5–3.0 | **identical** → base |
| `shoulder_hip_separation_deg` | 40–65 | 30–50 | 35–60 | marginal → base, widened to 30–65 |
| `swing_path_angle_deg` | 15–35 | −30–−8 | 0–14 | **no — disjoint** → excluded |
| `contact_height_ratio` | 0.75–1.05 | 0.55–0.95 | 0.85–1.15 | no → excluded |
| `follow_through_height_tu` | 0.30–0.90 | 0.0–0.45 | 0.20–0.70 | no → excluded |
| `peak_hand_speed_tu_s` | 6–12 | 4–9 | 6.5–13 | no → excluded |
| `elbow_angle_at_contact_deg` | 120–160 | 140–175 | 130–170 | no → excluded |
| `wrist_lag_deg` | 15–45 | 5–25 | 10–35 | no → excluded |
| `contact_point_forward_tu` | 0.25–0.60 | 0.20–0.55 | 0.30–0.65 | marginal → excluded (conservative) |

(Source rows: `references.py:70-85`, `:88-103`, `:106-121`.)

Under `BASE_BANDS`, an unbanded shot type scores `preparation` (all 3), `swing_path`
(2 of 3 — `swing_path_angle_deg` excluded), and `balance` (both) — exactly 3 of 5
categories, clearing C.6's floor. `contact` and `follow_through` score `None`, honestly,
because nobody has written down what a good one-handed-backhand contact height is.

**This is a real product decision and should be made explicitly, not inherited from
this document.** The options are: (a) `BASE_BANDS` as above — a partial score for every
shot type, with `contact` and `follow_through` blank on four of seven; (b) no fallback —
`overall_score = None` for backhands, serves and volleys at launch, which at least does
not pretend; (c) author real bands for the missing four before launch, which is the
correct answer and the expensive one. **(a) is recommended as the launch position with
(c) as the committed follow-up**, because (b) means a backhand-hitting user's entire
history is null.

### C.8 `priority_metric_names`, the feedback projection, and `rubric_version`

**`priority_metric_names`** (`feedback.py:84`) — the metrics Gemini is steered to coach.
Selection:

1. Consider only metrics with `verdict in {low, high}` — available and outside band. A
   metric scoring 100 is not an improvement area, and an `unavailable` metric cannot be
   coached (Gemini has no number for it).
2. Rank by `score_0_100` ascending (worst first), tie-broken by category weight ×
   metric weight descending.
3. Apply a **view-sensitivity demotion**: metrics with `view_sensitive = True`
   (`metrics.METRIC_VIEW_SENSITIVE`, `metrics.py:89-107`) sort after equally-bad
   view-insensitive metrics. `PIPELINE.md:945` calls this flag "the load-bearing
   mitigation" for camera-angle error; since `estimated_camera_view` is always
   `UNKNOWN` (`normalize.py:325`) we can never confirm the view was good, so a
   view-sensitive metric should not become the user's top coaching priority over a
   view-stable one that is equally out of band. **Demotion, not exclusion** — 9 of 17
   entries in that map are `True` (`metrics.py:89-107`), so excluding them could empty
   the list.
4. Cap at **3** — `Improvement.priority` is `ge=1, le=3` (`feedback.py:91`), so a fourth
   has nowhere to go.
5. Empty list is legal and correct when everything measurable is inside band.

**The feedback projection — finding (7).** `feedback.MetricScore` sets
`extra="forbid"` (`feedback.py:29`) and names the field `score` (`feedback.py:35`); the
wire model names it `score_0_100` and additionally carries `weight` and `category`
(`responses.py:256,257,260`). **A `model_dump()` passthrough from the wire model raises
a `ValidationError`** — on `weight` and `category` as extras, and on the missing
`score`.

Required: an explicit hand-written projection,
`to_feedback_metric_score(m: responses.MetricScore) -> feedback.MetricScore`, that
renames `score_0_100 → score` and drops `weight` and `category`. Carry `name`, `value`,
`unit`, `verdict`, `ideal_min`, `ideal_max`, `view_sensitive` unchanged.

**This is a feature, not an annoyance.** `extra="forbid"` is what keeps rubric
internals — weights, category structure — out of the Gemini payload, consistent with
`PIPELINE.md:1028` (payload is "only numbers, enum strings, and verdicts") and
CLAUDE.md:12-13. Do not "fix" it by relaxing `extra` or by renaming the field; the
projection is the boundary.

**`rubric_version`** — `"rubric_v1"` by default on both `Scorecard`
(`responses.py:279`) and `FeedbackInput` (`feedback.py:70`), persisted per row
(`PIPELINE.md:1015`). The two defaults must be set from one shared constant, not
independently — two string literals that must agree are a drift waiting to happen.

**Honesty note on the version string.** Every band in the source table is marked
`PLACEHOLDER - needs empirical tuning` (`references.py:7-12` and every row). Shipping
them as `rubric_v1` implies a validated rubric. Consider `"rubric_v0_placeholder"` until
the bands are tuned — it costs nothing, it is visible in every stored row, and it makes
the eventual tuning a version bump rather than a silent change to what `rubric_v1`
means. This contradicts `PIPELINE.md:1013`'s "rubric_version stays rubric_v1"; Part F.

### C.9 Test strategy — Stage 15

- **THE key-space test (mandatory, guards finding 2).** For **every** member of
  `ShotType`, assert the resolved band table is non-empty *or* that the member is on an
  explicit, named allowlist of unbanded types. Parametrized over `ShotType` so a new
  enum member fails the test until someone decides what it scores. **This is the test
  whose absence is the whole reason finding (2) survived.**
- **The end-to-end anti-vacuity test.** Build a `SwingMetrics` with *every* field set
  to a plausible in-band value, run the full Stage 15, and assert
  `overall_score is not None` **and** every category has a non-`None` score. A scorecard
  that is `None` on a fully-populated metric set is the exact bug finding (2)
  describes, and only an end-to-end assertion catches it — every unit-level assertion
  passes while it is broken.
- **`None` never becomes `0.0`.** Parametrized over each metric: set exactly that
  metric to `None`, assert its `score_0_100 is None`, its `verdict == UNAVAILABLE`, and
  that its category score changed *only* via renormalization (compute the expected
  value independently). Assert `is None`, never `== 0` — `0 == 0.0` and `None`-vs-`0`
  bugs hide behind truthiness.
- **Empty category.** Set every `balance` metric to `None`; assert
  `CategoryScore.score_0_100 is None` and `metrics_available == 0`, and that
  `overall_score` was computed over the other categories with renormalized weights —
  **not** with `balance` contributing `0`.
- **All-`None`.** `SwingMetrics()` → `overall_score is None`, `Scorecard` still
  returned with populated `categories` and `metrics` lists.
- **Ramp boundaries.** At `hard_min`, `ideal_min`, midpoint, `ideal_max`, `hard_max`,
  and outside both — assert `0, 100, 100, 100, 0, 0` and the verdicts. Plus the
  `lower_is_better` variant: assert a value at `0.0` scores `100` with verdict `ideal`,
  **never** `low`.
- **`phases=None` integration.** Run Stage 13 with `phases=None`, feed to Stage 15,
  assert `preparation` and `balance` are `None` and exactly three categories scored —
  pinning A.6 as a tested contract rather than a claim in a document.
- **The projection (finding 7).** Assert `feedback.MetricScore(**wire.model_dump())`
  **raises**, and that `to_feedback_metric_score(wire)` succeeds and preserves
  `score_0_100 → score`. Testing the failure mode is what stops someone reintroducing
  the passthrough.
- **Weight conservation, property-style.** Over randomized availability masks: within
  each category the renormalized weights of available metrics sum to 1.0 (or the
  category is `None`); category weights over scored categories sum to 1.0.
- **Derived-table agreement (if C.1's projection is adopted).** Assert
  `REFERENCE_RANGES` and `RUBRIC_V1` agree on every shared pair.

---

## Part D — the I/O seams

Stages 12/14/15 are pure. They are bracketed by two impure seams whose contracts are
already fixed by existing code, and both are easy to get wrong in a way that produces
plausible numbers.

### D.1 Stage 5 → Stage 6: the generator yields **absolute** PTS

`PIPELINE.md:157`: Stage 5 outputs `VideoMeta` plus a generator of
`(timestamp_s: float, frame_rgb: np.ndarray)`.

**The timestamps must be ABSOLUTE PTS in the source file — not window-relative.**
`extract_keypoints` subtracts `window_start_s` itself:

```python
# extractor.py:215-221
timestamp_ms = int(frame_timestamps_ms(
    np.asarray([timestamp_s], dtype=np.float64),
    window_start_s,                      # ← subtracted inside
    previous_ms=previous_ms)[0])

# sequence.py:47
rounded = int(round((float(timestamp_s) - float(window_start_s)) * 1000.0))
```

and then stores the **unmodified** `timestamp_s` as the retained full-precision PTS
(`extractor.py:227-228`).

**Why this is worth a warning.** Window-relative is the intuitive choice — the window
is what is being analysed. Yielding window-relative timestamps subtracts
`window_start_s` twice. On a short clip `window_start_s = 0.0` and the bug is
*invisible*; on a long clip with a motion-scan-located window (`PIPELINE.md:170`) the
MediaPipe timestamps go negative, the strict-monotonic clamp at `sequence.py:48` masks
it into a plausible increasing sequence, and `NormalizedSequence.timestamps_s` ends up
window-relative — which silently breaks `ContactDetection.contact_absolute_time_s`
(`responses.py:73-77`) and therefore the entire ball-speed seek. **Every short test
clip a developer would naturally reach for passes.**

Recommendations: name the generator's yield `absolute_pts_s`, not `timestamp_s`; state
it in the `VideoMeta`-returning function's docstring; and add an integration test with
`analysis_window_start_s > 0` asserting
`normalized.timestamps_s[0] >= analysis_window_start_s`.

### D.2 The BGR/RGB split — finding (5)

Two consumers, two incompatible colour conventions:

- MediaPipe: `mp.ImageFormat.SRGB` over `frame_rgb` (`extractor.py:146-147`).
- Ball detector: `cv2.COLOR_BGR2GRAY` (`detector.py:122,155`) and `COLOR_BGR2HSV`
  (`detector.py:166`); `detect_ball_track` documents its input as "(T, H, W, 3) BGR
  uint8" (`detector.py:349`).

**A shared decode path must never hand the same array to both.** The failure is silent:
`colour_mask` (`detector.py:164-166`) does HSV thresholding for a yellow-green ball;
fed RGB-as-BGR, the hue channel is wrong and the mask selects a different colour. No
exception — just a detector that finds nothing, or finds the wrong thing, reported as
`TOO_FEW_DETECTIONS` (`enums.py:64`) and looking exactly like a hard clip.

**Decision: two separate decode passes, not one shared buffer.** The structural
argument is stronger than the colour argument — the two consumers need genuinely
different streams:

| | Pose stream | Ball stream |
|---|---|---|
| Resolution | 640 px long edge (`PIPELINE.md:168`) | CAL_SPACE, 1280 px long edge (`PIPELINE.md:610`) |
| Rate | resampled to fixed 30 fps (`PIPELINE.md:166`) | **native** fps (`PIPELINE.md:166` parenthetical, `:614`) |
| Extent | up to 240 frames / 8 s (`PIPELINE.md:169`) | 0.25 s after contact (`speed.py:55`) |
| Colour | RGB | BGR |

`PIPELINE.md:166` is explicit that ball detection does **not** use the pose stream,
because Stage 5's nearest-PTS resampling selects duplicate frames on 24 fps sources —
which would corrupt the ball-speed denominator. So the streams were already separate by
necessity; making the colour spaces separate too costs nothing.

**Enforcement:**
- Name it in the API: the ball frame loader is `load_measurement_window_bgr(...)`,
  returning a documented BGR stack; the pose generator yields `frame_rgb`.
- **Never** pass a reversed *view* (`frame[:, :, ::-1]`) to cv2 — negative strides on a
  non-contiguous array; cv2 requires contiguity and `np.ascontiguousarray` copies
  anyway, so the "cheap conversion" is neither cheap nor safe.
- Test: render a synthetic frame with a known distinctly-hued blob, assert the
  ball-stream loader's channel order by sampling a pixel. A colour-order bug is
  invisible on greyscale synthetic fixtures — **use a coloured fixture**.

### D.3 Stage 10 runner: seeking off `contact_absolute_time_s`

The runner (`ball/frames.py`, unbuilt) is impure and owns:

1. **Seek** to `contact.contact_absolute_time_s` (`responses.py:73-77`) — the absolute
   PTS, explicitly computed once at `contact.py:528-532` for exactly this. **Not
   `time_s`**, which is window-relative (`responses.py:72`). On a 60 s clip with an 8 s
   window this is the difference between measuring the right 250 ms and measuring
   nothing (`PIPELINE.md:569`).
2. **Decode** `[contact_absolute_time_s, contact_absolute_time_s + WINDOW_S]` at native
   fps, `WINDOW_S = 0.25` (`speed.py:55`), into CAL_SPACE, as **BGR**.
3. **Build `exclusion_boxes` and `seed_xy`** — see D.4.
4. **Enforce the 6 s wall-clock deadline** (`PIPELINE.md:594`) → `DETECTION_TIMEOUT`.
5. Call the pure `detect_ball_track` (`detector.py:337`) and hand the result to the
   pure `compute_ball_speed` (`speed.py:223`).

All decisions stay in the pure functions; the runner only acquires frames and enforces
the clock.

### D.4 `NormalizedSequence.origin_px` — finding (6): recommended, with a caveat

**The problem.** `detect_ball_track` accepts `exclusion_boxes` and `seed_xy`
(`detector.py:341,344`), both in **CAL_SPACE pixels**. `torso_leg_exclusion_box`
requires "(K, 2) … shoulder, hip, knee and ankle positions ALREADY in CAL_SPACE"
(`detector.py:277-286`), and `seed_xy` is "the racket-hand wrist in CAL_SPACE"
(`detector.py:361`).

`NormalizedSequence` cannot supply them. Stage 7 step 5 subtracts the per-frame mid-hip
from every landmark (`normalize.py:280`, `to_body_frame` at `:104-107`), so `points` is
body-frame: **the mid-hip is identically `(0, 0)` on every frame.** The absolute
position is computed at `normalize.py:280` as `mid_hip(flipped)` and **discarded on the
same line**. `NormalizedSequence` carries `torso_scale_px`, `width_px`, `height_px`
(`internal.py:68-70`) — the scale but not the origin.

**Recommendation: add `origin_px: np.ndarray` to `NormalizedSequence`.** `(T, 2)`
float64, the per-frame mid-hip in **pose-stream pixels, y DOWN** (raw image
convention), captured before translation.

Specify the convention precisely, because the frame at `normalize.py:280` is
aspect-corrected and y-flipped, not raw pixels. From `mid_hip(flipped)`:

```
origin_px[:, 0] = mid_hip_flipped[:, 0] / (width_px / height_px) * width_px
origin_px[:, 1] = -mid_hip_flipped[:, 1] * height_px
```

(undoing `apply_aspect_correction`, `normalize.py:76-82`, and `flip_y`, `:85-89`; raw
landmarks are normalized [0,1] image coordinates).

Consumers:

- **Ball seam.** CAL_SPACE is exactly 2× the pose stream (`PIPELINE.md:614`), so any
  landmark reaches CAL_SPACE as
  `cal_xy = (origin_px[t] + points[t, k] * torso_scale_px * [1, -1]) * 2.0`
  — the `[1, -1]` undoing the y-flip. This unblocks `exclusion_boxes` (shoulder, hip,
  knee, ankle per `detector.py:280-285`) and `seed_xy` (racket wrist).
- **Stage 13.** `weight_transfer_tu` and `balance_sway_tu` become arithmetically
  computable: mid-hip displacement and positional std-dev in TU, from
  `origin_px / torso_scale_px`. `metrics.py:116-118` names exactly this as the
  unblocking condition ("until Stage 7 carries a pre-translation mid-hip trajectory").

**The caveat that must travel with the recommendation.** `origin_px` is the mid-hip in
**image** coordinates, which moves when the *camera* moves. On a handheld phone, a pan
or a small tripod drift is indistinguishable from the player transferring weight.
`origin_px` therefore unblocks these two metrics **arithmetically but not
epistemically** — the number would be real but would measure camera motion plus body
motion, and `estimated_camera_view` is `UNKNOWN` (`normalize.py:325`) so we cannot even
establish that the camera was static.

**Recommendation, in order:**
1. **Add `origin_px` now** — it is a small, well-defined change (capture the value
   already computed and thrown away at `normalize.py:280`), and the ball seam *needs*
   it with no such caveat: the exclusion box and seed only need to know where the
   player is in the frame, which `origin_px` answers correctly regardless of camera
   motion.
2. **Do not immediately un-`None` `weight_transfer_tu` and `balance_sway_tu`.** Keep
   them excluded from the rubric (C.3) until either camera-motion compensation exists
   (e.g. a static-background homography) or `estimate_camera_view` lands and can at
   least flag a moving camera. Shipping them would replace an honest `None` with a
   number that is right on a tripod and wrong handheld — the *exact* failure mode
   `metrics.py:109-118` refuses.

`NormalizedSequence` is a frozen dataclass (`internal.py:44`) with no defaults, so
adding a field is a breaking change for every construction site — `normalize.py:298`
and every test fixture. That is a small, mechanical, compile-time-visible cost, which is
the right kind.

---

## Part E — orchestration order and failure mapping

### E.1 Call sequence

```
 1  Stage 5   decode + sample        IMPURE   → VideoMeta, generator[(abs_pts_s, frame_rgb)]
 2  Stage 6   MediaPipe extraction   IMPURE   → RawPoseSequence → PoseSequence  [seam 1]
 3  Stage 7   normalize              PURE     → NormalizedSequence, PoseQuality
 4  Stage 8   handedness             PURE     → HandednessResult
 5  Stage 9   contact                PURE     → ContactDetection
 6  Stage 12  phases                 PURE     → SwingPhases | None        ← this doc
 7  Stage 13  metrics                PURE     → SwingMetrics, warnings
 8  Stage 14  shot type              PURE     → ShotTypeInference          ← this doc
 9  Stage 15  scorecard              PURE     → Scorecard                  ← this doc
10  Stage 10  ball frames + detect   IMPURE+CV→ BallTrack | None, BallDetectionSummary
11  Stage 11  ball speed             PURE     → BallSpeedResult
12  Stage 16  payload                PURE     → FeedbackInput → FeedbackPayload
13  Stage 17  Gemini + guard         IMPURE   → CoachingFeedback
14  Stage 18  persist                IMPURE
```

**Stages 12–15 run BEFORE 10–11, deliberately, for three reasons:**

1. **It makes CLAUDE.md:8 structural.** Stage 14 cannot consult the ball track if the
   ball track does not exist yet. `PIPELINE.md:982` achieves this by omitting the
   parameter; ordering makes it true at runtime too, and a future refactor that adds a
   ball argument would have to move the stage to compile.
2. **The fragile, slow, I/O-bound work runs last.** Stage 10 carries a 6 s hard
   deadline (`PIPELINE.md:594`) and a second decode pass. If it times out, the entire
   technique analysis is already complete in memory.
3. **10/11 depend on 9, not on 12–15.** Nothing is serialized that needn't be. Stage 10
   needs `contact_absolute_time_s` and `NormalizedSequence` — both available at step 5.

### E.2 `ErrorCode` per stage

`ErrorCode` has exactly 20 members (`enums.py:166-195`).

| Stage | Raises | Note |
|---|---|---|
| 5 | `DECODE_FAILED`, `UNSUPPORTED_CODEC`, `NO_VIDEO_STREAM`, `VIDEO_TOO_SHORT`, `VIDEO_TOO_LONG` | `PIPELINE.md:158` |
| 6 | `NO_POSE_DETECTED` (< 40% of sampled frames), `INTERNAL_ERROR` (model asset missing / SHA mismatch) | `PIPELINE.md:188` |
| 7 | `POSE_QUALITY_TOO_LOW` | `normalize_sequence` never raises; the **orchestrator** raises on `PoseQuality.usable == False` (`normalize.py:271-276`) |
| 8 | **none** | Never raises (`handedness.py:80`). Low confidence is data, not an error. |
| 9 | `CONTACT_NOT_FOUND` | `detect_contact_frame` never raises (`contact.py:526`). The **orchestrator** raises when `sanity_flags` contains `"sequence_unusable"` (`contact.py:515`). Low confidence alone does **not** raise — it downgrades to `PARTIAL` and skips ball speed (`PIPELINE.md:553`). |
| **12** | **none** | Returns `(None, warnings)`. |
| 13 | **none** | Never raises (`metrics.py:626,685-686`). |
| **14** | **none** | Returns `UNKNOWN` (`PIPELINE.md:978`). |
| **15** | **none** | Returns a `Scorecard` with `None` scores (`PIPELINE.md:1005`). |
| 10 | **none** | "Never fails the job" (`PIPELINE.md:594`) → `BallTrack = None` + a `BallSpeedUnavailableReason`. |
| 11 | **none** | `BallSpeedResult` with null speed + reason (`speed.py:211-221`). |
| 16–17 | **none** | Gemini failure → template fallback (`FeedbackSource.TEMPLATE`, `enums.py:47`). |
| 18 | `INTERNAL_ERROR`, `STORAGE_UNAVAILABLE` | |

**Stages 12, 14 and 15 raise nothing, ever.** There is no `SEGMENTATION_FAILED`, no
`SHOT_TYPE_UNKNOWN`, no `SCORING_FAILED` in `ErrorCode` — correctly, because none of
these is a failure. A swing that cannot be segmented, classified or scored is still a
swing that was analysed, and the response says so in the data. The only way these
stages contribute an error is `INTERNAL_ERROR` from the orchestrator's own wrapper,
which is a bug report, not a user-facing condition.

### E.3 Skippable stages

**Stages 10 and 11 are skipped entirely — never failing the job — when:**

- `ball_speed_calibration` is absent → `NOT_CALIBRATED` (`enums.py:58`, enforced at
  `speed.py:271`). **This is the common case** and must not produce a warning that
  reads like a fault.
- `contact.confidence < 0.35` (`speed.py:71`, `PIPELINE.md:553,596`) →
  `CONTACT_UNRELIABLE`.
- `PoseQuality.estimated_camera_view in {FRONT, BEHIND}` → `CAMERA_VIEW_UNSUITABLE`.
  **This gate is inert:** `estimated_camera_view` is hardcoded `UNKNOWN`
  (`normalize.py:325`) and `UNKNOWN` matches neither value, so it never fires
  (`PIPELINE.md:598`). A front-on capture — the `θ ≈ 90°` geometry
  `PIPELINE.md:600` calls unmeasurable — passes straight through and produces a
  confidently wrong mph rather than `null` + a reason. Until `estimate_camera_view`
  lands, **a ball-speed figure must not be surfaced as authoritative**
  (`PIPELINE.md:602`).

In every skip case, `BallSpeedResult` is still constructed with `ball_speed_mph = None`
and a non-null `unavailable_reason` — the pairing is enforced by a validator
(`responses.py:142-149`), so an inconsistent skip raises at construction rather than
shipping a null speed with no explanation.

**Stage 12 is skippable in the sense that Stage 13 accepts `phases=None`**
(`metrics.py:624`) — but per A.6 this deletes two of five scoring categories. It should
be attempted always and skipped only by its own `None` return.

### E.4 `complete` vs `partial`

`AnalysisStatus` has exactly two members (`enums.py:120-124`). Downgrade to `PARTIAL`
when any of:

- `contact.confidence < 0.35` (`PIPELINE.md:553`)
- `phases is None`, or any phase collapsed
- `shot_type == ShotType.UNKNOWN`
- `overall_score is None`, **or** fewer than 5 categories scored (C.6)
- Stage 13 returned a non-empty `warnings` list (`metrics.py:647-658` — the low
  handedness-confidence signal)
- `PoseQuality.flags` contains `subject_identity_unstable` (`normalize.py:54`)

Ball speed being unavailable is **not** grounds for `PARTIAL`. It is the expected state
for any uncalibrated clip (`PIPELINE.md:596`), and `AnalysisStatus.PARTIAL` is about the
*technique analysis* being incomplete. Marking every uncalibrated clip `partial` would
make the flag meaningless.

Warnings from Stages 12/13/14 accumulate into `AnalysisResponse.warnings`
(`responses.py:441`) and the scoring-related ones into
`FeedbackInput.low_confidence_warnings` (`feedback.py:85`), which is how the coaching
tone is softened without Gemini being handed the raw diagnostic text.

---

## Part F — judgment calls a reviewer should check rather than trust

Ordered by how much damage a wrong call does.

1. **`BASE_BANDS` (C.7) is a product decision presented as a fallback.** It determines
   whether filming a backhand yields a score at all. Option (c) — authoring real bands
   for the four unbanded shot types — is the correct answer and is not an engineering
   decision. **Escalate before launch.**
2. **`SLICE_FOLLOW_THROUGH_TU = 0.20` (B.5).** Entirely new. Not in `PIPELINE.md`, not
   measured. Derived by inspection from two placeholder bands
   (`references.py:82,100`). It changes shot classification, which changes which rubric
   is applied, which changes every score. **The weakest number in this design.**
3. **`MIN_SCORED_CATEGORIES = 3` (C.6).** New. Decides whether a thin analysis shows a
   score or a blank. Reasoned from A.6 (the `phases is None` case leaves exactly 3), not
   measured. Interacts with the persisted-history comparability argument.
4. **`hard_min`/`hard_max` derivation (C.4)** — "one ideal-band-width outside the band".
   New, applied to every metric, shapes the entire distribution of `score_0_100`. A
   shape, not a measurement.
5. **Replacing the dead conjunct rather than defining `takeback_displacement_tu`
   (B.5).** The alternative — specifying that metric properly in Stage 13 — may be
   better. It was rejected here to avoid inventing a Stage 13 metric from Stage 14,
   which `metrics.py:636-638` deliberately refuses.
6. **Category weights (C.2)** — `preparation` 0.20, `contact` 0.30, `swing_path` 0.25,
   `balance` 0.15, `follow_through` 0.10, and every within-category weight. **None of
   these is in `PIPELINE.md`.** `contact` is highest as the best-measured and most
   directly coachable group; `follow_through` lowest as a single metric. Coaching
   judgment, asserted by an engineer.
7. **`forward_swing` ends at `C - 2`, not `C - 1` (A.3).** Resolves a genuine
   contradiction in `PIPELINE.md:926-927` in favour of the non-overlap invariant
   (`responses.py:166`). The other resolution — overlap allowed — is defensible if a
   consumer wants a wider forward-swing window.
8. **The collapsed-phase encoding `end_frame = start_frame - 1` (A.4).** Correct given
   `metrics.py:300`, and the trap it avoids is real — but it will look like an
   off-by-one to every reader. Verify the reasoning before "fixing" it.
9. **Forehand/backhand as capped soft evidence, `FOREHAND_BACKHAND_MAX_EVIDENCE = 0.25`
   (B.2).** The cap value is new. The *decision* (that this axis is unreliable) is
   well-grounded; the number is not.
10. **`class_scores` all-zero on the no-evidence path (B.3)** contradicts
    `responses.py:240` ("Per-class scores, sum to 1.0"). Deliberate — summing to 1.0
    when nothing was measured is a fabrication — but it needs either sign-off or a
    docstring amendment.
11. **`rubric_version = "rubric_v0_placeholder"` (C.8)** contradicts
    `PIPELINE.md:1013`. Recommended on honesty grounds; it is a contract string
    persisted per row, so changing it later is not free.
12. **View-sensitivity demotion rather than exclusion in `priority_metric_names`
    (C.8).** With `estimated_camera_view` permanently `UNKNOWN` (`normalize.py:325`),
    exclusion could empty the list; demotion is the compromise. Revisit when
    `estimate_camera_view` lands.
13. **Dropping "trunk extended" from the serve rule (B.4).** No metric measures it.
    The remaining two terms may over-trigger serve on a high forehand.
14. **Reusing `contact.racket_hand_speed` in Stage 12 (A.2)** couples the two stages.
    Deliberate — consistency with the chosen contact frame matters more than
    independence — but it means a Stage 9 speed-curve change silently moves every phase
    boundary.
15. **Recommending `origin_px` while recommending *against* immediately un-`None`-ing
    the two metrics it unblocks (D.4).** Deliberately conservative; a reviewer may
    reasonably want the metrics shipped with a camera-motion warning instead.
16. **Stage 13 should append a warning on its exception path (defect 11,
    `metrics.py:685-686`)** so that "internal failure" and "clip unmeasurable" are
    distinguishable. Not strictly in scope for 12/14/15, but Stage 15 consumes the
    result and currently cannot tell them apart.
17. **Inherited but unvalidated `PIPELINE.md` numbers, restated so they are not
    mistaken for measurements:** `READY_EXIT_FRACTION` 0.10, `FOLLOW_EXIT_FRACTION`
    0.15, `CONTACT_HALF_WIDTH` 1, `MIN_PHASE_FRAMES` 2 (A.3); `SERVE_HEIGHT_RATIO`
    1.30, `TWO_HANDED_SEPARATION_TU` 0.35, `SLICE_PATH_ANGLE_DEG` −10,
    volley 1.0 TU / 0.35 s, `TOP_SCORE_MIN` 0.45, `MARGIN_MIN` 0.15 (B.3–B.6). **And
    every band in `REFERENCE_RANGES`, each individually marked `PLACEHOLDER - needs
    empirical tuning` (`references.py:7-12`).**
18. **The Stage 9 `+1` residual is accepted, not corrected (A.7)** — and it biases
    `swing_path_angle_deg` positive, in the same direction as a topspin prior, into
    B.5's threshold. Validate slice detection first; expect under-reporting.

---

## Part G — facts this design rests on

| # | Fact | Citation |
|---|---|---|
| 1 | `weight_transfer_tu` returns `None` unconditionally (typed `-> None`) | `metrics.py:550,559` |
| 2 | `balance_sway_tu` returns `None` unconditionally (typed `-> None`) | `metrics.py:562,568` |
| 3 | `takeback_displacement_tu` hardcoded `None` in the orchestrator | `metrics.py:682` |
| 4 | Reason for 1–3: Stage 7 pins the mid-hip at the origin | `metrics.py:109-118`; `normalize.py:280` |
| 5 | `phase_span` treats **only** `end_frame < start_frame` as unusable | `metrics.py:300` |
| 6 | `compute_swing_metrics` signature, `phases` optional and nullable | `metrics.py:620-625` |
| 7 | Stage 13 never raises; bare `SwingMetrics()` on exception | `metrics.py:626,685-686` |
| 8 | `METRIC_VIEW_SENSITIVE`, 17 entries, 9 `True` | `metrics.py:89-107` |
| 9 | `swing_path_fit` window is `[C-6, C+3]` — asymmetric | `metrics.py:64-66,483-486` |
| 10 | y is positive **up**; mid-hip is the origin | `metrics.py:7-21`; `normalize.py:85-89,104-107` |
| 11 | `REFERENCE_RANGES` keyed `"topspin"/"slice"/"flat"` | `references.py:69,87,105` |
| 12 | `ShotType` members are `"forehand_topspin"`, … — 7 total | `enums.py:127-134` |
| 13 | `reference_ranges_for` returns `{}` for an unknown key — never raises | `references.py:126-131` |
| 14 | ⇒ `reference_ranges_for(ShotType.FOREHAND_TOPSPIN.value) == {}` | 11 + 12 + 13 |
| 15 | `ReferenceRange` is `frozen`, `extra="forbid"`, only `minimum`/`maximum` | `references.py:36-42` |
| 16 | Dead bands exist for `weight_transfer_tu` / `balance_sway_tu` | `references.py:81,83,99,101,117,119` |
| 17 | `wrist_separation_at_contact_tu` deliberately excluded from references | `references.py:64-67` |
| 18 | `compare_to_reference` skips `None` values, never treats them as 0 | `references.py:150-151` |
| 19 | Every reference band marked `PLACEHOLDER - needs empirical tuning` | `references.py:7-12` + every row |
| 20 | `extract_keypoints` passes the raw `timestamp_s` plus `window_start_s` | `extractor.py:210-221` |
| 21 | `frame_timestamps_ms` subtracts `window_start_s` | `sequence.py:47` |
| 22 | The unmodified absolute PTS is what is retained | `extractor.py:227-228` |
| 23 | MediaPipe is fed `mp.ImageFormat.SRGB` | `extractor.py:146-147` |
| 24 | Ball detector uses `COLOR_BGR2GRAY` / `COLOR_BGR2HSV` | `detector.py:122,155,166` |
| 25 | `detect_ball_track` documents its input as BGR | `detector.py:349` |
| 26 | `exclusion_boxes` / `seed_xy` are CAL_SPACE pixels | `detector.py:341,344,277-286,361` |
| 27 | CAL_SPACE is exactly 2× the 640 px pose stream | `PIPELINE.md:610,614` |
| 28 | `NormalizedSequence` has no pre-translation position field | `internal.py:44-70` |
| 29 | The mid-hip origin is computed and discarded on one line | `normalize.py:280` |
| 30 | `torso_scale_px` **is** carried | `internal.py:68`; `normalize.py:297` |
| 31 | `feedback.MetricScore` sets `extra="forbid"`, field is `score` | `feedback.py:29,35` |
| 32 | Wire `MetricScore` has `score_0_100`, `weight`, `category` | `responses.py:256,257,260` |
| 33 | ⇒ a `model_dump()` passthrough raises | 31 + 32 |
| 34 | `FeedbackInput` is the hard consumer contract, `extra="forbid"` | `feedback.py:60-85` |
| 35 | `Improvement.priority` is `ge=1, le=3` ⇒ cap `priority_metric_names` at 3 | `feedback.py:91` |
| 36 | `HandednessSource.HINT_OVERRODE_DETECTION` exists | `enums.py:81` |
| 37 | `detect_handedness` only ever assigns `DETECTED` or `USER_HINT` | `handedness.py:149,158` |
| 38 | `HINT_CONFIDENCE_FLOOR = 0.5` | `handedness.py:26` |
| 39 | `racket_wrist_index` silently maps `UNKNOWN` → right wrist | `handedness.py:171-173` |
| 40 | `swing_direction_sign` derives from the longer-path wrist | `normalize.py:290-293` |
| 41 | Stage 8 is 0.05–0.36 confidence, wrong hand > half of a 7-clip batch | `PIPELINE.md:583` |
| 42 | A correct `handedness_hint` is a **precondition**, not a convenience | `PIPELINE.md:583` |
| 43 | `estimated_camera_view` hardcoded `CameraView.UNKNOWN` | `normalize.py:325`; default `responses.py:44` |
| 44 | ⇒ every camera-view gate is inert | `PIPELINE.md:598-602` |
| 45 | Stage 9 carries an accepted, unexplained `+1` frame residual | `PIPELINE.md:581` |
| 46 | Downstream must treat `frame_index` as possibly `+1`, esp. windows < ~4 frames | `PIPELINE.md:581` |
| 47 | Retuning thresholds on this speed curve changes nothing (byte-identical sweep) | `PIPELINE.md:579` |
| 48 | `contact_absolute_time_s` exists for the Stage 10 seek | `responses.py:73-77`; `contact.py:528-532` |
| 49 | Stage 9's degenerate return flags `"sequence_unusable"`, `frame_index = 0` | `contact.py:505-516` |
| 50 | `forward = points[:, wrist, 0] * swing_direction_sign` | `contact.py:548` |
| 51 | `forward_swing_window_start` — last local min of forward displacement before peak | `contact.py:335` |
| 52 | Ball-speed window is 0.25 s | `speed.py:55` |
| 53 | Ball speed requires `contact.confidence >= 0.35` | `speed.py:71`; `PIPELINE.md:553` |
| 54 | Absent calibration → `NOT_CALIBRATED`, never a failure | `speed.py:271`; `enums.py:58` |
| 55 | `BallSpeedResult` enforces null-speed ⇔ non-null reason | `responses.py:142-149` |
| 56 | `SwingPhases` must be ordered, contiguous, non-overlapping, length 5 | `responses.py:166` |
| 57 | `SwingPhases.tempo_ratio` is already `float \| None` | `responses.py:167-169` |
| 58 | `Scorecard.overall_score` is `float \| None` | `responses.py:276` |
| 59 | `CategoryScore.score_0_100` is `float \| None`; carries availability counts | `responses.py:266-272` |
| 60 | `MetricScore.score_0_100` is "None iff verdict == unavailable" | `responses.py:256` |
| 61 | `ShotTypeInference.class_scores` documented as summing to 1.0 | `responses.py:240` |
| 62 | `ScoreCategory` has exactly 5 members | `enums.py:137-142` |
| 63 | `MetricVerdict` has exactly 4 members incl. `unavailable` | `enums.py:15-19` |
| 64 | `MetricUnit` has no `mph` member, by design | `enums.py:22-27` |
| 65 | `ErrorCode` has exactly 20 members; none for segmentation/classification/scoring | `enums.py:166-195` |
| 66 | `AnalysisStatus` is `complete` / `partial`, disjoint from `JobStatus` | `enums.py:101-124` |
| 67 | `AnalysisListItem.overall_score` is persisted and nullable | `responses.py:459` |
| 68 | Stage 5 yields a generator of `(timestamp_s, frame_rgb)` | `PIPELINE.md:157` |
| 69 | Ball detection deliberately does **not** use the pose stream (VFR duplicates) | `PIPELINE.md:166` |
| 70 | Stage 10 never fails the job; 6 s hard deadline | `PIPELINE.md:594` |
| 71 | Stage 14 takes no `BallTrack` — structural, not conventional | `PIPELINE.md:982`; CLAUDE.md:8 |
| 72 | Stage 15 lives in `analysis/`, not `feedback/` | `PIPELINE.md:1002`; CLAUDE.md repo layout |
| 73 | `RUBRIC_V1: dict[ShotType, dict[str, MetricBand]]` already specified | `PIPELINE.md:1007` |
| 74 | Unavailable metrics excluded, weight redistributed within category | `PIPELINE.md:943,1009` |
| 75 | `unknown` falls back to a shot-type-agnostic rubric subset | `PIPELINE.md:994` |
| 76 | `ball_speed_mph` deliberately absent from the rubric | `PIPELINE.md:1013` |
| 77 | Stage 14 slice rule keys on `takeback_displacement_tu` | `PIPELINE.md:989` |
| 78 | Stage 13 declines to define `takeback_displacement_tu`, on purpose | `metrics.py:636-638` |
| 79 | Evidence strings are Python-generated, Gemini only paraphrases | `PIPELINE.md:992`; CLAUDE.md:12-13 |
| 80 | `phases.py`, `shot_type.py`, `rubric.py`, `scoring.py`, `ball/frames.py`, `ball/geometry.py` do not exist | working tree, `backend/app/analysis/`, `backend/app/ball/` |

---

*Design document. No implementation code. Every threshold not traceable to
`PIPELINE.md` or to the real-footage findings is listed in Part F.*
