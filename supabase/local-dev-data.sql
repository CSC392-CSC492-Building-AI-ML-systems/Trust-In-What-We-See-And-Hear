-- Demo data for the local dev stack only. `npm run setup`, `npm run dev` and `npm run docker` run
-- this after applying migrations. Nothing applies it to a hosted project: it is deliberately not
-- supabase/seed.sql, which Supabase preview branches would load.
--
-- Safe to run repeatedly: it restores whatever is missing and leaves everything else alone.
--
-- Two signed-up, onboarded friends (password `demo-password`), each with a usable consent to the
-- other's voice, and one scenario, so POST /api/v1/calls works straight away.
-- The voices are marked ready without a stored recording; upload one through
-- PUT /api/v1/users/me/voice to test anything that reads the audio.
--
-- One DO block because `supabase db query` runs a single statement.

do $$
begin

insert into auth.users (
  instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
  raw_app_meta_data, raw_user_meta_data, created_at, updated_at,
  confirmation_token, recovery_token, email_change, email_change_token_new
)
select
  '00000000-0000-0000-0000-000000000000', demo.id, 'authenticated', 'authenticated', demo.email,
  extensions.crypt('demo-password', extensions.gen_salt('bf')), now(),
  '{"provider": "email", "providers": ["email"]}'::jsonb,
  jsonb_build_object('first_name', demo.first_name, 'last_name', demo.last_name, 'email_verified', true),
  now(), now(), '', '', '', ''
from (values
  ('00000000-0000-4000-8000-00000000a11c'::uuid, 'alice@example.com', 'Alice', 'Demo'),
  ('00000000-0000-4000-8000-000000000b0b'::uuid, 'bob@example.com', 'Bob', 'Demo')
) as demo (id, email, first_name, last_name)
on conflict do nothing;

insert into auth.identities (
  provider_id, user_id, identity_data, provider, last_sign_in_at, created_at, updated_at
)
select
  u.id::text, u.id,
  jsonb_build_object('sub', u.id::text, 'email', u.email, 'email_verified', true),
  'email', now(), now(), now()
from auth.users u
where u.id in ('00000000-0000-4000-8000-00000000a11c', '00000000-0000-4000-8000-000000000b0b')
on conflict do nothing;

insert into public.users (
  id, username, first_name, last_name, privacy_policy_version, privacy_policy_accepted_at
)
select demo.id, demo.username, demo.first_name, 'Demo', '2026-11', now()
from (values
  ('00000000-0000-4000-8000-00000000a11c'::uuid, 'alice_demo', 'Alice'),
  ('00000000-0000-4000-8000-000000000b0b'::uuid, 'bob_demo', 'Bob')
) as demo (id, username, first_name)
join auth.users u on u.id = demo.id
on conflict do nothing;

if (
  select count(*)
  from public.users p
  join auth.users u on u.id = p.id
  where p.id in ('00000000-0000-4000-8000-00000000a11c', '00000000-0000-4000-8000-000000000b0b')
    and u.deleted_at is null
) <> 2 then
  raise exception 'A demo account was deleted, or another local account uses a demo email or '
    'username (alice@example.com, bob@example.com, alice_demo, bob_demo). '
    '`npm run supabase db reset` starts over.';
end if;

insert into public.friendships (requester_id, addressee_id, status, accepted_at)
values (
  '00000000-0000-4000-8000-00000000a11c', '00000000-0000-4000-8000-000000000b0b',
  'accepted', now()
)
on conflict (least(requester_id, addressee_id), greatest(requester_id, addressee_id))
do update set status = 'accepted', accepted_at = coalesce(friendships.accepted_at, now());

insert into public.voices (
  user_id, sample_storage_path, sample_duration_seconds, status, voice_owner_consent_at
)
select u.id, u.id || '/demo.wav', 20, 'ready', now()
from public.users u
where u.id in ('00000000-0000-4000-8000-00000000a11c', '00000000-0000-4000-8000-000000000b0b')
on conflict do nothing;

insert into public.scam_scenarios (
  id, title, category, description, system_prompt, ask, red_flags
)
values (
  '00000000-0000-4000-8000-00000000c411',
  'Friend in trouble (demo)',
  'grandparent_emergency',
  'A friend calls in a panic and needs money right away.',
  'Act as the friend. Sound stressed, and press for a quick decision.',
  'Send $500 in gift cards',
  '["urgency", "secrecy request", "gift cards"]'
)
on conflict do nothing;

-- A consent starts one call, so each run tops up any direction that has none left.
insert into public.scam_consents (
  requester_id, consenter_id, friendship_id, status, responded_at, expires_at
)
select pair.requester_id, pair.consenter_id, f.id, 'accepted', now(), now() + interval '1 day'
from public.friendships f
cross join lateral (values
  (f.requester_id, f.addressee_id),
  (f.addressee_id, f.requester_id)
) as pair (requester_id, consenter_id)
where f.status = 'accepted'
  and f.requester_id in ('00000000-0000-4000-8000-00000000a11c', '00000000-0000-4000-8000-000000000b0b')
  and f.addressee_id in ('00000000-0000-4000-8000-00000000a11c', '00000000-0000-4000-8000-000000000b0b')
  and not exists (
    select 1
    from public.scam_consents c
    where c.requester_id = pair.requester_id
      and c.consenter_id = pair.consenter_id
      and c.status = 'accepted'
      and c.used_at is null
      and c.expires_at > now()
  );

end
$$;
