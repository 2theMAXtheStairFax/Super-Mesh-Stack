"""Offline map tiles for the Mesh Stack node: a bbox of a Protomaps daily build (dark street map,
vector) and USGS National Map topo (raster), each into an MBTiles file.

  python fetch_tiles.py dark  <out.mbtiles> <build url> [lon0,lat0,lon1,lat1]
  python fetch_tiles.py topo  <out.mbtiles> [lon0,lat0,lon1,lat1]

Topo resumes: tiles already in the file are skipped, so a bigger box only fetches the new ones.

PMTiles v3 is read directly with HTTP range requests (no pmtiles CLI).
"""
import concurrent.futures as cf
import gzip
import io
import json
import math
import sqlite3
import struct
import sys
import time
import urllib.request

BBOX = (-98.80, 29.20, -96.50, 33.25)          # lon0, lat0, lon1, lat1: San Antonio to Dallas-Fort Worth
DARK_ZOOMS = range(0, 16)
TOPO_ZOOMS = range(8, 15)
USGS = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}"
UA = {"User-Agent": "mesh-stack-offline-tiles/1.0 (personal offline cache)"}


def tiles_in_bbox(z):
    n = 1 << z
    def tx(lon):
        return min(n - 1, max(0, int((lon + 180) / 360 * n)))
    def ty(lat):
        r = math.radians(lat)
        return min(n - 1, max(0, int((1 - math.asinh(math.tan(r)) / math.pi) / 2 * n)))
    lon0, lat0, lon1, lat1 = BBOX
    for x in range(tx(lon0), tx(lon1) + 1):
        for y in range(ty(lat1), ty(lat0) + 1):
            yield x, y


