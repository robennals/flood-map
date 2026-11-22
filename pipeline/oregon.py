"""Oregon's own sources: the state's flood hazard database, and FEMA's upcoming maps for the state.

FEMA's digital flood map is empty for about ten Oregon counties and parts of two more. There the state's database
has FEMA's older paper maps (1978-2002), digitised. They are only used where FEMA has no digital map, so a copy
of an old map never overrides a newer one.
"""
import zipfile

import geopandas as gpd
import pyogrio
import shapely

from . import config
from .arcgis import download_layer


def is_oregon(fips):
    return fips.startswith(config.OREGON_STATE_FIPS)


def fetch(download_file):
    """Download what covers the whole state: its database, and FEMA's preliminary and pending maps."""
    archive = config.OREGON_DIR / "Oregon_Statewide_Flood_Hazards.zip"
    download_file(config.OREGON_FLOOD_DATABASE, archive)
    if not config.OREGON_GDB.exists():
        unpacking = config.OREGON_DIR / "unpacking"
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(unpacking)
        (unpacking / config.OREGON_GDB.name).rename(config.OREGON_GDB)
    for name, (url, fields) in config.OREGON_UPCOMING.items():
        path = config.OREGON_DIR / f"{name}.geojson"
        if not path.exists():
            count = download_layer(url, config.OREGON_BOUNDS, path, fields,
                                   f"DFIRM_ID LIKE '{config.OREGON_STATE_FIPS}%'")
            print(f"  Oregon {name}: {count} shapes", flush=True)


def _polygons_only(shapes):
    """The same rows with valid, flat, purely polygonal outlines."""
    shapes = shapes[shapes.geometry.notna()].copy()
    shapes["geometry"] = shapely.make_valid(shapely.force_2d(shapes.geometry.values))
    parts = shapes.explode(index_parts=False)
    return parts[parts.geom_type.isin(("Polygon", "MultiPolygon"))].reset_index(drop=True)


def _database(layer, bounds):
    """A layer of the state's database within bounds (in config.CRS)."""
    source_crs = pyogrio.read_info(config.OREGON_GDB, layer=layer)["crs"]
    source_bounds = gpd.GeoSeries([shapely.box(*bounds)], crs=config.CRS).to_crs(source_crs).total_bounds
    return pyogrio.read_dataframe(config.OREGON_GDB, layer=layer, bbox=tuple(source_bounds)).to_crs(config.CRS)


def paper_zones_without_digital_map(paper, digital):
    """The flood zones of paper maps, minus the land FEMA's digital map covers."""
    from .model import is_flood_zone

    paper = paper[paper.SOURCE.fillna("").str.startswith(config.PAPER_MAP_SOURCE)
                  & is_flood_zone(paper.FLD_ZONE.fillna(""))].copy()
    covering = digital[~digital.FLD_ZONE.isin(config.NOT_MAPPED_LABELS)]
    if len(paper) and len(covering):
        seam = config.DIGITAL_MAP_SEAM_M  # close the gaps the server's simplifying leaves between zones
        covered = covering.geometry.buffer(seam).union_all().buffer(-seam)
        paper["geometry"] = paper.geometry.difference(covered)
    return paper[~paper.geometry.is_empty]


def paper_zones(bounds, digital):
    paper = _polygons_only(_database("FEMA_FLD_HAZ_AR", bounds))
    paper["geometry"] = paper.geometry.intersection(shapely.box(*bounds))
    return paper_zones_without_digital_map(paper, digital)


def upcoming_zones(bounds):
    """FEMA's preliminary and pending 1% zones within bounds."""
    from .model import is_flood_zone

    found = [gpd.GeoDataFrame(geometry=[], crs=config.CRS)]
    for name in ("preliminary_zones", "pending_zones"):
        zones = gpd.read_file(config.OREGON_DIR / f"{name}.geojson").to_crs(config.CRS)
        if not len(zones):  # none waiting to take effect, and an empty file has no columns
            continue
        zones = zones.cx[bounds[0]:bounds[2], bounds[1]:bounds[3]]
        found.append(_polygons_only(zones[is_flood_zone(zones.FLD_ZONE.fillna(""))]))
    return gpd.pd.concat(found)


def flood_levels(bounds, paper):
    """(shapes, field holding the level) for the levels the upcoming maps and the paper maps publish."""
    found = []
    for name, field in config.OREGON_UPCOMING_LEVELS.items():
        lines = gpd.read_file(config.OREGON_DIR / f"{name}.geojson").to_crs(config.CRS)
        found.append((lines.cx[bounds[0]:bounds[2], bounds[1]:bounds[3]], field))
    if len(paper):
        lines = _database("FEMA_BFE", bounds)
        found.append((lines[lines.intersects(paper.geometry.union_all())], "ELEV"))
    return found


def past_flood():
    """The February 1996 flood as the Army Corps mapped it, the one past flood outline we trust (the 1964 outline
    takes in land above that flood's crest)."""
    return _polygons_only(pyogrio.read_dataframe(config.OREGON_GDB, layer=config.OREGON_PAST_FLOOD["layer"])
                          .to_crs(config.CRS))
