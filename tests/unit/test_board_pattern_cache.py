"""
Unit tests for the DW Mini fork's board pattern cache (host library -> board LRU).

A FakeBoard stands in for the firmware's /upload protocol: a flat /patterns
directory on a LittleFS of fixed capacity, reporting human-readable sizes the
way the firmware does ("1.88 MB").
"""
import math
import os
import random
import zlib

import pytest

from modules.core import board_cache, board_pattern_cache as pc
from modules.core.state import state


def _human(n):
    for unit, size in (("MB", 1024 ** 2), ("KB", 1024)):
        if n >= size:
            return f"{n / size:.2f} {unit}"
    return f"{n} B"


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class FakeBoard:
    """Just enough of FluidNCClient for the cache: list, upload, delete."""

    def __init__(self, capacity=1920 * 1024):
        self.capacity = capacity
        self.files = {}          # name -> bytes
        self.uploads = []
        self.deletes = []

    def used(self):
        blocks = sum(math.ceil(max(len(d), 1) / 4096) + 1 for d in self.files.values())
        return (blocks + 2) * 4096

    def _get(self, path, params=None, timeout=None):
        assert path == "/upload" and params["path"] == "/patterns"
        return FakeResponse({
            "files": [{"name": n, "size": str(len(d))} for n, d in self.files.items()],
            "path": "patterns", "total": _human(self.capacity), "used": _human(self.used()),
            "status": "Ok",
        })

    def upload_file(self, sd_path, data, directory):
        name = sd_path.rsplit("/", 1)[-1]
        self.files[name] = data
        if self.used() > self.capacity:
            del self.files[name]
            raise RuntimeError("board full")
        self.uploads.append(name)
        return {}

    def delete_file(self, directory, filename):
        self.files.pop(filename, None)
        self.deletes.append(filename)
        return {}


def _pattern(path, kb, seed):
    """Write a .thr of roughly kb compressed KB (random -> poorly compressible)."""
    rnd = random.Random(seed)
    lines, size, target = [], 0, kb * 1024 * 2.2
    while size < target:
        line = f"{rnd.uniform(-500, 500):.5f} {rnd.random():.5f}\n"
        lines.append(line)
        size += len(line)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines))


@pytest.fixture
def lib(tmp_path, monkeypatch):
    """A library with the three Mini clears and some patterns, and an
    isolated per-board index."""
    root = tmp_path / "patterns"
    for stem in ("clear_from_in", "clear_from_out", "clear_sideway"):
        _pattern(root / f"{stem}.thr", 4, stem)            # upstream clears
        _pattern(root / f"{stem}_mini.thr", 20, stem + "m")  # Mini variants
    for i in range(8):
        _pattern(root / f"set/p{i} (copy).thr", 300, i)
    _pattern(root / "huge.thr", 2500, 99)
    import modules.core.pattern_manager as pm
    monkeypatch.setattr(pm, "THETA_RHO_DIR", str(root))
    monkeypatch.setattr(board_cache, "CACHE_ROOT", str(tmp_path / "board_cache"))
    monkeypatch.setattr(state, "board_mac", "88:13:bf:69:48:48", raising=False)
    monkeypatch.setattr(state, "custom_clear_from_in", None, raising=False)
    monkeypatch.setattr(state, "custom_clear_from_out", None, raising=False)
    monkeypatch.setattr(pc, "CLEAR_VARIANT", "mini")
    return root


def test_board_names_are_hashed_except_clears():
    name = pc.board_name_for("set/p1 (copy).thr")
    assert pc._HASHED.match(name) and " " not in name
    assert pc.board_name_for("clear_from_in.thr") == "clear_from_in.thr"
    assert pc.board_path_for("a.thr") == "/patterns/" + pc.board_name_for("a.thr")


def test_upload_is_zlib_with_small_window(lib):
    board = FakeBoard()
    mapping = pc.ensure(board, ["./patterns/set/p0 (copy).thr"])
    name = mapping["set/p0 (copy).thr"].rsplit("/", 1)[-1]
    data = board.files[name]
    assert data[0] & 0x0F == 8 and (data[0] >> 4) + 8 <= 13          # deflate, window <= 8 KB
    assert ((data[0] << 8) | data[1]) % 31 == 0 and not data[1] & 0x20
    assert zlib.decompress(data) == (lib / "set/p0 (copy).thr").read_bytes()


