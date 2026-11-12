"""Upload a region's finished map files to a Cloudflare R2 bucket (or any S3-compatible storage).

Settings come from environment variables, or from .env.local at the repo root (see .env.example):
  R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET
Files go to <bucket>/<region>/. Secret values are never printed.

Usage: python -m pipeline.upload <region>
"""
import os
import sys

import boto3
from botocore.config import Config
from dotenv import dotenv_values

from . import config

FILES = ("risk.pmtiles", "history.pmtiles")


def settings():
    """R2 settings from .env.local, with real environment variables taking precedence."""
    values = {**dotenv_values(config.REPO / ".env.local"), **os.environ}
    missing = [k for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET") if not values.get(k)]
    if missing:
        sys.exit(f"missing settings: {', '.join(missing)} (set them in .env.local; see .env.example)")
    return values


def main(region):
    r2 = settings()
    s3 = boto3.client("s3", endpoint_url=f"https://{r2['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
                      aws_access_key_id=r2["R2_ACCESS_KEY_ID"], aws_secret_access_key=r2["R2_SECRET_ACCESS_KEY"],
                      region_name="auto", config=Config(retries={"max_attempts": 5}))
    for name in FILES:
        path = config.OUT / region / name
        if not path.exists():
            sys.exit(f"{path} not found: run `make build REGION={region}` first")
        key = f"{region}/{name}"
        s3.upload_file(str(path), r2["R2_BUCKET"], key, ExtraArgs={
            "ContentType": "application/vnd.pmtiles", "CacheControl": "public, max-age=3600"})
        size = s3.head_object(Bucket=r2["R2_BUCKET"], Key=key)["ContentLength"]
        print(f"uploaded {key} ({size / 1e6:.2f} MB)")
    print("Browsers may keep the previous version for up to an hour (Cache-Control max-age=3600).")


if __name__ == "__main__":
    main(sys.argv[1])
