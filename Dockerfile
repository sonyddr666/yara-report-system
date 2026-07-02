FROM python:3.13-slim
WORKDIR /app
COPY . /app
RUN chmod +x /app/entrypoint.sh
ENV HOST=0.0.0.0
ENV PORT=8880
ENV YARA_DATA_DIR=/app/data
EXPOSE 8880
ENTRYPOINT ["/app/entrypoint.sh"]
