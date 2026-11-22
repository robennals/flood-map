"""The model's rules, checked on small made-up grids (30 m cells)."""
import numpy as np

from pipeline import config
from pipeline.model import ELEVATED, HIGHER, LOWER, MODERATE, classify

CELL_M = 30
FAR_ABOVE_CREEK = np.inf


def grid(rows=40, cols=200, ground=10.0):
    """Flat ground, a studied flood zone in the left 30 columns (about 10 hectares), no levees."""
    ground_m = np.full((rows, cols), ground, dtype=np.float32)
    zone = np.zeros((rows, cols), dtype=bool)
    zone[:, :30] = True
    levee = np.zeros((rows, cols), dtype=bool)
    above_creek_m = np.full((rows, cols), FAR_ABOVE_CREEK, dtype=np.float32)
    return ground_m, zone, levee, above_creek_m


def test_studied_zone_is_higher():
    level, _, _ = classify(*grid(), CELL_M)
    assert (level[:, :30] == HIGHER).all()


def test_flat_ground_steps_down_with_distance():
    level, _, _ = classify(*grid(), CELL_M)
    row = level[20]
    # 1 m of effective height per DIST_PENALTY_M metres: Elevated within 1 m, Moderate within 3 m
    elevated_edge = 30 + int(config.ELEV_ZONE_M * config.DIST_PENALTY_M / CELL_M)
    moderate_edge = 30 + int(config.MOD_ZONE_M * config.DIST_PENALTY_M / CELL_M)
    assert row[31] == ELEVATED
    assert row[elevated_edge + 1] == MODERATE
    assert row[moderate_edge + 2] == LOWER


def test_rising_ground_drops_to_lower_quickly():
    ground_m, zone, levee, above_creek_m = grid()
    ground_m[:, 30:] = 10 + np.arange(170) * 0.5  # a hillside rising 0.5 m per cell from the zone's edge
    level, _, _ = classify(ground_m, zone, levee, above_creek_m, CELL_M)
    assert level[20, 30] == ELEVATED  # at the edge
    assert level[20, 40] == LOWER  # 5 m up, only 300 m away


def test_ground_below_the_zone_edge_is_not_safer():
    ground_m, zone, levee, above_creek_m = grid()
    ground_m[:, 30:35] = 5.0  # a dip next to the zone
    level, _, _ = classify(ground_m, zone, levee, above_creek_m, CELL_M)
    assert (level[:, 30:35] == ELEVATED).all()


def test_small_zones_do_not_spread_risk():
    ground_m, zone, levee, above_creek_m = grid()
    zone[:] = False
    zone[18:21, 100:103] = True  # 9 cells, under 1 hectare
    level, _, _ = classify(ground_m, zone, levee, above_creek_m, CELL_M)
    assert (level[18:21, 100:103] == HIGHER).all()
    assert level[20, 104] == LOWER


def test_low_ground_near_a_creek_is_flagged_without_any_zone():
    ground_m, zone, levee, above_creek_m = grid()
    zone[:] = False
    above_creek_m[:, 100] = 1.0
    above_creek_m[:, 101] = 3.0
    level, _, _ = classify(ground_m, zone, levee, above_creek_m, CELL_M)
    assert level[20, 100] == ELEVATED
    assert level[20, 101] == MODERATE
    assert level[20, 150] == LOWER


def test_levee_protected_land_is_elevated():
    ground_m, zone, levee, above_creek_m = grid()
    levee[:, 180:] = True
    level, _, _ = classify(ground_m, zone, levee, above_creek_m, CELL_M)
    assert (level[:, 180:] == ELEVATED).all()


def test_unknown_elevation_and_creek_height_count_as_safe_ground():
    ground_m, zone, levee, above_creek_m = grid()
    ground_m[:, 150:] = np.nan
    above_creek_m[:, 150:] = np.nan
    level, elevated_score, _ = classify(ground_m, zone, levee, above_creek_m, CELL_M)
    assert (level[:, 150:] == LOWER).all()
    assert np.isfinite(elevated_score[:, :150]).all()


