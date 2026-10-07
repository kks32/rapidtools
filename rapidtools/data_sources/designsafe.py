# Copyright (c) 2025 The University of Washington
#
# This file is part of rapidtools.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
# this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
# this list of conditions and the following disclaimer in the documentation
# and/or other materials provided with the distribution.
#
# 3. Neither the name of the copyright holder nor the names of its contributors
# may be used to endorse or promote products derived from this software without
# specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#
# You should have received a copy of the BSD 3-Clause License along with
# rapidtools. If not, see <http://www.opensource.org/licenses/>.
#
# Contributors:
# Krishna Kumar
#
# Last updated:
# 10-06-2026

"""
Access to DesignSafe published datasets through ``dapi``.

DesignSafe publishes RAPID orthomosaics on the Tapis storage system
``designsafe.storage.published``. The Eaton Fire tiles in PRJ-5815 are 4 to
20 GB each, so this module never pulls a whole tile by default. It resolves a
Tapis URI to a path that can be read in place (the JupyterHub mount or a Tapis
job's staged input), falls back to a size-capped download elsewhere, and clips
a window out of the orthomosaic so the rest of a pipeline works on a small
patch.

``dapi`` is imported lazily; install it with ``pip install rapidtools[designsafe]``.

Example:
    >>> from rapidtools.data_sources.designsafe import (
    ...     EATON_FIRE_IMAGERY_URI,
    ...     clip_raster,
    ...     published_uri,
    ... )
    >>> published_uri('PRJ-5815', 'Mission--eaton-fire-burn-area--V3')
    'tapis://designsafe.storage.published/published-data/PRJ-5815/Mission--eaton-fire-burn-area--V3'
    >>> EATON_FIRE_IMAGERY_URI.rsplit('/', 1)[-1]
    'Imagery'
"""

from __future__ import annotations

import logging
import math
import os
from pathlib import Path
from typing import Any

import rasterio
from rasterio.enums import Resampling
from rasterio.errors import WindowError
from rasterio.transform import Affine
from rasterio.warp import transform as warp_transform
from rasterio.windows import Window, from_bounds

from ..core import BoundingBox

logger = logging.getLogger(__name__)

PUBLISHED_SYSTEM = 'designsafe.storage.published'
"""Tapis system id that holds every DesignSafe publication."""

PUBLISHED_ROOT = 'published-data'
"""Directory under the published system that holds the ``PRJ-*`` folders."""

JUPYTER_PUBLISHED_MOUNT = Path('/home/jupyter/NHERI-Published')
"""Where DesignSafe JupyterHub mounts the published system."""

TACC_PUBLISHED_ROOTS = (
    Path('/corral/projects/NHERI/published'),
    Path('/corral-repl/projects/NHERI/published'),
)
"""Corral roots of the published system on TACC hosts that mount corral."""

EATON_FIRE_PROJECT = 'PRJ-5815'
EATON_FIRE_IMAGERY_PATH = (
    'Mission--eaton-fire-burn-area--V3/data/'
    'Geoscience-collection--eaton-fire-processed-data-february-march-2025/'
    'data/Imagery'
)
EATON_FIRE_IMAGERY_URI = (
    f'tapis://{PUBLISHED_SYSTEM}/{PUBLISHED_ROOT}/{EATON_FIRE_PROJECT}/'
    f'{EATON_FIRE_IMAGERY_PATH}'
)
"""RAPID RGB orthomosaics of the 2025 Eaton Fire (DOI 10.17603/ds2-wsvx-7s97)."""

EATON_FIRE_SMALLEST_TILE = 'Eaton_Trinity38_RGBortho_20250214.tiff'
"""The 4.7 GB tile: 58463 x 41212 px at 1.9 cm, 1.1 x 0.8 km. Others are 10 to 20 GB."""

EATON_FIRE_PIXEL_M = 0.019
"""Ground sampling distance of the Eaton tiles in meters."""


def published_uri(project_id: str, path: str = '') -> str:
    """
    Build the Tapis URI of a path inside a DesignSafe publication.

    Args:
        project_id: DesignSafe project id, for example ``'PRJ-5815'``.
        path: Path inside the publication. Empty for the publication root.

    Returns:
        str: ``tapis://designsafe.storage.published/published-data/<id>/<path>``.

    Example:
        >>> published_uri('PRJ-5815')
        'tapis://designsafe.storage.published/published-data/PRJ-5815'
    """
    base = f'tapis://{PUBLISHED_SYSTEM}/{PUBLISHED_ROOT}/{project_id}'
    path = path.strip('/')
    return f'{base}/{path}' if path else base


