FROM python:3.8-alpine

LABEL maintainer="Kyunghan (Paul) Lee <contact@paullee.dev>"

WORKDIR /app

COPY . /app

ENV YARL_NO_EXTENSIONS=0
ENV MULTIDICT_NO_EXTENSIONS=0

RUN pip install --trusted-host pypi.python.org -r requirements.txt

CMD ["python", "bot.py"]
