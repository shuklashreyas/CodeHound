# Historic Requests revisions use collections.Mapping, removed in Python 3.10.
# This is an offline benchmark runtime, not the CodeHound API environment.
ARG PYTHON_BASE=python:3.9-slim
FROM ${PYTHON_BASE}
RUN python -m pip install --no-cache-dir --index-url https://pypi.org/simple pytest==8.4.2 ruff==0.11.13
ENV PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
