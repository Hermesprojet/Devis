# apps/worker — réservé (phase 2)

**Vide volontairement.** Aucun worker n'existe aujourd'hui, et aucune opération
de la phase 1 ne dépasse la seconde : démarrer un worker qui ne traite rien
serait un faux positif dans l'architecture.

Ce répertoire accueillera les traitements asynchrones de la phase 2 et
suivantes : OCR, extraction documentaire, conversions CAO, exports lourds. Le
modèle de données prévoit déjà `ProcessingJob` (voir `docs/DATA_MODEL.md`), et
`infra/docker-compose.yml` démarre Redis pour les files d'attente.

Contrat attendu de tout travail asynchrone (`docs/ARCHITECTURE.md`) :
observable, relançable, idempotent, et corrélé à la requête d'origine par
l'identifiant `X-Request-Id`.
