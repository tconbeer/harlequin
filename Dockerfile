FROM python:3-slim

COPY dist/*.whl .
RUN pip install $(find . -name "*.whl")
RUN rm *.whl

RUN mkdir /data
WORKDIR /data

CMD ["harlequin"]