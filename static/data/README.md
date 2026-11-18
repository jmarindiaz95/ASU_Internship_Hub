# Valley Metro Transit Data

Static GeoJSON route datasets for the ASU Internship Hub interactive map.

> **Note:** These datasets represent static transit route alignments and geometries only. They do not include individual transit stops or stations, real-time vehicle tracking, live arrival schedules, or multi-modal trip planning.

## Datasets

### 1. Bus Routes (`bus_routes.geojson`)
- **Source:** Valley Metro ArcGIS FeatureServer
- **Source URL:** `https://services2.arcgis.com/2t1927381mhTgWNC/arcgis/rest/services/ValleyMetroBusRoutes/FeatureServer/0/query?where=1%3D1&outFields=%2A&outSR=4326&f=geojson`
- **Query Filter:** `where=1=1`
- **Download Date:** November 17, 2026
- **Feature Count:** 205 features
- **File Size:** 1,859,318 bytes (~1.77 MB)
- **Geometry Types:** `LineString`, `MultiLineString`
- **Key Fields:** `route_id`, `route_long_name`, `ROUTE_NUMBER`, `Direction`, `SERVICE`, `WEBSITE`

### 2. Light Rail (`light_rail.geojson`)
- **Source:** Valley Metro ArcGIS FeatureServer
- **Source URL:** `https://services2.arcgis.com/2t1927381mhTgWNC/arcgis/rest/services/ValleyMetroRail/FeatureServer/0/query?where=SERVICE%3D%27Light%20Rail%27&outFields=%2A&outSR=4326&f=geojson`
- **Query Filter:** `where=SERVICE='Light Rail'` (filters for Light Rail, excluding streetcar)
- **Download Date:** November 17, 2026
- **Feature Count:** 4 features (Valley Metro Rail A Line Eastbound/Westbound, B Line Northbound/Southbound)
- **File Size:** 255,833 bytes (~250 KB)
- **Geometry Types:** `LineString`, `MultiLineString`
- **Key Fields:** `ROUTE_NUMBER`, `ROUTE`, `SYMBOLOGY`, `SERVICE`, `WEBSITE`
