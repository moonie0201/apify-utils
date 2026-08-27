FROM apify/actor-python:3.12
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr \
 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && pip freeze
COPY NOTICE blocklist.txt ./
COPY src ./src
RUN python -c "import src.main" && python -m compileall -q src
CMD ["python", "-m", "src.main"]
