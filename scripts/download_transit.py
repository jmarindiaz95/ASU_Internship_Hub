"""Download and validate Valley Metro transit datasets for ASU Internship Hub.

Datasets:
- Bus routes: Valley Metro ArcGIS FeatureServer
- Light rail: Valley Metro ArcGIS FeatureServer (filtered to SERVICE='Light Rail')

Saves to:
- static/data/bus_routes.geojson
- static/data/light_rail.geojson
"""

import json
import os
import sys
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "static", "data")

SERVICES = {
    "bus": {
        "name": "Bus Routes",
        "filename": "bus_routes.geojson",
        "url": "https://services2.arcgis.com/2t1927381mhTgWNC/arcgis/rest/services/ValleyMetroBusRoutes/FeatureServer/0/query?where=1%3D1&outFields=%2A&outSR=4326&f=geojson",
        "count_url": "https://services2.arcgis.com/2t1927381mhTgWNC/arcgis/rest/services/ValleyMetroBusRoutes/FeatureServer/0/query?where=1%3D1&returnCountOnly=true&f=json",
        "expected_service": None,
    },
    "rail": {
        "name": "Light Rail",
        "filename": "light_rail.geojson",
        "url": "https://services2.arcgis.com/2t1927381mhTgWNC/arcgis/rest/services/ValleyMetroRail/FeatureServer/0/query?where=SERVICE%3D%27Light%20Rail%27&outFields=%2A&outSR=4326&f=geojson",
        "count_url": "https://services2.arcgis.com/2t1927381mhTgWNC/arcgis/rest/services/ValleyMetroRail/FeatureServer/0/query?where=SERVICE%3D%27Light%20Rail%27&returnCountOnly=true&f=json",
        "expected_service": "Light Rail",
    },
}


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "ASU-Internship-Hub/2.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def extract_points(coords):
    if not coords:
        return
    if isinstance(coords[0], (int, float)):
        yield coords
    else:
        for sub in coords:
            yield from extract_points(sub)


def download_and_validate(key, cfg):
    os.makedirs(DATA_DIR, exist_ok=True)
    target_path = os.path.join(DATA_DIR, cfg["filename"])

    print(f"Checking expected count for {cfg['name']}...")
    count_data = json.loads(fetch(cfg["count_url"]).decode("utf-8"))
    expected_count = count_data.get("count")
    print(f"Service returned count: {expected_count}")

    print(f"Downloading {cfg['name']} from {cfg['url']}...")
    raw_bytes = fetch(cfg["url"])
    with open(target_path, "wb") as f:
        f.write(raw_bytes)

    size = os.path.getsize(target_path)
    print(f"Saved {target_path} ({size} bytes)")

    data = json.loads(raw_bytes.decode("utf-8"))
    assert data.get("type") == "FeatureCollection", "Expected FeatureCollection"
    features = data.get("features", [])
    print(f"Features in downloaded GeoJSON: {len(features)}")
    assert len(features) == expected_count, f"Count mismatch: {len(features)} != {expected_count}"

    for i, f in enumerate(features):
        assert f.get("type") == "Feature", f"Item {i} is not Feature"
        geom = f.get("geometry")
        assert geom, f"Feature {i} missing geometry"
        assert geom.get("type") in ("LineString", "MultiLineString"), f"Feature {i} unexpected geometry {geom.get('type')}"
        pts = list(extract_points(geom.get("coordinates")))
        assert pts, f"Feature {i} has empty coordinates"

    if cfg["expected_service"]:
        services = set(f["properties"].get("SERVICE") for f in features)
        assert services == {cfg["expected_service"]}, f"Unexpected services: {services}"
        print(f"Confirmed service values: {services}")

    print(f"Validation successful for {cfg['name']}.\n")


def main():
    for key, cfg in SERVICES.items():
        download_and_validate(key, cfg)
    print("All transit datasets downloaded and verified successfully.")


if __name__ == "__main__":
    main()
