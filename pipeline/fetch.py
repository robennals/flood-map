"""Download everything a region needs into data/raw/. Safe to re-run: finished files are skipped.

Usage: python -m pipeline.fetch <region>      e.g. python -m pipeline.fetch bay-area
"""
import json
import math
import os
import subprocess
import sys

import geopandas as gpd
import requests

from . import config
from .arcgis import download_layer


# Remote elevation tiles are read over HTTP; retry dropped connections instead of skipping a tile
GDAL_REMOTE_ENV = {**os.environ, "GDAL_HTTP_MAX_RETRY": "5", "GDAL_HTTP_RETRY_DELAY": "5",
                   "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR"}


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def download_file(url, path):
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".partial")
    subprocess.run(["curl", "-sSfL", "--retry", "5", "--retry-delay", "10", "-o", str(tmp), url], check=True)
    tmp.rename(path)


def fetch_county_outline(fips):
    path = config.county_raw(fips) / "county.geojson"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        resp = requests.get(config.TIGER_COUNTY, timeout=120, params={
            "where": f"GEOID='{fips}'", "outFields": "GEOID,NAME", "outSR": 4326, "f": "geojson"})
        resp.raise_for_status()
        path.write_text(resp.text)
    return gpd.read_file(path)


def fetch_county(fips):
    raw = config.county_raw(fips)
    county = fetch_county_outline(fips)
    minx, miny, maxx, maxy = county.total_bounds
    m = config.COUNTY_MARGIN_DEG
    bbox = (minx - m, miny - m, maxx + m, maxy + m)

    layers = {"fema_zones": (config.FEMA_ZONES, "FLD_ZONE,ZONE_SUBTY,DFIRM_ID")}
    layers.update({name: (f"{config.DWR_BAM}/{num}", "*") for name, num in config.DWR_LAYERS.items()})
    for name, (url, fields) in layers.items():
        path = raw / f"{name}.geojson"
        if not path.exists():
            log(f"  {fips} {name}: {download_layer(url, bbox, path, fields)} shapes")

    claims_path = raw / "claims.json"
    if not claims_path.exists():
        rows, skip = [], 0
        while True:
            page = requests.get(config.FEMA_CLAIMS, timeout=180, params={
                "$filter": f"countyCode eq '{fips}'", "$top": 10000, "$skip": skip,
                "$select": "yearOfLoss,censusGeoid,ratedFloodZone,amountPaidOnBuildingClaim"}).json()["NfipClaims"]
            rows += page
            skip += len(page)
            if len(page) < 10000:
                break
        claims_path.write_text(json.dumps(rows))
        log(f"  {fips} claims: {len(rows)}")

    download_file(config.TIGER_BLOCK_GROUPS.format(fips=fips), raw / f"tl_2010_{fips}_bg10.zip")
    download_file(config.TIGER_WATER.format(fips=fips), raw / "water.zip")


def region_bounds(fips_list):
    outlines = gpd.pd.concat([fetch_county_outline(f) for f in fips_list])
    return outlines.total_bounds


def fetch_terrain(bounds):
    """Height-above-nearest-drainage files for every river basin (HUC6) the region touches."""
    minx, miny, maxx, maxy = bounds
    resp = requests.get(config.WBD_HUC6, timeout=120, params={
        "geometry": f"{minx},{miny},{maxx},{maxy}", "geometryType": "esriGeometryEnvelope", "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects", "outFields": "huc6", "returnGeometry": "false", "f": "json"})
    basins = sorted({feature["attributes"]["huc6"] for feature in resp.json()["features"]})
    log(f"river basins: {', '.join(basins)}")
    for huc6 in basins:
        path = config.RAW / "hand" / f"{huc6}hand.tif"
        if not path.exists():
            log(f"  downloading terrain for basin {huc6} (1-3 GB)")
        download_file(config.HAND_TIF.format(huc6=huc6), path)
    return [config.RAW / "hand" / f"{h}hand.tif" for h in basins]


def elevation_tiles(bounds):
    """USGS 1x1 degree elevation tiles covering the region (read remotely; tiles over open ocean don't exist)."""
    minx, miny, maxx, maxy = bounds
    urls = []
    for lat in range(math.floor(miny) + 1, math.floor(maxy) + 2):
        for lon in range(math.floor(-maxx) + 1, math.floor(-minx) + 2):
            url = config.USGS_DEM_TILE.format(tile=f"n{lat}w{lon}")
            if requests.head(url, timeout=60).status_code == 200:
                urls.append(url)
    return urls


def fetch_valley_water_floods():
    for name, (service, _, _) in config.VALLEY_WATER_FLOODS.items():
        path = config.RAW / "history" / f"{name}.geojson"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            download_layer(config.VALLEY_WATER_SERVICE.format(service=service), (-123, 36, -121, 38), path)


def main(region):
    counties = config.REGIONS[region]
    failed = []
    for fips, name in counties.items():
        log(f"{fips} {name}")
        try:
            fetch_county(fips)
        except Exception as err:  # keep going; a re-run picks up where this left off
            log(f"  {fips} FAILED: {err}")
            failed.append(name)
    bounds = region_bounds(counties)
    work = config.WORK / region
    work.mkdir(parents=True, exist_ok=True)
    hand = fetch_terrain(bounds)
    subprocess.run(["gdalbuildvrt", "-q", "-overwrite", str(work / "hand.vrt"), *map(str, hand)], check=True)
    dem = elevation_tiles(bounds)
    vrt = work / "elevation.vrt"
    subprocess.run(["gdalbuildvrt", "-q", "-overwrite", str(vrt), *[f"/vsicurl/{u}" for u in dem]],
                   check=True, env=GDAL_REMOTE_ENV)
    included = vrt.read_text().count("<SourceFilename")
    if included != len(dem):  # gdalbuildvrt only warns when a remote tile can't be opened
        sys.exit(f"only {included} of {len(dem)} elevation tiles could be read; re-run to retry")
    log(f"elevation tiles: {len(dem)}")
    if config.VALLEY_WATER_COUNTY in counties:
        fetch_valley_water_floods()
    if failed:
        sys.exit(f"fetch incomplete, re-run to retry: {', '.join(failed)}")
    log("fetch complete")


if __name__ == "__main__":
    main(sys.argv[1])
