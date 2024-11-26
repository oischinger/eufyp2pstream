#!/usr/bin/with-contenv bashio
export PYTHONUNBUFFERED=1
set +u
CONFIG_PATH=/data/options.json

echo "Starting EufyP2PStream"

EUFY_WS_PORT=$(jq --raw-output ".eufy_security_ws_port" $CONFIG_PATH)
CAM1_SN=$(jq --raw-output ".camera_1_serial_number" $CONFIG_PATH)
CAM2_SN=$(jq --raw-output ".camera_2_serial_number" $CONFIG_PATH)
CAM3_SN=$(jq --raw-output ".camera_3_serial_number" $CONFIG_PATH)
CAM4_SN=$(jq --raw-output ".camera_4_serial_number" $CONFIG_PATH)
CAM5_SN=$(jq --raw-output ".camera_5_serial_number" $CONFIG_PATH)
CAM6_SN=$(jq --raw-output ".camera_6_serial_number" $CONFIG_PATH)
CAM7_SN=$(jq --raw-output ".camera_7_serial_number" $CONFIG_PATH)
CAM8_SN=$(jq --raw-output ".camera_8_serial_number" $CONFIG_PATH)
CAM9_SN=$(jq --raw-output ".camera_9_serial_number" $CONFIG_PATH)
CAM10_SN=$(jq --raw-output ".camera_10_serial_number" $CONFIG_PATH)
CAM11_SN=$(jq --raw-output ".camera_11_serial_number" $CONFIG_PATH)
CAM12_SN=$(jq --raw-output ".camera_12_serial_number" $CONFIG_PATH)
CAM13_SN=$(jq --raw-output ".camera_13_serial_number" $CONFIG_PATH)
CAM14_SN=$(jq --raw-output ".camera_14_serial_number" $CONFIG_PATH)
CAM15_SN=$(jq --raw-output ".camera_15_serial_number" $CONFIG_PATH)
DEBUG_LOG=$(jq --raw-output ".debug_log" $CONFIG_PATH)
if [ "$DEBUG_LOG" == "true" ]; then
    DEBUG_LOG="--debug"
else
    DEBUG_LOG=""
fi

echo "Starting EufyP2PStream. eufy_security_ws_port is $EUFY_WS_PORT"
python3 -u /eufyp2pstream.py $DEBUG_LOG --ws_security_port $EUFY_WS_PORT --camera_serials $CAM1_SN $CAM2_SN $CAM3_SN $CAM4_SN $CAM5_SN $CAM6_SN $CAM7_SN $CAM8_SN $CAM9_SN $CAM10_SN $CAM11_SN $CAM12_SN $CAM13_SN $CAM14_SN $CAM15_SN
echo "Exited with code $?"