# Dependencies only; candidate Sphinx is imported read-only from /workspace.
ARG PYTHON_BASE=codehound-requests-benchmark:pilot
FROM ${PYTHON_BASE}
RUN python -m pip install --no-cache-dir --no-deps --index-url https://pypi.org/simple \
    docutils==0.17.1 Jinja2==3.1.2 MarkupSafe==2.1.2 Pygments==2.11.2 \
    snowballstemmer==2.2.0 Babel==2.10.3 pytz==2022.1 alabaster==0.7.12 imagesize==1.3.0 \
    requests==2.27.1 urllib3==1.26.20 charset-normalizer==2.0.12 idna==3.4 certifi==2024.2.2 \
    packaging==21.3 pyparsing==3.0.9 importlib-metadata==4.11.4 zipp==3.8.1 \
    sphinxcontrib-applehelp==1.0.4 sphinxcontrib-devhelp==1.0.2 sphinxcontrib-jsmath==1.0.1 \
    sphinxcontrib-htmlhelp==2.0.1 sphinxcontrib-serializinghtml==1.1.5 sphinxcontrib-qthelp==1.0.3
ENV PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