def test_cached_patterns_are_not_reuploaded(lib):
    board = FakeBoard()
    pc.ensure(board, ["set/p0 (copy).thr"])
    pc.ensure(board, ["set/p0 (copy).thr"])
    assert len(board.uploads) == 1


def test_changed_source_is_reuploaded(lib):
    board = FakeBoard()
    pc.ensure(board, ["set/p0 (copy).thr"])
    _pattern(lib / "set/p0 (copy).thr", 310, 1234)
    os.utime(lib / "set/p0 (copy).thr", (1, 2))
    pc.ensure(board, ["set/p0 (copy).thr"])
    assert len(board.uploads) == 2


def test_lru_eviction_keeps_recent_and_clears(lib, monkeypatch):
    board = FakeBoard()
    pc.ensure_clears(board)
    t = [1000.0]
    monkeypatch.setattr(pc.time, "time", lambda: t[0])
    for i in range(5):                       # fills most of the 1.9 MB cache
        t[0] += 10
        pc.ensure(board, [f"set/p{i} (copy).thr"])
    t[0] += 10
    pc.ensure(board, ["set/p0 (copy).thr"])   # touch p0: now most recent
    for i in range(5, 8):
        t[0] += 10
        pc.ensure(board, [f"set/p{i} (copy).thr"])
    evicted = set(board.deletes)
    assert pc.board_name_for("set/p1 (copy).thr") in evicted   # oldest untouched
    assert pc.board_name_for("set/p0 (copy).thr") not in evicted
    assert not evicted & set(pc.CLEAR_NAMES)
    assert board.used() <= board.capacity


def test_playlist_that_cannot_fit_is_rejected_before_any_change(lib):
    board = FakeBoard()
    pc.ensure_clears(board)
    before = dict(board.files)
    with pytest.raises(pc.CacheError, match="Use fewer or smaller patterns"):
        pc.ensure(board, [f"set/p{i} (copy).thr" for i in range(8)])
    assert board.files == before and not board.deletes


def test_single_oversized_pattern_is_rejected(lib):
    with pytest.raises(pc.CacheError):
        pc.ensure(FakeBoard(), ["huge.thr"])


def test_missing_pattern_is_an_error(lib):
    with pytest.raises(pc.CacheError, match="not found"):
        pc.ensure(FakeBoard(), ["nope.thr"])


def test_wiped_board_is_refilled(lib):
    """A firmware update re-formats the cache: the index must notice."""
    board = FakeBoard()
    pc.ensure_clears(board)
    pc.ensure(board, ["set/p0 (copy).thr"])
    board.files.clear()
    board.uploads.clear()
    pc.ensure_clears(board)
    pc.ensure(board, ["set/p0 (copy).thr"])
    assert set(board.uploads) == set(pc.CLEAR_NAMES) | {pc.board_name_for("set/p0 (copy).thr")}


def test_clears_use_mini_variant_and_custom_override(lib, monkeypatch):
    board = FakeBoard()
    pc.ensure_clears(board)
    assert zlib.decompress(board.files["clear_from_in.thr"]) == (lib / "clear_from_in_mini.thr").read_bytes()
    monkeypatch.setattr(state, "custom_clear_from_in", "set/p3 (copy).thr", raising=False)
    pc.ensure_clears(board, force=True)
    assert zlib.decompress(board.files["clear_from_in.thr"]) == (lib / "set/p3 (copy).thr").read_bytes()


def test_unknown_leftover_files_are_evicted_first(lib, monkeypatch):
    board = FakeBoard()
    board.files["0123456789ab.thr"] = os.urandom(600 * 1024)   # orphan from a lost index
    board.files["index.json"] = b"[]"                            # not ours: never touched
    for i in range(5):
        pc.ensure(board, [f"set/p{i} (copy).thr"])
    assert "0123456789ab.thr" in board.deletes
    assert "index.json" in board.files


def test_board_path_maps_back_to_host(lib):
    board = FakeBoard()
    mapping = pc.ensure(board, ["set/p2 (copy).thr"])
    assert pc.host_rel_for_board_path(mapping["set/p2 (copy).thr"]) == "set/p2 (copy).thr"
    assert pc.host_rel_for_board_path("/sd/patterns/clear_sideway.thr") == "clear_sideway.thr"
    assert pc.host_rel_for_board_path("/patterns/ffffffffffff.thr") is None


