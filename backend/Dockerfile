FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --timeout 120 --retries 5 -r /app/requirements.txt
COPY backend/app /app/app
COPY knowledge /app/knowledge
ENV IT_OPS_ROOT=/app
ENV IT_OPS_DB=/app/data/it_ops.db
EXPOSE 8030
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8030"]