def test_levees_often_overtopped_are_higher():
    ground_m, zone, levee, above_creek_m = grid()
    levee[:, 150:] = True
    often = np.zeros_like(levee)
    often[:, 180:] = True  # this levee is overtopped more often than the 100-year standard
    level, _, _ = classify(ground_m, zone, levee, above_creek_m, CELL_M, levee_often_overtopped=often)
    assert (level[:, 150:180] == ELEVATED).all()
    assert (level[:, 180:] == HIGHER).all()


def test_ignored_channels_do_not_count_for_the_creek_rule():
    ground_m, zone, levee, above_creek_m = grid()
    zone[:] = False
    above_creek_m[:, 100:] = 1.0  # low ground beside a channel everywhere east of column 100
    ignored = np.zeros_like(levee)
    ignored[:, 150:] = True  # from column 150 on the channel is a canal or a small dry-region wash
    level, _, _ = classify(ground_m, zone, levee, above_creek_m, CELL_M, creek_ignored=ignored)
    assert (level[:, 100:150] == ELEVATED).all()
    assert (level[:, 150:] == LOWER).all()


def test_mudflow_properties_get_a_level_by_storm_size_and_issue():
    import geopandas as gpd
    from shapely.geometry import box
    from pipeline.model import mudflow_layers

    listed = {  # (PHASE1, PHASE2, PHASE3) -> expected layer
        ("Safety Issue", None, None): "mudflow_higher",
        (None, "Safety", None): "mudflow_higher",
        (None, None, "Safety Issue"): "mudflow_elevated",
        ("Access Issue", None, None): "mudflow_moderate",
        (None, None, "Access Issue"): "mudflow_moderate",
    }
    properties = gpd.GeoDataFrame(
        [dict(zip(("PHASE1", "PHASE2", "PHASE3"), phases), expected=expected) for phases, expected in listed.items()],
        geometry=[box(i, 0, i + 1, 1) for i in range(len(listed))])
    layers = mudflow_layers(properties)
    for name, shapes in layers.items():
        assert (shapes.expected == name).all(), name
    assert sum(len(shapes) for shapes in layers.values()) == len(listed)


def test_primo_parcels_get_a_level_by_depth_and_storm():
    import geopandas as gpd
    from shapely.geometry import box
    from pipeline.model import primo_layers

    deep, shallow, dry = config.PRIMO_DEEP_M + 0.1, (config.PRIMO_SHALLOW_M + config.PRIMO_DEEP_M) / 2, 0.0
    listed = {  # (depth in the 1-in-20-year storm, in the 1-in-100-year storm) -> expected layer
        (deep, deep): "primo_elevated",
        (shallow, deep): "primo_moderate",
        (dry, deep): "primo_moderate",
        (dry, shallow): "primo_moderate",
    }
    parcels = gpd.GeoDataFrame(
        [dict(h_20_drain=h20, h_100_drain=h100, expected=expected) for (h20, h100), expected in listed.items()]
        + [dict(h_20_drain=dry, h_100_drain=dry, expected=None)],
        geometry=[box(i, 0, i + 1, 1) for i in range(len(listed) + 1)])
    layers = primo_layers(parcels)
    for name, shapes in layers.items():
        assert (shapes.expected == name).all(), name
    assert sum(len(shapes) for shapes in layers.values()) == len(listed)  # the dry parcel is left out


def test_steep_bank_is_judged_against_the_published_flood_level():
    ground_m, zone, levee, above_creek_m = grid()
    ground_m[:, :30] = 3.0  # the river's surface, which is what the zone's edge cells hold at a seawall
    ground_m[:, 30:] = 10.5
    ground_m[:, 60:] = 20.0  # a bluff further back
    published = np.where(zone, 10.0, np.nan)  # FEMA's 100-year flood level
    level, _, _ = classify(ground_m, zone, levee, above_creek_m, CELL_M, published_flood_m=published)
    assert level[20, 31] == ELEVATED  # half a metre above the flood, though 7.5 m above the river
    assert level[20, 45] == MODERATE  # 480 m back adds 1.2 m
    assert level[20, 61] == LOWER


def test_published_flood_level_never_makes_ground_safer():
    ground_m, zone, levee, above_creek_m = grid()
    ground_m[:, 30:] = 10.5
    published = np.where(zone, 4.0, np.nan)  # below the ground at the zone's edge
    level, _, _ = classify(ground_m, zone, levee, above_creek_m, CELL_M, published_flood_m=published)
    assert (level == classify(ground_m, zone, levee, above_creek_m, CELL_M)[0]).all()


