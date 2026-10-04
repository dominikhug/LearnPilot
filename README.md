# LearnPilot

Turns an uploaded document into a map of prerequisites and tutors the learner through it from the basics upward. See [CONCEPT.md](CONCEPT.md).

## Development

Development happens in a VS Code dev container. It brings Python, Node, `uv`, Claude Code and a PostgreSQL database, so nothing else needs to be installed locally.

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (on Windows with the WSL 2 backend), running
- [VS Code](https://code.visualstudio.com/) with the **Dev Containers** extension (`ms-vscode-remote.remote-containers`)

### Open the repo in the dev container

1. Clone the repo and open the folder in VS Code.
2. Copy `.env.example` to `.env` and set at least `APP_PASSWORD` and `SESSION_SECRET` (and `ANTHROPIC_API_KEY` for LLM features). The container reads `.env` when it starts.
3. Open the Command Palette (`Ctrl+Shift+P`) and run **Dev Containers: Reopen in Container**, or click **Reopen in Container** in the notification VS Code shows on opening the folder. The first build takes a few minutes.
4. VS Code reloads inside the container. The terminal now runs in the container, with the repo at `/workspaces/LearnPilot`.

After changing `.env` or anything in `.devcontainer/`, run **Dev Containers: Rebuild Container**. The repo is mounted from the host and the database and Claude Code login live in Docker volumes, so a rebuild loses nothing.

### Run the app

Use two terminals (the **+** button in the terminal panel):

```bash
# Terminal 1 – backend (http://localhost:8000, API docs at /api/docs)
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn learnpilot.main:app --reload

# Terminal 2 – frontend with hot reload (http://localhost:5173, proxies /api to the backend)
cd frontend
npm install
npm run dev
```

Open http://localhost:5173 in the browser (VS Code forwards the ports) and log in with `APP_PASSWORD`. To run it like production instead, build the frontend with `npm run build` and open http://localhost:8000; the backend serves the build.

If login succeeds but you land on the login screen again, the browser rejected the session cookie over plain `http://`: add `SESSION_COOKIE_SECURE=false` to `.env` and restart the backend.

### Checks

```bash
cd backend && uv run pytest && uv run ruff check . && uv run ruff format --check .
cd frontend && npm run build
```

## Deployment (Railway)

One service built from the root `Dockerfile` plus the PostgreSQL add-on. Set `DATABASE_URL` (reference the add-on), `APP_PASSWORD`, `SESSION_SECRET` and `ANTHROPIC_API_KEY` as service variables. Migrations run automatically on start; the health check is `/api/health`.
