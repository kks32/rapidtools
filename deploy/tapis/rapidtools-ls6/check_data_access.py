"""Report where this host can read a DesignSafe published tile from, and clip it.

Run it as a cheap CPU job (for example through the DesignSafe python-s3 app)
before spending GPU time. It answers two questions for the node it runs on:
is the published system mounted (JupyterHub, corral on TACC) or must Tapis
stage the file, and how long does the 200 m, 5 cm clip take there. It never
downloads a tile. Results go to check_data_access.json next to the script.

Usage::

    python check_data_access.py [TILE_URI_OR_PATH] [--size-m 200] [--resolution-m 0.05]

The default tile is the smallest Eaton Fire orthomosaic in PRJ-5815.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

from rapidtools.data_sources.designsafe import (
    EATON_FIRE_IMAGERY_URI,
    EATON_FIRE_SMALLEST_TILE,
    JUPYTER_PUBLISHED_MOUNT,
    TACC_PUBLISHED_ROOTS,
    clip_raster,
    local_candidates,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument(
        'tile',
        nargs='?',
        default=f'{EATON_FIRE_IMAGERY_URI}/{EATON_FIRE_SMALLEST_TILE}',
        help='Tapis URI or local path of the orthomosaic.',
    )
    parser.add_argument('--size-m', type=float, default=200.0)
    parser.add_argument('--resolution-m', type=float, default=0.05)
    parser.add_argument('--output', default='check_data_access.json')
    args = parser.parse_args()

    report: dict = {
        'hostname': platform.node(),
        'python': platform.python_version(),
        'tile': args.tile,
        'staged_input': os.environ.get('inputDirectory'),
        'job_uuid': os.environ.get('_tapisJobUUID'),
        'mounts': {},
        'candidates': [],
        'source': None,
        'clip': None,
    }
    for root in (JUPYTER_PUBLISHED_MOUNT, *TACC_PUBLISHED_ROOTS):
        report['mounts'][str(root)] = root.is_dir()
    if shutil.which('df'):
        out = subprocess.run(['df', '-h'], capture_output=True, text=True).stdout
        report['df_corral'] = [line for line in out.splitlines() if 'corral' in line]

    source: Path | None = None
    if args.tile.startswith('tapis://'):
        for candidate in local_candidates(args.tile):
            exists = candidate.is_file()
            report['candidates'].append({'path': str(candidate), 'exists': exists})
            if exists and source is None:
                source = candidate
    else:
        source = Path(args.tile) if Path(args.tile).is_file() else None

    # A Tapis job may have staged the tile into the input directory instead.
    staged = os.environ.get('inputDirectory')
    if source is None and staged:
        for candidate in Path(staged).glob('*.tif*'):
            source = candidate
            report['candidates'].append({'path': str(candidate), 'exists': True})
            break

    if source is None:
        report['source'] = None
        report['conclusion'] = (
            'The published system is not readable on this host. '
            'Stage the tile as a Tapis file input or run on JupyterHub.'
        )
    else:
        report['source'] = str(source)
        report['source_bytes'] = source.stat().st_size
        t0 = time.time()
        patch = clip_raster(
            source,
            Path('output') / f'{source.stem}_patch.tiff',
            size_m=args.size_m,
            resolution_m=args.resolution_m,
        )
        report['clip'] = {
            'path': str(patch),
            'bytes': patch.stat().st_size,
            'seconds': round(time.time() - t0, 1),
            'size_m': args.size_m,
            'resolution_m': args.resolution_m,
        }
        report['conclusion'] = f'Read {source} in place and clipped a patch.'

    Path(args.output).write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