def test_a_published_level_above_the_dry_bank_is_not_believed():
    ground_m, zone, levee, above_creek_m = grid()
    ground_m[:, :30] = 3.0
    ground_m[:, 30:] = 10.5
    published = np.where(zone, 15.0, np.nan)  # a level from upstream: this would have flooded the bank FEMA shows dry
    level, _, _ = classify(ground_m, zone, levee, above_creek_m, CELL_M, published_flood_m=published)
    assert (level == classify(ground_m, zone, levee, above_creek_m, CELL_M)[0]).all()


def river_with_lines(*levels):
    """A river 60 cells long with a flood level line across it at columns 10, 30 and so on, and a second river
    a short way off over dry ground with no lines."""
    zone = np.zeros((20, 60), dtype=bool)
    zone[2:5, :] = True
    zone[8:11, :] = True
    line_at = np.zeros(zone.shape, dtype=np.int32)  # 0 for none, else the line's position in `levels` plus one
    for number, _ in enumerate(levels, start=1):
        line_at[1:6, 10 + 20 * (number - 1)] = number
    return line_at, np.array(levels), zone


def test_between_two_lines_the_flood_is_at_least_the_downstream_level():
    from pipeline.model import levels_between_lines

    levels = levels_between_lines(*river_with_lines(10.0, 14.0))
    assert (levels[2:5, 10:31] == 10.0).all()  # never the upstream level, which would be too high further down


def test_lines_with_the_same_level_still_bound_a_stretch():
    from pipeline.model import levels_between_lines

    assert (levels_between_lines(*river_with_lines(10.0, 10.0))[2:5, 11:30] == 10.0).all()


def test_flood_levels_stop_at_the_last_line_and_stay_in_their_own_river():
    from pipeline.model import levels_between_lines

    levels = levels_between_lines(*river_with_lines(10.0, 14.0))
    assert np.isnan(levels[2:5, :10]).all()
    assert np.isnan(levels[2:5, 31:]).all()
    assert np.isnan(levels[8:11]).all()
    assert np.isnan(levels[1]).all()  # where a line pokes out of the zone


def test_a_mistyped_level_is_outvoted_by_its_neighbours():
    from pipeline.model import levels_between_lines

    levels = levels_between_lines(*river_with_lines(10.0, 977.0, 14.0))
    assert np.nanmax(levels) == 14.0


def test_flood_level_lines_are_metres_above_the_same_datum_as_the_ground():
    import geopandas as gpd
    from shapely.geometry import LineString
    from pipeline.model import usable_flood_levels

    rows = [  # level, datum, unit
        (100.0, "NAVD88", "Feet"),
        (12.0, "NAVD88", "Meters"),
        (100.0, "NGVD29", "Feet"),  # an older datum, about a metre different in Oregon
        (100.0, "NP", "Feet"),  # no datum given
        (-9999.0, "NAVD88", "Feet"),  # FEMA's "no value"
        (100.0, "NAVD88", "NP"),
        (None, "NAVD88", "Feet"),
    ]
    lines = gpd.GeoDataFrame([dict(ELEV=e, V_DATUM=d, LEN_UNIT=u) for e, d, u in rows],
                             geometry=[LineString([(i, 0), (i, 1)]) for i in range(len(rows))])
    to_navd88_m = np.full(len(rows), 1.05)
    assert [round(m, 2) for _, m in usable_flood_levels(lines, "ELEV", to_navd88_m)] == [30.48, 12.0, 31.53]


