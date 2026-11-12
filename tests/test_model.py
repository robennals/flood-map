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
