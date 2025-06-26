FROM python:3.11

WORKDIR /app

RUN apt-get update && apt-get install -y libsndfile1 && apt-get install -y ffmpeg && apt-get install -y poppler-utils && apt-get install -y libreoffice
RUN pip install --upgrade pip

COPY app/plugins ./app/plugins
COPY requirements.txt generateRequirements.sh start.sh ./

RUN sed -i 's/\r$//' generateRequirements.sh
RUN bash generateRequirements.sh

RUN pip install -r requirements.txt

RUN pip install gunicorn

COPY . .

RUN sed -i 's/\r$//' start.sh

RUN chmod +x /app/start.sh