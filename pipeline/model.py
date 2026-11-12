"""The risk model for one county. The rules are explained for the public in web/about.html.

- Higher: FEMA's studied 1% (100-year) flood zones, current and preliminary, plus DWR "awareness"
  floodplains for streams FEMA never studied.
- For every other spot, an "effective height" above the nearest large flood zone:
  (ground height - ground height at that zone's nearest edge, floored at 0)
  + 1 m for every DIST_PENALTY_M metres of distance.
  Elevated if that is under ELEV_ZONE_M, or the spot is under ELEV_CREEK_M above the nearest creek.
  Moderate if under MOD_ZONE_M, or under MOD_CREEK_M above the nearest creek.
- Land FEMA lists as levee-protected is Elevated.
- FEMA's 500-year, "undetermined" and "minimal" labels are notes only: outside studied creek
  corridors they follow city lines, not the land.

Writes data/work/counties/<fips>/level.tif (level << 12, used by validate.py) and
risk.geojsonseq (map layers, clipped to the county).
"""
import json
import os
import subprocess
import tempfile

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.warp import reproject
from scipy.ndimage import distance_transform_edt, label

from . import config

HIGHER, ELEVATED, MODERATE, LOWER = 4, 3, 2, 1
LEVEL_SHIFT = 12  # level.tif stores level << 12
LOOSE_LAYERS = {"terrain_elevated", "terrain_moderate", "undetermined", "note_fema_500", "coverage"}
GDAL_REMOTE_ENV = {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "GDAL_HTTP_MAX_RETRY": "5", "GDAL_HTTP_RETRY_DELAY": "5"}


def classify(ground_m, studied_zone, levee, above_creek_m, cell_m):
    """The model's rules on a grid. All arrays share one shape; `cell_m` is the grid spacing.

    ground_m: ground elevation (NaN where unknown)
    studied_zone: True inside a studied 1% flood zone
    levee: True on land FEMA lists as levee-protected
    above_creek_m: height above the nearest creek (NaN where unknown)

    Returns (level, elevated_score, moderate_score). A score under 1 means the spot qualifies for
    that level by terrain; the scores are continuous so their outlines can be traced like contours.
    """
    # Tiny zones (ponds, basins, slivers) are Higher themselves but don't spread risk,
    # or each would spread a circle of risk across flat land.
    patches, _ = label(studied_zone, structure=np.ones((3, 3)))
    patch_ha = np.bincount(patches.ravel()) * cell_m * cell_m / 1e4
    spreading_zone = studied_zone & (patch_ha[patches] >= config.MIN_SOURCE_HA)

    # Effective height above the nearest spreading zone: height above its nearest edge (floored at 0,
    # since ground lower than a flood zone's edge is no safer), plus a penalty for distance.
    if spreading_zone.any():
        distance_cells, (nearest_y, nearest_x) = distance_transform_edt(~spreading_zone, return_indices=True)
        above_zone_m = (np.maximum(ground_m - ground_m[nearest_y, nearest_x], 0)
                        + distance_cells * cell_m / config.DIST_PENALTY_M)
    else:
        above_zone_m = np.full(ground_m.shape, np.inf, dtype=np.float32)
    above_zone_m = np.where(np.isnan(above_zone_m), np.inf, above_zone_m)
    above_creek_m = np.where(np.isnan(above_creek_m), np.inf, above_creek_m)

    elevated_score = np.minimum(above_zone_m / config.ELEV_ZONE_M, above_creek_m / config.ELEV_CREEK_M)
    moderate_score = np.minimum(above_zone_m / config.MOD_ZONE_M, above_creek_m / config.MOD_CREEK_M)

    level = np.full(ground_m.shape, LOWER, dtype=np.uint16)
    level[moderate_score < 1] = MODERATE
    level[(elevated_score < 1) | levee] = ELEVATED
    level[studied_zone] = HIGHER
    return level, elevated_score, moderate_score


def _read_shapes(path):
    shapes = gpd.read_file(path)
    if len(shapes) == 0:
        return gpd.GeoDataFrame({"FLD_ZONE": [], "ZONE_SUBTY": []}, geometry=[], crs=config.CRS)
    shapes = shapes[shapes.geometry.notna() & ~shapes.geometry.is_empty].to_crs(config.CRS)
    shapes["geometry"] = shapes.geometry.make_valid()
    return shapes


def _land_outline(fips):
    """The county's outline minus large water bodies (county outlines include their share of the Bay and ocean)."""
    raw = config.county_raw(fips)
    outline = _read_shapes(raw / "county.geojson").geometry.union_all()
    water = gpd.read_file(f"zip://{raw}/water.zip").to_crs(config.CRS)
    water = water[water.area >= config.MIN_WATER_KM2 * 1e6]
    return outline.difference(water.geometry.union_all()) if len(water) else outline


def _ground_elevation(region, fips, land):
    """30 m ground elevation for the county plus a margin, read from the region's USGS tiles."""
    path = config.county_work(fips) / "elevation30.tif"
    if not path.exists():
        minx, miny, maxx, maxy = land.bounds
        margin = config.COUNTY_MARGIN_M
        subprocess.run(["gdalwarp", "-q", "-overwrite", "-t_srs", config.CRS,
                        "-te", str(minx - margin), str(miny - margin), str(maxx + margin), str(maxy + margin),
                        "-tr", str(config.GRID_M), str(config.GRID_M), "-r", "average", "-co", "COMPRESS=DEFLATE",
                        str(config.WORK / region / "elevation.vrt"), str(path)],
                       check=True, env={**os.environ, **GDAL_REMOTE_ENV})
    with rasterio.open(path) as src:
        ground_m = src.read(1).astype(np.float32)
        ground_m[ground_m < -100] = np.nan  # no-data
        return ground_m, src.transform


