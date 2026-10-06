# rapidtools-ls6: a DesignSafe Tapis app for rAPIdtools

Runs `examples/eaton_fire_designsafe.ipynb` with papermill on a Lonestar6 GPU node. Tapis stages one orthomosaic from the DesignSafe published system, the wrapper clips a patch and runs the pipeline inside the rapidtools container, and the outputs (patch, GeoJSON, executed notebook, HTML, `manifest.json`, `job-summary.json`) archive to My Data.

This directory follows the layout of the WMA-Tapis-Templates `applications/` folder so it can move there unchanged. Until then it lives here and deploys as a user-owned app.

## Files

- `app.json` the Tapis app definition (ZIP runtime, `ls6`, `gpu-a100-dev`).
- `tapisjob_app.sh` the wrapper Tapis runs on the node.
- `Dockerfile` the container with rapidtools, papermill, and dapi.
- `secrets.env.example` template for API keys passed through the Extra inputs directory.

## Build the image

```
docker build -t ghcr.io/kks32/rapidtools:tapis-0.2.0 -f deploy/tapis/rapidtools-ls6/Dockerfile .
docker push ghcr.io/kks32/rapidtools:tapis-0.2.0
```

The default `CONTAINER_IMAGE` in `app.json` points at that tag. Pulling an 8 GB image at every job is slow, so pull it once on Lonestar6 and pass the `.sif` path as `CONTAINER_IMAGE`:

```
module load tacc-apptainer
apptainer pull $WORK/rapidtools/rapidtools-tapis-0.2.0.sif docker://ghcr.io/kks32/rapidtools:tapis-0.2.0
```

## Deploy as a user-owned app with dapi

```python
from dapi import DSClient

ds = DSClient()
ds.apps.deploy('deploy/tapis/rapidtools-ls6')
```

`deploy` zips the wrapper, uploads it to your My Data, rewrites `containerImage` to that zip, and registers `rapidtools-ls6` under your account. Rerunning with the same version updates it in place. Portal deployment under `cloud.data/.../applications/v3/rapidtools-ls6/` is a WMA step.

## Submit a job

```python
TILE = (
    'tapis://designsafe.storage.published/published-data/PRJ-5815/'
    'Mission--eaton-fire-burn-area--V3/data/'
    'Geoscience-collection--eaton-fire-processed-data-february-march-2025/'
    'data/Imagery/Eaton_Trinity38_RGBortho_20250214.tiff'
)
job = ds.jobs.generate(
    app_id='rapidtools-ls6',
    input_dir_uri=TILE,
    input_dir_param_name='Orthomosaic',
    allocation='<your allocation>',
    max_minutes=60,
    extra_env_vars=[
        {'key': 'TARGET_ASSET', 'value': 'building'},
        {'key': 'PATCH_SIZE_M', 'value': '400'},
        {'key': 'STEPS', 'value': 'all'},
    ],
)
submitted = ds.jobs.submit(job)
submitted.monitor()
```

Set `STEPS` to `patch` for a one-minute check of staging and the clip that needs no GPU and no model download. Pass `PATCH_CENTER` as `lon,lat` to move the patch. The tiles are 1.9 cm per pixel; `PATCH_RESOLUTION_M` (default 0.05) resamples the patch from the tile overviews so a 200 m patch is 4,000 pixels across instead of 10,000.

## Secrets

Gemini grading (`RUN_DAMAGE_ANALYSIS=True`) needs `GOOGLE_API_KEY`. SAM 3 weights on the Hugging Face Hub may need `HF_TOKEN`. Put them in a `secrets.env` file inside a directory you pass as **Extra inputs**; the wrapper sources it. Job parameters are visible in job records, so keys never go there.

## Model weights

SAM 3 downloads from the Hub on first use into `HF_CACHE` (default `$WORK/rapidtools/hf-cache`), so later jobs start without a download. If the compute node cannot reach huggingface.co the wrapper sets `HF_HUB_OFFLINE=1` and uses the cache. If the cache is empty in that case, prefetch once from a login node:

```
module load tacc-apptainer
apptainer exec --bind $WORK/rapidtools/hf-cache:/hf --env HF_HOME=/hf,HF_TOKEN=<token> \
  $WORK/rapidtools/rapidtools-tapis-0.2.0.sif python -c "from rapidtools.models import SAM3Inference; SAM3Inference(device='cpu')"
```

TODO: confirm outbound internet from Lonestar6 GPU nodes before relying on in-job downloads and on the Bing and Gemini steps.
