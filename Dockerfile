ARG BUILD_FROM=ghcr.io/hassio-addons/base:14.1.0
# hadolint ignore=DL3006
FROM ${BUILD_FROM}

RUN apk add --no-cache python3
RUN apk add --no-cache py-pip

RUN pip install aiohttp asyncio

COPY files/run.sh /
COPY files/eufyp2pstream.py /
COPY files/websocket.py /
RUN chmod a+x /run.sh

CMD ["/run.sh"]
