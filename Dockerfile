FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git ca-certificates curl otf2bdf \
 && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 https://github.com/olikraus/u8g2.git /tmp/u8g2 \
 && make -C /tmp/u8g2/tools/font/bdfconv \
 && install -m 0755 /tmp/u8g2/tools/font/bdfconv/bdfconv /usr/local/bin/bdfconv \
 && strip /usr/local/bin/bdfconv \
 && rm -rf /tmp/u8g2

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./app.py
COPY templates ./templates
COPY static ./static

ENV PYTHONUNBUFFERED=1
CMD exec gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 2 --timeout 300 app:app
