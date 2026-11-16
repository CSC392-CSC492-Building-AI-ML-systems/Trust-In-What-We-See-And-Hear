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
