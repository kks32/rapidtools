# rapidtools image for the Tapis app. Build from the repository root:
#   docker build -t ghcr.io/kks32/rapidtools:tapis-0.2.0 -f deploy/tapis/rapidtools-ls6/Dockerfile .
#   docker push ghcr.io/kks32/rapidtools:tapis-0.2.0
# Then on Lonestar6, once, to skip the pull at every job:
#   module load tacc-apptainer
#   apptainer pull $WORK/rapidtools/rapidtools-tapis-0.2.0.sif docker://ghcr.io/kks32/rapidtools:tapis-0.2.0
ARG BASE_IMAGE=pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime
FROM ${BASE_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/hf \
    MPLBACKEND=Agg

RUN apt-get update && apt-get install -y --no-install-recommends git curl && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY . /src
RUN pip install ".[designsafe]" papermill ipykernel nbconvert matplotlib \
    && python -m ipykernel install --name python3 --display-name "Python 3"

# Bind targets for the Tapis wrapper: the staged tile, the extra inputs, outputs, and the model cache.
RUN mkdir -p /opt/rapidtools/examples /inputs/extra /output /hf \
    && touch /inputs/orthomosaic.tiff \
    && cp /src/examples/eaton_fire_designsafe.ipynb /opt/rapidtools/examples/

WORKDIR /output
CMD ["papermill", "--help"]
