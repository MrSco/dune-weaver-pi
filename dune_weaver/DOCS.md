# Dune Weaver (Mini fork)

Runs the Dune Weaver host software for a sand table running the
[Dune Weaver Mini firmware](https://github.com/MrSco/dune-weaver-firmware):
web UI, pattern library with previews, playlists, and MQTT entities.

## How it works

- **Pattern library** lives in `/share/dune_weaver/patterns` (reachable over
  the Samba add-on). Drop `.thr` files there or upload them in the UI.
- **The table holds a cache.** The Mini has no SD card; before a pattern or
  playlist plays, it is uploaded to the table compressed, and the least
  recently played patterns are evicted. A playlist must fit in the cache
  (about 1.7 MB compressed, typically 15-25 patterns).
- **Firmware updates for the table** appear in the Dune Weaver settings when
  a new Mini release is published. A new firmware stays on probation for its
  first 90 seconds and the table falls back to the previous one if it fails.

## Options

- `board_url`: the table's address (for example `http://192.168.1.50`). Leave
  empty to find it automatically on the network (mDNS).
- `log_level`: add-on log verbosity.

## Access

Open it from the sidebar, or directly on port 8099 on your network.

## Backups

Settings, playlists and play history are copied to
`/share/dune_weaver/data-backup` at start and hourly. A fresh install restores
them from there automatically.
