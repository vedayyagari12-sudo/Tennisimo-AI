# Prompt: live end-to-end check (paste into Claude Code on a machine with access)

Use this from a session that can reach `tennisform-api-143709056949.us-east1.run.app`
and `qrjpheqayeudazcrqxpl.supabase.co`, and that has the Supabase SQL editor or a
service-role key available to YOU (the person), not pasted into chat.

---

You are verifying Tennisimo end to end against the LIVE stack. Do not change
production data except for one throwaway test account you create and delete.
Stop and ask me before any other write.

1. **Deploy drift.** Compare the deployed Cloud Run revision with `main`
   (`gcloud run services describe tennisform-api --region us-east1`). Confirm
   `GET /openapi.json` lists `DELETE /v1/account`, `/v1/matches`, and
   `/v1/uploads/ticket`. If the deployed revision is older than the latest
   backend commit, tell me and stop.

2. **Unauthenticated probes (safe).** `DELETE /v1/account` with no token must
   return 401 with the error envelope and delete nothing. Same with a garbage
   bearer token.

3. **Schema check (read-only).** In the Supabase SQL editor run the query in
   `docs/DATABASE_SETUP.md` (section after Part 7.5, "ON DELETE behaviour of every FK")
   plus the same for `public.user_activity`. Expected: every `user_id` FK is
   `ON DELETE CASCADE`. A `user_id` FK WITHOUT cascade on any table makes the
   final GoTrue delete fail; report it with the fix from that section.

4. **Real deletion, throwaway account.**
   - Sign up `tennisimo-e2e+<timestamp>@<a domain I control>` in the web app
     (`flutter run -d chrome` from `frontend/`, or the deployed site).
   - Upload one short swing clip and, if possible, one short rally video, and
     wait for both results.
   - Record the user id. In the SQL editor, note the row counts for that id in
     `analyses`, `analysis_jobs`, `user_activity`, and the object count under
     `swing-videos/<user_id>/` in Storage.
   - In the app: Profile → Delete account → type DELETE → Delete forever.
     Expect the login screen with "Your account and every recorded swing and
     video have been permanently deleted."
   - Verify, read-only: zero rows for that id in all three tables, zero
     objects under `swing-videos/<user_id>/` (including `matches/`), and no
     row in `auth.users` for that id. Signing in with the old credentials
     must fail.
   - Report each count before and after.

5. **Frontend checks on a real device.** Android and iOS builds (`flutter build
   apk`, Xcode for iOS): toggle Profile → Appearance between System / Light /
   Dark; the status-bar icons must stay readable on both canvases. Check the
   merged Android manifest in `build/` still lists only INTERNET, CAMERA,
   ACCESS_NETWORK_STATE, WAKE_LOCK (CLAUDE.md).

6. **Store links.** Once the web build is deployed, confirm `/privacy.html`,
   `/terms.html` and `/delete-account.html` load publicly. These are the URLs
   for the App Store / Play Console privacy and account-deletion fields.

7. Run `/verify-app` (backend pytest, flutter analyze + test) and report the
   totals.

Report: a table of each step, pass/fail, and the evidence. Fix nothing in
production without asking; code fixes go on a branch.

---

Before shipping, also replace the placeholder support address
`kSupportEmail` in `frontend/lib/models/legal_content.dart` with a real
inbox, then run `dart run tool/gen_legal_pages.dart` from `frontend/` and rebuild
the web app.
