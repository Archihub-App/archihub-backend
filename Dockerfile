FROM python:3.9.6

WORKDIR /app

RUN pip install --upgrade pip

COPY requirements.txt requirements.txt

RUN pip install -r requirements.txt

RUN apt-get update && apt-get install -y libsndfile1 && apt-get install -y ffmpeg

COPY . .
