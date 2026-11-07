-- Initial schema: enums, tables, indexes and Row Level Security.
-- See supabase/database-schema.md for the reasoning behind each table.

-- ---------------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------------

create type public.friendship_status as enum ('pending', 'accepted');

create type public.consent_status as enum ('pending', 'accepted', 'declined');

create type public.voice_status as enum ('uploaded', 'training', 'ready', 'failed');

create type public.scam_category as enum (
  'grandparent_emergency', 'executive_impersonation', 'bank_fraud_alert',
  'government_agency', 'tech_support', 'romance', 'prize_lottery', 'other'
);

create type public.call_status as enum ('queued', 'in_progress', 'completed', 'failed', 'canceled');

create type public.call_outcome as enum (
  'recognized_scam',      -- hung up / called it out / verified identity
  'partially_complied',   -- shared some info but stopped before money/credentials
  'fully_complied',       -- agreed to send money or shared sensitive info
  'inconclusive'          -- call too short to grade
);

-- ---------------------------------------------------------------------------
-- users: app profile, created by the backend when onboarding finishes
-- ---------------------------------------------------------------------------

create table public.users (
  id                        uuid primary key references auth.users(id) on delete cascade,
  username                  text unique not null,  -- how friends find each other
  display_name              text not null,
  preferred_language        text not null default 'en',
  verified_at               timestamptz,           -- null = ID not verified (VerifyUser)
  onboarding_completed_at   timestamptz,           -- null = onboarding not finished (finishOnboarding)
  -- Answers used to tailor the scenario. Null if never given or removed by the user.
  -- e.g. {"city": "Toronto", "pet_name": "Max", "school": "UofT"}
  onboarding_answers        jsonb,
  privacy_policy_version    text not null,
  privacy_policy_accepted_at timestamptz not null,
  created_at                timestamptz not null default now(),
  updated_at                timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- friendships
-- ---------------------------------------------------------------------------

create table public.friendships (
  id            uuid primary key default gen_random_uuid(),
  requester_id  uuid not null references public.users(id) on delete cascade,
  addressee_id  uuid not null references public.users(id) on delete cascade,
  status        public.friendship_status not null default 'pending',
  created_at    timestamptz not null default now(),
  accepted_at   timestamptz,
  check (requester_id <> addressee_id)
);

-- Only one row per pair, in either direction
create unique index friendships_pair_idx on public.friendships (
  least(requester_id, addressee_id), greatest(requester_id, addressee_id)
);
create index friendships_requester_id_idx on public.friendships(requester_id);
create index friendships_addressee_id_idx on public.friendships(addressee_id);

-- ---------------------------------------------------------------------------
-- voices
-- ---------------------------------------------------------------------------

create table public.voices (
  id                    uuid primary key default gen_random_uuid(),
  user_id               uuid unique not null references public.users(id) on delete cascade,
  sample_storage_path   text,                      -- Supabase Storage: voice-samples/{user_id}/{voice_id}.mp3
  sample_duration_seconds numeric(6,2),
  model_storage_path    text,                      -- Supabase Storage: voice-models/{user_id}/{voice_id}.pt, set once training completes
  status                public.voice_status not null default 'uploaded',
  error_message         text,
  -- User confirms the sample is their own voice and agrees to it being cloned
  voice_owner_consent_at timestamptz not null,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- scam_consents
-- ---------------------------------------------------------------------------

create table public.scam_consents (
  id            uuid primary key default gen_random_uuid(),
  requester_id  uuid not null references public.users(id) on delete cascade,  -- who will receive the call
  consenter_id  uuid not null references public.users(id) on delete cascade,  -- whose profile and voice are used
  status        public.consent_status not null default 'pending',
  requested_at  timestamptz not null default now(),
  responded_at  timestamptz,
  expires_at    timestamptz,
  check (requester_id <> consenter_id),
  check (status <> 'accepted' or expires_at is not null)
);

-- Only one unanswered request at a time for each requester and friend
create unique index scam_consents_one_pending_idx on public.scam_consents(requester_id, consenter_id)
  where status = 'pending';
create index scam_consents_requester_id_idx on public.scam_consents(requester_id, status);
create index scam_consents_consenter_id_idx on public.scam_consents(consenter_id, status);

-- ---------------------------------------------------------------------------
-- scam_scenarios
-- ---------------------------------------------------------------------------

create table public.scam_scenarios (
  id              uuid primary key default gen_random_uuid(),
  created_by      uuid references public.users(id) on delete cascade,  -- null = built-in
  title           text not null,
  category        public.scam_category not null,
  difficulty      smallint not null default 2 check (difficulty between 1 and 3),
  description     text not null,                   -- shown to the user when choosing a scenario
  system_prompt   text not null,                   -- OpenAI prompt for the scammer agent
  red_flags       jsonb not null default '[]'::jsonb, -- ["urgency", "secrecy request", "gift cards"]
  max_duration_seconds int not null default 300,
  is_active       boolean not null default true,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create index scam_scenarios_created_by_idx on public.scam_scenarios(created_by);

-- ---------------------------------------------------------------------------
-- calls
-- ---------------------------------------------------------------------------

create table public.calls (
  id                    uuid primary key default gen_random_uuid(),
  user_id               uuid not null references public.users(id) on delete cascade,  -- who receives the call
  persona_user_id       uuid references public.users(id) on delete set null,          -- who is impersonated
  consent_id            uuid references public.scam_consents(id) on delete set null,
  scenario_id           uuid references public.scam_scenarios(id) on delete set null,
  -- snapshots so history still reads well if a user or scenario is deleted
  persona_name          text not null,
  scenario_title        text not null,
  status                public.call_status not null default 'queued',
  started_at            timestamptz,
  ended_at              timestamptz,
  duration_seconds      int,
  recording_storage_path text,                     -- optional; subject to retention policy
  failure_reason        text,
  created_at            timestamptz not null default now()
);

create index calls_user_id_created_at_idx on public.calls(user_id, created_at desc);
create index calls_persona_user_id_idx on public.calls(persona_user_id);
create index calls_consent_id_idx on public.calls(consent_id);
create index calls_scenario_id_idx on public.calls(scenario_id);

-- ---------------------------------------------------------------------------
-- call_results
-- ---------------------------------------------------------------------------

create table public.call_results (
  call_id             uuid primary key references public.calls(id) on delete cascade,
  outcome             public.call_outcome not null,
  score               smallint not null check (score between 0 and 100),
  summary             text not null,
  did_well            jsonb not null default '[]'::jsonb,   -- ["Asked a personal question only the real person would know"]
  to_improve          jsonb not null default '[]'::jsonb,   -- ["Agreed to keep the call secret"]
  red_flags_noticed   jsonb not null default '[]'::jsonb,
  red_flags_missed    jsonb not null default '[]'::jsonb,
  sensitive_info_shared boolean not null default false,
  model_version       text,                                  -- which OpenAI model/prompt graded it
  created_at          timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Row Level Security
--
-- All writes go through the backend with the secret (service role) key, which
-- bypasses RLS. Clients only get the read policies below.
-- ---------------------------------------------------------------------------

alter table public.users          enable row level security;
alter table public.friendships    enable row level security;
alter table public.voices         enable row level security;
alter table public.scam_consents  enable row level security;
alter table public.scam_scenarios enable row level security;
alter table public.calls          enable row level security;
alter table public.call_results   enable row level security;

-- users: a user can only read their own row (this includes onboarding answers)
create policy "own user (read)" on public.users
  for select using (id = auth.uid());

-- friendships / consents: readable by either user involved
create policy "own friendships (read)" on public.friendships
  for select using (auth.uid() in (requester_id, addressee_id));
create policy "own consents (read)" on public.scam_consents
  for select using (auth.uid() in (requester_id, consenter_id));

-- voices / calls: owned directly by user_id
create policy "own voice (read)" on public.voices
  for select using (user_id = auth.uid());
create policy "own calls (read)" on public.calls
  for select using (user_id = auth.uid());

-- child table: owned through its parent
create policy "own call results (read)" on public.call_results
  for select using (exists (select 1 from public.calls c
                            where c.id = call_id and c.user_id = auth.uid()));

-- scenarios: any signed-in user can read active built-in scenarios and their own
create policy "read scenarios" on public.scam_scenarios
  for select to authenticated
  using ((created_by is null and is_active) or created_by = auth.uid());
