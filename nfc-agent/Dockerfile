FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends libusb-1.0-0 \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir nfcpy==1.0.4
COPY agent.py /agent.py
CMD ["python3", "-u", "/agent.py"]
