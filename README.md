# EufyP2PStream

A small project that provides a Video/Audio Stream from Eufy cameras that don't directly support RTSP.

It uses [go2RTC](https://github.com/AlexxIT/go2rtc) to provide the live stream.

# Howto install

Copy this directory to /addons/eufyp2pstream and run it.

Open the WebUI: http://HOME_ASSISTANT_HOST::1984/webrtc.html?src=camera1 and enjoy a camera stream with audio via WEBRTC.

# References
This project is inspired by:

[eufy_security Home Assistant Custom Component](https://github.com/fuatakgun/eufy_security)
[go2RTC](https://github.com/AlexxIT/go2rtc)
[Video Decoding in ioBroker.eusec](https://github.com/bropat/ioBroker.eusec/blob/0a15e1d125f4fd00144af66d57d8d738140ea619/src/lib/eufy-security/video.ts#L14-L65
)