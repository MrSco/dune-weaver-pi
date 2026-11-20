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

mkdir -p "$LIB" "$DATA/board_cache" "$DATA/static_custom"

# Seed the library with the bundled patterns (never overwrites the user's).
cp -rn "$APP/patterns/." "$LIB/" 2>/dev/null || true

cd "$DATA"
ln -sfn "$LIB" patterns
for item in templates VERSION wifi steps_calibration; do
  [ -e "$APP/$item" ] && ln -sfn "$APP/$item" "$item"
done
mkdir -p static
for entry in "$APP"/static/*; do
  name=$(basename "$entry")
  [ "$name" = custom ] && continue
  ln -sfn "$entry" "static/$name"
done
ln -sfn "$DATA/static_custom" static/custom

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

nginx
echo "Dune Weaver web UI on port 8099; pattern library in $LIB"
exec python3 "$APP/main.py"
