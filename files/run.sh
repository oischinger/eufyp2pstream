#!/usr/bin/with-contenv bashio

set +e

cd /files

while true; do
    killall python3
    killall go2rtc
    python3 hello.py &
    go2rtc
    sleep 5
done

