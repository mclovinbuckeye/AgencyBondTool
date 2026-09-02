FROM python:3.14-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY bond_processor.py .
COPY salesforce_client.py .
COPY salesforce_oauth.py .
COPY config.example.json .

CMD ["python", "bond_processor.py", "process"]