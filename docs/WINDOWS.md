# Windows quick start

Use Docker Desktop; Windows does not need a separate Python or PostgreSQL installation for this setup.

## 1. Install Docker

Install [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/) with the WSL 2 backend and Linux containers. Follow the installer prompts and restart Windows if requested.

If WSL needs installation, open PowerShell as administrator, run `wsl --install`, and restart. See [Microsoft's WSL instructions](https://learn.microsoft.com/en-us/windows/wsl/install).

Open Docker Desktop and wait until its engine is running. In a normal PowerShell window, check:

```powershell
docker version
docker compose version
docker info --format '{{.OSType}}'
```

The last command should print `linux`.

## 2. Get this version

With Git installed:

```powershell
git clone https://github.com/jmarindiaz95/ASU_Internship_Hub.git
cd ASU_Internship_Hub
```

Alternatively, on the repository’s `main` branch, choose **Code → Download ZIP**, extract it, and open PowerShell in the extracted folder containing `compose.yaml`.

## 3. Start and verify

```powershell
docker compose up --build -d
docker compose ps
```

Wait for `db` and `web` to be healthy, then open [localhost:8080](http://localhost:8080/).

```powershell
Invoke-RestMethod http://localhost:8080/api/health
```

Expected: `status` is `ok` and `database` is `connected`. Career disciplines should appear immediately; new job collection takes a few minutes. No workbook or Mac database is required.

If startup fails, collect the error with:

```powershell
docker compose logs --tail 80 web db
```

## Updates and stopping

For a Git clone:

```powershell
git pull
docker compose up --build -d
```

Stop with `docker compose down`. The database remains in its Docker volume. The Mac-only **Start Hub.command** and Live Server are not needed for this workflow.
