"""Check the model against real floods. None of this data is used to set the levels, so it's a fair test.

- Real flood outlines (where published): what share of flooded land each level covers.
- FEMA flood insurance claims since SINCE_YEAR, by census block group: do claims concentrate in
  block groups the model flags?

Usage: python -m pipeline.validate <region>     (after `python -m pipeline.build <region>`)
"""
import json
import sys

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import rasterize

from . import config
from .model import LEVEL_SHIFT

NAMES = {4: "higher", 3: "elevated", 2: "moderate", 1: "lower"}
BANDS = [(0, 1e-9, "none"), (1e-9, 0.1, "under 10%"), (0.1, 0.3, "10-30%"), (0.3, 2, "over 30%")]


def check_county(fips):
    with rasterio.open(config.county_work(fips) / "level.tif") as src:
        level = src.read(1) >> LEVEL_SHIFT
        transform, crs = src.transform, src.crs
    inside = level > 0
    cell_km2 = transform.a ** 2 / 1e6

    def shares(mask):
        n = (mask & inside).sum()
        return n, {NAMES[v]: 100 * ((level == v) & mask & inside).sum() / max(n, 1) for v in (4, 3, 2, 1)}

    result = {"county_share": shares(np.ones_like(inside))[1], "floods": {}, "claims": {}}

    if fips == config.VALLEY_WATER_COUNTY:
        for name in config.VALLEY_WATER_FLOODS:
            path = config.RAW / "history" / f"{name}.geojson"
            if path.exists():
                outline = gpd.read_file(path).to_crs(crs)
                mask = rasterize(((g, 1) for g in outline.geometry), out_shape=level.shape, transform=transform,
                                 dtype="uint8") > 0
                n, s = shares(mask)
                result["floods"][name] = {"km2": round(n * cell_km2, 2), **{k: round(v) for k, v in s.items()}}

    raw = config.county_raw(fips)
    claims = [claim for claim in json.loads((raw / "claims.json").read_text())
              if claim["censusGeoid"] and claim["yearOfLoss"] and claim["yearOfLoss"] >= config.SINCE_YEAR]
    block_groups = gpd.read_file(f"zip://{raw}/tl_2010_{fips}_bg10.zip").to_crs(crs)
    ids = rasterize(((g, i + 1) for i, g in enumerate(block_groups.geometry)), out_shape=level.shape,
                    transform=transform, dtype="int32")
    flat_ids, flat_lvl = ids[inside], level[inside]
    cells = np.bincount(flat_ids, minlength=len(block_groups) + 1)[1:].astype(float)
    flagged = np.bincount(flat_ids, weights=(flat_lvl >= 3), minlength=len(block_groups) + 1)[1:] / np.maximum(cells, 1)
    counts = gpd.pd.Series([claim["censusGeoid"] for claim in claims]).value_counts()
    claims_per_block_group = block_groups.GEOID10.map(counts).fillna(0).to_numpy()
    for lo, hi, label in BANDS:
        sel = (flagged >= lo) & (flagged < hi) & (cells > 0)
        result["claims"][label] = {"block_groups": int(sel.sum()), "claims": int(claims_per_block_group[sel].sum())}
    result["claims_total"] = len(claims)
    result["claims_placed"] = int(claims_per_block_group[cells > 0].sum())
    return result


def main(region):
    report = {}
    for fips, name in config.REGIONS[region].items():
        if not (config.county_work(fips) / "level.tif").exists():
            continue  # not modelled yet
        result = check_county(fips)
        report[fips] = {"name": name, **result}
        print(f"\n{name} ({fips})   " + "  ".join(f"{k} {v:.0f}%" for k, v in result["county_share"].items()))
        for flood, flood_share in result["floods"].items():
            print(f"  real flood {flood:28s} {flood_share['km2']:6.2f} km²  higher {flood_share['higher']}%  elevated {flood_share['elevated']}%"
                  f"  moderate {flood_share['moderate']}%  lower {flood_share['lower']}%")
        print(f"  claims since {config.SINCE_YEAR}: {result['claims_placed']} of {result['claims_total']} placed. "
              "Block groups by share of land rated higher/elevated:")
        for label, band in result["claims"].items():
            per = band["claims"] / band["block_groups"] if band["block_groups"] else 0
            print(f"    {label:10s} {band['block_groups']:5d} block groups {band['claims']:5d} claims  ({per:.2f} each)")
    out = config.OUT / region / "validation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1))
    print(f"\nwritten {out}")


if __name__ == "__main__":
    main(sys.argv[1])
