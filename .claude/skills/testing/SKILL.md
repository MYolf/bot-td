---
name: testing
description: >-
  Stratégie de tests du projet avec pytest, pytest-asyncio et httpx : tests
  du webhook, de la validation, de la déduplication, du paper trading, des
  commandes Discord et des défaillances (DB, Discord). Utiliser ce skill dès
  qu'on écrit ou exécute des tests dans tests/, ou avant de considérer une
  phase comme terminée.
---

# Testing — pytest

## Approche

Développement **test-oriented** : pour chaque fonctionnalité, écrire les tests
en même temps (idéalement avant) que le code. `pytest` doit passer **avant
chaque déploiement** et avant de considérer une phase comme terminée
(voir Projet.md).

## Stack

```text
pytest              moteur de tests
pytest-asyncio      tests asynchrones
httpx               appels HTTP contre l'app FastAPI sans serveur
```

Fixture client (AsyncClient + `ASGITransport`) : voir exemple dans le skill
`fastapi`. Fixture DB : base de test dédiée (ou transaction rollback par
test), jamais la base de développement/production.

## Isolation

- **Discord toujours mocké** dans les tests : patcher `discord_service` /
  le bot ; vérifier que l'embed envoyé contient les bons champs (via le mock).
- **DB** : soit un PostgreSQL de test (docker) avec migrations appliquées,
  soit mocks du repository pour les tests unitaires du processor.
- Aucun test ne doit envoyer un vrai message Discord ni appeler l'extérieur.
- `TRADINGVIEW_WEBHOOK_SECRET` de test défini via fixture/env de test.

## Catalogue obligatoire (mapping fichiers)

```text
tests/test_webhook.py        webhook valide, secret invalide (401), secret absent,
                             JSON invalide, réponse rapide 2xx pour rejets métiers
tests/test_validation.py     BUY valide, SELL valide, symbole/strategy/timeframe non
                             autorisés, prix <= 0, timestamp expiré, champs manquants
tests/test_deduplication.py  même signal_uid envoyé 2x => un seul SENT, un seul
                             envoi Discord ; uid identique quel que soit l'ordre d'arrivée
tests/test_database.py       insertion, contrainte UNIQUE, statuts, repository (stats)
tests/test_discord.py        format des embeds (LONG/SHORT, RR, UTC), commandes slash
                             (mockées), erreur d'envoi Discord => statut ERROR
tests/test_paper_trading.py  ouverture BUY/SELL, TP atteint (+RR), SL atteint (-1R),
                             calcul du résultat en R, statistiques (win rate, total R)
```

Cas transverses imposés par le cahier des charges :

- BUY avec SL >= entry (incohérent) => `REJECTED`
- SELL avec SL <= entry => `REJECTED`
- TP du mauvais côté => `REJECTED`
- Database failure lors du traitement => erreur gérée, logguée, pas de crash
- Discord failure après stockage => signal conservé en base (`ERROR`)

## Style

```python
async def test_buy_with_stop_above_entry_is_rejected(client, valid_buy_payload):
    payload = {**valid_buy_payload, "stop_loss": str(float(payload_price) + 500)}
    resp = await client.post("/webhook/tradingview", json=payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "REJECTED"
```

- Nommage explicite : `test_<sujet>_<cas>_<résultat_attendu>`.
- Fixtures réutilisables : `valid_buy_payload`, `valid_sell_payload`,
  `client`, `db_session` — dans `tests/conftest.py`.
- Un test = un comportement ; pas d'assertions d'implémentation (tester les
  résultats observables : code HTTP, statut métier, contenu de l'embed).
- Tester aussi les **chemins d'erreur**, pas seulement les cas heureux.

## Commandes

```bash
pytest                              # tout
pytest tests/test_validation.py     # un fichier
pytest tests/test_validation.py::test_buy_with_stop_above_entry_is_rejected  # un test
pytest -x                           # stop au premier échec
pytest --asyncio-mode=auto          # si configuré ainsi
```

## Critères de validation

- [ ] Tous les tests passent (`pytest` vert) avant de passer à la phase suivante.
- [ ] Le catalogue obligatoire ci-dessus est couvert au fil des phases.
- [ ] Aucun test ne dépend de services externes réels (Discord, TradingView).
- [ ] Les doublons sont testés avec la vraie contrainte DB (UNIQUE).
- [ ] Les cas de défaillance (DB, Discord) sont testés et loggués.
