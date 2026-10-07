"""Tests for the DesignSafe published-data source. No network, no dapi."""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from rapidtools.core import BoundingBox
from rapidtools.data_sources import designsafe as ds_src


@pytest.fixture
def utm_raster(tmp_path: Path) -> Path:
    """A 400 x 300 px RGB raster at 0.5 m in UTM zone 11N (Altadena)."""
    path = tmp_path / 'ortho.tiff'
    transform = from_origin(394_000.0, 3_784_000.0, 0.5, 0.5)
    data = np.random.default_rng(0).integers(0, 255, (3, 300, 400), dtype='uint8')
    with rasterio.open(
        path,
        'w',
        driver='GTiff',
        height=300,
        width=400,
        count=3,
        dtype='uint8',
        crs='EPSG:32611',
        transform=transform,
    ) as dst:
        dst.write(data)
    return path


@pytest.fixture
def wgs84_raster(tmp_path: Path) -> Path:
    """A 200 x 200 px single-band raster in WGS84."""
    path = tmp_path / 'geo.tiff'
    transform = from_origin(-118.13, 34.19, 1e-5, 1e-5)
    data = np.arange(200 * 200, dtype='uint16').reshape(1, 200, 200)
    with rasterio.open(
        path,
        'w',
        driver='GTiff',
        height=200,
        width=200,
        count=1,
        dtype='uint16',
        crs='EPSG:4326',
        transform=transform,
    ) as dst:
        dst.write(data)
    return path


class FakeFiles:
    def __init__(self, entries, downloads):
        self._entries = entries
        self.downloads = downloads
        self.listed = []

    def list(self, uri, limit=100):
        self.listed.append((uri, limit))
        return self._entries

    def download(self, uri, local):
        self.downloads.append((uri, local))
        Path(local).write_bytes(b'tiff')


def fake_client(entries):
    downloads: list = []
    return SimpleNamespace(files=FakeFiles(entries, downloads)), downloads


def entry(name, size, type_='file'):
    return SimpleNamespace(name=name, size=size, type=type_)


def test_published_uri_builds_under_published_data():
    assert ds_src.published_uri('PRJ-5815') == (
        'tapis://designsafe.storage.published/published-data/PRJ-5815'
    )
    assert ds_src.published_uri('PRJ-5815', '/a/b/').endswith('/PRJ-5815/a/b')
    assert ds_src.EATON_FIRE_IMAGERY_URI.startswith(
        ds_src.published_uri('PRJ-5815', 'Mission--eaton-fire-burn-area--V3')
    )


def test_list_published_files_sorts_and_sizes():
    client, _ = fake_client(
        [entry('b.tiff', 2_000_000_000), entry('a.tiff', 500), entry('sub', 0, 'dir')]
    )
    rows = ds_src.list_published_files('tapis://sys/dir', client=client)
    assert [r['name'] for r in rows] == ['a.tiff', 'b.tiff', 'sub']
    assert rows[1]['size_gb'] == 2.0
    assert rows[0]['uri'] == 'tapis://sys/dir/a.tiff'
    assert rows[2]['type'] == 'dir'
    assert client.files.listed == [('tapis://sys/dir/', 500)]


def test_local_candidates_prefers_override(monkeypatch, tmp_path):
    uri = ds_src.published_uri('PRJ-5815', 'x/y.tiff')
    monkeypatch.setenv('RAPIDTOOLS_PUBLISHED_ROOT', str(tmp_path))
    candidates = ds_src.local_candidates(uri)
    assert candidates[0] == tmp_path / 'PRJ-5815/x/y.tiff'
    assert candidates[1] == ds_src.JUPYTER_PUBLISHED_MOUNT / 'PRJ-5815/x/y.tiff'
    assert candidates[2] == (
        ds_src.JUPYTER_PUBLISHED_MOUNT / 'published-data/PRJ-5815/x/y.tiff'
    )
    assert candidates[3:] == [
        root / 'published-data/PRJ-5815/x/y.tiff'
        for root in ds_src.TACC_PUBLISHED_ROOTS
    ]


def test_local_candidates_rejects_other_systems():
    with pytest.raises(ValueError):
        ds_src.local_candidates('tapis://designsafe.storage.default/u/f.tiff')
    with pytest.raises(ValueError):
        ds_src.local_candidates('/not/a/uri')


def test_resolve_returns_existing_local_path(tmp_path):
    local = tmp_path / 'patch.tiff'
    local.write_bytes(b'x')
    assert ds_src.resolve_published_file(str(local)) == local
    with pytest.raises(FileNotFoundError):
        ds_src.resolve_published_file(str(tmp_path / 'missing.tiff'))


def test_resolve_reads_mounted_copy_without_dapi(monkeypatch, tmp_path):
    uri = ds_src.published_uri('PRJ-5815', 'x/y.tiff')
    mounted = tmp_path / 'PRJ-5815/x/y.tiff'
    mounted.parent.mkdir(parents=True)
    mounted.write_bytes(b'tiff')
    monkeypatch.setenv('RAPIDTOOLS_PUBLISHED_ROOT', str(tmp_path))
    assert ds_src.resolve_published_file(uri, client=object()) == mounted


def test_resolve_downloads_small_files(monkeypatch, tmp_path):
    monkeypatch.delenv('RAPIDTOOLS_PUBLISHED_ROOT', raising=False)
    client, downloads = fake_client([entry('small.tiff', 4)])
    uri = ds_src.published_uri('PRJ-1', 'd/small.tiff')
    out = ds_src.resolve_published_file(uri, client=client, download_dir=tmp_path)
    assert out == tmp_path / 'small.tiff' and out.read_bytes() == b'tiff'
    assert downloads == [(uri, str(out))]
    # A second call reuses the download instead of fetching again.
    ds_src.resolve_published_file(uri, client=client, download_dir=tmp_path)
    assert len(downloads) == 1


