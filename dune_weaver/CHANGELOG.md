# Changelog

## 5.0.1-mini.4

- Installs from the GitHub add-on repository (MrSco/dune-weaver-pi), so new
  versions show up as updates in Home Assistant.
- Table firmware updates: the Dune Weaver settings now offer the Mini firmware
  releases from MrSco/dune-weaver-firmware (one click, over Wi-Fi, with
  automatic rollback if the new firmware fails to start).
- Settings, playlists and play history are backed up to
  `/share/dune_weaver/data-backup` at start and hourly, and restored
  automatically on a fresh install.

## 5.0.1-mini.3

- Fix the logo and favicons (404 in the add-on).

## 5.0.1-mini.2

- Sidebar panel in Home Assistant (ingress).

## 5.0.1-mini.1

- First add-on release: host-owned pattern library with an on-demand,
  compressed pattern cache on the table; MQTT through Home Assistant's broker.
