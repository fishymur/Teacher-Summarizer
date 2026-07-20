# Deploying the Curriculum Coherence Layer (small private group)

The app is one long-running Python process. It reads all config from environment
variables, so the same code runs locally and hosted. For a persistent, free,
no-server-to-manage setup, use managed Postgres (Neon) via `DATABASE_URL`:

| Variable              | Purpose                                             | Example                     |
|-----------------------|-----------------------------------------------------|-----------------------------|
| `HOST`                | Bind address. Use `0.0.0.0` when hosted.            | `0.0.0.0`                   |
| `PORT`                | Port to listen on (platforms usually inject this).  | `8000`                      |
| `DATABASE_URL`        | Managed Postgres (Neon). Takes priority over CCL_DB.| `postgresql://u:p@host/db?sslmode=require` |
| `CCL_DB`              | SQLite file path (used only if DATABASE_URL unset). | `/data/ccl.db`              |
| `CCL_ACCESS_PASSWORD` | Shared password gating the whole app (Basic Auth).  | *(a long random string)*    |
| `ANTHROPIC_API_KEY`   | Optional. Set for the live model; omit for the stub.| `sk-ant-...`                |

## Persistence

Point `DATABASE_URL` at a Neon Postgres database (free tier, keeps your data,
supports pgvector for future semantic search). Because the data lives in Neon,
the app container is stateless — a free/ephemeral host is fine and nothing is
lost on restart, and no disk volume is required. If instead you set only
`CCL_DB`, the app uses a SQLite file, which must sit on a persistent disk to
survive restarts.

## The access gate (your "private group" control)

Set `CCL_ACCESS_PASSWORD` to a long random string and share it with your group.
Every page and API call then requires it (the browser shows a login box once per
session; username can be anything). Leave it unset only for local use.

Two things to understand:
- The password keeps outsiders out. It does NOT decide teacher vs student — the
  page still declares that and the RBAC layer enforces it. Everyone with the
  password can pick either interface. That's the right level for a trusted group;
  per-person accounts are a later step.
- Basic Auth sends the password base64-encoded (not encrypted), so it must run
  over HTTPS. The platforms below terminate HTTPS for you automatically. Never
  expose it over plain `http://`.

`GET /healthz` is intentionally left open (no password) so the platform's health
check passes.

## Step 1 — Create the database (Neon, free)

1. Sign up at neon.tech (no credit card required) and create a project. Choose a
   region near your users.
2. Copy the connection string it gives you — it looks like
   `postgresql://user:pass@ep-xxx.region.aws.neon.tech/dbname?sslmode=require`.
   That whole string is your `DATABASE_URL`.

That's the persistence layer. The app creates its own tables on first run.

## Step 2 — Host the app

### Option A — Render (simplest; recommended)

1. Push this project to a GitHub repo (private is fine).
2. On Render: New → Web Service → connect the repo. Render detects the
   `Dockerfile`.
3. Pick any instance — since the data lives in Neon, you do **not** need a paid
   instance or a disk; the free instance is fine (it may cold-start after idle).
4. Environment variables: `HOST=0.0.0.0`, `DATABASE_URL=<your Neon string>`,
   `CCL_ACCESS_PASSWORD=<your random string>`, and optionally
   `ANTHROPIC_API_KEY=<your key>`. (Render injects `PORT` itself.)
5. Deploy. You get an `https://<name>.onrender.com` URL. Share it plus the
   password with your group.

## Option B — Fly.io

1. Install flyctl and `fly launch` in this folder (it reads the `Dockerfile`;
   decline its offer to deploy immediately).
2. Create a volume: `fly volumes create data --size 1`, and mount it at `/data`
   in `fly.toml`.
3. Set secrets: `fly secrets set HOST=0.0.0.0 CCL_DB=/data/ccl.db \
   CCL_ACCESS_PASSWORD=<random> ANTHROPIC_API_KEY=<key>`.
4. `fly deploy`. Fly provides an HTTPS URL.

## Option C — a small always-on VM

Run the container with a mounted volume (see the header of `Dockerfile`), and
put a TLS reverse proxy (Caddy or nginx) in front so it's served over HTTPS.

## Resetting

Delete the DB file on the volume (or bump `SCHEMA_VERSION` in the code) to wipe
back to the seeded demo state.

## Things I can't do for you

Creating the host account, pasting `ANTHROPIC_API_KEY` / `CCL_ACCESS_PASSWORD`
as secrets, and clicking deploy are steps you do in the platform's own dashboard
— secrets should go in there directly, never through a chat.
