FROM python:3.13-slim

WORKDIR /app

COPY server.py .
COPY schema.sql .
COPY index.html .
COPY app/ app/
COPY tools/ tools/

RUN mkdir -p data/images

EXPOSE 8890
ENV HOST=0.0.0.0
ENV PORT=8890

VOLUME ["/app/data", "/app/database.db"]

CMD ["python", "server.py"]
