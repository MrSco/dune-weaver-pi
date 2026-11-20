#!/bin/sh
# Start the Dune Weaver host inside the Home Assistant add-on.
#
# The backend keeps all of its data relative to the working directory, so the
# working directory is the add-on's persistent /data, and the read-only code
# assets it expects there are symlinked in from /app. The pattern library
# lives in /share/dune_weaver/patterns so it can be managed over Samba.
set -e

APP=/app
DATA=/data
LIB=/share/dune_weaver/patterns
BACKUP=/share/dune_weaver/data-backup
# The app's persistent state (relative to $DATA).
PERSIST="settings.json state.json playlists.json metadata_cache.json execution_times.jsonl board_cache static/custom"

# A fresh install (no settings yet) restores the last backup, e.g. when moving
# from the local add-on to the repository one, or after a reinstall.
if [ ! -e "$DATA/settings.json" ] && [ -d "$BACKUP" ]; then
  echo "Restoring app data from $BACKUP"
  cp -a "$BACKUP/." "$DATA/"
fi

backup_data() {
  mkdir -p "$BACKUP.tmp"
  ( cd "$DATA" && for p in $PERSIST; do [ -e "$p" ] && cp -a --parents "$p" "$BACKUP.tmp/"; done ) || return 0
  rm -rf "$BACKUP" && mv "$BACKUP.tmp" "$BACKUP"
}

# 5.0.1-mini.1..3 made static/custom a symlink (to $DATA/static_custom); it
# must become a real directory.
[ -L "$DATA/static/custom" ] && rm -f "$DATA/static/custom"
mkdir -p "$LIB" "$DATA/board_cache" "$DATA/static/custom"
if [ -d "$DATA/static_custom" ]; then
  cp -rn "$DATA/static_custom/." "$DATA/static/custom/" 2>/dev/null || true
  rm -rf "$DATA/static_custom"
fi

# Seed the library with the bundled patterns (never overwrites the user's).
cp -rn "$APP/patterns/." "$LIB/" 2>/dev/null || true

cd "$DATA"
ln -sfn "$LIB" patterns
for item in templates VERSION wifi steps_calibration; do
  [ -e "$APP/$item" ] && ln -sfn "$APP/$item" "$item"
done
# /static is served by Starlette's StaticFiles, which refuses symlinked files,
# so the bundled assets are copied (refreshed every start). static/custom
# (the uploaded logo) is real, persistent data. dist/ is served by nginx
# straight from /app and is not needed here.
for entry in "$APP"/static/*; do
  name=$(basename "$entry")
  case "$name" in custom|dist) continue ;; esac
  [ -L "static/$name" ] && rm -f "static/$name"
  cp -rf "$entry" static/
done

# Add-on options.
opt() { python3 -c "import json,sys; print(json.load(open('$DATA/options.json')).get(sys.argv[1]) or '')" "$1" 2>/dev/null; }
BOARD_URL=$(opt board_url)
[ -n "$BOARD_URL" ] && export DUNE_BOARD_URL="$BOARD_URL"
LOG_LEVEL=$(opt log_level)
export LOG_LEVEL="${LOG_LEVEL:-INFO}"

# MQTT from Home Assistant's broker service (used unless MQTT is configured
# in the app's own settings, which take precedence).
if [ -n "$SUPERVISOR_TOKEN" ]; then
  if curl -fsS -H "Authorization: Bearer $SUPERVISOR_TOKEN" http://supervisor/services/mqtt -o /tmp/mqtt.json 2>/dev/null; then
    eval "$(python3 - <<'EOF'
import json, shlex
d = json.load(open("/tmp/mqtt.json")).get("data") or {}
for env, key in (("MQTT_BROKER", "host"), ("MQTT_PORT", "port"),
                 ("MQTT_USERNAME", "username"), ("MQTT_PASSWORD", "password")):
    if d.get(key) not in (None, ""):
        print(f"export {env}={shlex.quote(str(d[key]))}")
EOF
)"
    rm -f /tmp/mqtt.json
    [ -n "$MQTT_BROKER" ] && echo "MQTT broker from Home Assistant: $MQTT_BROKER:$MQTT_PORT"
  fi
fi

backup_data
( while sleep 3600; do backup_data; done ) &

nginx
echo "Dune Weaver web UI on port 8099; pattern library in $LIB"
exec python3 "$APP/main.py"
