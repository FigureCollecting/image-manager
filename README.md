# image-manager

Production-ready image microservice built with FastAPI, SQLAlchemy 2.x, Postgres, Celery, and S3/MinIO. Includes multi-tenant links, albums, tags, age-gating with safe variants, content-addressable storage, background transforms, and search.

Local dev (Docker/Coolify-friendly):

- Copy `.env.example` to `.env` and adjust if needed.
- Start stack: `make dev-up`
- Initialize DB: `make db-upgrade`
- Watch logs: `make logs`
- Get a local token: `make token USER_ID=<uuid> TENANT_ID=<uuid>`

Endpoints (highlights):

- Auth: `POST /auth/dev-token` (local only)
- Upload: `POST /images/initiate-upload`, `POST /images/complete`
- Versions: `POST /images/{id}/versions`, visibility and safe-alt operations
- Serving: `GET /serve/{image_id}@{version_id}`, `GET /public/{image_id}@{version_id}`
- Albums: CRUD, share link, cover mosaic
- Tags/Search: tag images/albums and search by text and tags

Storage layout:

- Originals: `{sha[:2]}/{sha[2:4]}/{sha}.{ext}`
- Staging: `uploads/{uuid}/{filename}`
- Derived: `versions/{image_id}/v{no}-{shortid}.{ext}`
- Album covers: `albums/{album_id}/cover-{hash}.webp`

Switching MinIO → other S3 (R2/B2/Wasabi):

- Update `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET` in `.env` or compose env.
- Keep `S3_REGION` to a supported value; set `S3_SECURE=true` for TLS endpoints.

Hosted Postgres (Neon/Supabase):

- Set `DATABASE_URL` accordingly; ensure extensions `citext` and `pg_trgm` are available. Run `make db-upgrade`.

Coolify deploy tips:

- Use the provided `Dockerfile.api` and `Dockerfile.worker` images.
- Provision Postgres, Redis, and MinIO (or external S3) services; set env vars in the app.
- Configure healthcheck on `/healthz`.

Testing:

- Run locally: `make test` (requires Python 3.12 + Poetry). Integration tests are skipped unless backing services are available.

