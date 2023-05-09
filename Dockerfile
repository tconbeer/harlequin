FROM python:3-slim

RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY dist/*.whl .
RUN pip install $(find . -name "*.whl")
RUN rm *.whl

RUN mkdir /data
WORKDIR /data

CMD ["harlequin"]