def test_resolve_refuses_large_files(monkeypatch, tmp_path):
    monkeypatch.delenv('RAPIDTOOLS_PUBLISHED_ROOT', raising=False)
    client, downloads = fake_client([entry('big.tiff', 15 * 10**9)])
    uri = ds_src.published_uri('PRJ-1', 'd/big.tiff')
    with pytest.raises(RuntimeError, match='15.0 GB'):
        ds_src.resolve_published_file(uri, client=client, download_dir=tmp_path)
    assert downloads == []
    with pytest.raises(FileNotFoundError):
        ds_src.resolve_published_file(
            ds_src.published_uri('PRJ-1', 'd/other.tiff'), client=client
        )


def test_clip_center_default_is_raster_center(utm_raster, tmp_path):
    out = ds_src.clip_raster(utm_raster, tmp_path / 'out/patch.tiff', size_m=50)
    with rasterio.open(out) as dst, rasterio.open(utm_raster) as src:
        assert (dst.width, dst.height) == (100, 100)
        assert dst.count == 3 and dst.crs == src.crs
        assert dst.profile['tiled'] is True
        cx = (dst.bounds.left + dst.bounds.right) / 2
        assert cx == pytest.approx((src.bounds.left + src.bounds.right) / 2, abs=1)


def test_clip_region_in_wgs84_is_reprojected_and_clamped(utm_raster, tmp_path):
    whole = BoundingBox.from_raster(utm_raster)
    minx, miny, maxx, maxy = whole.bounds
    # A region reaching past the north-east corner is clamped to the raster.
    region = BoundingBox((minx + maxx) / 2, (miny + maxy) / 2, maxx + 1, maxy + 1)
    out = ds_src.clip_raster(utm_raster, tmp_path / 'patch.tiff', region=region)
    with rasterio.open(out) as dst, rasterio.open(utm_raster) as src:
        assert dst.width == pytest.approx(src.width / 2, abs=2)
        assert dst.height == pytest.approx(src.height / 2, abs=2)
        assert dst.bounds.right == pytest.approx(src.bounds.right)
        assert dst.bounds.top == pytest.approx(src.bounds.top)


def test_clip_wgs84_raster_uses_degree_conversion(wgs84_raster, tmp_path):
    out = ds_src.clip_raster(
        wgs84_raster, tmp_path / 'patch.tiff', center=(-118.129, 34.189), size_m=20
    )
    with rasterio.open(out) as dst:
        # 20 m is about 18 px north-south at 1e-5 degree pixels.
        assert 15 <= dst.height <= 21
        assert dst.width >= dst.height
        assert dst.read(1).dtype == np.uint16


def test_clip_resolution_reads_downsampled(utm_raster, tmp_path):
    out = ds_src.clip_raster(
        utm_raster, tmp_path / 'patch.tiff', size_m=50, resolution_m=2.0
    )
    with rasterio.open(out) as dst:
        # 50 m at 2 m per pixel, from a 0.5 m source.
        assert (dst.width, dst.height) == (25, 25)
        assert dst.res[0] == pytest.approx(2.0)
        assert dst.count == 3
    # A finer target than the source changes nothing.
    out = ds_src.clip_raster(
        utm_raster, tmp_path / 'full.tiff', size_m=50, resolution_m=0.1
    )
    with rasterio.open(out) as dst:
        assert (dst.width, dst.height) == (100, 100)


@pytest.fixture
def feet_raster(tmp_path: Path) -> Path:
    """A raster in NAD83 / California zone 5 (ftUS), EPSG:2229, 1 ft pixels."""
    path = tmp_path / 'feet.tiff'
    assert rasterio.crs.CRS.from_epsg(2229).linear_units_factor[1] == pytest.approx(
        0.3048, abs=1e-3
    )
    with rasterio.open(
        path,
        'w',
        driver='GTiff',
        height=600,
        width=600,
        count=1,
        dtype='uint8',
        crs='EPSG:2229',
        transform=from_origin(6_500_000.0, 1_900_000.0, 1.0, 1.0),
    ) as dst:
        dst.write(np.zeros((1, 600, 600), dtype='uint8'))
    return path


def test_clip_converts_meters_to_crs_feet(feet_raster, tmp_path):
    out = ds_src.clip_raster(feet_raster, tmp_path / 'p.tiff', size_m=50)
    with rasterio.open(out) as dst:
        # 50 m is 164 US survey feet, so 164 one-foot pixels.
        assert 163 <= dst.width <= 165 and 163 <= dst.height <= 165
    out = ds_src.clip_raster(
        feet_raster, tmp_path / 'q.tiff', size_m=50, resolution_m=1.0
    )
    with rasterio.open(out) as dst:
        assert 49 <= dst.width <= 51


def test_clip_outside_raster_raises(utm_raster, tmp_path):
    region = BoundingBox(-120.0, 35.0, -119.99, 35.01)
    with pytest.raises(ValueError):
        ds_src.clip_raster(utm_raster, tmp_path / 'patch.tiff', region=region)


def test_patch_from_published_uses_resolver(monkeypatch, utm_raster, tmp_path):
    monkeypatch.setattr(
        ds_src, 'resolve_published_file', lambda uri, client=None: utm_raster
    )
    out = ds_src.patch_from_published(
        'tapis://x/y.tiff', tmp_path / 'p.tiff', size_m=10
    )
    with rasterio.open(out) as dst:
        assert (dst.width, dst.height) == (20, 20)
