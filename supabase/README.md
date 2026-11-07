# Supabase

- `migrations/`: SQL migrations, named `<number>_migration<number>.sql` so they sort in the order
  they must run. This is the source of truth for the database.
- `database-schema.md`: explains the schema (tables, relationships, RLS and why).

## Applying migrations

The Supabase project is connected to this repository through the Supabase GitHub integration. When
a PR that adds a file to `supabase/migrations/` is merged into the production branch, Supabase runs
the new migrations on the database automatically. Do **not** also run them in the SQL Editor.

- Supabase records which migrations have run (in `supabase_migrations.schema_migrations`) and only
  runs new ones, in filename order.
- Each migration runs in a single transaction, so a failing one changes nothing.
- The result of each deploy is shown on the merged PR and under **Project Settings → Integrations →
  GitHub** in the dashboard.

Integration settings this relies on (**Project Settings → Integrations → GitHub**):

- **Working directory**: `.` (the `supabase/` folder is at the repository root)
- **Production branch**: `main`
- **Deploy to production**: on

| Migration | What it does |
|---|---|
| `0001_migration1.sql` | Initial schema: enums, tables, indexes, `updated_at` triggers and Row Level Security |

## Making a schema change

Never edit a migration that has already been merged; it will not run again, so the change would
never reach the database. Add a new file with the next number instead, e.g.
`migrations/0002_migration2.sql`, and:

1. Write the SQL for the change only (e.g. `alter table …`), not the whole schema.
2. Update `database-schema.md` to match.
3. Add a row to the table above.
4. Open a PR. The migration runs when it is merged.
