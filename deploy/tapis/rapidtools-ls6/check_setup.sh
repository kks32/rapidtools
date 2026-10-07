#!/bin/bash
# PRE_SCRIPT for running check_data_access.py through the DesignSafe python-s3
# app. Installs rapidtools without its model dependencies (no torch) into the
# job's Python environment. Pair it with
#   PIP_PACKAGES=rasterio,shapely,pyshp,scikit-image,huggingface_hub,pandas,matplotlib,requests,tqdm
# which makes python-s3 create the venv this script installs into.
set -euo pipefail
BRANCH="${RAPIDTOOLS_REF:-dapi-eaton-fire}"
REPO="${RAPIDTOOLS_REPO:-https://github.com/kks32/rapidtools}"
pip3 install --no-deps "git+${REPO}@${BRANCH}"
python3 -c "import rapidtools, rapidtools.data_sources.designsafe as d; print('rapidtools', rapidtools.__version__, 'from', d.__file__)"
