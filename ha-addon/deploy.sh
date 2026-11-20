#!/bin/sh
# Assemble the local Home Assistant add-on and copy it to the HA host.
#
#   ha-addon/deploy.sh [root@homeassistant.local]
#
# Ships the COMMITTED state of this repo (git archive HEAD) as app/, plus the
# add-on files from this folder, to /addons/dune_weaver. Then, on first
# install: Settings > Add-ons > Add-on store > (menu) Check for updates >
# "Dune Weaver" under Local add-ons. After later deploys, bump `version` in
# config.yaml and use Rebuild on the add-on page.
set -e

HOST=${1:-root@homeassistant.local}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/.." && pwd)
DEST=/addons/dune_weaver

if [ -n "$(git -C "$REPO" status --porcelain -- modules main.py static patterns templates requirements-nonrpi.txt)" ]; then
  echo "warning: uncommitted app changes are NOT deployed (git archive HEAD)" >&2
fi

ssh "$HOST" "rm -rf $DEST.new && mkdir -p $DEST.new/app"
git -C "$REPO" archive --format=tar HEAD \
  main.py modules static templates patterns VERSION requirements-nonrpi.txt \
  | ssh "$HOST" "tar -xf - -C $DEST.new/app"
for f in config.yaml Dockerfile run.sh nginx.conf; do
  tr -d '\r' < "$HERE/$f" | ssh "$HOST" "cat > $DEST.new/$f"
done
ssh "$HOST" "chmod +x $DEST.new/run.sh && rm -rf $DEST.old && { [ -d $DEST ] && mv $DEST $DEST.old || true; } && mv $DEST.new $DEST && rm -rf $DEST.old && du -sh $DEST"
echo "Deployed to $HOST:$DEST"
