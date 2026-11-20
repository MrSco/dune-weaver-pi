"""DW Mini fork: host-owned pattern library, board-side LRU pattern cache.

Upstream 5.x treats the board's SD card as the pattern library. Boards with
no SD card (the Dune Weaver Mini running our firmware fork) serve /sd from a
~1.9 MB LittleFS cache in the spare OTA slot instead, so here the HOST owns
the library (./patterns) and the board only holds the patterns about to be
drawn:

* Before a pattern or playlist runs, every pattern it needs is uploaded
  zlib-compressed (4 KB window; the firmware inflates on the fly) if the
  board lacks it, evicting the least recently used cached patterns to make
  room. A playlist must fit the cache as a whole.
* Board file names are short hashes of the host path ('/patterns/<12 hex>.thr'):
  pattern names often contain spaces, which break `$Sand/Run=<path> clear=..`,
  and hashing sidesteps file-system name limits. An index per board maps them
  back to host paths for status display.
* The three clear patterns live under their fixed names and are never evicted.
* A firmware update re-formats the cache; reconciling against the board's
  listing notices that and simply re-uploads on the next run.

Enabled by default; DW_PATTERN_CACHE=0 restores upstream behaviour.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import threading
import time
import zlib
from typing import Iterable, Optional

from modules.core.state import state

logger = logging.getLogger(__name__)

ENABLED = os.getenv("DW_PATTERN_CACHE", "1") != "0"

BOARD_DIR = "/patterns"
BLOCK = 4096                 # LittleFS block size on the board
FILE_OVERHEAD = BLOCK        # metadata pair per file (conservative)
RESERVE = 96 * 1024          # headroom: playlists, index churn, rounding of reported sizes
ZLIB_LEVEL = 9
ZLIB_WBITS = 12              # 4 KB window; the firmware accepts <= 8 KB

CLEAR_NAMES = ("clear_from_in.thr", "clear_from_out.thr", "clear_sideway.thr")
# Table-specific stock clears: '' = upstream files, 'mini' = clear_*_mini.thr.
CLEAR_VARIANT = os.getenv("DW_CLEAR_VARIANT", "mini").strip().lower()

_HASHED = re.compile(r"^[0-9a-f]{12}\.thr$")
_lock = threading.RLock()
_compressed_memo: dict = {}   # (path, mtime, size) -> bytes


class CacheError(Exception):
    """The board cache cannot satisfy a request (unavailable or too small)."""


# --------------------------------------------------------------------- paths

def _patterns_dir() -> str:
    from modules.core.pattern_manager import THETA_RHO_DIR
    return THETA_RHO_DIR


def host_rel(file_path) -> str:
    from modules.core.pattern_manager import _host_rel_path
    return _host_rel_path(file_path)


def board_name_for(rel: str) -> str:
    if rel in CLEAR_NAMES:
        return rel
    return hashlib.sha1(rel.encode("utf-8")).hexdigest()[:12] + ".thr"


def board_path_for(rel: str) -> str:
    return f"{BOARD_DIR}/{board_name_for(rel)}"


def _clear_source(name: str) -> str:
    """Host file whose content backs a fixed-name clear on the board."""
    custom = {
        "clear_from_in.thr": getattr(state, "custom_clear_from_in", None),
        "clear_from_out.thr": getattr(state, "custom_clear_from_out", None),
    }.get(name)
    base = _patterns_dir()
    if custom and os.path.exists(os.path.join(base, host_rel(custom))):
        return os.path.join(base, host_rel(custom))
    if CLEAR_VARIANT:
        stem = name[:-len(".thr")]
        variant = os.path.join(base, f"{stem}_{CLEAR_VARIANT}.thr")
        if os.path.exists(variant):
            return variant
    return os.path.join(base, name)


def _source_for(rel: str) -> str:
    if rel in CLEAR_NAMES:
        return _clear_source(rel)
    return os.path.join(_patterns_dir(), rel)


# ---------------------------------------------------------------- compression

def compress_file(path: str) -> bytes:
    st = os.stat(path)
    key = (path, st.st_mtime_ns, st.st_size)
    data = _compressed_memo.get(key)
    if data is None:
        with open(path, "rb") as f:
            raw = f.read()
        c = zlib.compressobj(ZLIB_LEVEL, zlib.DEFLATED, ZLIB_WBITS)
        data = c.compress(raw) + c.flush()
        if len(_compressed_memo) > 256:
            _compressed_memo.clear()
        _compressed_memo[key] = data
    return data


def _source_sig(path: str) -> list:
    st = os.stat(path)
    return [st.st_mtime_ns, st.st_size]


def footprint(nbytes: int) -> int:
    """Flash a file of nbytes occupies on the board's LittleFS."""
    return math.ceil(max(nbytes, 1) / BLOCK) * BLOCK + FILE_OVERHEAD


