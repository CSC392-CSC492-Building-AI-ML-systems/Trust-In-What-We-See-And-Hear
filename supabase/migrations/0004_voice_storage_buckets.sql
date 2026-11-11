-- DRAFT (KAN-17): needs the schema owner's agreement before it is applied.
--
-- Private buckets for voice reference samples and any files the voice
-- pipeline derives from them. No storage.objects policies are added, so
-- clients can neither read nor write these buckets; only the backend can,
-- with the secret key. The size limit and types match the backend's defaults
-- (VOICE_SAMPLE_MAX_BYTES and app/modules/voices/audio.py).
--
-- AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values
  (
    'voice-samples', 'voice-samples', false, 10485760,
    array['audio/wav']
  ),
  ('voice-models', 'voice-models', false, null, null)
on conflict (id) do nothing;
