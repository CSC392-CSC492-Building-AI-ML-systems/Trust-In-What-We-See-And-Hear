-- DRAFT (KAN-16): needs the schema owner's agreement before it is applied.
--
-- Each accepted consent can start one call. `used_at` records when that
-- happened; the consent also expires (`expires_at`) if it is not used in time,
-- so a requester cannot hold an unused consent until the consenter forgets it.
-- A consent is usable when:
--   status = 'accepted' and used_at is null and expires_at > now()

alter table public.scam_consents
  add column used_at timestamptz,
  add constraint scam_consents_used_only_if_accepted
    check (used_at is null or status = 'accepted');

-- Lists the requester's usable consents (GET /consents).
create index scam_consents_usable_idx on public.scam_consents(requester_id, expires_at)
  where status = 'accepted' and used_at is null;
