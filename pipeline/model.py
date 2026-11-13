"""The risk model for one county. The rules are explained for the public in web/about.html.

- Higher: FEMA's studied 1% (100-year) flood zones, current and preliminary, plus DWR "awareness"
  floodplains for streams FEMA never studied.
- For every other spot, an "effective height" above the nearest large flood zone:
  (ground height - ground height at that zone's nearest edge, floored at 0)
  + 1 m for every DIST_PENALTY_M metres of distance.
  Elevated if that is under ELEV_ZONE_M, or the spot is under ELEV_CREEK_M above the nearest creek.
  Moderate if under MOD_ZONE_M, or under MOD_CREEK_M above the nearest creek.
- The creek part of those rules only counts real streams: never canals, ditches or pipelines, and in dry regions
  only rivers that drain a large area or carry snowmelt (see the channel settings in config.py).
- Land a levee protects (from FEMA or the Army Corps National Levee Database) is Elevated, or Higher if the
  Army Corps expects the levee to be overtopped more often than the 100-year standard (LEVEE_HIGHER_OVERTOPPING).
- Properties LA County Public Works expects mud and debris flows from a recent burn area to reach: Higher if the
  property itself is endangered in a small or moderate storm, Elevated if only in a big one, Moderate if only the
  road to it may be blocked (see mudflow_layers).
- Parcels UC Irvine's PRIMo-Drain model of the Los Angeles basin floods: Elevated if more than PRIMO_DEEP_M deep
  in its 1-in-20-year storm, Moderate if more than PRIMO_SHALLOW_M deep in its 1-in-100-year storm (see
  primo_layers).
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
import shapely
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


def classify(ground_m, studied_zone, levee, above_creek_m, cell_m, levee_often_overtopped=None, creek_ignored=None):
    """The model's rules on a grid. All arrays share one shape; `cell_m` is the grid spacing.

    ground_m: ground elevation (NaN where unknown)
    studied_zone: True inside a studied 1% flood zone
    levee: True on levee-protected land
    above_creek_m: height above the nearest creek (NaN where unknown)
    levee_often_overtopped: True behind levees overtopped at least LEVEE_HIGHER_OVERTOPPING of years
    creek_ignored: True where the nearest channel doesn't count (a canal, or a small dry wash in a dry region)

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
    if creek_ignored is not None:
        above_creek_m = np.where(creek_ignored, np.inf, above_creek_m)

    elevated_score = np.minimum(above_zone_m / config.ELEV_ZONE_M, above_creek_m / config.ELEV_CREEK_M)
    moderate_score = np.minimum(above_zone_m / config.MOD_ZONE_M, above_creek_m / config.MOD_CREEK_M)

    level = np.full(ground_m.shape, LOWER, dtype=np.uint16)
    level[moderate_score < 1] = MODERATE
    level[(elevated_score < 1) | levee] = ELEVATED
    level[studied_zone] = HIGHER
    if levee_often_overtopped is not None:
        level[levee_often_overtopped] = HIGHER
    return level, elevated_score, moderate_score


def _area_only(geometry):
    """A valid, purely polygonal version of a downloaded shape, or None. Agency data occasionally has
    self-crossing outlines, or collections mixing areas with stray lines and points."""
    try:
        geometry = shapely.make_valid(geometry)
    except shapely.errors.GEOSException:
        geometry = geometry.buffer(0)
    if geometry.geom_type == "GeometryCollection":
        geometry = shapely.union_all([g for g in geometry.geoms if g.geom_type in ("Polygon", "MultiPolygon")])
    if geometry.is_empty or geometry.geom_type not in ("Polygon", "MultiPolygon"):
        return None
    return geometry


