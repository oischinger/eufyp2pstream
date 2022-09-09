# EufyP2PStream

A small project that provides a Video/Audio Stream from Eufy cameras that don't directly support RTSP.

It uses [go2RTC](https://github.com/AlexxIT/go2rtc) to provide the live stream.

# Howto install

## Prerequisites
Install and configure the [Eufy Security Addon](https://github.com/fuatakgun/eufy_security_addon) first. See the instructions [here](https://github.com/fuatakgun/eufy_security).

## Install this addon
Copy this directory to /addons/eufyp2pstream and run it.

Open the [Addon's WebUI](https://my.home-assistant.io/redirect/supervisor_ingress/?addon=eufyp2pstream) and enjoy a camera stream with audio via WEBRTC.

## Setting up a camera in Home Assistance

Go to devices and [setup a new generic camera](https://my.home-assistant.io/redirect/config_flow_start/?domain=generic) with the following parameters (replace with your Home Assistant IP address):

![Generic Camera Configuration](doc/hass_generic_camera_setup.png)


# References
This project is inspired by:

- [eufy_security Home Assistant Custom Component](https://github.com/fuatakgun/eufy_security)
- [go2RTC](https://github.com/AlexxIT/go2rtc)
- [Video Decoding in ioBroker.eusec](https://github.com/bropat/ioBroker.eusec/blob/0a15e1d125f4fd00144af66d57d8d738140ea619/src/lib/eufy-security/video.ts#L14-L65
)

# TODO

## Screenshot:

`ffmpeg -f h264 -i tcp://127.0.0.1:63336?timeout=100000000 -strict -2  -hls_init_time 0 -hls_time 2 -hls_segment_type mpegts -fflags genpts+nobuffer+flush_packets -frames:v 1  test.jpg`

## Talkback

To play an audio file start the P2P stream via the WebRTC URL and spin up an ffmpeg process like this:

`ffmpeg  -re -stream_loop -1 -i test.mp3 -vn -sn -dn -c:a aac  -b:a 20k -ar 16k -ac 2 -f adts tcp://127.0.0.1:63338`
