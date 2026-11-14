FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY wtm ./wtm
COPY tests ./tests
COPY tests_e2e ./tests_e2e
COPY pytest.ini .
# sql/ and fixtures/ are mounted by docker compose
EXPOSE 8000
CMD ["uvicorn", "wtm.api:app", "--host", "0.0.0.0", "--port", "8000"]
