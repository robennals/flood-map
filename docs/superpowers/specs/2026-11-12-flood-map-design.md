# California "How worried should I be?" Flood Map — Handoff Spec

**Summary:** Build a free public website where a Californian types their
address and gets a plain-language answer to "how much should I worry about
flooding where I live this winter?", backed by a map. Start with the 9-county
Bay Area as a proof of concept; expand to the rest of California only if it
works. The whole site, data included, must fit on Vercel's free (Hobby) plan.

Status: requirements agreed with Rob in conversation (2026-11-12). A first
feasibility test had just started when the work moved here: data endpoints
were checked, no data processed yet. Detail on sources is in
[Data sources](#data-sources); verified facts are marked **(verified)**.

---

## Where this ended up (updated after the feasibility test)

The design below is the original brief. Testing on Santa Clara County changed several calls:

- **FEMA's 500-year zones no longer set the level.** Outside studied creek corridors, FEMA's 500-year,
  "minimal" and "undetermined" labels follow city lines rather than the land (92% of Saratoga is
  "500-year", 96% of neighbouring Monte Sereno is "minimal"). They are now notes in the reasons list.
- **Terrain grades everything outside studied zones.** Elevated and Moderate come from how high a spot
  sits above the nearest large flood zone (plus a distance penalty) and above the nearest creek.
  See `pipeline/model.py` and the public explanation in `web/about.html`.
- **Four levels, not five.** "Not studied" became a note rather than a level.
- **Map files live on Cloudflare R2,** not inside the Vercel deployment, so the whole state can fit.
- **Checked against real floods:** 97–100% of each published Santa Clara flood outline is rated
  Higher or Elevated, and insurance claims concentrate in flagged neighbourhoods. `make check` reruns this.

How to rebuild everything is in the README.

## 1. Why this exists

- **The moment:** forecasters expect a strong El Niño this winter (2026–27),
  and the Governor began statewide flood preparations in October 2026. People
  are hearing "flood warnings" and want to know whether that means *them*.
- **The problem:** existing online flood maps are slow, broken, or too
  coarse. FEMA's own viewer is unreliable. FEMA's maps also miss a lot:
  outdated studies, unstudied streams, levee-protected land shown as low risk,
  no storm-drain flooding. Commercial models (First Street / Flood Factor)
  aren't open and are known to be unreliable at neighborhood scale.
- **Deadline:** big storms usually arrive December–January. The Bay Area
  version needs to be **live within a few weeks**. A great tool in February is
  much less useful.

## 2. Goals, non-goals, anti-goals

**Goals**
- **Address first:** someone on a phone enters an address and gets a short
  verdict on a five-level scale, plus the reasons in plain words, for
  example "FEMA marks this block as high-risk", "you're behind a levee",
  "you're about 1.5 m above the nearest creek", "flood insurance claims were
  filed on this block in 2017".
- **A map second:** a map for people who want to look around, coloured by
  the same five levels.
- **This year's risk:** it covers flooding as it stands today, not decades out.
- **Useful next steps on every answer:** sign up for county emergency alerts,
  current National Weather Service warnings, the state's MyFloodRisk lookup
  (https://water.ca.gov/myfloodrisk), and flood insurance basics, including
  that NFIP policies usually take **30 days** to take effect.
- **Hosting:** free Vercel hosting, with no separate file storage.

**Non-goals**
- Fine detail down to the building ("is my house 30 cm above my
  neighbour's"). Rob wants regional, broad-scale guidance.
- Sea-level-rise scenarios for 2050/2100.
- Official, regulatory flood determinations.
- Live flood forecasting. Link to NWS for that; don't rebuild it.

**Anti-goals (failure even if it technically works)**
- **False comfort.** The worst outcome is telling someone "low risk" because
  no agency ever studied their street, and then it floods. Where official maps
  are silent, the answer must say so. It then leans on the terrain estimate
  and historical claims, and never implies "safe".
- **Levee blind spot.** Land protected by levees must get its own message
  ("protected, but flooding would be serious if the levee fails"). Pajaro in
  2023 is the example.
- **Unexplained scary colours.** Every verdict must be traceable to the
  sources that triggered it.
- Looking official, or failing to say clearly that it isn't.

## 3. What Rob decided

1. **Bay Area first** (Alameda, Contra Costa, Marin, Napa, San Francisco,
   San Mateo, Santa Clara, Solano, Sonoma). Expand later if it works.
2. **Coarse terrain is fine:** "Maybe 100 m is fine. It's really just about
   knowing regions on a broad scale." Rob also wants the data compressed
   hard. The idea is that whole regions share one risk level, so the data
   can be stored as shapes, not as a grid of cells.
3. **Hosting on Vercel's free plan**, with little effort spent on hosting.
4. **Feasibility test first** (see §6), covering four questions: how good is
   the data, how small does it compress, how well does it match historical
   floods, and how usable a tool can be built from it.

## 4. Calls Claude made, for Rob to review

These are proposals, not things Rob explicitly agreed to. The builder may
revisit them, but should say so.

- **One merged risk scale.** All sources are merged into a single five-level
  scale (suggested: High / Elevated / Moderate / Low / Not studied, plus a
  "behind levee" flag). Each shape lists the sources that flagged it.
  *Rejected:* serving each source as its own map layer, which is bigger and
  harder for a worried person to read.
- **Official outlines stay as shapes; only the terrain estimate is coarse.**
  FEMA and DWR flood zones are already published as outlines. They should be
  kept as simplified outlines (simplify to roughly 10–30 m), not converted to a
  100 m grid. Many Bay Area creek flood zones are only 50–150 m wide and would
  disappear or swell into blobs on a 100 m grid. Since file size depends on
  how wiggly the edges are, not on area, this costs little. The terrain
  estimate starts at 100 m; go finer (down to 30 m) only if creeks visibly
  vanish. This refines Rob's "100 m is fine", so confirm with him if it costs
  real size.
- **One map file inside the site.** The merged map ships as a single PMTiles
  file, a single-file map format the browser reads in small pieces as you zoom,
  served as a static file from the Vercel site. No tile server, no database.
- **Today's sea level only** for coastal flooding.
- **No terrain layer at 1 m statewide:** too large (about a terabyte of input
  and days of processing). Not needed at Rob's broad scale anyway.

## 5. Size budget (estimates, not yet measured)

| Area | Merged risk map as PMTiles |
|---|---|
| Bay Area (~18,000 km²) | roughly 1–5 MB |
| All of California (~424,000 km²) | roughly 20–60 MB |

- **Vercel Hobby limit (verified):** 100 MB of uploaded static files per CLI
  deployment ([Vercel limits](https://vercel.com/docs/limits)).
- **Not verified:** the Hobby monthly bandwidth allowance, believed to be
  about 100 GB. If the site goes viral during a storm it could hit this.
  Check the fair-use page.
- **Non-commercial:** Hobby is for non-commercial use only, which fits.
- **Hard ceiling:** if the full state can't fit under 100 MB, that's a
  decision for Rob (Pro plan, outside storage, or a coarser map), not
  something to work around silently.

## 6. The feasibility test (do this first, throwaway)

Run it on **Santa Clara County** (FIPS 06085). It has many creeks, a levee'd
bayshore (Alviso), and a well-documented real flood: **Coyote Creek,
February 2017**, Rock Springs neighbourhood in San José, ~14,000 people
evacuated. Report back to Rob on each question with real numbers and
screenshots:

1. **Data quality:** which sources downloaded, how complete they are, and any
   areas no source covers.
2. **Compression:** actual PMTiles size for the merged county map, and
   whether creek corridors survive at 100 m terrain resolution (screenshot).
3. **Historical fit:**
   - **2017 test:** does the Feb 2017 Coyote Creek flood area fall in
     High/Elevated?
   - **Claims test:** do block groups with flood insurance claims fall in
     higher levels?
   - **Silent areas:** how many claims fall in "Low" or "Not studied" (the
     false-comfort rate)?
4. **Usability:** a rough page with address search, map and verdict text.
   Rob should be able to try it with a few real addresses.

Then extrapolate the size and effort to the full Bay Area and to the state.

## 7. Data sources

| Source | What it gives | Access | Status |
|---|---|---|---|
| **FEMA National Flood Hazard Layer**: flood zones | Official 1% (100-yr) and 0.2% (500-yr) zones, plus levee-protected zone X | ArcGIS REST: `https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28` (layer "Flood Hazard Zones", fields `FLD_ZONE`, `ZONE_SUBTY`). Also county/state zip downloads via FEMA Map Service Center | **(verified)** responds. Layer also has a `minScale`, so query it, don't rely on its rendering |
| **DWR Best Available Maps (BAM 2025)** | 100/200/500-yr floodplains from FEMA + regional studies + USACE, and **"DWR Awareness"** floodplains for streams FEMA never studied, plus levee centerlines | `https://arcgis04.geiconsultants.com/arcgis/rest/services/BAM/BAM2025/MapServer`. Layers: 1 Federal levees, 2 Non-federal levees, 4 100-yr group (5 FEMA eff., 6 FEMA prelim., 7 Regional studies, 8 **DWR Awareness**, 9 USACE), 10 200-yr group, 12 500-yr group, 17 Floodways | **(verified)** layer list. Not yet checked whether query/export is allowed. Old `gis.water.ca.gov/.../Boundaries/BAM` returns 404 |
| **Terrain: Height Above Nearest Drainage (HAND), 10 m** | Height of each spot above the nearest stream, a proxy for "floods first" | TACC v0.1 per river basin: `https://web.corral.tacc.utexas.edu/nfiedata/HAND/180500/180500hand.tif` (SF Bay basin, 603 MB). South Santa Clara (Llagas/Uvas, Pajaro watershed) is in basin `180600`. Newer **v0.2 at https://cfim.ornl.gov/data/**, so prefer it if accessible | **(verified)** v0.1 file exists. Try reading only the needed window remotely with GDAL `/vsicurl/` before downloading whole files. Alternative: compute HAND yourself from USGS 3DEP 10 m elevation at 30–100 m |
| **FEMA flood insurance claims (NFIP Redacted Claims v3)** | Historical claims by year, located by census **block group** (`censusGeoid`); lat/long are rounded to 0.1° (useless for location) | `https://www.fema.gov/api/open/v3/NfipClaims` (OData: `$filter`, `$select`, `$top`, `$skip`). Fields include `countyCode`, `censusGeoid`, `yearOfLoss`, `dateOfLoss`, `floodZoneCurrent`, `amountPaidOnBuildingClaim` | **v2 endpoint is gone** (removed 2026-10-15, returns an HTML error page). v3 endpoint taken from FEMA's data page, **not yet called** |
| **Feb 2017 Coyote Creek flood extent** | Real observed flood outline | Valley Water open data portal https://data-valleywater.opendata.arcgis.com/ (look for 2017 inundation); Valley Water 2017 flood report PDF with inundation maps; Dartmouth Flood Observatory event 4434 (satellite water extent): https://floodobservatory.colorado.edu/Events/2017USA4434/2017USA4434.html | Not yet located as GIS data. A PDF map exists ("Reaches 4-5 Spilling Map") |
| **USGS CoSMoS** coastal storm flooding | Coastal/bay flooding depth for storms at current sea level | https://cmgds.marine.usgs.gov/data-releases/community/cosmos. Central California v3.1 covers SF Bay. Use the **0 m sea-level-rise** scenarios (e.g. 1-yr and 100-yr storm) | Not yet downloaded |
| **USGS high-water marks (STN)** | Surveyed flood high-water points per event | `https://stn.wim.usgs.gov/STNServices/...` | **Did not respond** during checks; optional |
| **Census TIGER** | County and block-group boundaries | TIGERweb REST or TIGER/Line shapefiles | Not yet fetched |
| **Address lookup** | Address → coordinates | US Census Geocoder (free, but check browser CORS) or Nominatim/Photon | Undecided; builder's call |

**Known blind spot:** in cities, storm-drain backups cause much of the
flooding, and none of the open sources model it. The claims data is the
main check on this. Say so honestly in the answer text.

## 8. Left to the builder

- **Stack:** the web framework and map library. Something light suggested:
  MapLibre GL + the `pmtiles` JS library on a static page.
- **The merge rules:** which source combinations map to which level. This is
  the core judgement call. Write the rules down in plain words so Rob can
  review them, and tune them against the historical tests.
- **Wording:** exact verdict wording and disclaimers. Plain English, no
  jargon, no alarmism, never "safe".
- **Address lookup:** choice of geocoder.
- **Processing approach:** GDAL/ogr2ogr, Python (geopandas/rasterio), and
  tippecanoe for building the PMTiles file.

## 9. Rebuilding

See the README: `make setup fetch build check`, then `make upload` to publish.
