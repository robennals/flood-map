"""Download every feature of an ArcGIS REST layer inside a bounding box, as GeoJSON.

Public government map servers are often busy, so this uses small pages, pauses between them,
and retries with growing waits.
"""
import json
import sys
import time

import requests

PAGE_SIZE = 500
PAGE_PAUSE_S = 1.0
ATTEMPTS = 20
SIMPLIFY_DEG = 0.00003  # about 3 m


def _get(url, params, what, attempts=ATTEMPTS):
    for attempt in range(attempts):
        try:
            resp = requests.get(url, params=params, timeout=180)
            return resp.json()
        except (requests.RequestException, ValueError) as err:
            print(f"  retry {attempt + 1}/{attempts} {what}: {type(err).__name__}", file=sys.stderr)
            time.sleep(min(10 * (attempt + 1), 120))
    raise RuntimeError(f"giving up on {what} from {url}")


def download_layer(layer_url, bbox, out_path, fields="*", where="1=1"):
    """bbox is (minx, miny, maxx, maxy) in longitude/latitude. Writes a GeoJSON FeatureCollection."""
    info = _get(layer_url, {"f": "json"}, "layer info")
    page_size = min(info.get("maxRecordCount", 1000), PAGE_SIZE)
    params = {
        "where": where, "geometry": ",".join(str(v) for v in bbox), "geometryType": "esriGeometryEnvelope",
        "inSR": 4326, "spatialRel": "esriSpatialRelIntersects", "outFields": fields, "outSR": 4326,
        "returnGeometry": "true", "f": "geojson",
        # Let the server simplify shapes to about 3 m (we simplify to 10 m+ later anyway): much smaller responses
        "maxAllowableOffset": SIMPLIFY_DEG, "geometryPrecision": 6,
    }
    features, offset = [], 0
    while True:
        # Busy servers tend to drop large responses, so after repeated failures ask for fewer shapes at a time
        while True:
            try:
                page = _get(f"{layer_url}/query", {**params, "resultOffset": offset, "resultRecordCount": page_size},
                            f"features from {offset} ({page_size} per page)", attempts=3 if page_size > 25 else ATTEMPTS)
                break
            except RuntimeError:
                if page_size <= 25:
                    raise
                page_size = max(25, page_size // 2)
        if "error" in page:
            raise RuntimeError(f"server error from {layer_url}: {page['error']}")
        batch = page.get("features", [])
        features.extend(batch)
        more = page.get("exceededTransferLimit") or page.get("properties", {}).get("exceededTransferLimit")
        if not batch or (len(batch) < page_size and not more):
            break
        offset += len(batch)
        time.sleep(PAGE_PAUSE_S)
    tmp = out_path.with_suffix(".partial")
    tmp.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    tmp.rename(out_path)  # only a complete download gets the real name
    return len(features)
