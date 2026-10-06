#!/bin/bash
set -euo pipefail
set -x

# rapidtools-ls6: run the rAPIdtools Eaton Fire notebook as a Tapis batch job.
#
#   inputs   orthomosaic.tiff (staged by Tapis from the Orthomosaic file input)
#            inputs/            optional: secrets.env, notebook, .sif image
#   output   output/            patch, GeoJSON results, executed notebook, HTML, manifest.json
#
# The container runs with the GPU (--nv). Hugging Face weights live in HF_CACHE
# (default $WORK/rapidtools/hf-cache) so SAM 3 is downloaded once per user.

export APP_ID="rapidtools-ls6"
export APP_VERSION="0.1.0"

ROOT_DIR="$(pwd)"
INPUT_DIR="${_tapisExecSystemInputDir:-$ROOT_DIR}"
OUT_DIR="${ROOT_DIR}/output"
mkdir -p "${OUT_DIR}"
JOB_START=$(date +%s)

write_summary() {
  rc=$?
  python3 - "$rc" <<'PYEOF' || echo "WARNING: could not write job-summary.json" >&2
import json, os, sys, time
summary = {
    "app_id": os.environ["APP_ID"],
    "app_version": os.environ["APP_VERSION"],
    "job_uuid": os.environ.get("_tapisJobUUID"),
    "hostname": os.uname().nodename,
    "container_image": os.environ.get("CONTAINER_IMAGE"),
    "notebook": os.environ.get("SUMMARY_NOTEBOOK"),
    "parameters": {k: os.environ.get(k) for k in [
        "TARGET_ASSET", "APPRX_ASSET_SIZE", "PATCH_SIZE_M", "PATCH_RESOLUTION_M", "PATCH_CENTER",
        "DETECT_IN_RECON_IMAGERY", "RUN_DAMAGE_ANALYSIS", "STEPS"]},
    "exit_code": int(sys.argv[1]),
    "total_seconds": int(time.time()) - int(os.environ["JOB_START"]),
}
with open("job-summary.json", "w") as f:
    json.dump(summary, f, indent=2)
PYEOF
}
export JOB_START
trap write_summary EXIT

# Lmod completion hooks are not nounset-clean.
set +u
module load tacc-apptainer || true
set -u

# ------------------------------------------------------------- inputs ----
ORTHO="${ORTHOMOSAIC:-${INPUT_DIR}/orthomosaic.tiff}"
if [[ "${ORTHO}" != /* ]]; then
  ORTHO="${INPUT_DIR}/${ORTHO}"
fi
if [[ ! -f "${ORTHO}" ]]; then
  echo "ERROR: orthomosaic not found at ${ORTHO}" >&2
  exit 66
fi
EXTRA="${INPUT_DIR}/inputs"

# Secrets never travel as job parameters. A secrets.env in the extra inputs
# directory may set GOOGLE_API_KEY and HF_TOKEN. They reach the container as
# APPTAINERENV_* variables, which --cleanenv keeps, so they never appear on
# the traced command line below.
set +x
GOOGLE_API_KEY="${GOOGLE_API_KEY:-}"
HF_TOKEN="${HF_TOKEN:-}"
if [[ -f "${EXTRA}/secrets.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${EXTRA}/secrets.env"
  set +a
fi
export APPTAINERENV_GOOGLE_API_KEY="${GOOGLE_API_KEY}"
export APPTAINERENV_HF_TOKEN="${HF_TOKEN}"
set -x

# ---------------------------------------------------------- container ----
IMAGE="${CONTAINER_IMAGE:?CONTAINER_IMAGE is required}"
if [[ "${IMAGE}" != docker://* && "${IMAGE}" != /* ]]; then
  IMAGE="${EXTRA}/${IMAGE}"
fi
export APPTAINER_CACHEDIR="${ROOT_DIR}/apptainer-cache"
mkdir -p "${APPTAINER_CACHEDIR}"

HF_HOME_HOST="${HF_CACHE:-${WORK:-$HOME}/rapidtools/hf-cache}"
mkdir -p "${HF_HOME_HOST}"

# Model weights come from the Hub on first use. If the node cannot reach it,
# run offline from the cache populated by an earlier job.
HF_OFFLINE=0
if ! curl -sfI --max-time 10 https://huggingface.co >/dev/null 2>&1; then
  echo "NOTE: huggingface.co unreachable from $(hostname); using cached weights only." >&2
  HF_OFFLINE=1
fi

# ----------------------------------------------------------- notebook ----
NB_IN_CONTAINER="/opt/rapidtools/examples/eaton_fire_designsafe.ipynb"
if [[ -n "${NOTEBOOK:-}" ]]; then
  if [[ ! -f "${EXTRA}/${NOTEBOOK}" ]]; then
    echo "ERROR: NOTEBOOK '${NOTEBOOK}' not found in extra inputs" >&2
    exit 66
  fi
  NB_IN_CONTAINER="/inputs/${NOTEBOOK}"
fi
export SUMMARY_NOTEBOOK="${NB_IN_CONTAINER}"

# 0 or empty keeps the source resolution (None in the notebook).
PATCH_RESOLUTION_M_YAML="${PATCH_RESOLUTION_M:-0.05}"
if [[ "${PATCH_RESOLUTION_M_YAML}" == "0" || -z "${PATCH_RESOLUTION_M_YAML}" ]]; then
  PATCH_RESOLUTION_M_YAML="null"
fi

PATCH_CENTER_ARG=()
if [[ -n "${PATCH_CENTER:-}" ]]; then
  PATCH_CENTER_ARG=(-y "PATCH_CENTER: [${PATCH_CENTER}]")
fi

BINDS="${ORTHO}:/inputs/orthomosaic.tiff,${OUT_DIR}:/output,${HF_HOME_HOST}:/hf"
if [[ -d "${EXTRA}" ]]; then
  BINDS="${BINDS},${EXTRA}:/inputs/extra"
fi

# ---------------------------------------------------------------- run ----
apptainer exec --nv --cleanenv \
  --env "HF_HOME=/hf,HF_HUB_OFFLINE=${HF_OFFLINE},PYTHONUNBUFFERED=1,MPLBACKEND=Agg" \
  --bind "${BINDS}" \
  --pwd /output \
  "${IMAGE}" \
  papermill "${NB_IN_CONTAINER}" /output/eaton_fire_designsafe.ipynb \
    -p RASTER_PATH /inputs/orthomosaic.tiff \
    -p OUTPUT_DIR /output \
    -p TARGET_ASSET "${TARGET_ASSET:-building}" \
    -p APPRX_ASSET_SIZE "${APPRX_ASSET_SIZE:-50}" \
    -p PATCH_SIZE_M "${PATCH_SIZE_M:-200}" \
    -y "PATCH_RESOLUTION_M: ${PATCH_RESOLUTION_M_YAML}" \
    -p DETECT_IN_RECON_IMAGERY "${DETECT_IN_RECON_IMAGERY:-True}" \
    -p RUN_DAMAGE_ANALYSIS "${RUN_DAMAGE_ANALYSIS:-False}" \
    -p STEPS "${STEPS:-all}" \
    "${PATCH_CENTER_ARG[@]}" \
    --log-output

apptainer exec --cleanenv --bind "${OUT_DIR}:/output" "${IMAGE}" \
  jupyter nbconvert --to html /output/eaton_fire_designsafe.ipynb || \
  echo "WARNING: HTML export failed; the executed notebook is still in output/" >&2

echo "Done. Outputs in ${OUT_DIR}"
