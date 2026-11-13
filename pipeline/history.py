"""The "past floods" layers: real flood outlines, plus FEMA flood insurance claims per census block group.

FEMA only says which census block group (a neighbourhood of a few hundred homes) each claim is in,
so each block group with claims becomes one point at its middle.
"""
import collections
import json

import geopandas as gpd

from . import config


def write_layers(counties, out_path):
    """Returns (claims placed on the map, claims since SINCE_YEAR in total)."""
    placed = total = 0
    with open(out_path, "w") as out:
        for name, (_, creek, year) in config.VALLEY_WATER_FLOODS.items():
            path = config.RAW / "history" / f"{name}.geojson"
            if not path.exists():
                continue
            outline = gpd.read_file(path).to_crs(4326)
            if year is None and "FLOOD_YEAR" in outline.columns and outline.FLOOD_YEAR.notna().any():
                year = int(outline.FLOOD_YEAR.dropna().iloc[0])
            for part in outline.geometry.explode(index_parts=False):
                out.write(json.dumps({"type": "Feature", "tippecanoe": {"layer": "past_floods"},
                                      "properties": {"creek": creek, "year": year},
                                      "geometry": part.__geo_interface__}) + "\n")

        for fips in counties:
            raw = config.county_raw(fips)
            claims = [claim for claim in json.loads((raw / "claims.json").read_text())
                      if claim["censusGeoid"] and claim["yearOfLoss"] and claim["yearOfLoss"] >= config.SINCE_YEAR]
            total += len(claims)
            years_by_block_group = collections.defaultdict(collections.Counter)
            for claim in claims:
                years_by_block_group[claim["censusGeoid"]][claim["yearOfLoss"]] += 1
            block_groups = gpd.read_file(f"zip://{raw}/tl_2010_{fips}_bg10.zip")
            block_groups = block_groups[block_groups.GEOID10.isin(years_by_block_group)].to_crs(config.CRS)
            middles = gpd.GeoSeries(block_groups.geometry.representative_point(), crs=config.CRS).to_crs(4326)
            # The neighbourhood's outline, shown when its circle is clicked: the claims could be anywhere in it
            outlines = block_groups.geometry.simplify(20).to_crs(4326)
            for geoid, middle, outline in zip(block_groups.GEOID10, middles, outlines):
                years = years_by_block_group[geoid]
                placed += sum(years.values())
                out.write(json.dumps({"type": "Feature", "tippecanoe": {"layer": "claims"},
                                      "properties": {"geoid": geoid, "claims": sum(years.values()),
                                                     "years": ", ".join(f"{y} ({n})" if n > 1 else str(y)
                                                                        for y, n in sorted(years.items()))},
                                      "geometry": middle.__geo_interface__}) + "\n")
                out.write(json.dumps({"type": "Feature", "tippecanoe": {"layer": "claim_areas"},
                                      "properties": {"geoid": geoid}, "geometry": outline.__geo_interface__}) + "\n")
    return placed, total
