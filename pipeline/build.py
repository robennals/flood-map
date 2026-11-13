"""Run the model for every county in a region and build the map files the website reads.

Usage: python -m pipeline.build <region> [county_fips ...]   (default: every county in the region)
       python -m pipeline.build <region> --tiles-only        (just rebuild the map files from existing results)
Writes data/out/<region>/risk.pmtiles and history.pmtiles, and copies them to web/data/ for local testing.
"""
import os
import shutil
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed

from . import config
from . import history, model


def tippecanoe(src, dest):
    tmp = os.environ.get("TMPDIR", "/tmp")
    # Zoomed-out tiles cover a lot of ground, so shapes there are simplified harder to stay under the tile size
    # limit (detailed desert terrain overflowed it); street-level zooms keep full detail.
    subprocess.run(["tippecanoe", "-q", "-t", tmp, "-o", str(dest), "-Z5", "-z12", "--force",
                    "--coalesce-densest-as-needed", "--extend-zooms-if-still-dropping",
                    "--simplification=10", "--simplify-only-low-zooms", str(src)], check=True)


def main(region, only=None, run_model=True):
    counties = config.REGIONS[region]
    out = config.OUT / region
    out.mkdir(parents=True, exist_ok=True)
    todo = {fips: name for fips, name in counties.items() if run_model and (not only or fips in only)}
    failed = []
    report = lambda name, share: print(f"{name:14s} " + "  ".join(f"{k} {v}%" for k, v in share.items()), flush=True)
    # Counties are independent, so several run at once (each can use a few GB of memory)
    try:
        pool = ProcessPoolExecutor(max_workers=config.BUILD_WORKERS)
    except OSError:  # some sandboxes don't allow worker processes; fall back to one county at a time
        pool = None
    if pool is None or config.BUILD_WORKERS == 1:
        for fips, name in todo.items():
            report(name, model.run(region, fips, name))
    else:
        with pool:
            jobs = {pool.submit(model.run, region, fips, name): name for fips, name in todo.items()}
            for job in as_completed(jobs):
                try:
                    report(jobs[job], job.result())
                except Exception as err:  # keep going; report every failure at the end
                    print(f"{jobs[job]:14s} FAILED: {type(err).__name__}: {err}", flush=True)
                    failed.append(jobs[job])

    if failed:
        sys.exit(f"modelling failed for: {', '.join(failed)} (map files not rebuilt)")
    built = [fips for fips in counties if (config.county_work(fips) / "risk.geojsonseq").exists()]
    missing = [counties[fips] for fips in counties if fips not in built]
    if missing:
        print(f"not modelled yet, left out of the map files: {', '.join(missing)}")
    combined = config.WORK / region / "risk.geojsonseq"
    with open(combined, "wb") as dst:
        for fips in built:
            with open(config.county_work(fips) / "risk.geojsonseq", "rb") as src:
                shutil.copyfileobj(src, dst)
    tippecanoe(combined, out / "risk.pmtiles")

    placed, total = history.write_layers(built, config.WORK / region / "history.geojsonseq")
    tippecanoe(config.WORK / region / "history.geojsonseq", out / "history.pmtiles")
    print(f"claims since {config.SINCE_YEAR}: {placed} of {total} placed on a census block group")

    local = config.REPO / "web" / "data" / region
    local.mkdir(parents=True, exist_ok=True)
    for f in ("risk.pmtiles", "history.pmtiles"):
        shutil.copy(out / f, local / f)
        print(f"{f}: {(out / f).stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    args = sys.argv[2:]
    main(sys.argv[1], set(a for a in args if a != "--tiles-only") or None, run_model="--tiles-only" not in args)
