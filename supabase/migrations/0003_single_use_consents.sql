-- DRAFT (KAN-16): needs the schema owner's agreement before it is applied.
--
-- Each accepted consent can start one call. `used_at` records when that
-- happened; the consent also expires (`expires_at`) if it is not used in time,
-- so a requester cannot hold an unused consent until the consenter forgets it.
-- The consenter can revoke an accepted consent before it is used; that gets
-- its own status, so a call already starting cannot still match it.
-- A consent is usable when:
--   status = 'accepted' and used_at is null and expires_at > now()
--
-- AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.

alter type public.consent_status add value 'revoked';

-- Each consent belongs to the friendship it was requested under and is deleted
-- with it, in the same statement, so no consent outlives the friendship or is
-- revived when the two users become friends again. The table has no rows yet
-- (no endpoint wrote to it before this migration), so the column can be not null.
alter table public.scam_consents
  add column friendship_id uuid not null references public.friendships(id) on delete cascade,
  add column used_at timestamptz,
  add constraint scam_consents_used_only_if_accepted
    check (used_at is null or status = 'accepted');

-- Lists the requester's usable consents (GET /consents).
create index scam_consents_friendship_id_idx on public.scam_consents(friendship_id);
create index scam_consents_usable_idx on public.scam_consents(requester_id, expires_at)
  where status = 'accepted' and used_at is null;
