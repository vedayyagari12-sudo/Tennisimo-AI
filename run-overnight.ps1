# run-overnight.ps1
# TennisForm AI - unattended overnight build runner
#
# WHAT THIS DOES: runs the phases that are safe to leave unattended, in order,
# each as a separate headless Claude Code call. Logs everything to timestamped
# files so you can review what happened when you're back.
#
# WHAT THIS DELIBERATELY DOES NOT DO: run Phase 2, Phase 3, or Phase 3.6.
# Those phases produce numbers (contact frame, swing angles, ball speed) that
# can only be validated by you scrubbing real footage and checking the output
# makes sense. An unattended agent will report "tests pass" with the same
# confidence whether the math is right or subtly wrong - that's the one thing
# this script can't safely automate. Run those interactively, with your eyes
# on the output, when you're home.
#
# USAGE: open PowerShell in your TennisApp root folder and run:
#   .\run-overnight.ps1
#
# Requires: Claude Code CLI installed and authenticated already (if `claude`
# works interactively, this will work).

$ErrorActionPreference = "Stop"
$logDir = ".\overnight-logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$timestamp = Get-Date -Format "yyyy-MM-dd_HHmm"
$summaryLog = "$logDir\SUMMARY_$timestamp.txt"

function Run-Phase {
    param(
        [string]$Name,
        [string]$Prompt
    )
    $logFile = "$logDir\$($Name)_$timestamp.txt"
    Write-Host "=== Starting $Name ===" -ForegroundColor Cyan
    Add-Content -Path $summaryLog -Value "`n=== $Name - started $(Get-Date -Format 'HH:mm:ss') ==="

    $maxRetries = 8          # ~8 retries covers a full 5-hour window at 40min spacing
    $retryDelaySeconds = 2400 # 40 minutes - usage windows reset on a fixed schedule,
                              # this isn't exact but avoids hammering right at the wall
    $attempt = 0

    while ($true) {
        $attempt++
        claude -p $Prompt `
            --permission-mode bypassPermissions `
            --output-format text `
            *> $logFile

        $exitCode = $LASTEXITCODE
        if ($exitCode -eq 0) {
            Write-Host "=== $Name finished OK ===" -ForegroundColor Green
            Add-Content -Path $summaryLog -Value "$Name - OK ($(Get-Date -Format 'HH:mm:ss'), attempt $attempt)"
            return
        }

        # Check if this looks like a usage-limit stop rather than a real error
        $logContent = Get-Content $logFile -Raw -ErrorAction SilentlyContinue
        $isLimitHit = $logContent -match "usage limit|rate limit|limit reached"

        if ($isLimitHit -and $attempt -lt $maxRetries) {
            Write-Host "=== $Name hit a usage limit (attempt $attempt/$maxRetries). Waiting $($retryDelaySeconds/60) min... ===" -ForegroundColor Yellow
            Add-Content -Path $summaryLog -Value "$Name - usage limit hit on attempt $attempt, retrying after $($retryDelaySeconds/60) min"
            Start-Sleep -Seconds $retryDelaySeconds
            continue
        }

        # Real failure (not a limit issue), or retries exhausted - stop the chain
        Write-Host "=== $Name FAILED (exit $exitCode) - stopping chain ===" -ForegroundColor Red
        Add-Content -Path $summaryLog -Value "$Name - FAILED, exit $exitCode after $attempt attempt(s). Chain stopped here."
        Write-Host "See $logFile for details."
        exit 1
    }
}

Write-Host "Overnight build starting. Logs in $logDir" -ForegroundColor Yellow
Add-Content -Path $summaryLog -Value "Overnight build run - $timestamp"
Add-Content -Path $summaryLog -Value "Phases 2, 3, 3.6 are DELIBERATELY SKIPPED - run those yourself with real footage."

# ---------------------------------------------------------------------------
# PHASE 4 - Reference ranges + Gemini feedback (self-contained, testable
# without real footage - uses synthetic/mocked data throughout)
# ---------------------------------------------------------------------------
Run-Phase -Name "Phase4_GeminiFeedback" -Prompt @"
Use the builder agent.

Implement backend/app/feedback/.

1. references.py - a REFERENCE_RANGES dict mapping each shot type
   ("topspin", "slice", "flat") to min/max ranges for each metric from
   docs/PIPELINE.md. Use reasonable coaching values as placeholders and add
   a comment on each saying it needs empirical tuning. Include a
   compare_to_reference(metrics, shot_type) -> list[Deviation] function
   returning which metrics fall outside range and by how much.

2. gemini.py - builds a TEXT-ONLY prompt for Gemini 2.5 Flash per the schema
   in docs/PIPELINE.md, including the fenced ball_speed_mph handling (banned
   substring when null, tightly scoped mph usage when present). It must NOT
   send video, images, or raw keypoints.

3. When contact confidence was low or truncated_clip was flagged, skip
   Gemini entirely and return a fixed fallback message.

Write tests with a mocked Gemini client covering: valid JSON, JSON wrapped
in fences, malformed response, the low-confidence skip path, and the
ball-speed-null vs ball-speed-present guard cases.

Run the tests and report the result. Do not proceed to any other phase.
"@

# ---------------------------------------------------------------------------
# PHASE 3.5 (builder step only) - Ball detection module, tested against
# SYNTHETIC frames only (a drawn circle on a plain background), not real
# footage. This validates the code runs and the logic is sound; it does
# NOT validate real-world accuracy. That still needs your review later.
# ---------------------------------------------------------------------------
Run-Phase -Name "Phase3.5_BallDetection" -Prompt @"
Use the builder agent.

Implement backend/app/pose/ball_detector.py per docs/BALL_TRACKING.md and
docs/PIPELINE.md. Pure module: frames in, ball positions out.

Write backend/tests/test_ball_detector.py using SYNTHETIC frames (a drawn
circle moving across a plain background) so tests don't depend on real
footage. Cover: clean detection, no ball present, implausible-jump rejection.

Run the tests and report the result. Note explicitly in your summary that
this has NOT been validated against real tennis footage and needs a human
review pass with real video before being trusted. Do not proceed to any
other phase.
"@

# ---------------------------------------------------------------------------
# PHASE 6 - Flutter frontend (well-specified UI work, testable without
# real footage since it's mocking the backend response shape)
# ---------------------------------------------------------------------------
Run-Phase -Name "Phase6_FlutterUI" -Prompt @"
Use the builder agent.

Build the Flutter side per the earlier spec: Record screen (shot-type +
handedness selectors, calibration tap step, 15s cap, tripod setup hint),
Analyzing screen (upload progress, retry on error), Results screen (shot
type, ball speed if present shown as approximate, coaching summary/cues,
metrics vs reference range readout), plus history list.

Reuse the existing auth service pattern from my other Flutter project.
Follow the repo's existing service/model structure. Do NOT add streaks,
social features, settings screens, onboarding flows, or any widget not
explicitly listed above.

Report what you built and any assumptions made about the backend response
shape, since Phase 5's API endpoint may not be finalized yet. Do not
proceed to any other phase.
"@

# ---------------------------------------------------------------------------
# PHASE 6.5 - Progress tracking (depends on DB schema existing conceptually,
# not on real analysis data - safe to scaffold ahead)
# ---------------------------------------------------------------------------
Run-Phase -Name "Phase6.5_ProgressTracking" -Prompt @"
Use the builder agent.

Add progress tracking per the earlier spec: GET /api/v1/progress endpoint
(filtered by shot_type, form_score computed at read time not stored,
consistency via stddev of swing_path_angle_deg, excluding low-confidence
analyses), and the Flutter Progress screen (shot selector, form_score line
chart, one raw-metric chart with reference band, summary stat cards, empty
state under 3 analyses).

Use fl_chart. Do NOT add goals, streaks, badges, achievements, or
notifications.

Report what you built. This is the last automated phase - stop after this.
"@

Write-Host "`nOvernight chain complete. See $summaryLog for the run summary." -ForegroundColor Yellow
Write-Host "REMINDER: Phases 2, 3, and 3.6 still need to be run interactively," -ForegroundColor Yellow
Write-Host "with you reviewing real footage output, before this is trustworthy." -ForegroundColor Yellow