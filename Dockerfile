FROM python:3.12-slim

RUN adduser --disabled-password --gecos '' appuser

WORKDIR /app

# Dependencies first so this layer is cached until requirements.txt changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code last, since it changes far more often than dependencies
COPY etl_pokeapi.py pokeapi_schema.sql dashboard.py entrypoint.sh ./
RUN chmod +x entrypoint.sh && mkdir -p data

# appuser needs to write to /app/data at runtime (builds pokeapi.db there on first run)
RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8501

ENTRYPOINT ["./entrypoint.sh"]