def _read_shapes(path):
    shapes = gpd.read_file(path)
    if len(shapes) == 0:
        return gpd.GeoDataFrame({"FLD_ZONE": [], "ZONE_SUBTY": []}, geometry=[], crs=config.CRS)
    shapes = shapes[shapes.geometry.notna() & ~shapes.geometry.is_empty].to_crs(config.CRS)
    shapes["geometry"] = [_area_only(g) for g in shapes.geometry]
    return shapes[shapes.geometry.notna()]


# Layers of individual properties, and the level each gives
PROPERTY_LEVELS = {"mudflow_higher": HIGHER, "mudflow_elevated": ELEVATED, "mudflow_moderate": MODERATE,
                   "primo_elevated": ELEVATED, "primo_moderate": MODERATE}


def mudflow_layers(properties):
    """Split LA County's list of properties mud flows could reach (config.LA_MUDFLOW_PROPERTIES) into map layers.

    Each property is listed at the first storm size ("phase") that reaches it. A "Safety Issue" in phase 1 or 2
    (small or moderate storms) is Higher, a "Safety Issue" only in phase 3 (big storms) is Elevated, and an
    "Access Issue" (the road may be blocked, but the property itself isn't expected to be hit) is Moderate.
    """
    def safety(phase):
        return properties[phase].fillna("").str.startswith("Safety")

    early = safety("PHASE1") | safety("PHASE2")
    late = safety("PHASE3") & ~early
    return {"mudflow_higher": properties[early], "mudflow_elevated": properties[late],
            "mudflow_moderate": properties[~early & ~late]}


def primo_layers(parcels):
    """Split parcels by PRIMo-Drain flood depth (columns h_20_drain and h_100_drain, in metres) into map layers.

    Deeper than PRIMO_DEEP_M in the 1-in-20-year storm is Elevated, and deeper than PRIMO_SHALLOW_M in the
    1-in-100-year storm Moderate. Drier parcels are left out. The levels match flood insurance claims: in Los Angeles
    neighbourhoods, these parcels go with claim rates like our Elevated and Moderate land, well below official
    100-year zones.
    """
    elevated = parcels.h_20_drain > config.PRIMO_DEEP_M
    moderate = ~elevated & (parcels.h_100_drain > config.PRIMO_SHALLOW_M)
    return {"primo_elevated": parcels[elevated], "primo_moderate": parcels[moderate]}


def _primo_parcels(raw):
    """Outlines of the parcels PRIMo-Drain floods, with their depths, or None where there are none."""
    path = raw / "primo_parcels.geojson"
    if not (path.exists() and config.PRIMO_DEPTHS.exists()):
        return None
    depths = gpd.pd.read_csv(config.PRIMO_DEPTHS, usecols=["apn", "h_20_drain", "h_100_drain"])
    depths["AIN"] = depths.apn.str.replace("-", "")
    return _read_shapes(path).merge(depths.drop(columns="apn"), on="AIN")


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


def _yearly_rainfall(shape, transform):
    """Average yearly rainfall in mm (PRISM 1991-2020), interpolated onto this grid."""
    rainfall = np.full(shape, np.nan, dtype=np.float32)
    path = config.RAW / "climate" / "prism_annual_rainfall.zip"
    with rasterio.open(f"/vsizip/{path}/prism_ppt_us_25m_2020_avg_30y.tif") as src:
        reproject(rasterio.band(src, 1), rainfall, dst_transform=transform, dst_crs=config.CRS,
                  dst_nodata=np.nan, resampling=Resampling.bilinear)
    return rainfall


