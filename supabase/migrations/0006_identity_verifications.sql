-- Identity verification through Didit (backend/app/modules/verification).
--
-- One row per verification session the backend starts with Didit. It holds
-- only what is needed to follow the session: Didit's session ID and its
-- status. Names, dates of birth, document numbers and images stay with Didit
-- and are never copied here. users.verified_at records that a session passed.
--
-- Only the backend reads or writes this table (with the secret key), so Row
-- Level Security is on with no policies: clients can neither read nor write it.
--
-- AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.

create table public.identity_verifications (
  id                  uuid primary key default gen_random_uuid(),
  user_id             uuid not null references public.users(id) on delete cascade,
  provider_session_id text unique not null,  -- Didit's session_id
  -- Didit's status in snake case, e.g. 'Not Started' is stored as 'not_started'.
  status              text not null check (status in (
    'not_started', 'in_progress', 'in_review', 'awaiting_user', 'resubmitted',
    'approved', 'declined', 'expired', 'abandoned', 'kyc_expired'
  )),
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);

-- A user's latest session (GET /users/me/verification).
create index identity_verifications_user_created_idx
  on public.identity_verifications (user_id, created_at desc);

create trigger identity_verifications_set_updated_at
  before update on public.identity_verifications
  for each row execute function public.set_updated_at();

alter table public.identity_verifications enable row level security;
