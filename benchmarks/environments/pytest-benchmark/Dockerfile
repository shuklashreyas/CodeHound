# Trusted dependencies only: benchmark candidate pytest is loaded read-only from /workspace/src.
# pluggy 0.13.1 satisfies both historical pytest 4.x (<1) and pytest 7.x (<2) requirements.
ARG PYTHON_BASE=python:3.9-slim
FROM ${PYTHON_BASE}
RUN python -m pip install --no-cache-dir --index-url https://pypi.org/simple \
    py==1.11.0 pluggy==0.13.1 attrs==21.4.0 iniconfig==1.1.1 \
    packaging==21.3 pyparsing==3.0.9 tomli==2.0.1 atomicwrites==1.4.1 \
    more-itertools==8.14.0 wcwidth==0.2.5 six==1.16.0 setuptools==62.6.0 numpy==1.23.5 importlib-metadata==4.11.4 zipp==3.8.1
ENV PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
