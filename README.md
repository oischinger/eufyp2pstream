# EufyP2PStream
A small project that provides an audio/video WebRTC stream from Eufy cameras that don't directly support RTSP.
Main goal of this addon is to allow blazing-fast activation of the video-stream on [Home Assistant](https://www.home-assistant.io). See [Example Use Cases](#example-use-cases)

# Howto install
The addon requires a few other addons to provide the live stream.

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
  doorbell: exec:ffmpeg -analyzeduration 1200000 -i tcp://127.0.0.1:63337?timeout=100000000 -analyzeduration 1200000 -f h264 -i tcp://127.0.0.1:63336?timeout=100000000 -strict -2 -c:a opus -c:v copy  -hls_init_time 0 -hls_time 1 -hls_segment_type mpegts -hls_playlist_type event  -hls_list_size 0 -preset ultrafast -tune zerolatency -g 15 -sc_threshold 0 -fflags genpts+nobuffer+flush_packets  -sc_threshold 0  -g 15 -map 0:a -map 1:v -rtsp_transport tcp -f rtsp {output}
```

# References
This project is inspired by:

- [eufy_security Home Assistant Custom Component](https://github.com/fuatakgun/eufy_security)
- [go2RTC](https://github.com/AlexxIT/go2rtc)
- [Video Decoding in ioBroker.eusec](https://github.com/bropat/ioBroker.eusec/blob/0a15e1d125f4fd00144af66d57d8d738140ea619/src/lib/eufy-security/video.ts#L14-L65
)

# Example Use Cases

With the [WebRTC Camera Custom Card](https://github.com/AlexxIT/WebRTC) you can show the camera stream on your lovelace dashboard. It ensures that the stream is only started when the card is visible. It makes sense to display it only on conditions (e.g. as part of a conditional card that pops up when your doorbell rings or when an input_boolean is on):

Install the [WebRTC Camera Custom Component](https://github.com/AlexxIT/WebRTC) and create a card as follows:

```
type: custom:webrtc-camera
url: doorbell               # Use the camera name from the go2RTC configuration
mode: webrtc                # For faster activation you can force it to use webrtc instead of auto-detection
style: 'video {aspect-ratio: 16/12; object-fit: fill;}' # Use this to ensure your lovelace layouts doesn't jump when the stream is activated 
```

# TODO

## Talkback

To play an audio file start the P2P stream via the WebRTC URL and spin up an ffmpeg process like this:

`ffmpeg  -re -stream_loop -1 -i test.mp3 -vn -sn -dn -c:a aac  -b:a 20k -ar 16k -ac 2 -f adts tcp://127.0.0.1:63338`