def test_published_levels_are_read_from_the_downloaded_lines(tmp_path):
    import geopandas as gpd
    from rasterio.transform import from_origin
    from shapely.geometry import LineString
    from pipeline.model import _published_flood_levels

    zone = np.zeros((20, 60), dtype=bool)
    zone[5:10, :] = True
    transform = from_origin(0, 600, CELL_M, CELL_M)
    (sections, (_, section_field)), (others, _) = config.FEMA_LEVEL_LINES.items()
    (tmp_path / f"{others}.geojson").write_text('{"type": "FeatureCollection", "features": []}')
    (tmp_path / "fema_zone_levels.geojson").write_text('{"type": "FeatureCollection", "features": []}')

    def write_sections(*rows):
        gpd.GeoDataFrame([{section_field: feet, "V_DATUM": "NAVD88", "LEN_UNIT": "Feet"} for _, feet in rows],
                         geometry=[LineString([(x, 240), (x, 480)]) for x, _ in rows],
                         crs=config.CRS).to_crs(4326).to_file(tmp_path / f"{sections}.geojson", driver="GeoJSON")

    write_sections((315, 100.0), (915, 110.0))
    assert np.isclose(_published_flood_levels(tmp_path, zone, transform)[7, 20], 30.48)
    (tmp_path / f"{sections}.geojson").write_text('{"type": "FeatureCollection", "features": []}')
    assert np.isnan(_published_flood_levels(tmp_path, zone, transform)).all()  # a county with no detailed study


def test_a_zone_with_a_level_of_its_own_uses_it(tmp_path):
    import geopandas as gpd
    from rasterio.transform import from_origin
    from shapely.geometry import LineString, box
    from pipeline.model import _published_flood_levels

    zone = np.zeros((20, 60), dtype=bool)
    zone[5:10, :] = True
    transform = from_origin(0, 600, CELL_M, CELL_M)
    (sections, (_, section_field)), (others, _) = config.FEMA_LEVEL_LINES.items()
    (tmp_path / f"{others}.geojson").write_text('{"type": "FeatureCollection", "features": []}')
    gpd.GeoDataFrame([{section_field: 10.0, "V_DATUM": "NAVD88", "LEN_UNIT": "Feet"}] * 2,
                     geometry=[LineString([(x, 240), (x, 480)]) for x in (315, 1515)],
                     crs=config.CRS).to_crs(4326).to_file(tmp_path / f"{sections}.geojson", driver="GeoJSON")
    # Part of the zone is a wave zone on the coast, which FEMA gives one level
    gpd.GeoDataFrame([{"STATIC_BFE": 30.0, "V_DATUM": "NAVD88", "LEN_UNIT": "Feet"}],
                     geometry=[box(450, 300, 900, 450)],
                     crs=config.CRS).to_crs(4326).to_file(tmp_path / "fema_zone_levels.geojson", driver="GeoJSON")
    levels = _published_flood_levels(tmp_path, zone, transform)
    assert np.isclose(levels[7, 20], 9.144)  # its own level, not the lower one from the lines either side
    assert np.isclose(levels[7, 40], 3.048)


def test_only_flood_zone_labels_count_as_flood_zones():
    import pandas as pd
    from pipeline.model import is_flood_zone

    expected = {"A": True, "AE": True, "AH": True, "AO": True, "AR": True, "AR/AE": True, "A4": True, "A22": True,
                "V": True, "VE": True, "VO": True, "V12": True,
                "AREA NOT INCLUDED": False, "ANI": False, "A99": False, "X": False, "D": False, "OPEN WATER": False,
                "0.2 PCT ANNUAL CHANCE FLOOD HAZARD": False, "": False}
    assert dict(zip(expected, is_flood_zone(pd.Series(list(expected))))) == expected


def test_older_datum_levels_are_converted_with_noaas_grid(tmp_path):
    import geopandas as gpd
    import rasterio
    from rasterio.transform import from_origin
    from shapely.geometry import LineString
    from pipeline.model import ngvd29_to_navd88_m

    grid_path = tmp_path / "vertcon.tif"
    with rasterio.open(grid_path, "w", driver="GTiff", width=2, height=1, count=1, dtype="float32",
                       crs="EPSG:4267", transform=from_origin(-124, 46, 1, 1)) as dst:
        dst.write(np.array([[1.1, 0.8]], dtype=np.float32), 1)
    lines = gpd.GeoSeries([LineString([(-123.6, 45.4), (-123.4, 45.6)]),  # in the grid's west cell
                           LineString([(-122.6, 45.4), (-122.4, 45.6)])],  # and in its east cell
                          crs=4326).to_crs(config.CRS)
    assert np.allclose(ngvd29_to_navd88_m(lines, grid_path), [1.1, 0.8])


