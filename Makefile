# Rebuild the flood map from public data. See README.md.
REGION ?= bay-area
PYTHON := uv run --quiet python

.PHONY: setup test fetch build check upload serve all clean

setup:  ## Install the Python packages (needs uv, GDAL and tippecanoe; see README)
	uv sync

test:  ## Run the unit tests for the model rules
	uv run --quiet pytest -q

fetch:  ## Download all source data into data/raw (several GB; safe to re-run)
	$(PYTHON) -m pipeline.fetch $(REGION)

build:  ## Run the model and make the map files in data/out/$(REGION) and web/data/$(REGION)
	$(PYTHON) -m pipeline.build $(REGION)

check:  ## Compare the result with real floods and insurance claims
	$(PYTHON) -m pipeline.validate $(REGION)

upload:  ## Upload the map files to R2 (settings in .env.local)
	$(PYTHON) -m pipeline.upload $(REGION)

serve:  ## Serve the website locally at http://localhost:8765/?data=local
	$(PYTHON) -m pipeline.serve 8765

all: fetch build check

clean:  ## Delete intermediate and output files (keeps downloads)
	rm -rf data/work data/out web/data
