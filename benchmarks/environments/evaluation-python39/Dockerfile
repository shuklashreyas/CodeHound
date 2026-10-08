# Trusted dependencies for historical Django/SymPy task probes.
# Candidate repositories are mounted read-only and are never installed.
ARG PYTHON_BASE=python:3.9-slim
FROM ${PYTHON_BASE}
RUN python -m pip install --no-cache-dir --index-url https://pypi.org/simple \
    pytest==8.4.2 ruff==0.11.13 mpmath==1.3.0 pytz==2025.2 \
    sqlparse==0.5.3 asgiref==3.8.1
ENV PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
