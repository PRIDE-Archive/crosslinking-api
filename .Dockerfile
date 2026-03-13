FROM python:3.11-slim as base

# Setup env
ENV LANG C.UTF-8
ENV LC_ALL C.UTF-8
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONFAULTHANDLER 1
ENV PYTHONUNBUFFERED 1

# Install pipenv and compilation dependencies
RUN python3 -m pip install wheel pip --upgrade && pip install pipenv
RUN apt-get update && apt-get install

COPY Pipfile .
COPY Pipfile.lock .
RUN pipenv requirements > /tmp/requirements.txt && pip install -r /tmp/requirements.txt

# Create and switch to a new user to ensure security
RUN useradd --create-home appuser
WORKDIR /home/appuser
USER appuser
RUN mkdir -p /home/appuser/mzId_convertor_temp
RUN mkdir -p /home/appuser/logs

# Install application into container
COPY app ./app
COPY *.py .
COPY default.database.ini .
COPY logging.ini .
COPY .kubernetes.yml .