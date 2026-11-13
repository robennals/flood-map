"""Shared settings: where files live, which counties make up each region, and the model's thresholds."""
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"  # git-ignored; everything in it can be re-created with `make fetch build`
RAW = DATA / "raw"  # downloads, exactly as received
WORK = DATA / "work"  # intermediate files
OUT = DATA / "out"  # finished map files, one folder per region

# Counties are added in groups, roughly in order of population (FIPS codes; state 06 = California)
COUNTY_GROUPS = {
    "bay-area": {
        "06001": "Alameda", "06013": "Contra Costa", "06041": "Marin", "06055": "Napa",
        "06075": "San Francisco", "06081": "San Mateo", "06085": "Santa Clara",
        "06095": "Solano", "06097": "Sonoma",
        "06087": "Santa Cruz",  # not one of the nine ABAG counties, but floods with the same storms
    },
    "southern-california": {
        "06037": "Los Angeles", "06073": "San Diego", "06059": "Orange", "06065": "Riverside",
        "06071": "San Bernardino", "06111": "Ventura",
    },
    "sacramento-northern-valley": {
        "06067": "Sacramento", "06077": "San Joaquin", "06061": "Placer", "06113": "Yolo",
        "06017": "El Dorado", "06099": "Stanislaus", "06101": "Sutter", "06115": "Yuba",
    },
    "southern-valley-central-coast": {
        "06019": "Fresno", "06029": "Kern", "06107": "Tulare", "06047": "Merced", "06039": "Madera",
        "06031": "Kings", "06053": "Monterey", "06083": "Santa Barbara", "06079": "San Luis Obispo",
        "06069": "San Benito",
    },
    "rest-of-california": {
        "06007": "Butte", "06089": "Shasta", "06025": "Imperial", "06023": "Humboldt", "06057": "Nevada",
        "06045": "Mendocino", "06033": "Lake", "06103": "Tehama", "06109": "Tuolumne", "06009": "Calaveras",
        "06093": "Siskiyou", "06005": "Amador", "06021": "Glenn", "06035": "Lassen", "06015": "Del Norte",
        "06011": "Colusa", "06063": "Plumas", "06027": "Inyo", "06043": "Mariposa", "06105": "Trinity",
        "06051": "Mono", "06049": "Modoc", "06091": "Sierra", "06003": "Alpine",
    },
}
# A region is one group, or "california" for all of them; the website shows one region.
REGIONS = {**COUNTY_GROUPS, "california": {fips: name for group in COUNTY_GROUPS.values() for fips, name in group.items()}}

# Counties modelled at once by `make build` (each can use a few GB of memory)
BUILD_WORKERS = int(os.environ.get("BUILD_WORKERS", 3))

# Downloads stop before free disk space would fall below this
MIN_FREE_DISK_GB = 15

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
SINCE_YEAR = 1995
# Which stream channels the "height above the nearest creek" rule counts. A channel's flood potential comes from
# the water arriving from upstream (rain and snowmelt over its whole drainage area), not from local rainfall:
# - spots not tied to any stream reach never count (flat basins that drain nowhere);
# - canals, ditches and pipelines never count;
# - in wet regions every natural stream counts;
# - in dry regions (average rainfall under ARID_RAINFALL_MM a year) only real rivers count: draining at least
#   RIVER_MIN_AREA_KM2, or with average flow of at least RIVER_MIN_FLOW_CFS (snowmelt-fed rivers like the Kern).
#   Small desert washes rarely flow, and on flat desert ground the rule marked huge areas.
ARID_RAINFALL_MM = 250
RIVER_MIN_AREA_KM2 = 100
RIVER_MIN_FLOW_CFS = 10
ARTIFICIAL_CHANNEL_TYPES = {"CanalDitch", "Pipeline"}  # flood insurance claims shown and checked from this year on

# Data sources
FEMA_ZONES = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28"
DWR_BAM = "https://arcgis04.geiconsultants.com/arcgis/rest/services/BAM/BAM2025/MapServer"
DWR_LAYERS = {"bam_6": 6, "bam_8": 8, "bam_14": 14}  # FEMA preliminary 1%, DWR awareness, FEMA preliminary 0.2%
# Army Corps National Levee Database: the land each levee protects, with its yearly overtopping chance
NLD_LEVEED_AREAS = "https://geospatial.sec.usace.army.mil/dls/rest/services/NLD/Public/FeatureServer/16"
# Levees overtopped at least this often each year count as Higher. The Army Corps records overtopping in steps
# (0.5%, 1%, 2%, 5%...), and levees built to the 100-year standard sit in the 1% step, so 2% means "more often
# than the 100-year standard".
LEVEE_HIGHER_OVERTOPPING = 0.02
TIGER_COUNTY = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/13/query"
TIGER_WATER = "https://www2.census.gov/geo/tiger/TIGER2025/AREAWATER/tl_2025_{fips}_areawater.zip"
MIN_WATER_KM2 = 1.0  # water bodies at least this big (Bay, ocean, big reservoirs) are cut out of the map
TIGER_BLOCK_GROUPS = "https://www2.census.gov/geo/tiger/TIGER2010/BG/2010/tl_2010_{fips}_bg10.zip"
FEMA_CLAIMS = "https://www.fema.gov/api/open/v3/NfipClaims"
WBD_HUC6 = "https://hydro.nationalmap.gov/arcgis/rest/services/wbd/MapServer/3/query"
HAND_TIF = "https://web.corral.tacc.utexas.edu/nfiedata/HAND/{huc6}/{huc6}hand.tif"
# Each spot's catchment (which NHDPlus stream reach it drains to), and those reaches' attributes
HAND_CATCHMENTS = "https://web.corral.tacc.utexas.edu/nfiedata/HAND/{huc6}/{huc6}catchmask.tif"
HAND_REACHES = "https://web.corral.tacc.utexas.edu/nfiedata/HAND/{huc6}/{huc6}-flows.dbf"
PRISM_ANNUAL_RAINFALL = ("https://data.prism.oregonstate.edu/normals/us/4km/ppt/monthly/"
                         "prism_ppt_us_25m_2020_avg_30y.zip")  # Oregon State PRISM 1991-2020 average, about 4 km grid
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