def test_units_count_however_they_are_capitalised():
    import geopandas as gpd
    from shapely.geometry import LineString
    from pipeline.model import usable_flood_levels

    lines = gpd.GeoDataFrame([dict(ELEV=100.0, V_DATUM="NAVD88", LEN_UNIT="FEET")],  # as Oregon's database has it
                             geometry=[LineString([(0, 0), (0, 1)])])
    assert [round(m, 2) for _, m in usable_flood_levels(lines, "ELEV", np.zeros(1))] == [30.48]


def test_paper_maps_only_fill_in_where_fema_has_no_digital_map():
    import geopandas as gpd
    from shapely.geometry import box
    from pipeline.oregon import paper_zones_without_digital_map

    paper = gpd.GeoDataFrame(
        {"FLD_ZONE": ["A", "A4", "X", "AE"],
         "SOURCE": ["FEMA FIRM - Grant County 19820518"] * 3 + ["FEMA DFIRM - Jackson County 20110503"]},
        geometry=[box(0, 0, 4000, 100), box(0, 1000, 4000, 1100), box(0, 2000, 4000, 2100), box(0, 3000, 4000, 3100)],
        crs=config.CRS)
    digital = gpd.GeoDataFrame(
        {"FLD_ZONE": ["X", "AE", "D"]},
        # two zones with a 4 m seam between them (the server simplifies shapes), and an "undetermined" one
        geometry=[box(0, -50, 1000, 1200), box(1004, -50, 2000, 1200), box(3000, -50, 4000, 1200)],
        crs=config.CRS)
    filled = paper_zones_without_digital_map(paper, digital)
    assert set(filled.FLD_ZONE) == {"A", "A4"}  # only flood zones, and only those from paper maps
    for (minx, _, maxx, _) in filled.geometry.bounds.itertuples(index=False):
        assert abs(minx - 2000) < 6 and abs(maxx - 4000) < 6  # not over the digital map, or in its seams


def test_every_oregon_county_is_in_the_oregon_region():
    assert list(config.REGIONS["oregon"]) == [f"41{n:03d}" for n in range(1, 72, 2)]
    assert not any(fips.startswith("41") for fips in config.REGIONS["california"])


def test_a_failed_elevation_download_leaves_nothing_to_reuse(tmp_path, monkeypatch):
    import subprocess
    from pathlib import Path

    import pytest
    from shapely.geometry import box
    from pipeline import model

    def dropped_connection(command, **options):
        Path(command[-1]).write_bytes(b"half a file")
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(config, "WORK", tmp_path)
    monkeypatch.setattr(model.subprocess, "run", dropped_connection)
    (tmp_path / "counties" / "41019").mkdir(parents=True)
    with pytest.raises(subprocess.CalledProcessError):
        model._ground_elevation("oregon", "41019", box(0, 0, 100, 100))
    assert not (tmp_path / "counties" / "41019" / "elevation30.tif").exists()


def test_hills_inside_a_filled_in_closed_basin_are_not_at_creek_level():
    from pipeline.model import without_filled_basins

    ground_m = np.full((100, 300), 100.0, dtype=np.float32)
    ground_m[:, 100:200] = 100 + np.arange(100) * 0.5  # up from the basin's floor
    ground_m[:, 200:] = 150.0
    above_creek_m = np.full(ground_m.shape, 20.0, dtype=np.float32)
    above_creek_m[:, :200] = 0.0  # the national data fills the basin to the brim
    fixed = without_filled_basins(above_creek_m, ground_m, CELL_M)
    assert (fixed[:, :99] < config.ELEV_CREEK_M).all()  # the floor, where water collects, is still low ground
    assert (fixed[:, 115:200] >= config.MOD_CREEK_M).all()  # the hillside isn't
    assert (fixed[:, 200:] == 20.0).all()


def test_shallow_flats_at_creek_level_are_left_alone():
    from pipeline.model import without_filled_basins

    ground_m = np.tile(np.linspace(10, 15, 200, dtype=np.float32), (100, 1))  # like Portland's Columbia flats
    above_creek_m = np.zeros(ground_m.shape, dtype=np.float32)
    assert (without_filled_basins(above_creek_m, ground_m, CELL_M) == 0).all()