def _split_uri(uri: str) -> tuple[str, str]:
    """Return ``(system_id, path)`` for a ``tapis://`` URI."""
    if not uri.startswith('tapis://'):
        raise ValueError(f'Not a Tapis URI: {uri!r}')
    rest = uri[len('tapis://') :]
    system_id, _, path = rest.partition('/')
    if not system_id:
        raise ValueError(f'Tapis URI has no system id: {uri!r}')
    return system_id, path.strip('/')


def _client(client: Any | None) -> Any:
    """Return *client* or a new authenticated ``dapi.DSClient``."""
    if client is not None:
        return client
    try:
        from dapi import DSClient
    except ImportError as exc:  # pragma: no cover - exercised only without dapi
        raise ImportError(
            'dapi is required to reach DesignSafe. '
            'Install it with: pip install "rapidtools[designsafe]"'
        ) from exc
    return DSClient()


def list_published_files(
    uri: str, client: Any | None = None, limit: int = 500
) -> list[dict[str, Any]]:
    """
    List the files in a folder on the DesignSafe published system.

    Args:
        uri: Tapis URI of the folder, for example :data:`EATON_FIRE_IMAGERY_URI`.
        client: An authenticated ``dapi.DSClient``. A new one is created when
            omitted, which reads credentials from the environment or ``.env``.
        limit: Maximum number of entries to return.

    Returns:
        list[dict]: One dict per entry with ``name``, ``type`` (``'file'`` or
        ``'dir'``), ``size`` in bytes, ``size_gb`` and the entry's ``uri``,
        sorted by name.

    Example:
        >>> tiles = list_published_files(EATON_FIRE_IMAGERY_URI)  # doctest: +SKIP
        >>> tiles[0]['name']  # doctest: +SKIP
        'Eaton_Trinity26_RGBortho_20250211.tiff'
    """
    ds = _client(client)
    entries = ds.files.list(uri.rstrip('/') + '/', limit=limit)
    rows = []
    for entry in entries:
        size = int(getattr(entry, 'size', 0) or 0)
        rows.append(
            {
                'name': entry.name,
                'type': entry.type,
                'size': size,
                'size_gb': round(size / 1e9, 2),
                'uri': f'{uri.rstrip("/")}/{entry.name}',
            }
        )
    return sorted(rows, key=lambda r: r['name'])


def local_candidates(uri: str) -> list[Path]:
    """
    Paths where a published file may already be readable without a download.

    Checked in order: the directory named by ``RAPIDTOOLS_PUBLISHED_ROOT``
    (set it when the published system is mounted somewhere else), the
    DesignSafe JupyterHub mount with and without the ``published-data``
    prefix, then the corral roots on TACC hosts.

    Args:
        uri: Tapis URI on the published system.

    Returns:
        list[Path]: Candidate paths. None of them is guaranteed to exist.

    Example:
        >>> local_candidates(published_uri('PRJ-5815', 'a/b.tiff'))[-1]
        PosixPath('/corral-repl/projects/NHERI/published/published-data/PRJ-5815/a/b.tiff')
    """
    system_id, path = _split_uri(uri)
    if system_id != PUBLISHED_SYSTEM:
        raise ValueError(f'{uri!r} is not on {PUBLISHED_SYSTEM}')
    without_root = path.removeprefix(f'{PUBLISHED_ROOT}/')
    candidates: list[Path] = []
    override = os.environ.get('RAPIDTOOLS_PUBLISHED_ROOT')
    if override:
        candidates.append(Path(override) / without_root)
    candidates.append(JUPYTER_PUBLISHED_MOUNT / without_root)
    candidates.append(JUPYTER_PUBLISHED_MOUNT / path)
    candidates.extend(root / path for root in TACC_PUBLISHED_ROOTS)
    return candidates


