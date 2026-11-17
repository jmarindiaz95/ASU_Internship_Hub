import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for line in (ROOT / ".env").read_text().splitlines() if (ROOT / ".env").exists() else []:
    if line.strip() and not line.lstrip().startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))

DATABASE_URL = os.environ.get("DATABASE_URL", "dbname=webgis")
PORT = int(os.environ.get("PORT", "8080"))
AUTO_REFRESH = os.environ.get("AUTO_REFRESH", "1").lower() not in ("0", "false", "no")
REFRESH_POLL_SECONDS = max(10, float(os.environ.get("REFRESH_POLL_SECONDS", "60")))
MAX_AGE = int(os.environ.get("MAX_VERIFICATION_AGE_DAYS", "7"))
CAMPUS = {"name": "ASU Memorial Union", "latitude": 33.4177504, "longitude": -111.9343818}
CENSUS_GEOCODER_URL = os.environ.get('CENSUS_GEOCODER_URL','https://geocoding.geo.census.gov/geocoder/locations/onelineaddress')
COMPANY_LOOKUP_URL = os.environ.get('COMPANY_LOOKUP_URL','https://overpass-api.de/api/interpreter')