def _channel_ignored(region, shape, transform):
    """True where the stream reach a spot drains to doesn't count for the creek rule: no known reach at all (in
    flat basins that drain nowhere the national data gives height-above-drainage 0 with no channel behind it), a
    canal, ditch or pipeline, or in a dry region a reach that isn't a real river (by drainage area or typical flow)."""
    catchment = np.zeros(shape, dtype=np.int32)
    with rasterio.open(config.WORK / region / "catchments.vrt") as src:
        reproject(rasterio.band(src, 1), catchment, dst_transform=transform, dst_crs=config.CRS,
                  dst_nodata=0, resampling=Resampling.nearest)
    reaches = gpd.pd.read_csv(config.WORK / region / "stream_reaches.csv").set_index("COMID")
    artificial = reaches.FTYPE.isin(config.ARTIFICIAL_CHANNEL_TYPES)
    river = (reaches.TotDASqKM >= config.RIVER_MIN_AREA_KM2) | (reaches.Q0001E >= config.RIVER_MIN_FLOW_CFS)
    ids, cell_index = np.unique(catchment, return_inverse=True)
    known = np.isin(ids, reaches.index)
    is_artificial = np.where(known, artificial.reindex(ids, fill_value=False), False)
    is_river = np.where(known, river.reindex(ids, fill_value=False), False)
    dry = _yearly_rainfall(shape, transform) < config.ARID_RAINFALL_MM
    no_reach = ~known[cell_index].reshape(shape)
    return no_reach | is_artificial[cell_index].reshape(shape) | (dry & ~is_river[cell_index].reshape(shape))


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

    def burn(shapes, all_touched=False):
        shapes = [s for s in shapes if s is not None and not s.is_empty]
        if not shapes:
            return np.zeros(shape, dtype=bool)
        return rasterize(((s, 1) for s in shapes), out_shape=shape, transform=transform, dtype="uint8",
                         all_touched=all_touched) > 0

    fema = _read_shapes(raw / "fema_zones.geojson")
    leveed = _read_shapes(raw / "nld_leveed_areas.geojson")
    overtopping = gpd.pd.to_numeric(leveed.get("OVERTOPPING_ACE", gpd.pd.Series(dtype=float)), errors="coerce")
    often = overtopping >= config.LEVEE_HIGHER_OVERTOPPING
    zone, subtype = fema.FLD_ZONE.fillna(""), fema.ZONE_SUBTY.fillna("")
    source_layers = {
        "fema_high": fema[zone.str.startswith(("A", "V")) & (zone != "A99")],
        "prelim_high": _read_shapes(raw / "bam_6.geojson"),
        "dwr_awareness": _read_shapes(raw / "bam_8.geojson"),
        "levee_often_overtopped": leveed[often],
        "levee": gpd.pd.concat([fema[(zone == "A99") | subtype.str.contains("LEVEE")], leveed[~often]]),
        "note_fema_500": gpd.pd.concat([fema[(zone == "X") & subtype.str.contains("0.2 PCT|1 PCT DEPTH")],
                                        _read_shapes(raw / "bam_14.geojson")]),
        "undetermined": fema[zone == "D"],
    }
    if (raw / "mudflow_properties.geojson").exists():
        source_layers.update(mudflow_layers(_read_shapes(raw / "mudflow_properties.geojson")))
    primo_parcels = _primo_parcels(raw)
    if primo_parcels is not None:
        source_layers.update(primo_layers(primo_parcels))
    studied_zone = (burn(source_layers["fema_high"].geometry) | burn(source_layers["prelim_high"].geometry)
                    | burn(source_layers["dwr_awareness"].geometry))
    level, elevated_score, moderate_score = classify(
        ground_m, studied_zone, burn(source_layers["levee"].geometry),
        _height_above_creek(region, shape, transform), transform.a,
        levee_often_overtopped=burn(source_layers["levee_often_overtopped"].geometry),
        creek_ignored=_channel_ignored(region, shape, transform))
    for name, value in PROPERTY_LEVELS.items():
        if name in source_layers:  # properties are smaller than a grid cell, so count every cell they touch
            level = np.where(burn(source_layers[name].geometry, all_touched=True), np.maximum(level, value), level)

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
    zones = (layer_shapes["fema_high"].union(layer_shapes["prelim_high"]).union(layer_shapes["dwr_awareness"])
             .union(layer_shapes["levee_often_overtopped"]))
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
