-- DRAFT (KAN-18): needs the schema owner's agreement before it is applied.
--
-- What starting a call needs (voice-pipeline/BACKEND.md):
--
-- 1. scam_scenarios.ask: what the caller asks the user to do, e.g. "Send $500
--    in gift cards". The call context requires it and must never invent one,
--    so existing rows stay null and cannot start a call until an ask is set.
-- 2. calls: the one-time token the browser hands to the voice pipeline, and
--    the setup the pipeline receives when it claims the call. Only a SHA-256
--    hash of the token is stored. The context is built and validated when the
--    call is created, so a consent is never used up by a call that cannot run.
-- 3. friendships: each user's private note about a friend, e.g. "We met at
--    UofT. Calls me Bo." A friendship is one row shared by both users, so each
--    side has its own column: requester_notes is written by requester_id,
--    addressee_notes by addressee_id. When a user starts a call with a
--    friend's voice, the user's own note about that friend goes into the call
--    context (details), so the AI caller knows what the user knows. The
--    100-character limit matches a call context detail, so a note is never
--    cut short.
--
-- AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.

alter table public.scam_scenarios
  add column ask text
    check (ask is null or char_length(btrim(ask)) between 1 and 160);

alter table public.calls
  add column claim_token_hash     text,         -- hex SHA-256 of the token in ws_url
  add column claim_expires_at     timestamptz,  -- the token cannot be claimed after this
  add column call_context         jsonb,        -- validated CallContext for the pipeline
  add column max_duration_seconds int check (max_duration_seconds > 0);

alter table public.friendships
  add column requester_notes text check (char_length(requester_notes) <= 100),
  add column addressee_notes text check (char_length(addressee_notes) <= 100);

-- Row Level Security lets both friends read the whole row, which would show
-- each of them the other's note. Clients never read this table directly (the
-- backend does, with the secret key), so clients lose access to the note
-- columns only; the other columns stay readable as before.
revoke select on public.friendships from anon, authenticated;
grant select (id, requester_id, addressee_id, status, created_at, accepted_at)
  on public.friendships to authenticated;
