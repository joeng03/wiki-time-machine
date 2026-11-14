FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HOME=/data/hf PYTHONPATH=/app
WORKDIR /app
COPY extensions/WikiTimeMachine/service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY extensions/WikiTimeMachine/service/wtm ./wtm
COPY extensions/WikiTimeMachine/sql ./sql
COPY extensions/WikiTimeMachine/fixtures ./fixtures
EXPOSE 8000
CMD ["uvicorn", "wtm.api:app", "--host", "::", "--port", "8000"]
