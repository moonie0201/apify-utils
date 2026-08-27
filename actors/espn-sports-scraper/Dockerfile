FROM apify/actor-python:3.12

COPY requirements.txt ./
RUN echo "Python: $(python --version)" \
 && pip install --no-cache-dir -r requirements.txt \
 && pip freeze

COPY blocklist.txt ./
COPY src ./src

RUN python -c "import src.main"

CMD ["python", "-m", "src.main"]
