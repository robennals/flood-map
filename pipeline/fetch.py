"""Download everything a region needs into data/raw/. Safe to re-run: finished files are skipped.

Usage: python -m pipeline.fetch <region>      e.g. python -m pipeline.fetch bay-area
"""
import json
import math
import os
import shutil
import subprocess
import sys

import geopandas as gpd
import requests

from . import config
from .arcgis import download_by_ids, download_layer, get_json


# Remote elevation tiles are read over HTTP; retry dropped connections instead of skipping a tile
GDAL_REMOTE_ENV = {**os.environ, "GDAL_HTTP_MAX_RETRY": "5", "GDAL_HTTP_RETRY_DELAY": "5",
                   "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR"}


def build_vrt(path, sources):
    """Index many raster files as one. The old index is only replaced once the new one is complete, since a
    running build may be reading it and gdalbuildvrt only warns when a remote file can't be opened."""
    tmp = path.with_name(path.stem + ".new.vrt")
    for attempt in range(3):
        subprocess.run(["gdalbuildvrt", "-q", "-overwrite", str(tmp), *sources], check=True, env=GDAL_REMOTE_ENV)
        included = tmp.read_text().count("<SourceFilename")
        if included == len(sources):
            os.replace(tmp, path)
            return
        log(f"  only {included} of {len(sources)} files could be opened for {path.name}; retrying")
    sys.exit(f"could not open every file for {path.name}; re-run to retry")


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def download_file(url, path):
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    size = int(requests.head(url, timeout=60, allow_redirects=True).headers.get("Content-Length", 0))
    free = shutil.disk_usage(path.parent).free
    if free - size < config.MIN_FREE_DISK_GB * 1e9:
        sys.exit(f"not downloading {url}: it is {size / 1e9:.1f} GB and only {free / 1e9:.0f} GB is free "
                 f"(keeping at least {config.MIN_FREE_DISK_GB} GB free; see MIN_FREE_DISK_GB in pipeline/config.py)")
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
    layers["nld_leveed_areas"] = (config.NLD_LEVEED_AREAS, "SYSTEM_NAME,OVERTOPPING_ACE,FEMA_ACCREDITATION_RATING")
    if fips == config.LA_COUNTY:
        layers["mudflow_properties"] = (config.LA_MUDFLOW_PROPERTIES, "FIRE,FIRE_YEAR,PHASE1,PHASE2,PHASE3")
    for name, (url, fields) in layers.items():
        path = raw / f"{name}.geojson"
        if not path.exists():
            log(f"  {fips} {name}: {download_layer(url, bbox, path, fields)} shapes")

    claims_path = raw / "claims.json"
    if not claims_path.exists():
        rows, skip = [], 0
        while True:
            page = get_json(config.FEMA_CLAIMS, {
                "$filter": f"countyCode eq '{fips}'", "$top": 10000, "$skip": skip,
                "$select": "yearOfLoss,censusGeoid,ratedFloodZone,amountPaidOnBuildingClaim"},
                f"claims from {skip}")["NfipClaims"]
            rows += page
            skip += len(page)
            if len(page) < 10000:
                break
        claims_path.write_text(json.dumps(rows))
        log(f"  {fips} claims: {len(rows)}")

    if fips == config.LA_COUNTY:
        fetch_primo_parcels(raw / "primo_parcels.geojson")

    download_file(config.TIGER_BLOCK_GROUPS.format(fips=fips), raw / f"tl_2010_{fips}_bg10.zip")
    download_file(config.TIGER_WATER.format(fips=fips), raw / "water.zip")


def fetch_primo_parcels(path):
    """Outlines of the parcels PRIMo-Drain floods (config.PRIMO_DATASET), from LA County's parcel map."""
    if path.exists():
        return
    if not config.PRIMO_DEPTHS.exists():
        log(f"  PRIMo-Drain depths not found: download {config.PRIMO_DEPTHS.name} from {config.PRIMO_DATASET} into {config.PRIMO_DIR}")
        return
    depths = gpd.pd.read_csv(config.PRIMO_DEPTHS)
    flooded = depths[(depths.h_100_drain > config.PRIMO_SHALLOW_M) | (depths.h_20_drain > config.PRIMO_DEEP_M)]
    parcel_numbers = flooded.apn.str.replace("-", "")  # the county's AIN is the APN without dashes
    log(f"  PRIMo-Drain parcels: {download_by_ids(config.LA_PARCELS, 'AIN', parcel_numbers, path, 'AIN')} shapes")


