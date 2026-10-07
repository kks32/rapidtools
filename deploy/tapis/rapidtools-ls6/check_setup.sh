#!/bin/bash
# PRE_SCRIPT for running check_data_access.py through the DesignSafe python-s3
# app. Installs rapidtools without its model dependencies (no torch) into the
# job's Python environment. Set PIP_PACKAGES to any package (for example
# "rasterio") so python-s3 creates the venv this script installs into; the
# full import set is installed here.
set -euo pipefail
BRANCH="${RAPIDTOOLS_REF:-dapi-eaton-fire}"
REPO="${RAPIDTOOLS_REPO:-https://github.com/kks32/rapidtools}"
pip3 install --no-deps "git+${REPO}@${BRANCH}"
# Everything `import rapidtools` touches before the lazy model imports.
pip3 install huggingface_hub mapbox_vector_tile matplotlib networkx numpy pandas \
  pillow "pyshp<3.0.9" rasterio requests scikit-image scipy shapely tqdm
python3 -c "import rapidtools, rapidtools.data_sources.designsafe as d; print('rapidtools', rapidtools.__version__, 'from', d.__file__)"