def resolve_published_file(
    uri: str,
    client: Any | None = None,
    download_dir: str | Path = 'data',
    max_download_gb: float = 2.0,
) -> Path:
    """
    Return a local path for a file on the DesignSafe published system.

    A local path or an existing file is returned as is. A ``tapis://`` URI is
    resolved against :func:`local_candidates` first, so on DesignSafe
    JupyterHub nothing is copied. Elsewhere the file is downloaded with
    ``dapi`` into *download_dir*, but only if it is smaller than
    *max_download_gb*; the Eaton tiles exceed that cap, which is the point.

    Args:
        uri: Tapis URI or local path of the raster.
        client: An authenticated ``dapi.DSClient``; created when omitted.
        download_dir: Where downloads land. Created if missing.
        max_download_gb: Refuse to download files larger than this.

    Returns:
        Path: A readable file.

    Raises:
        FileNotFoundError: If the URI cannot be listed on the published system.
        RuntimeError: If the file is larger than *max_download_gb* and no
            local copy exists.

    Example:
        >>> resolve_published_file('data/eaton_patch.tiff')  # doctest: +SKIP
        PosixPath('data/eaton_patch.tiff')
    """
    if not uri.startswith('tapis://'):
        local = Path(uri)
        if local.exists():
            return local
        raise FileNotFoundError(f'{local} does not exist')

    for candidate in local_candidates(uri):
        if candidate.is_file():
            logger.info(f'Reading {candidate} in place.')
            return candidate

    ds = _client(client)
    folder, _, name = uri.rpartition('/')
    entries = {e['name']: e for e in list_published_files(folder, client=ds)}
    if name not in entries:
        raise FileNotFoundError(f'{name} is not in {folder}')
    size_gb = entries[name]['size_gb']
    target = Path(download_dir) / name
    if target.is_file() and target.stat().st_size == entries[name]['size']:
        logger.info(f'Reusing earlier download {target}.')
        return target
    if size_gb > max_download_gb:
        raise RuntimeError(
            f'{name} is {size_gb} GB, above the {max_download_gb} GB download cap. '
            'Run this on DesignSafe JupyterHub, where published data is mounted, '
            'or as a Tapis job with the tile as a file input, or raise '
            'max_download_gb.'
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f'Downloading {name} ({size_gb} GB) to {target}.')
    ds.files.download(uri, str(target))
    return target


def _meters_to_crs_units(size_m: float, crs: Any, lat: float) -> tuple[float, float]:
    """Return the (dx, dy) extent of *size_m* meters in the raster CRS."""
    if crs is not None and crs.is_projected:
        factor = crs.linear_units_factor[1] if crs.linear_units_factor else 1.0
        extent = size_m / factor
        return extent, extent
    deg_lat = size_m / 111_320.0
    deg_lon = size_m / (111_320.0 * max(math.cos(math.radians(lat)), 1e-6))
    return deg_lon, deg_lat