def mbtiles(path, name, fmt, zooms, extra=None):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS metadata (name TEXT, value TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS tiles (zoom_level INTEGER, tile_column INTEGER, "
               "tile_row INTEGER, tile_data BLOB)")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS tile_index ON tiles (zoom_level, tile_column, tile_row)")
    db.execute("DELETE FROM metadata")
    meta = {"name": name, "format": fmt, "minzoom": min(zooms), "maxzoom": max(zooms),
            "bounds": ",".join(map(str, BBOX)), **(extra or {})}
    db.executemany("INSERT INTO metadata VALUES (?,?)", [(k, str(v)) for k, v in meta.items()])
    db.commit()
    return db


def put(db, z, x, y, data):
    db.execute("INSERT OR REPLACE INTO tiles VALUES (?,?,?,?)", (z, x, (1 << z) - 1 - y, data))


# ── PMTiles v3 ─────────────────────────────────────────────────────────────
def zxy_to_tileid(z, x, y):
    acc = ((1 << (2 * z)) - 1) // 3
    n = 1 << z
    d = 0
    s = n >> 1
    while s > 0:
        rx = 1 if x & s else 0
        ry = 1 if y & s else 0
        d += s * s * ((3 * rx) ^ ry)
        if ry == 0:
            if rx == 1:
                x, y = n - 1 - x, n - 1 - y
            x, y = y, x
        s >>= 1
    return acc + d


def get_range(url, off, length, tries=5):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={**UA, "Range": f"bytes={off}-{off + length - 1}"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            if len(data) == length:
                return data
        except Exception as e:
            err = e
        time.sleep(2 ** i)
    raise RuntimeError(f"range {off}+{length} failed: {err if 'err' in dir() else 'short read'}")


def varint(buf):
    shift = val = 0
    while True:
        b = buf.read(1)[0]
        val |= (b & 0x7F) << shift
        if not b & 0x80:
            return val
        shift += 7


def decompress(data, kind):
    if kind == 1:
        return data
    if kind == 2:
        return gzip.decompress(data)
    raise RuntimeError(f"compression {kind} not supported")


def parse_dir(data):
    b = io.BytesIO(data)
    n = varint(b)
    ids, last = [], 0
    for _ in range(n):
        last += varint(b)
        ids.append(last)
    runs = [varint(b) for _ in range(n)]
    lens = [varint(b) for _ in range(n)]
    offs = []
    for i in range(n):
        v = varint(b)
        offs.append(offs[-1] + lens[i - 1] if v == 0 and i > 0 else v - 1)
    return list(zip(ids, runs, lens, offs))


def dark(out, url):
    h = get_range(url, 0, 127)
    assert h[:7] == b"PMTiles" and h[7] == 3, "not a PMTiles v3 archive"
    (root_off, root_len, meta_off, meta_len, leaf_off, leaf_len, data_off, data_len,
     _a, _e, _c) = struct.unpack("<11Q", h[8:96])
    clustered, icomp, tcomp, ttype = h[96], h[97], h[98], h[99]
    assert ttype == 1, "expected MVT tiles"
    meta = json.loads(decompress(get_range(url, meta_off, meta_len), icomp))
    root = parse_dir(decompress(get_range(url, root_off, root_len), icomp))

    want = {}                                 # tile id -> (z, x, y)
    for z in DARK_ZOOMS:
        for x, y in tiles_in_bbox(z):
            want[zxy_to_tileid(z, x, y)] = (z, x, y)
    print(f"dark: {len(want)} tiles wanted", flush=True)

    leaf_cache = {}
    def find(tid, entries):
        lo, hi = 0, len(entries) - 1
        while lo <= hi:                       # last entry with id <= tid
            mid = (lo + hi) // 2
            if entries[mid][0] <= tid:
                lo = mid + 1
            else:
                hi = mid - 1
        if hi < 0:
            return None
        eid, run, ln, off = entries[hi]
        if run == 0:                          # leaf directory
            key = (off, ln)
            if key not in leaf_cache:
                leaf_cache[key] = parse_dir(decompress(get_range(url, leaf_off + off, ln), icomp))
            return find(tid, leaf_cache[key])
        return (off, ln) if tid < eid + run else None

    by_data = {}                              # (off, len) -> [(z, x, y)]
    for tid, zxy in sorted(want.items()):
        hit = find(tid, root)
        if hit:
            by_data.setdefault(hit, []).append(zxy)
    print(f"dark: {len(by_data)} distinct tiles, {len(leaf_cache)} leaf dirs", flush=True)

    spans, cur = [], None                     # merge nearby reads into bigger ranges
    for off, ln in sorted(by_data):
        if cur and off - (cur[0] + cur[1]) < 256 * 1024 and cur[1] < 8 * 1024 * 1024:
            cur[1] = max(cur[1], off + ln - cur[0])
            cur[2].append((off, ln))
        else:
            cur = [off, ln, [(off, ln)]]
            spans.append(cur)
    print(f"dark: {len(spans)} range requests, "
          f"{sum(s[1] for s in spans) / 1e6:.0f} MB to read", flush=True)

    db = mbtiles(out, "Protomaps basemap (dark)", "pbf", DARK_ZOOMS,
                 {"json": json.dumps({"vector_layers": meta.get("vector_layers", [])}),
                  "attribution": meta.get("attribution", "Protomaps, OpenStreetMap"),
                  "compression": "gzip" if tcomp == 2 else "none"})
    done = 0
    with cf.ThreadPoolExecutor(6) as ex:
        futs = {ex.submit(get_range, url, data_off + s[0], s[1]): s for s in spans}
        for f in cf.as_completed(futs):
            start, _ln, parts = futs[f]
            blob = f.result()
            for off, ln in parts:
                tile = blob[off - start:off - start + ln]
                for z, x, y in by_data[(off, ln)]:
                    put(db, z, x, y, tile)
            done += 1
            if done % 50 == 0:
                db.commit()
                print(f"dark: {done}/{len(spans)}", flush=True)
    db.commit()
    db.execute("VACUUM")
    db.close()
    print("dark: done", flush=True)


def topo(out):
    db = mbtiles(out, "USGS Topo", "jpg", TOPO_ZOOMS,
                 {"attribution": "USGS The National Map"})
    have = {(z, x, (1 << z) - 1 - r) for z, x, r in
            db.execute("SELECT zoom_level, tile_column, tile_row FROM tiles")}
    jobs = [(z, x, y) for z in TOPO_ZOOMS for x, y in tiles_in_bbox(z) if (z, x, y) not in have]
    print(f"topo: {len(jobs)} tiles to fetch ({len(have)} already)", flush=True)

    def fetch(job):
        z, x, y = job
        for i in range(5):
            try:
                req = urllib.request.Request(USGS.format(z=z, x=x, y=y), headers=UA)
                with urllib.request.urlopen(req, timeout=60) as r:
                    return job, r.read()
            except Exception:
                time.sleep(2 ** i)
        return job, None

    done = failed = 0
    with cf.ThreadPoolExecutor(4) as ex:            # gentle on a public server
        for (z, x, y), data in ex.map(fetch, jobs):
            if data:
                put(db, z, x, y, data)
            else:
                failed += 1
            done += 1
            if done % 200 == 0:
                db.commit()
                print(f"topo: {done}/{len(jobs)} ({failed} failed)", flush=True)
    db.commit()
    db.close()
    print(f"topo: done ({failed} failed)", flush=True)


if __name__ == "__main__":
    box = sys.argv[4 if sys.argv[1] == "dark" else 3:][:1]
    if box:
        BBOX = tuple(float(v) for v in box[0].split(","))
    if sys.argv[1] == "dark":
        dark(sys.argv[2], sys.argv[3])
    else:
        topo(sys.argv[2])
