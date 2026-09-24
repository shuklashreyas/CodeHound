ARG PYTHON_BASE=python:3.12-slim
FROM ${PYTHON_BASE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --create-home codehound \
    && mkdir /app/data && chown codehound:codehound /app/data
ENV CODEHOUND_DATA_DIR=/app/data
USER codehound
EXPOSE 8000
CMD ["uvicorn", "codehound.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