def clip_raster(
    source: str | Path,
    output_path: str | Path,
    region: BoundingBox | None = None,
    center: tuple[float, float] | None = None,
    size_m: float = 200.0,
    resolution_m: float | None = None,
    compress: str = 'lzw',
) -> Path:
    """
    Write a patch of a large orthomosaic to a new GeoTIFF.

    Only the pixels inside the window are read. The window is either *region*
    (a WGS84 :class:`~rapidtools.core.BoundingBox`) or a square *size_m*
    meters wide around *center* (WGS84 ``(lon, lat)``), which defaults to the
    raster's center. The window is clamped to the raster.

    The Eaton tiles have 1.9 cm pixels, so a 200 m window is a 10,000 pixel
    square at full resolution. Pass *resolution_m* to resample the patch to
    a coarser ground sampling distance; rasterio then reads from the tile's
    overviews, which turns minutes into seconds and keeps the patch small.
    Building detection works at 5 cm.

    Args:
        source: Path of the orthomosaic.
        output_path: Path of the patch to write. Parent directories are created.
        region: WGS84 bounding box of the patch. Takes precedence over
            *center* and *size_m*.
        center: WGS84 ``(lon, lat)`` of the patch center.
        size_m: Width and height of the patch in meters when *region* is None.
        resolution_m: Target pixel size in meters. None keeps the source
            resolution; a value finer than the source is ignored.
        compress: GeoTIFF compression. ``'lzw'``, ``'deflate'`` or ``'none'``.

    Returns:
        Path: The written patch.

    Raises:
        ValueError: If the window does not overlap the raster.

    Example:
        >>> patch = clip_raster(
        ...     'Eaton_Trinity38_RGBortho_20250214.tiff',
        ...     'output/eaton_trinity38_patch.tiff',
        ...     center=(-118.1412, 34.1876),
        ...     size_m=200,
        ...     resolution_m=0.05,
        ... )  # doctest: +SKIP
    """
    output = Path(output_path)
    with rasterio.open(source) as src:
        crs = src.crs
        if region is not None:
            minx, miny, maxx, maxy = region.bounds
            lat_deg = (miny + maxy) / 2
            if crs is not None and crs.to_string() != 'EPSG:4326':
                xs, ys = warp_transform('EPSG:4326', crs, [minx, maxx], [miny, maxy])
                minx, maxx = min(xs), max(xs)
                miny, maxy = min(ys), max(ys)
        else:
            if center is None:
                cx = (src.bounds.left + src.bounds.right) / 2
                cy = (src.bounds.bottom + src.bounds.top) / 2
                if crs is not None and crs.to_string() != 'EPSG:4326':
                    lon, lat = warp_transform(crs, 'EPSG:4326', [cx], [cy])
                    lat_deg = lat[0]
                else:
                    lat_deg = cy
            else:
                lon_c, lat_deg = center
                if crs is not None and crs.to_string() != 'EPSG:4326':
                    xs, ys = warp_transform('EPSG:4326', crs, [lon_c], [lat_deg])
                    cx, cy = xs[0], ys[0]
                else:
                    cx, cy = lon_c, lat_deg
            dx, dy = _meters_to_crs_units(size_m, crs, lat_deg)
            minx, maxx = cx - dx / 2, cx + dx / 2
            miny, maxy = cy - dy / 2, cy + dy / 2

        window = from_bounds(minx, miny, maxx, maxy, transform=src.transform)
        window = window.round_offsets().round_lengths()
        full = Window(0, 0, src.width, src.height)
        try:
            window = window.intersection(full)
        except WindowError as exc:
            raise ValueError(
                'The requested window does not overlap the raster.'
            ) from exc

        out_w, out_h = int(window.width), int(window.height)
        transform = src.window_transform(window)
        if resolution_m is not None:
            unit_m = crs.linear_units_factor[1] if crs and crs.is_projected else 1.0
            if crs is None or not crs.is_projected:
                unit_m = 111_320.0 * max(math.cos(math.radians(lat_deg)), 1e-6)
            scale = resolution_m / (abs(src.res[0]) * unit_m)
            if scale > 1.0:
                out_w = max(1, round(window.width / scale))
                out_h = max(1, round(window.height / scale))
                transform = transform * Affine.scale(
                    window.width / out_w, window.height / out_h
                )
        data = src.read(
            window=window,
            out_shape=(src.count, out_h, out_w),
            resampling=Resampling.average,
        )
        profile = src.profile.copy()
        profile.update(
            driver='GTiff',
            height=out_h,
            width=out_w,
            transform=transform,
            tiled=True,
            blockxsize=256,
            blockysize=256,
            compress=None if compress == 'none' else compress,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(output, 'w', **profile) as dst:
            dst.write(data)
            dst.colorinterp = src.colorinterp
    logger.info(f'Wrote {output} ({out_w} x {out_h} px) from {Path(source).name}.')
    return output


def patch_from_published(
    uri: str,
    output_path: str | Path,
    client: Any | None = None,
    **clip_kwargs: Any,
) -> Path:
    """
    Resolve a published orthomosaic and clip a patch from it in one call.

    Args:
        uri: Tapis URI or local path of the orthomosaic.
        output_path: Path of the patch to write.
        client: An authenticated ``dapi.DSClient``; created when needed.
        **clip_kwargs: Passed to :func:`clip_raster` (``region``, ``center``,
            ``size_m``, ``resolution_m``, ``compress``).

    Returns:
        Path: The written patch.

    Example:
        >>> patch = patch_from_published(
        ...     f'{EATON_FIRE_IMAGERY_URI}/{EATON_FIRE_SMALLEST_TILE}',
        ...     'output/eaton_trinity38_patch.tiff',
        ...     size_m=200,
        ...     resolution_m=0.05,
        ... )  # doctest: +SKIP
    """
    source = resolve_published_file(uri, client=client)
    return clip_raster(source, output_path, **clip_kwargs)
