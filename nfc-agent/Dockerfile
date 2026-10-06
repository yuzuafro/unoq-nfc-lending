# pyscard has no aarch64 wheel on PyPI, so build one in a throwaway stage.
FROM python:3.13-slim AS pyscard
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev swig libpcsclite-dev \
    && pip wheel --no-cache-dir -w /wheels pyscard==2.3.1

FROM python:3.13-slim
# libusb: nfcpy (RC-S380). pcscd + libccid: CCID readers such as the Zoweetek ZW-12026-12.
RUN apt-get update && apt-get install -y --no-install-recommends libusb-1.0-0 pcscd libccid \
    && rm -rf /var/lib/apt/lists/*
COPY --from=pyscard /wheels /wheels
RUN pip install --no-cache-dir nfcpy==1.0.4 /wheels/*.whl && rm -rf /wheels
COPY agent.py pcsc.py /
CMD ["python3", "-u", "/agent.py"]
