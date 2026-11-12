"""Run the model for every county in a region and build the map files the website reads.

Usage: python -m pipeline.build <region> [county_fips ...]   (default: every county in the region)
Writes data/out/<region>/risk.pmtiles and history.pmtiles, and copies them to web/data/ for local testing.
"""
import os
import shutil
import subprocess
import sys

from . import config
from . import history, model


def tippecanoe(src, dest):
    tmp = os.environ.get("TMPDIR", "/tmp")
    subprocess.run(["tippecanoe", "-q", "-t", tmp, "-o", str(dest), "-Z8", "-z12", "--force",
                    "--coalesce-densest-as-needed", "--extend-zooms-if-still-dropping", str(src)], check=True)


def main(region, only=None):
    counties = config.REGIONS[region]
    out = config.OUT / region
    out.mkdir(parents=True, exist_ok=True)
    for fips, name in counties.items():
        if only and fips not in only:
            continue
        share = model.run(region, fips, name)
        print(f"{name:14s} " + "  ".join(f"{k} {v}%" for k, v in share.items()), flush=True)

    combined = config.WORK / region / "risk.geojsonseq"
    with open(combined, "wb") as dst:
        for fips in counties:
            with open(config.county_work(fips) / "risk.geojsonseq", "rb") as src:
                shutil.copyfileobj(src, dst)
    tippecanoe(combined, out / "risk.pmtiles")

    placed, total = history.write_layers(counties, config.WORK / region / "history.geojsonseq")
    tippecanoe(config.WORK / region / "history.geojsonseq", out / "history.pmtiles")
    print(f"claims since {config.SINCE_YEAR}: {placed} of {total} placed on a census block group")

    local = config.REPO / "web" / "data" / region
    local.mkdir(parents=True, exist_ok=True)
    for f in ("risk.pmtiles", "history.pmtiles"):
        shutil.copy(out / f, local / f)
        print(f"{f}: {(out / f).stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main(sys.argv[1], set(sys.argv[2:]) or None)
