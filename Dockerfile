FROM python:3.13-slim

WORKDIR /app

COPY server.py .
COPY server_core.py .
COPY server_hardening.py .
COPY schema.sql .
COPY index.html .
COPY logic-bootstrap.js .
COPY logic-fixes.js .
COPY sync-hardening.js .
COPY tools/ tools/
COPY entrypoint.sh .

RUN chmod +x entrypoint.sh
RUN mkdir -p data/images db-stock data-stock

COPY database.db /app/db-stock/database.db
COPY data/ /app/data-stock/

EXPOSE 8880
ENV HOST=0.0.0.0
ENV PORT=8880

ENTRYPOINT ["/app/entrypoint.sh"]
