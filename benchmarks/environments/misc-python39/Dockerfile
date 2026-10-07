# Trusted dependencies; repository candidates are never installed.
ARG PYTHON_BASE=python:3.9-slim
FROM ${PYTHON_BASE}
RUN python -m pip install --no-cache-dir --index-url https://pypi.org/simple \
    pytest==8.4.2 ruff==0.11.13 \
    Werkzeug==2.3.8 Jinja2==3.1.6 itsdangerous==2.2.0 click==8.1.8 \
    importlib_metadata==6.8.0 numpy==1.26.4 pandas==1.5.3 \
    matplotlib==3.6.3 scipy==1.10.1 \
    astroid==3.0.0 isort==5.12.0 mccabe==0.7.0 dill==0.3.7 \
    tomli==2.0.1 tomlkit==0.12.1 platformdirs==3.11.0
ENV PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 MPLCONFIGDIR=/tmp/mplconfig