def test_pinned_autostart_patterns_survive_eviction(lib, monkeypatch):
    board = FakeBoard()
    t = [1000.0]
    monkeypatch.setattr(pc.time, "time", lambda: t[0])
    pc.ensure(board, ["set/p0 (copy).thr"])
    pc.set_pinned(["set/p0 (copy).thr"])
    for i in range(1, 8):
        t[0] += 10
        pc.ensure(board, [f"set/p{i} (copy).thr"])
    assert pc.board_name_for("set/p0 (copy).thr") in board.files


def test_parse_size():
    assert pc.parse_size("1.88 MB") == int(1.88 * 1024 ** 2)
    assert pc.parse_size("164.00 KB") == 164 * 1024
    assert pc.parse_size("12 B") == 12
    assert pc.parse_size(None) == 0


# --------------------------------------------------------------------------
# Execution paths with the cache enabled
# --------------------------------------------------------------------------

from unittest.mock import patch

from modules.core import execution


class FakeTable(FakeBoard):
    """FakeBoard plus the run/settings calls execution makes."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.calls = []

    def is_connected(self):
        return True

    def get_status(self):
        return {"state": "Idle", "running": False, "playlist": {"active": False}}

    def stop(self):
        self.calls.append(("stop",))

    def set_setting(self, key, value):
        self.calls.append(("set_setting", key, str(value)))

    def set_feed(self, mm=None, **kw):
        self.calls.append(("set_feed", mm))

    def run_command(self, plain):
        self.calls.append(("run_command", plain))
        return "ok"

    def run_pattern(self, sd_path, clear=None):
        self.calls.append(("run_pattern", sd_path, clear))

    def upload_file(self, sd_path, data, directory):
        self.calls.append(("upload_file", sd_path))
        return super().upload_file(sd_path, data, directory)


@pytest.fixture
def table(lib, monkeypatch):
    t = FakeTable()
    saved = (state.conn, execution.current_run, state.current_playing_file,
             state.current_playlist, state.current_playlist_name)
    state.conn = t
    with patch.object(state, "save"):
        yield t
    (state.conn, execution.current_run, state.current_playing_file,
     state.current_playlist, state.current_playlist_name) = saved


@pytest.mark.pattern_cache
async def test_run_pattern_uploads_then_runs_cached_path(table):
    await execution.run_pattern("./patterns/set/p1 (copy).thr", "adaptive")
    cached = pc.board_path_for("set/p1 (copy).thr")
    names = [c[:2] for c in table.calls]
    assert ("upload_file", cached) in names
    assert ("run_pattern", cached) in names
    assert names.index(("upload_file", cached)) < names.index(("run_pattern", cached))
    assert set(pc.CLEAR_NAMES) <= set(table.files)        # a clear runs first
    assert execution._from_sd_path(cached) == "./patterns/set/p1 (copy).thr"


@pytest.mark.pattern_cache
async def test_run_pattern_too_big_is_an_execution_error(table):
    with pytest.raises(execution.ExecutionError):
        await execution.run_pattern("./patterns/huge.thr", "none")
    assert not any(c[0] == "run_pattern" for c in table.calls)


@pytest.mark.pattern_cache
async def test_start_playlist_caches_everything_before_run(table):
    files = ["set/p0 (copy).thr", "set/p2 (copy).thr"]
    with patch("modules.core.playlist_manager.get_playlist",
               return_value={"name": "fav", "files": files}):
        await execution.start_playlist("fav", clear_pattern="clear_from_in")
    run_at = table.calls.index(("run_command", "$Playlist/Run=fav"))
    uploads = [i for i, c in enumerate(table.calls) if c[0] == "upload_file"]
    assert uploads and max(uploads) < run_at
    txt = table.files["fav.txt"].decode()
    assert txt.split() == [pc.board_path_for(f) for f in files]


@pytest.mark.pattern_cache
async def test_start_playlist_that_does_not_fit_never_runs(table):
    files = [f"set/p{i} (copy).thr" for i in range(8)]
    with patch("modules.core.playlist_manager.get_playlist",
               return_value={"name": "big", "files": files}):
        with pytest.raises(execution.ExecutionError, match="big"):
            await execution.start_playlist("big")
    assert ("run_command", "$Playlist/Run=big") not in table.calls
