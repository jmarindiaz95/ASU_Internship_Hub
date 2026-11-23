import json
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRANSIT_DATA = PROJECT_ROOT / "static" / "data"


@pytest.mark.parametrize(
    "filename",
    ["bus_routes.geojson", "light_rail.geojson"],
)
def test_transit_dataset_exists_and_has_features(filename):
    """Check that each transit dataset exists and contains geographic features."""
    path = TRANSIT_DATA / filename

    assert path.is_file(), f"Missing transit dataset: {filename}"

    data = json.loads(path.read_text(encoding="utf-8"))

    assert data["type"] == "FeatureCollection"
    assert isinstance(data["features"], list)
    assert len(data["features"]) > 0


@pytest.mark.parametrize(
    "filename",
    ["bus_routes.geojson", "light_rail.geojson"],
)
def test_transit_features_have_valid_geometry(filename):
    """Check that transit features contain supported geographic geometry."""
    path = TRANSIT_DATA / filename
    data = json.loads(path.read_text(encoding="utf-8"))

    for feature in data["features"]:
        assert feature["type"] == "Feature"
        assert isinstance(feature.get("properties"), dict)

        geometry = feature.get("geometry")
        assert geometry is not None
        assert geometry["type"] in {
            "LineString",
            "MultiLineString",
        }
        assert geometry.get("coordinates")
        
