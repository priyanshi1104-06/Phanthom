# Phantom sandbox: the image the worker runs in.
# Build from the repo root:  docker build -f sandbox/Dockerfile -t phantom-sandbox .
# runner/executor.py adds the lockdown at run time: no network, memory cap, read-only filesystem.
FROM python:3.14-slim
COPY sandbox/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY worker.py /app/worker.py
# (runner/executor.py mounts the current worker.py over this copy at run time)
USER 65534:65534
WORKDIR /tmp
ENTRYPOINT ["python", "-I", "-B", "/app/worker.py"]
