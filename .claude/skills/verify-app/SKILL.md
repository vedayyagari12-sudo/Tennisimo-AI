---
name: verify-app
description: Full health check of Tennisimo - backend pytest, flutter analyze + test, live API probe, and deploy drift (is Cloud Run behind main?). Use when asked to "check everything works", before a deploy, or after a large change.
---

# Verify Tennisimo

Run these from the repo root. Each step is independent; run them in parallel.

1. **Concurrent edits first.** `git status --short`. Uncommitted changes you did not
   make mean another session is working in this checkout. Attribute failures before
   "fixing" them, never commit or revert their files, and never deploy with
   `--source .` while they exist (it uploads the working tree, not HEAD).
2. **Backend tests:** `cd backend && python -m pytest -q` (about 900 tests, ~2 min).
3. **Frontend:** `cd frontend && flutter analyze && flutter test` (about 1,100 tests).
   Flutter lives at `C:/Users/shiva/Desktop/AI fitness/flutter/bin/flutter`.
4. **Live API:** `curl -s https://tennisform-api-143709056949.us-east1.run.app/v1/analyses`
   should return 401 JSON. `/healthz` is intercepted by Google's frontend (404 HTML),
   so that is not a failure.
5. **Deploy drift:**
   `gcloud run revisions list --service tennisform-api --region us-east1 --limit 1`
   versus `git log -1 --format=%ci -- backend`. If backend commits are newer than
   the revision, production is missing features. Report it. Deploy only when the
   user says so.

Report: pass/fail counts per suite, any failures with attribution (yours vs. pre-existing
vs. another session's), live status, and drift.
