FROM python:3.11

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libsndfile1 \
    ffmpeg \
    poppler-utils \
    libvips-dev \
    libreoffice \
    libimage-exiftool-perl \
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