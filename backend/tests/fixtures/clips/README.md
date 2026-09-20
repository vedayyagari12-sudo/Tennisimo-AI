# Vendored test clip

One real clip, committed deliberately so the end-to-end pipeline test is
self-contained and runs on any machine. Every Stage 9 bug found in this project
was found on real footage and none was reproducible on synthetic data, which is
why this is a real clip and not a generated one.

## `serve_vertical_10340710.mp4`

| | |
|---|---|
| Source | https://www.pexels.com/video/man-serving-tennis-ball-10340710/ |
| License | Pexels license — free to use, no attribution required |
| SHA-256 | `4d39b5695de6f912dc79e355b2c112c94852a68eb89d437f83579dc1c48753d7` |
| Bytes | 2,853,152 |
| Duration | 10.4 s |
| Frame rate | 25 fps |
| Frames | 260 |
| Dimensions | 1080 × 2048 (**portrait**) |

Vendored **whole and unmodified**. It was deliberately not trimmed: the frame
indices below come from the Stage 9 real-footage validation recorded in
`docs/PIPELINE.md` §9.1, and trimming would shift every index and silently
invalidate that record.

## Why this clip

- **Portrait orientation**, which is what a phone actually produces and what
  most of the stock-footage corpus is not.
- It has **established ground truth** from the Stage 9 validation rounds, so an
  end-to-end assertion has something real to check against.
- At 2.7 MB it is the smallest clip that satisfies both of the above.

## Known ground truth

- Ball is visible at **frame 84**.
- Contact occurs at **frames 86–88**.

A caution for anyone asserting against this, recorded because it already caused
a wrong conclusion once: a 5-frame filmstrip centred on an early candidate
detection made it look as though the clip contained no ball strike at all. It
does. Verify against a wider window before concluding a detection is spurious.

Stage 9 carries an accepted **+1 frame residual** on real footage, so an
end-to-end test should assert contact within a tolerance band around 86–88, not
on an exact frame.

## Rules

- Do not re-encode, trim, or "optimize" this file. The SHA-256 above is the
  identity the ground truth is attached to.
- If a second clip is ever added, give it its own row and its own ground-truth
  section rather than generalizing these numbers.
