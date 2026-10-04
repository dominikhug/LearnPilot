# LearnPilot

Turns an uploaded document into a map of prerequisites and tutors the learner through it from the basics upward. See [CONCEPT.md](CONCEPT.md).

## Development

Open the repo in the dev container (includes PostgreSQL). Copy `.env.example` to `.env` and set at least `APP_PASSWORD` and `SESSION_SECRET`.

```bash
# Backend (http://localhost:8000, API docs at /api/docs)
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn learnpilot.main:app --reload

# Frontend with hot reload (http://localhost:5173, proxies /api to the backend)
cd frontend
npm install
npm run dev
```

Checks:

```bash
cd backend && uv run pytest && uv run ruff check . && uv run ruff format --check .
cd frontend && npm run build
```

## Deployment (Railway)

One service built from the root `Dockerfile` plus the PostgreSQL add-on. Set `DATABASE_URL` (reference the add-on), `APP_PASSWORD`, `SESSION_SECRET` and `ANTHROPIC_API_KEY` as service variables. Migrations run automatically on start; the health check is `/api/health`.