def county_bounds(fips_list):
    """Each county's bounding box plus a margin. Working per county keeps a region made of separate
    areas (say the Bay Area and Southern California) from pulling in everything in between."""
    m = config.COUNTY_MARGIN_DEG
    boxes = []
    for fips in fips_list:
        minx, miny, maxx, maxy = fetch_county_outline(fips).total_bounds
        boxes.append((minx - m, miny - m, maxx + m, maxy + m))
    return boxes


def fetch_terrain(boxes):
    """Height-above-nearest-drainage files for every river basin (HUC6) the region's counties touch."""
    basins = set()
    for minx, miny, maxx, maxy in boxes:
        resp = requests.get(config.WBD_HUC6, timeout=120, params={
            "geometry": f"{minx},{miny},{maxx},{maxy}", "geometryType": "esriGeometryEnvelope", "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects", "outFields": "huc6", "returnGeometry": "false", "f": "json"})
        basins |= {feature["attributes"]["huc6"] for feature in resp.json()["features"]}
    basins = sorted(basins)
    log(f"river basins: {', '.join(basins)}")
    for huc6 in basins:
        path = config.RAW / "hand" / f"{huc6}hand.tif"
        if not path.exists():
            log(f"  downloading terrain for basin {huc6} (1-3 GB)")
        download_file(config.HAND_TIF.format(huc6=huc6), path)
        download_file(config.HAND_CATCHMENTS.format(huc6=huc6), config.RAW / "hand" / f"{huc6}catchmask.tif")
        download_file(config.HAND_REACHES.format(huc6=huc6), config.RAW / "hand" / f"{huc6}-flows.dbf")
    return basins


def write_stream_reaches(basins, path):
    """One table of every stream reach in the region: whether it's man-made, and how big a river it is."""
    reaches = gpd.pd.concat([gpd.read_file(config.RAW / "hand" / f"{h}-flows.dbf",
                                           columns=["COMID", "FTYPE", "TotDASqKM", "Q0001E"]) for h in basins])
    reaches = reaches.drop(columns="geometry", errors="ignore").drop_duplicates("COMID")
    reaches.to_csv(path, index=False)
    log(f"stream reaches: {len(reaches):,} ({(reaches.FTYPE.isin(config.ARTIFICIAL_CHANNEL_TYPES)).sum():,} canals or pipelines)")


def elevation_tiles(boxes):
    """USGS 1x1 degree elevation tiles covering the region's counties (read remotely; tiles over open
    ocean don't exist)."""
    tiles = set()
    for minx, miny, maxx, maxy in boxes:
        for lat in range(math.floor(miny) + 1, math.floor(maxy) + 2):
            for lon in range(math.floor(-maxx) + 1, math.floor(-minx) + 2):
                tiles.add(f"n{lat}w{lon}")
    urls = [config.USGS_DEM_TILE.format(tile=tile) for tile in sorted(tiles)]
    return [url for url in urls if requests.head(url, timeout=60).status_code == 200]


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
    boxes = county_bounds(counties)
    work = config.WORK / region
    work.mkdir(parents=True, exist_ok=True)
    basins = fetch_terrain(boxes)
    build_vrt(work / "hand.vrt", [str(config.RAW / "hand" / f"{h}hand.tif") for h in basins])
    build_vrt(work / "catchments.vrt", [str(config.RAW / "hand" / f"{h}catchmask.tif") for h in basins])
    write_stream_reaches(basins, work / "stream_reaches.csv")
    dem = elevation_tiles(boxes)
    build_vrt(work / "elevation.vrt", [f"/vsicurl/{u}" for u in dem])
    log(f"elevation tiles: {len(dem)}")
    download_file(config.PRISM_ANNUAL_RAINFALL, config.RAW / "climate" / "prism_annual_rainfall.zip")
    if config.VALLEY_WATER_COUNTY in counties:
        fetch_valley_water_floods()
    if failed:
        sys.exit(f"fetch incomplete, re-run to retry: {', '.join(failed)}")
    log("fetch complete")


if __name__ == "__main__":
    main(sys.argv[1])
