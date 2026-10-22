FROM python:3.13-slim
RUN pip install --no-cache-dir boto3==1.42.55 \
    && groupadd --gid 2001 mirror \
    && useradd --uid 2001 --gid 2001 --no-create-home mirror \
    && mkdir /state && chown 2001:2001 /state
WORKDIR /app
COPY importer.py /app/importer.py
USER 2001:2001
CMD ["python", "-u", "/app/importer.py"]
