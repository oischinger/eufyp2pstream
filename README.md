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
  doorbell:
    - exec:ffmpeg -thread_queue_size 512 -an -sn -dn -analyzeduration 1200000 -f h264 -fflags +discardcorrupt+nobuffer -flags low_delay -i tcp://127.0.0.1:63336?timeout=100000000 -thread_queue_size 512 -vn -sn -dn -analyzeduration 1200000 -fflags +discardcorrupt+nobuffer -flags low_delay -i tcp://127.0.0.1:63337?timeout=100000000 -preset ultrafast -tune zerolatency -sc_threshold 0 -fflags genpts+nobuffer+flush_packets -map 0:v -map 1:a -preset ultrafast -c:a opus -application lowdelay -frame_duration 20 -codec:v copy -tune zerolatency -movflags +faststart -g 15 -r 15 -strict -2 -rtsp_transport tcp -f rtsp {output}
    - "exec:ffmpeg -fflags nobuffer -f alaw -ar 8000 -i pipe: -vn -sn -dn -c:a aac -b:a 24k -ar 16k -ac 2 -f adts tcp://127.0.0.1:63338#backchannel=1#killsignal=15#killtimeout=3"
```
This configures two ffmpeg processes. The first one is for video+audio, the second one is for Talkback.

# References
This project is inspired by:

- [eufy_security Home Assistant Custom Component](https://github.com/fuatakgun/eufy_security)
- [go2RTC](https://github.com/AlexxIT/go2rtc)
- [Video Decoding in ioBroker.eusec](https://github.com/bropat/ioBroker.eusec/blob/0a15e1d125f4fd00144af66d57d8d738140ea619/src/lib/eufy-security/video.ts#L14-L65
)

# Example Use Cases

## Audio/Video Streaming with WebRTC card
With the [WebRTC Camera Custom Card](https://github.com/AlexxIT/WebRTC) you can show the camera stream on your lovelace dashboard. It ensures that the stream is only started when the card is visible. It makes sense to display it only on conditions (e.g. as part of a conditional card that pops up when your doorbell rings or when an input_boolean is on):

Install the [WebRTC Camera Custom Component](https://github.com/AlexxIT/WebRTC) and create a card as follows:

```
type: custom:webrtc-camera
url: doorbell               # Use the camera name from the go2RTC configuration
mode: webrtc                # For faster activation you can force it to use webrtc instead of auto-detection
style: 'video {aspect-ratio: 16/12; object-fit: fill;}' # Use this to ensure your lovelace layouts doesn't jump when the stream is activated 
```

## Talkback with WebRTC card
Append the following line to your webrtc-camera card in lovelace: `media: video,audio,microphone` to enable talkback. Note that your Home Assistant instance needs to use https with a valid certificate. Otherwise browsers will not allow microphone access.
