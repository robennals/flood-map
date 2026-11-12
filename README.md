# Flood map: "How worried should I be about flooding?"

A free website where someone types their address and gets a plain-language answer about flood risk
where they live, with the reasons and what to do now. It currently covers the nine San Francisco Bay
Area counties and Santa Cruz County.

- **What it shows:** four levels (Higher, Elevated, Moderate, Lower) built from FEMA's and California's
  studied creek flood zones plus the shape of the land, with an optional view of past floods and flood
  insurance claims.
- **How it works:** explained for the public in [web/about.html](web/about.html). The model itself is
  [pipeline/model.py](pipeline/model.py).
- **Not official:** this is not a FEMA flood determination. Use
  [FEMA's maps](https://msc.fema.gov/portal/home) for insurance and building decisions.

## How the pieces fit

```
public data ──make fetch──▶ data/raw/ ──make build──▶ data/out/<region>/*.pmtiles ──make upload──▶ R2 bucket
                                                                                                   │
                                                web/ (static site, reads the map files) ◀──────────┘
```

- **`pipeline/`:** Python steps: download, model, past-floods layer, checks, upload.
- **`web/`:** the website. Plain HTML and JavaScript, no build step. Map drawing uses
  [MapLibre](https://maplibre.org/) and [PMTiles](https://docs.protomaps.com/pmtiles/).
- **`data/`:** downloads and generated files. Git-ignored and several GB; everything in it can be
  re-created with `make fetch build`.
- **`docs/`:** design notes.

## Rebuilding the map

### 1. Install the tools

You need [uv](https://docs.astral.sh/uv/) (Python), [GDAL](https://gdal.org/) command-line tools,
[tippecanoe](https://github.com/felt/tippecanoe), `curl`, and Node.js (only for `make serve`).

```bash
# macOS
brew install uv gdal tippecanoe

# Debian / Ubuntu
sudo apt install gdal-bin curl && curl -LsSf https://astral.sh/uv/install.sh | sh
# tippecanoe: build from source, see https://github.com/felt/tippecanoe#installation
```

Then install the Python packages (exact versions are pinned in `uv.lock`):

```bash
make setup
```

### 2. Download, build and check

```bash
make fetch     # about 10 GB of downloads for the Bay Area; safe to re-run, finished files are skipped
make build     # runs the model for each county; writes data/out/bay-area/ and web/data/bay-area/
make check     # compares the result with real floods and insurance claims
make serve     # then open http://localhost:8765/?data=local
```

Use `REGION=<name>` to pick a region defined in [pipeline/config.py](pipeline/config.py)
(default `bay-area`). To add a region, add its county FIPS codes there; the download step works out
which river basins and elevation tiles it needs from the county outlines.

**Expect slow downloads.** FEMA's map service is busy and often drops connections. The downloader
uses small pages and retries; a full Bay Area fetch takes a while.

**What's downloaded:**

| Data | Source |
|---|---|
| Flood hazard zones | FEMA National Flood Hazard Layer (map service) |
| Preliminary FEMA zones, DWR awareness floodplains | California DWR Best Available Maps 2025 |
| Height above nearest creek, 10 m | National Flood Interoperability Experiment (TACC), one file per river basin |
| Ground elevation, 10 m | USGS 3D Elevation Program (read remotely, not downloaded in full) |
| Flood insurance claims | OpenFEMA NFIP Redacted Claims v3 |
| Census block groups, county outlines | US Census Bureau |
| Historical flood outlines (Santa Clara County) | Santa Clara Valley Water District |

### 3. Publish

The website reads its map files from the address in [web/config.js](web/config.js). To host your own:

1. **Create a bucket** on Cloudflare R2 (or any S3-compatible storage) and give it a public address,
   ideally a custom domain.
2. **Allow browsers to read it** with a CORS rule (in R2: bucket → Settings → CORS policy):

   ```json
   [{"AllowedOrigins": ["*"], "AllowedMethods": ["GET", "HEAD"],
     "AllowedHeaders": ["Range", "If-Match", "If-None-Match"],
     "ExposeHeaders": ["ETag", "Content-Length", "Content-Range", "Accept-Ranges"], "MaxAgeSeconds": 86400}]
   ```

3. **Add credentials:** copy `.env.example` to `.env.local` and fill it in. Use an R2 API token with
   Object Read & Write on the bucket.
4. **Upload:** `make upload` puts the files at `<bucket>/<region>/`.
5. **Point the site at it:** set `dataUrl` in `web/config.js`, then host the `web/` folder on any static
   host. The live site is the `floodmap` Vercel project, connected to this repo with Root Directory
   `web`: merging to `main` deploys to production, and other branches get preview deployments. For a
   manual deploy, `cd web && vercel link` once, then `vercel deploy --prod`; `web/.vercelignore` keeps
   local map copies and env files out of that upload.

## The model in brief

- **Higher:** inside a studied 1% (100-year) flood zone: FEMA's current or preliminary maps, or a DWR
  awareness floodplain.
- **Elevated / Moderate:** graded by the land. How high a spot sits above the nearest large flood
  zone (plus a distance penalty), and above the nearest creek. Levee-protected land is Elevated.
- **Not used to set levels:** FEMA's 500-year, "minimal" and "undetermined" labels. Outside studied
  creek corridors they follow city lines rather than the land (for example, 92% of Saratoga is
  labelled 500-year but 96% of neighbouring Monte Sereno is labelled minimal).

The thresholds live in [pipeline/config.py](pipeline/config.py). If you change them, update
[web/about.html](web/about.html) too, and re-run `make check`.

## License

Apache 2.0. See [LICENSE](LICENSE). Data comes from the public sources listed above under their own terms.
