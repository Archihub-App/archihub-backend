FROM python:3.11

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libsndfile1 \
    ffmpeg \
    poppler-utils \
    libvips-dev \
    libreoffice \
    libimage-exiftool-perl \
    # Build dependencies for python-ldap, which is a C extension rather than a
    # pure-Python package and will not install without these headers. Required
    # by the LDAP login path (app/api/auth does `import ldap`). Note that
    # `ldap3` - a different, pure-Python library with an incompatible API - is
    # NOT a substitute for it.
    libldap2-dev \
    libsasl2-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip

COPY app/plugins ./app/plugins
COPY requirements.txt generateRequirements.sh start.sh ./

RUN sed -i 's/\r$//' generateRequirements.sh
RUN bash generateRequirements.sh

RUN pip install -r requirements.txt

RUN pip install gunicorn

COPY . .

RUN sed -i 's/\r$//' start.sh
RUN sed -i 's/\r$//' start_celery.sh

RUN chmod +x /app/start.sh
RUN chmod +x /app/start_celery.sh