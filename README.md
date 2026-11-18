# ASU Internship Hub — Version 2

A local WebGIS app for jobs and internships near ASU Tempe, with career filters, an interactive map, and automatic collection from public career sites.

Version 2 runs from the repository root. The original static prototype is preserved in [version-1/](version-1/). See [Changelog](CHANGELOG.md) for the summarized changes.

## Run with Docker

Install Docker and run this from the project folder:

```sh
docker compose up --build -d
```

Open [127.0.0.1:8080](http://127.0.0.1:8080/). This creates an independent PostGIS database and starts public job collection. The first results take a few minutes. Data persists between restarts.

See [Windows quick start](docs/WINDOWS.md) or [Setup](docs/SETUP.md) for installation and updates. On a configured Mac, **Start Hub.command** starts or reopens the native app. Live Server opens the launch page.

## Structure

| Path | Purpose |
| --- | --- |
| `hub/templates/index.html` | Page layout |
| `static/css/`, `static/js/`, `static/images/` | Styles, interaction, and app images |
| `hub/` | App endpoints, collection, and address lookup |
| `db/schema.sql` | Tables in the `internship_hub` schema |
| `config/` | Sources, refresh intervals, and company addresses |
| `docker/`, `compose.yaml` | Isolated app and database setup |
| `docs/`, `docs/images/` | Setup notes and documentation images |
| `data/`, `reports/` | Local inputs, exports, and generated reports; ignored by Git |
| `manage.py`, `scan_pages.py` | Database and collection commands |
| `tests/` | Backend and offline interface checks |
| `version-1/` | Original GitHub files, preserved unchanged |

## Data

Eight sources cover ASU Career Services, Arizona state jobs, Carvana, Axon, One Medical, Khalsa McBrearty, WP+D, and AccuPro Advisors. Checks run while the app is running: ASU every hour, state and large employer boards every 3 hours, and smaller employers every 6 hours. Use **Show latest jobs** to update the list without interrupting browsing.

Available results require a successful source check within 7 days and no known closure, missing page, or passed deadline. Spreadsheet-only examples stay hidden.

Job addresses take priority. Company headquarters or offices are labeled as references when the worksite is unknown. Unresolved locations remain listed. Coordinates and straight-line distances are approximate. Optional Valley Metro bus and light-rail overlays show route alignments; stops, live arrivals, and trip planning remain pending.

## Commands

```sh
docker compose exec web python manage.py collect
docker compose exec web python -m pytest -q
docker compose down
```

Connection settings belong in `.env`; personal databases are not included in Git. Run `manage.py init-db` after changing native source configuration. Node.js or `NODE_BINARY` enables offline JavaScript checks. See [Changelog](CHANGELOG.md) for development milestones.

Student project; not an official ASU service. Team: Jennifer Lavoe Yawa, Yahya Mohamed, Ryan Bishop, Jesus Marin Diaz, and Naji Araiza.
