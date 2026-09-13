# Image applicative bot-td (Phase 28).
# Contient l'API FastAPI + le bot Discord dans un seul process (app/main.py).
# Aucun secret n'est intégré à l'image : tout vient des variables
# d'environnement fournies par docker-compose (.env non copié, voir .dockerignore).

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dépendances d'abord pour profiter du cache Docker.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code applicatif et migrations uniquement.
COPY alembic.ini .
COPY migrations/ migrations/
COPY app/ app/
COPY engine/ engine/
# Planning macro versionné (dépendance de production du moteur, MACRO.md §4) :
# uniquement ce fichier — data/cache et autres données locales n'entrent pas.
COPY data/macro/events.json data/macro/events.json

# Exécution sans privilèges.
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Les migrations sont appliquées au démarrage (Phase 29 : jamais de
# modification directe des tables), puis l'API démarre.
# Linux : pas besoin de --loop app.main:selector_loop (contournement Windows).
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
