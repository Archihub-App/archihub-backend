FROM python:3.11

WORKDIR /app

RUN apt-get update && apt-get install -y libsndfile1 && apt-get install -y ffmpeg && apt-get install -y poppler-utils && apt-get install -y libreoffice

RUN cd /tmp && \
    latest_filename=$(curl -s https://exiftool.org/ | grep -E -o 'Image-ExifTool-[0-9]+.[0-9]+.tar.gz' | head -1) && \
    curl -O "https://exiftool.org/$latest_filename" && \
    tar -xzf "$latest_filename" && \
    dir_name=$(echo "$latest_filename" | sed 's/.tar.gz$//') && \
    cd "$dir_name" && \
    perl Makefile.PL && \
    make && \
    make install && \
    cd / && \
    rm -rf /tmp/Image-ExifTool-*

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