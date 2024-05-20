FROM python:3.9.6

WORKDIR /app

RUN pip install --upgrade pip

COPY . .

RUN sed -i 's/\r$//' generateRequirements.sh

RUN bash generateRequirements.sh

RUN pip install torch==2.1.0

RUN pip install -r requirements.txt

RUN apt-get update && apt-get install -y libsndfile1 && apt-get install -y ffmpeg && apt-get install -y poppler-utils

RUN chmod +x /app/backup_mongo.sh