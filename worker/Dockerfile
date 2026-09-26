FROM python:3.12-slim

# llama-cpp-python compiles llama.cpp from source: needs a C/C++ toolchain.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential cmake \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Model baked into the image (v1 decision): big image, but fast and self-contained startup.
COPY models/Qwen3-1.7B-Instruct-Q8_0.gguf /models/model.gguf
COPY summarizer.py worker.py db.py webhook.py ./

ENV MODEL_PATH=/models/model.gguf \
    MODEL_NAME=qwen3-1.7b-instruct-q8_0 \
    N_THREADS=1 \
    PYTHONUNBUFFERED=1

# Don't run as root.
RUN useradd --create-home app
USER app

CMD ["python", "worker.py"]