def _height_above_creek(region, shape, transform):
    """Height above the nearest creek, 10 m data averaged onto this grid."""
    above_creek_m = np.full(shape, np.nan, dtype=np.float32)
    with rasterio.open(config.WORK / region / "hand.vrt") as src:
        reproject(rasterio.band(src, 1), above_creek_m, dst_transform=transform, dst_crs=config.CRS,
                  dst_nodata=np.nan, resampling=Resampling.average)
    return above_creek_m


def run(region, fips, county_name):
    raw, work = config.county_raw(fips), config.county_work(fips)
    work.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))

    land = _land_outline(fips)
    ground_m, transform = _ground_elevation(region, fips, land)
    shape = ground_m.shape

    def burn(shapes):
        shapes = [s for s in shapes if s is not None and not s.is_empty]
        if not shapes:
            return np.zeros(shape, dtype=bool)
        return rasterize(((s, 1) for s in shapes), out_shape=shape, transform=transform, dtype="uint8") > 0

    fema = _read_shapes(raw / "fema_zones.geojson")
    zone, subtype = fema.FLD_ZONE.fillna(""), fema.ZONE_SUBTY.fillna("")
    source_layers = {
        "fema_high": fema[zone.str.startswith(("A", "V")) & (zone != "A99")],
        "prelim_high": _read_shapes(raw / "bam_6.geojson"),
        "dwr_awareness": _read_shapes(raw / "bam_8.geojson"),
        "levee": fema[(zone == "A99") | subtype.str.contains("LEVEE")],
        "note_fema_500": gpd.pd.concat([fema[(zone == "X") & subtype.str.contains("0.2 PCT|1 PCT DEPTH")],
                                        _read_shapes(raw / "bam_14.geojson")]),
        "undetermined": fema[zone == "D"],
    }
    studied_zone = (burn(source_layers["fema_high"].geometry) | burn(source_layers["prelim_high"].geometry)
                    | burn(source_layers["dwr_awareness"].geometry))
    level, elevated_score, moderate_score = classify(
        ground_m, studied_zone, burn(source_layers["levee"].geometry),
        _height_above_creek(region, shape, transform), transform.a)

    on_land = burn([land])
    with rasterio.open(work / "level.tif", "w", driver="GTiff", width=shape[1], height=shape[0], count=1,
                       dtype="uint16", crs=config.CRS, transform=transform, compress="deflate", nodata=0) as dst:
        dst.write(np.where(on_land, level << LEVEL_SHIFT, 0).astype(np.uint16), 1)

    def outline_below_one(score, name):
        """Outline of where `score` < 1, traced like a contour line so edges follow the land, not grid squares."""
        tif, gpkg = f"{tmp}/{name}.tif", f"{tmp}/{name}.gpkg"
        with rasterio.open(tif, "w", driver="GTiff", width=shape[1], height=shape[0], count=1,
                           dtype="float32", crs=config.CRS, transform=transform) as dst:
            dst.write(np.minimum(score, 50).astype(np.float32), 1)
        subprocess.run(["gdal_contour", "-q", "-p", "-amin", "lo", "-amax", "hi", "-fl", "-1", "1", tif, gpkg],
                       check=True)
        bands = gpd.read_file(gpkg)
        return bands[bands.hi <= 1].geometry.make_valid().union_all()

    layer_shapes = {name: shapes.geometry.union_all().intersection(land) for name, shapes in source_layers.items()}
    # Terrain layers only cover land outside studied zones, so a spot inside a zone gets the zone's reason alone
    zones = layer_shapes["fema_high"].union(layer_shapes["prelim_high"]).union(layer_shapes["dwr_awareness"])
    layer_shapes["terrain_elevated"] = outline_below_one(elevated_score, "elevated").intersection(land).difference(zones)
    layer_shapes["terrain_moderate"] = (outline_below_one(moderate_score, "moderate").intersection(land)
                                        .difference(zones).difference(layer_shapes["terrain_elevated"]))
    layer_shapes["coverage"] = land

    with open(work / "risk.geojsonseq", "w") as out:
        for name, shape_union in layer_shapes.items():
            tolerance = config.LOOSE_TOL_M if name in LOOSE_LAYERS else config.SHARP_TOL_M
            parts = gpd.GeoSeries([shape_union], crs=config.CRS).simplify(tolerance).explode(index_parts=False)
            parts = parts[parts.area > 400]  # drop specks under 20 m x 20 m
            properties = {"county": county_name} if name == "coverage" else {}
            for part in parts.to_crs(4326):
                out.write(json.dumps({"type": "Feature", "tippecanoe": {"layer": name}, "properties": properties,
                                      "geometry": part.__geo_interface__}) + "\n")

    return {name: round(100 * float(((level == value) & on_land).sum()) / float(on_land.sum()), 1)
            for name, value in [("higher", HIGHER), ("elevated", ELEVATED), ("moderate", MODERATE), ("lower", LOWER)]}
