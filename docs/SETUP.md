# Classmate setup

## Docker: Mac, Windows, or Linux

On Windows, follow the [Windows quick start](WINDOWS.md) for Docker installation and PowerShell commands.

Install [Docker Desktop](https://docs.docker.com/get-started/get-docker/) or Docker Engine with Compose. Start Docker, then run:

```sh
git clone --branch codex/finance-postgis-hub https://github.com/jmarindiaz95/ASU_Internship_Hub.git
cd ASU_Internship_Hub
docker compose up --build -d
```

Use the branch containing the application changes. Open [127.0.0.1:8080](http://127.0.0.1:8080/).

This builds the app and a separate PostgreSQL/PostGIS database. It does not connect to Jesus's local `webgis` database. Tables and employer identities are initialized automatically; public job collection starts in the background. The first listings can take a few minutes to appear. No workbook is required.

Each classmate has an independent dataset. A named Docker volume retains it between restarts. Only the web page is exposed on the local computer; the database has no published port. The supplied database credentials are for this isolated development setup.

## Pull updates

```sh
git pull
docker compose up --build -d
```

This rebuilds changed code and keeps the database. Make a branch for your edits before updating shared work.

```sh
docker compose logs -f web
docker compose exec web python manage.py collect --source az_state
docker compose exec web python -m pytest -q
docker compose down
```

The last command stops the app and database while retaining the data volume. If port 8080 is busy, set `HUB_PORT=8081` in a root `.env` file and open port 8081 instead.

## Existing PostgreSQL: native setup

Requires Python 3.12+ and a local PostgreSQL installation with PostGIS available. In a Mac/Linux terminal:

```sh
createdb internship_hub
psql -d internship_hub -c 'CREATE EXTENSION IF NOT EXISTS postgis;'
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
.venv/bin/python manage.py init-db
.venv/bin/python server.py
```

Set `DATABASE_URL` in `.env` for your own database. On Mac, **Start Hub.command** can reopen the configured app. Keep private connection strings and local files out of Git.

## Code, data, and hosting

GitHub shares the code, schema, and source configuration. It does not share PostgreSQL records. Local imports and exports belong in `data/`; generated reports belong in `reports/`. Both are ignored by Git.

To give everyone the same live dataset, deploy one app and PostGIS database and share its URL. GitHub Pages alone cannot run this Python/PostGIS app. Shared hosting is a separate deployment step.
