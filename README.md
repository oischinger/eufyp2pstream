# EufyP2PStream

A small project that provides a Video/Audio WebRTC stream from Eufy cameras that don't directly support RTSP.

It requires a few other addons to provide the live stream.

# Howto install

## Prerequisites
Install and configure the [Eufy Security WS Addon](https://github.com/bropat/hassio-eufy-security-ws).
Install the [go2RTC Addon](https://github.com/AlexxIT/go2rtc).

## Install this addon
Copy this directory to `/addons/eufyp2pstream` on your Home Assistant host and [install the addon](https://my.home-assistant.io/redirect/supervisor_addon/?addon=local_eufyp2pstream).

Open the [Addon's WebUI](https://my.home-assistant.io/redirect/supervisor_ingress/?addon=local_eufyp2pstream) and enjoy a camera stream with audio via WEBRTC.

## Setting up a camera in go2RTC

Go to the go2RTC Webui and select `Config`. Enter a new configuration for the Eufy camera:
```
streams:
  doorbell: exec:ffmpeg -analyzeduration 1200000 -i tcp://IP_ADDR:63337?timeout=100000000 -analyzeduration 1200000 -f h264 -i tcp://IP_ADDR:63336?timeout=100000000 -strict -2 -c:a opus -c:v copy -hls_init_time 0 -hls_time 2 -hls_segment_type mpegts  -fflags genpts+nobuffer+flush_packets -flags low_delay -hls_playlist_type event -sc_threshold 0 -g 15 -map 0:a -map 1:v -rtsp_transport tcp -f rtsp {output}
```
Replace `IP_ADDR` with the IP of your Home Assistant instance. 

# References
This project is inspired by:

- [eufy_security Home Assistant Custom Component](https://github.com/fuatakgun/eufy_security)
- [go2RTC](https://github.com/AlexxIT/go2rtc)
- [Video Decoding in ioBroker.eusec](https://github.com/bropat/ioBroker.eusec/blob/0a15e1d125f4fd00144af66d57d8d738140ea619/src/lib/eufy-security/video.ts#L14-L65
)

# TODO

## Talkback

To play an audio file start the P2P stream via the WebRTC URL and spin up an ffmpeg process like this:

`ffmpeg  -re -stream_loop -1 -i test.mp3 -vn -sn -dn -c:a aac  -b:a 20k -ar 16k -ac 2 -f adts tcp://127.0.0.1:63338`
