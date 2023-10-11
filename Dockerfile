FROM python:3.11

ENV PYTHONUNBUFFERED=1
ENV PIPX_HOME=/opt/pipx
ENV PIPX_BIN_DIR=/usr/local/bin

RUN pip install pipx && \
    pipx install harlequin

RUN apt-get update -y && apt-get install -y \
    wget

RUN wget -O /usr/local/bin/tty2web "https://github.com/kost/tty2web/releases/download/v3.0.0/tty2web_linux_amd64" && \
    chmod +x /usr/local/bin/tty2web

VOLUME ["/data"]
EXPOSE 1294

# styling
#COPY harlequin_tty2web.conf /root/.tty2web
#COPY harlequin_bg.jpg /root/harlequin_bg.jpg
CMD bash -c "COLORTERM=truecolor tty2web --address 0.0.0.0 --port ${PORT:-1294} --permit-write --reconnect harlequin"

# other commands that can be used
#CMD harlequin
