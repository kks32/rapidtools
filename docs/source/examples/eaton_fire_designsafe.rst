Eaton Fire orthomosaics from DesignSafe
=======================================

The RAPID Facility published its Eaton Fire orthomosaics on DesignSafe as
`PRJ-5815 <https://doi.org/10.17603/ds2-wsvx-7s97>`_. Each tile is 4.7 to
20 GB at 1.9 cm per pixel; the smallest covers 1.1 by 0.8 km. This example
reads a tile in place, clips a patch of a few hundred meters at a coarser
resolution, and runs the asset pipeline on the patch.

Source: ``examples/eaton_fire_designsafe.ipynb``. The same steps, with the
Dropbox sample replaced by the published tile, are in
``examples/asset_analysis_example_generalized.ipynb``.

Install the DesignSafe extra, which adds `dapi <https://designsafe-ci.github.io/dapi/>`_::

   pip install "rapidtools[designsafe]"

.. code-block:: python

   from rapidtools import BoundingBox, SAM3OrthoFeatureExtractor
   from rapidtools.data_sources.designsafe import (
       EATON_FIRE_IMAGERY_URI,
       EATON_FIRE_SMALLEST_TILE,
       list_published_files,
       patch_from_published,
   )

   # 1. What is in the publication (needs DesignSafe credentials).
   for tile in list_published_files(EATON_FIRE_IMAGERY_URI):
       print(tile['name'], tile['size_gb'], 'GB')

   # 2. A 200 m patch around the tile center, resampled from 1.9 cm to 5 cm
   #    pixels through the tile's overviews. On DesignSafe JupyterHub the
   #    tile is read from ~/NHERI-Published; nothing is downloaded.
   raster_path = patch_from_published(
       f'{EATON_FIRE_IMAGERY_URI}/{EATON_FIRE_SMALLEST_TILE}',
       'data/eaton_trinity38_patch.tiff',
       size_m=200,
       resolution_m=0.05,
   )
   region = BoundingBox.from_raster(raster_path)

   # 3. The rest of the pipeline is unchanged.
   buildings = SAM3OrthoFeatureExtractor(prompt='building', patch_size=50)(raster_path)
   buildings.to_geojson('eaton_trinity38_buildings.geojson')

Where the tile is read from
---------------------------

:func:`~rapidtools.data_sources.designsafe.resolve_published_file` checks, in
order, the directory named by ``RAPIDTOOLS_PUBLISHED_ROOT``, the JupyterHub
mount ``/home/jupyter/NHERI-Published``, and only then a dapi download. The
download refuses files above ``max_download_gb`` (2 GB by default), so an
Eaton tile is never pulled to a laptop by accident.

Running it as a Tapis job
-------------------------

``deploy/tapis/rapidtools-ls6`` packages the notebook as a DesignSafe app:
Tapis stages the tile to a Lonestar6 GPU node, the wrapper runs the notebook
with papermill inside the rapidtools container, and the outputs archive to
My Data with a ``manifest.json`` that pins the tile, the patch window, the
parameters, and the software version. The README in that directory covers
the image build, deployment with ``dapi``, and job submission.
