FROM alexxit/go2rtc:latest
RUN apk add --no-cache python3
RUN apk add --no-cache py-pip

RUN pip install aiohttp asyncio

COPY files /files
COPY files/go2rtc.yaml /root/

CMD [ "/files/run.sh" ]
