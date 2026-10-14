FROM python:3.12-slim
WORKDIR /app
COPY *.py ./
COPY tests/ ./tests/
ENV PARLAY_DATA=/data
VOLUME /data
HEALTHCHECK --interval=60s --timeout=10s --start-period=120s --retries=3 \
  CMD ["python", "-c", "import time,os,sys; p='/data/heartbeat'; sys.exit(0 if os.path.exists(p) and time.time()-float((open(p).read() or '0').strip())<1500 else 1)"]
CMD ["python", "app.py", "serve"]
