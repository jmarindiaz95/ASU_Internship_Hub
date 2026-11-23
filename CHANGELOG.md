# Changelog

## Unreleased

- Fixed Career Disciplines checkbox clipping in the filter sidebar at wider browser sizes while preserving scrolling and the existing layout.
- Improved filter sidebar by standardizing headings and refining checkbox spacing.
- Added automated transit data tests verifying that bus and light-rail GeoJSON files exist, contain features, and use valid line geometries.

## Version 2.0.1 — 2026-11-18

- Added optional Valley Metro bus and light-rail overlays with independent map controls and distinct legend colors ([PR #2](https://github.com/jmarindiaz95/ASU_Internship_Hub/pull/2)).
- Added route popups with names, directions where available, and official schedule links.
- Included static GeoJSON datasets with 205 bus-route features and 4 light-rail features, source notes, and a download/validation script.
- Clarified that overlays show route alignments; stops, stations, live arrivals, and trip planning are not included.

## Version 2.0.0 — 2026-11-16

- Made the working application the repository's main version; preserved the original GitHub files in `version-1/`.
- Retained the filters–map–listings layout and maroon-and-gold identity, with lighter panels, rounded cards, and responsive layouts.
- Added an interactive Leaflet map, 18 career disciplines, search, pay and schedule filters, and listing details.
- Connected PostgreSQL/PostGIS and eight public career sources with pagination, automatic refreshes, and stale-listing exclusions.
- Added address lookup and clear worksite, headquarters, office-reference, and unresolved-location labels; no manual review is required.
- Kept background updates from interrupting browsing and fixed startup, filter, and map-asset loading issues.
- Organized static assets, documentation, and local-data folders; added Docker/PostGIS and Windows setup instructions.

## Version 1.0.0 — 2026-11-14

- Established the original HTML/CSS prototype and maroon, gold, and cream palette.
- Defined the three-column layout, example cards, mobile breakpoints, and proposed career, hours, pay, transit, and distance controls.
- Used placeholder map and listing content without a database or collection service.
- Archived the original files from commit `29fd6d5` unchanged in `version-1/`.
