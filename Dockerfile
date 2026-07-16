FROM mcr.microsoft.com/playwright/python:v1.61.0-noble

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=5 \
    TZ=Asia/Shanghai

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data/barcode /app/data/config /app/data/results /app/session /app/legacy/barcode /app/legacy/results

EXPOSE 5001

HEALTHCHECK --interval=15s --timeout=8s --start-period=30s --retries=10 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5001/healthz', timeout=5).read()"

CMD ["sh", "-c", "Xvfb :99 -screen 0 1920x1080x24 >/tmp/xvfb.log 2>&1 & export DISPLAY=:99; exec gunicorn -w 1 --threads 4 --timeout 300 --bind 0.0.0.0:5001 app:app"]
