#!/bin/bash
set +u
CONFIG_PATH=/data/options.json

EUFY_WS_PORT=$(jq --raw-output ".eufy_security_ws_port" $CONFIG_PATH)

echo "Starting EufyP2PStream. eufy_security_ws_port is $EUFY_WS_PORT"
python3 -u /eufyp2pstream.py $EUFY_WS_PORT
echo "Exited with code $?"