-- Replace users.display_name with first_name and last_name.
-- Existing names are split on the first whitespace: "Alex Morgan" -> "Alex" / "Morgan".
-- A one-word name keeps the word as first_name and gets an empty last_name.

alter table public.users
  add column first_name text,
  add column last_name  text;

update public.users
set
  first_name = split_part(btrim(display_name), ' ', 1),
  last_name  = btrim(substr(btrim(display_name), length(split_part(btrim(display_name), ' ', 1)) + 1));

alter table public.users
  alter column first_name set not null,
  alter column last_name  set not null,
  drop column display_name;
