FROM python:3.12.7

ENV PYTHONUNBUFFERED 1

RUN mkdir /app
WORKDIR /app
COPY requirements.txt /app/
RUN pip install --upgrade pip
RUN pip install -r /app/requirements.txt
RUN apt-get update -y
RUN apt-get -y install postgresql-client-15