# ----------------------------------------------------------------- the index

def _index_path() -> Optional[str]:
    from modules.core import board_cache
    key = board_cache.board_key()
    if not key:
        return None
    return os.path.join(board_cache._board_dir(key), "pattern_cache.json")


def _load_index() -> dict:
    from modules.core import board_cache
    path = _index_path()
    data = board_cache._read_json(path, None) if path else None
    if not isinstance(data, dict):
        data = {}
    data.setdefault("files", {})    # board name -> {rel, size, sig, last_used}
    data.setdefault("pinned", [])   # host rels that must stay (autostart playlist)
    return data


def _save_index(index: dict) -> None:
    from modules.core import board_cache
    path = _index_path()
    if path:
        board_cache._write_json(path, index)


# ------------------------------------------------------------ board listing

_SIZE_RE = re.compile(r"^\s*([\d.]+)\s*([KMGT]?B)\s*$", re.I)
_UNITS = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}


def parse_size(text) -> int:
    m = _SIZE_RE.match(str(text or ""))
    if not m:
        return 0
    return int(float(m.group(1)) * _UNITS[m.group(2).upper()])


def list_board(conn) -> tuple[dict, int, int]:
    """({name: size}, total_bytes, used_bytes) for the board's /patterns."""
    r = conn._get("/upload", params={"path": BOARD_DIR, "list": "yes"}, timeout=15.0)
    data = r.json()
    total = parse_size(data.get("total"))
    if not total:
        raise CacheError(f"Board pattern storage unavailable: {data.get('status') or data}")
    used = parse_size(data.get("used"))
    files = {}
    for entry in data.get("files") or []:
        try:
            size = int(entry.get("size", -1))
        except (TypeError, ValueError):
            continue
        if size >= 0:   # -1 marks a directory
            files[entry.get("name")] = size
    return files, total, used


# ---------------------------------------------------------------- core logic

def _reconcile(index: dict, on_board: dict) -> None:
    """Forget index entries the board no longer has (e.g. after a firmware
    update re-formatted the cache) and adopt sizes from the listing."""
    files = index["files"]
    for name in list(files):
        if name not in on_board:
            del files[name]
        else:
            files[name]["size"] = on_board[name]


def _delete(conn, name: str) -> None:
    conn.delete_file(BOARD_DIR, name)
    logger.info(f"Pattern cache: evicted {name}")


