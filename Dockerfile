# ArchiHUB backend.
#
# THREE INSTALL LAYERS, IN THIS ORDER, AND THE ORDER IS THE POINT:
#
#   1. System packages the backend itself needs.
#   2. Core Python, from pyproject.toml's [project].dependencies.
#   3. Whatever each installed PLUGIN declares - system packages from its
#      packages.txt, Python from its requirements.txt, constrained by what
#      layer 2 resolved so a plugin cannot move a version the backend pinned.
#
# Layer 3 is what makes the documented workflow real: copy a plugin directory
# in, rebuild, and its dependencies are installed. Before this it was not -
# a merge script concatenated every plugin's requirements into the tracked
# requirements.txt, rewrote that file in place during the build, and stripped
# the comments in which authors had been declaring their system packages.

# PINNED. `python:3.11` is a moving tag and has already carried this image
# across a Debian major release, changing package names and the Tesseract
# tessdata path underneath a build that did not change at all.
FROM python:3.11.14-slim-bookworm

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ---------------------------------------------------------------------------
# 1. System packages
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Media and document handling used by the core file pipeline.
    ffmpeg \
    libsndfile1 \
    poppler-utils \
    libvips-dev \
    libreoffice \
    libimage-exiftool-perl \
    # `python-magic` calls into this. It was present only because the previous
    # base image happened to carry it, so a base change could have removed
    # content-type sniffing with nothing in this file to explain the breakage.
    libmagic1 \
    # Build dependencies for python-ldap, a C extension that will not install
    # without these headers. Required by the LDAP login path (`import ldap`).
    # `ldap3` is a different, pure-Python library with an incompatible API and
    # is NOT a substitute.
    libldap2-dev \
    libsasl2-dev \
    # Compilers for packages that ship as source rather than wheels. `gcc` alone
    # is not enough: a plugin building a C++ torch extension needs `g++`, and
    # its absence surfaces only at the very end of a long build as
    # "No such file or directory: 'g++'". `ninja-build` is not required, but
    # without it torch falls back to a serial distutils build that takes an
    # order of magnitude longer.
    gcc \
    g++ \
    ninja-build \
    # Needed to fetch a plugin requirement given as a git+ URL.
    git \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# 2. Core Python, from the manifest
# ---------------------------------------------------------------------------
# Copied on their own so this layer is rebuilt only when the manifest changes,
# not on every source edit.
COPY pyproject.toml ./
COPY scripts/export_core_requirements.py ./scripts/

RUN python scripts/export_core_requirements.py pyproject.toml /tmp/core-requirements.txt \
    && pip install --upgrade pip setuptools wheel \
    && pip install -r /tmp/core-requirements.txt \
    && pip install gunicorn

# The constraint set every plugin install is held to. Taken after the core
# resolve, so it reflects what is actually installed rather than what was asked
# for - a plugin may add packages, never move one of these.
RUN pip freeze --exclude-editable > /tmp/constraints.txt

# ---------------------------------------------------------------------------
# 3. Plugins
# ---------------------------------------------------------------------------
# Both trees, because both exist until the legacy stack is removed: the running
# stack is chosen at start time by ARCHIHUB_STACK, so the image has to be able
# to serve either.
COPY scripts/install_plugin_deps.sh ./scripts/
COPY app/plugins ./app/plugins
COPY archihub/plugins ./archihub/plugins

RUN sed -i 's/\r$//' scripts/install_plugin_deps.sh \
    && chmod +x scripts/install_plugin_deps.sh \
    && PLUGIN_CONSTRAINTS=/tmp/constraints.txt \
       ./scripts/install_plugin_deps.sh /app/app/plugins /app/archihub/plugins

# Where Tesseract looks for language files. Set from the directory that exists
# in THIS image rather than assumed: the path carries Tesseract's major version,
# and writing languages to a stale one succeeds while leaving them somewhere
# nothing reads.
ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
# Last, so a source change does not invalidate the dependency layers above.
# `.dockerignore` decides what this includes - notably NOT `.env`, which holds
# the signing keys and database credentials and is injected at run time.
COPY . .

RUN sed -i 's/\r$//' start.sh start_celery.sh \
    && chmod +x start.sh start_celery.sh

# Directories the application writes to. Created here rather than by a build
# script so they exist even when nothing is bind-mounted over them.
RUN mkdir -p /app/uploads /app/userfiles /app/webfiles /app/temporal
