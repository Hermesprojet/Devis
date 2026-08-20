# scripts — réservé

**Vide volontairement.** Tout ce qui est nécessaire aujourd'hui passe par des
commandes documentées dans `README.md` et `docs/TESTING.md` :

```bash
alembic -c alembic.ini upgrade head       # migrations
python -m metreo_api.seed [--reset]       # jeu de démonstration fictif
pytest / ruff / mypy / npm run build      # qualité
```

Ce répertoire accueillera les scripts d'exploitation lorsqu'il y en aura de
vrais : sauvegarde et restauration, migration de données, chargement d'un pack
régional validé, purge RGPD. Un script d'enveloppe qui se contente de relayer
une commande d'une ligne serait une indirection de plus à maintenir.
