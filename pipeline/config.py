"""Shared settings: where files live, which counties make up each region, and the model's thresholds."""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"  # git-ignored; everything in it can be re-created with `make fetch build`
RAW = DATA / "raw"  # downloads, exactly as received
WORK = DATA / "work"  # intermediate files
OUT = DATA / "out"  # finished map files, one folder per region

# Regions are lists of county FIPS codes (state 06 = California)
REGIONS = {
    "bay-area": {
        "06001": "Alameda", "06013": "Contra Costa", "06041": "Marin", "06055": "Napa",
        "06075": "San Francisco", "06081": "San Mateo", "06085": "Santa Clara",
        "06095": "Solano", "06097": "Sonoma",
        "06087": "Santa Cruz",  # not one of the nine ABAG counties, but floods with the same storms
    },
}

CRS = "EPSG:3310"  # California Albers, in metres
GRID_M = 30  # terrain grid
COUNTY_MARGIN_DEG = 0.05  # download official zones about 5 km beyond each county line
COUNTY_MARGIN_M = 3000  # and model terrain 3 km beyond it, so zones across the line still count

# Model thresholds (explained in web/about.html; keep the two in sync)
DIST_PENALTY_M = 400  # 1 m of effective height per this many metres from a flood zone
ELEV_ZONE_M, ELEV_CREEK_M = 1.0, 1.5  # Elevated: within this height of a zone's level / above a creek
MOD_ZONE_M, MOD_CREEK_M = 3.0, 4.0  # Moderate: same, looser
MIN_SOURCE_HA = 5  # only flood zones at least this big spread risk to nearby ground
SHARP_TOL_M, LOOSE_TOL_M = 10, 30  # outline simplification: official zones / terrain and notes
SINCE_YEAR = 1995  # flood insurance claims shown and checked from this year on

# Data sources
FEMA_ZONES = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28"
DWR_BAM = "https://arcgis04.geiconsultants.com/arcgis/rest/services/BAM/BAM2025/MapServer"
DWR_LAYERS = {"bam_6": 6, "bam_8": 8, "bam_14": 14}  # FEMA preliminary 1%, DWR awareness, FEMA preliminary 0.2%
TIGER_COUNTY = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/13/query"
TIGER_WATER = "https://www2.census.gov/geo/tiger/TIGER2025/AREAWATER/tl_2025_{fips}_areawater.zip"
MIN_WATER_KM2 = 1.0  # water bodies at least this big (Bay, ocean, big reservoirs) are cut out of the map
TIGER_BLOCK_GROUPS = "https://www2.census.gov/geo/tiger/TIGER2010/BG/2010/tl_2010_{fips}_bg10.zip"
FEMA_CLAIMS = "https://www.fema.gov/api/open/v3/NfipClaims"
WBD_HUC6 = "https://hydro.nationalmap.gov/arcgis/rest/services/wbd/MapServer/3/query"
HAND_TIF = "https://web.corral.tacc.utexas.edu/nfiedata/HAND/{huc6}/{huc6}hand.tif"
USGS_DEM_TILE = ("https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/current/"
                 "{tile}/USGS_13_{tile}.tif")

# Real flood outlines published by Santa Clara Valley Water District (ArcGIS Online owner SCVWDHHG):
# file name -> (feature service name, creek, flood year if known)
VALLEY_WATER_FLOODS = {
    "Coy15yr_2017": ("Coy15yr_2017", "Coyote Creek", 2017),
    "SFC_7_2kStanford_1998": ("SFC_7_2kStanford_1998", "San Francisquito Creek", 1998),
    "SFC_5_8kStanford_2023": ("SFC_5_8kStanford_2023", "San Francisquito Creek", 2023),
    "Uvas20yr_8kLuchessa_HistFld": ("Uvas20yr_8kLuchessa", "Uvas Creek", None),
    "WLL_Edmundson350_HistFld": ("WLL_Edmundson350_HistFld", "West Little Llagas Creek", None),
}
VALLEY_WATER_SERVICE = "https://services2.arcgis.com/9KdAx8qBsHiGXOEw/arcgis/rest/services/{service}/FeatureServer/0"
VALLEY_WATER_COUNTY = "06085"


def county_raw(fips):
    return RAW / "counties" / fips


def county_work(fips):
    return WORK / "counties" / fips
