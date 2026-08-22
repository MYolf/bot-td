---
name: fastapi
description: >-
  Couche HTTP du projet avec FastAPI : application, lifespan, routes,
  endpoint POST /webhook/tradingview, Pydantic, codes HTTP, sécurité, logging,
  architecture modulaire et tests HTTP avec httpx. Utiliser ce skill dès
  qu'on travaille sur app/main.py, app/api/, les dépendances FastAPI, les
  schémas Pydantic d'entrée ou les tests du endpoint webhook.
---

# FastAPI — Couche HTTP

## Rôle

La couche HTTP est **mince** : elle reçoit la requête, délègue au Signal
Engine (voir skill `signal-engine`) et retourne une réponse. **Aucune logique
métier dans les routes.**

## Structure

```text
app/main.py               création de l'app, lifespan (DB + bot Discord)
app/config/settings.py    configuration typée (pydantic-settings, .env)
app/api/tradingview.py    routeur du webhook
```

- `app/api/` ne contient que des routeurs ; la logique vit dans
  `app/signals/` et `app/services/`.
- Configuration via `pydantic-settings` : classe typée, lue depuis `.env`/
  variables d'environnement. **Jamais de valeur secrète dans le code.**

## Application et démarrage

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI):
    # démarrage : init DB, lancer le bot Discord en tâche de fond
    yield
    # arrêt : fermer proprement le bot et les sessions DB

app = FastAPI(title="bot-td", lifespan=lifespan)
app.include_router(tradingview_router)
```

- Le bot Discord tourne **dans le même process** que l'API (voir skill
  `discord-bot`) : lancé via `asyncio.create_task` dans le lifespan, annulé
  proprement à l'arrêt.
- Développement : `uvicorn app.main:app --reload`.
- Endpoint de santé : `GET /health` -> `{"status": "ok"}` (utilisé par
  `/status` Discord et le monitoring).

## Endpoint principal

```python
@router.post("/webhook/tradingview")
async def receive_signal(payload: TradingViewSignal, response: Response):
    ...
```

### Codes HTTP

| Cas                            | Code   | Remarque                                          |
|--------------------------------|--------|---------------------------------------------------|
| Signal validé et notifié       | 200    | statut métier `SENT` dans le corps                |
| Signal invalide (métier)       | 200    | statut `REJECTED` : TradingView attend un 2xx     |
| Doublon                        | 200    | statut `DUPLICATE`                                |
| Secret absent/invalide         | 401    | réponse générique, comparaison en temps constant  |
| JSON illisible / body invalide | 400/422| loggué                                            |
| Défaillance interne (DB...)    | 500    | loggué, jamais silencieux                         |

Rappel : TradingView considère un échec si la réponse n'est pas 2xx. Les
rejets métiers doivent donc rester en 200 avec un statut explicite, pour ne
pas polluer le statut de l'alerte TradingView avec des cas normaux.

### Sécurité

- Le secret est dans le **body** (TradingView ne permet pas d'en-têtes
  personnalisés) : vérifié par le Signal Engine avec
  `secrets.compare_digest`.
- HTTPS obligatoire en production (reverse proxy / domaine réel).
- Rate limiting du endpoint recommandé (ex. middleware simple par IP).
- Aucun endpoint d'administration non protégé ; pas de CORS inutile
  (pas de frontend).
- Réponses d'erreur **génériques** : jamais de secret, jamais de détail
  d'exception brute en production.

## Pydantic

- Modèle d'entrée `TradingViewSignal` : voir le schéma dans le skill
  `signal-engine`. Points de vigilance :
  - `price`, `stop_loss`, `take_profit` acceptés en `str` **et** en nombre
    (TradingView envoie des chaînes) => validateur de conversion + `> 0`.
  - `action` : `Literal["BUY", "SELL"]`.
  - `timestamp` : datetime ISO 8601, converti en UTC.
- La validation Pydantic (422) ne remplace pas la validation métier
  (listes blanches, cohérence SL/TP) : les deux couches existent.

## Async

- Tous les handlers en `async def` ; les dépendances I/O (DB, Discord) sont
  asynchrones.
- **Ne jamais bloquer l'event loop** : pas d'appels bloquants dans les
  handlers (aucune lib synchrone de DB, aucun `time.sleep`).
- Les traitements potentiellement lents restent rapides ou sont découplés :
  TradingView attend une réponse en quelques secondes.

## Gestion des erreurs et logs

- Exception handlers globaux : toute exception non gérée => 500 + log ERROR
  avec contexte (uid, stratégie, symbole) mais **sans secrets**.
- Un logger nommé par module (`logging.getLogger(__name__)`), niveau depuis
  `LOG_LEVEL`, format structured friendly (voir skill `signal-engine`).

## Tests HTTP

Avec `httpx` + `pytest-asyncio` (voir skill `testing`) :

```python
@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

async def test_webhook_ok(client, ...):
    resp = await client.post("/webhook/tradingview", json=valid_payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "SENT"
```

- Le service Discord et le repository sont **mockés/patchés** dans les tests
  HTTP : on teste la couche web, pas Discord.
- Cas obligatoires : secret invalide, JSON invalide, BUY/SELL valides, SL/TP
  incohérents, timestamp expiré, doublon.

## Critères de validation

- [ ] Aucune logique métier dans `app/api/` (délégation au Signal Engine).
- [ ] Codes HTTP conformes au tableau ci-dessus.
- [ ] Validation Pydantic stricte (str ou nombre, Literal, datetime).
- [ ] Secret vérifié en temps constant, jamais loggé ni retourné.
- [ ] App testable avec httpx sans serveur ni Discord réels.
- [ ] `GET /health` fonctionnel.