def ensure(conn, file_paths: Iterable, protect: Iterable = ()) -> dict:
    """Make every pattern in file_paths present on the board.

    Returns {host_rel: board_path}. Raises CacheError when the board cache is
    unavailable or the set cannot fit. `protect` lists extra host paths that
    must not be evicted (e.g. the pattern the table is drawing).
    """
    rels = list(dict.fromkeys(host_rel(p) for p in file_paths))
    with _lock:
        index = _load_index()
        on_board, total, used = list_board(conn)
        _reconcile(index, on_board)
        files = index["files"]

        keep_rels = set(rels) | {host_rel(p) for p in protect} | set(index["pinned"]) | set(CLEAR_NAMES)
        keep_names = {board_name_for(r) for r in keep_rels}

        # What has to be uploaded, and the footprint of the whole working set.
        uploads = []   # (rel, name, data, sig)
        working = 0
        for rel in rels:
            src = _source_for(rel)
            if not os.path.exists(src):
                raise CacheError(f"Pattern not found in the library: {rel}")
            name = board_name_for(rel)
            sig = _source_sig(src)
            entry = files.get(name)
            if entry and entry.get("sig") == sig and name in on_board:
                working += footprint(entry["size"])
                continue
            data = compress_file(src)
            uploads.append((rel, name, data, sig))
            working += footprint(len(data))

        capacity = total - RESERVE
        pinned_extra = sum(footprint(files[n]["size"]) for n in keep_names
                           if n in files and files[n]["rel"] not in rels)
        if working + pinned_extra > capacity:
            raise CacheError(
                f"Needs {working // 1024} KB on the table but only "
                f"{max(capacity - pinned_extra, 0) // 1024} KB is available "
                f"(table storage {total // 1024} KB). Use fewer or smaller patterns.")

        need = sum(footprint(len(d)) for _, _, d, _ in uploads)
        free = total - used
        if need + RESERVE > free:
            # Least recently used first; files we don't know (no index entry)
            # are leftovers and go before anything we do.
            candidates = [n for n in on_board
                          if n not in keep_names and (n in files or _HASHED.match(n))]
            candidates.sort(key=lambda n: files.get(n, {}).get("last_used", 0))
            for name in candidates:
                if need + RESERVE <= free:
                    break
                _delete(conn, name)
                free += footprint(on_board.get(name, 0))
                files.pop(name, None)
            if need + RESERVE > free:
                _, total, used = list_board(conn)
                free = total - used
                if need + RESERVE > free:
                    _save_index(index)
                    raise CacheError(f"Could not free enough table storage "
                                     f"({free // 1024} KB free, need {(need + RESERVE) // 1024} KB)")

        now = time.time()
        for rel, name, data, sig in uploads:
            t0 = time.time()
            conn.upload_file(f"{BOARD_DIR}/{name}", data, BOARD_DIR)
            files[name] = {"rel": rel, "size": len(data), "sig": sig, "last_used": now}
            logger.info(f"Pattern cache: uploaded {rel} as {name} "
                        f"({len(data) // 1024} KB, {time.time() - t0:.1f}s)")
        for rel in rels:
            entry = files.get(board_name_for(rel))
            if entry:
                entry["last_used"] = now
        _save_index(index)
        return {rel: board_path_for(rel) for rel in rels}


def ensure_clears(conn, force: bool = False) -> None:
    """Keep the three fixed-name clear patterns on the board. With force,
    re-upload even when present (the custom clear selection changed)."""
    with _lock:
        if force:
            index = _load_index()
            for name in CLEAR_NAMES:
                index["files"].pop(name, None)
            _save_index(index)
        ensure(conn, CLEAR_NAMES)


def set_pinned(file_paths: Iterable) -> None:
    """Patterns that must stay cached even when nothing is playing them
    (the board's autostart playlist runs without the host)."""
    with _lock:
        index = _load_index()
        index["pinned"] = list(dict.fromkeys(host_rel(p) for p in file_paths))
        _save_index(index)


def host_rel_for_board_path(sd_path: str) -> Optional[str]:
    """Map a board path from /sand_status back to the host library path."""
    if not sd_path:
        return None
    name = sd_path.replace("\\", "/").rsplit("/", 1)[-1]
    if name in CLEAR_NAMES:
        return name
    entry = _load_index()["files"].get(name)
    return entry.get("rel") if entry else None


def status(conn) -> dict:
    """Cache summary for the API/UI."""
    on_board, total, used = list_board(conn)
    index = _load_index()
    _reconcile(index, on_board)
    return {
        "total": total,
        "used": used,
        "free": max(total - used, 0),
        "reserve": RESERVE,
        "files": sorted(
            ({"name": n, "rel": e.get("rel"), "size": e.get("size"),
              "last_used": e.get("last_used")} for n, e in index["files"].items()),
            key=lambda e: -(e["last_used"] or 0)),
        "pinned": index["pinned"],
    }


def estimate(file_paths: Iterable) -> dict:
    """Compressed footprint of a set of library patterns (playlist sizing)."""
    rels = list(dict.fromkeys(host_rel(p) for p in file_paths))
    total = 0
    missing = []
    for rel in rels:
        src = _source_for(rel)
        if not os.path.exists(src):
            missing.append(rel)
            continue
        total += footprint(len(compress_file(src)))
    return {"bytes": total, "patterns": len(rels), "missing": missing}
