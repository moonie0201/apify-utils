FROM apify/actor-python:3.12

COPY requirements.txt ./
RUN echo "Python: $(python --version)" \
 && pip install --no-cache-dir -r requirements.txt \
 && pip freeze

COPY src ./src
COPY blocklist.txt ./

RUN python -c "import src.main" \
 && python -m compileall -q src

CMD ["python", "-m", "src.main"]
