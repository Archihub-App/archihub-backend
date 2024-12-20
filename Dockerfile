FROM python:3.9.6

WORKDIR /app

RUN pip install --upgrade pip

COPY . .

RUN sed -i 's/\r$//' generateRequirements.sh
RUN sed -i 's/\r$//' start.sh

RUN bash generateRequirements.sh

RUN pip install -r requirements.txt

RUN pip install gunicorn

RUN apt-get update && apt-get install -y libsndfile1 && apt-get install -y ffmpeg && apt-get install -y poppler-utils && apt-get install -y libreoffice

RUN chmod +x /app/start.sh