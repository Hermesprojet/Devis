# ADR 0002 — Isolation multi-tenant

- **Statut** : accepté
- **Date** : 2026-08-20

## Contexte

« Les données de deux entreprises clientes doivent être strictement isolées. »
Trois approches classiques : une base par client, un schéma par client, ou une
colonne discriminante avec Row-Level Security.

## Décision

**Colonne `organization_id` sur toute table métier, filtrage imposé par la
couche service, et 404 — jamais 403 — pour une ressource d'un autre tenant.**

Trois mécanismes se cumulent :

1. **L'organisation vient du jeton, jamais de la requête.** `current_context`
   (`security/auth.py`) est le seul chemin qui produit un `TenantContext`. Aucun
   handler n'accepte d'`organization_id` en paramètre ou dans un corps.
2. **Toute lecture passe par `get_owned`** (`services/tenant.py`), qui applique
   le filtre et le `deleted_at IS NULL`. Écrire une requête sans lui est visible
   en revue.
3. **Les écritures croisées sont refusées.** Un poste de bordereau qui pointe un
   prix d'un autre tenant est rejeté en 404 à l'écriture, pas au calcul.

Le **404** est délibéré : répondre 403 confirmerait qu'un identifiant existe.

## Alternatives écartées

**Une base par client.** Isolation la plus forte, mais migrations à exécuter
*n* fois, connexions multipliées, et administration lourde dès quelques dizaines
de clients. Reste possible plus tard : la colonne discriminante ne l'empêche
pas, l'inverse serait plus coûteux.

**Row-Level Security PostgreSQL maintenant.** C'est la bonne défense en
profondeur, mais elle rendrait la suite de tests inexécutable sur SQLite, donc
le projet non démarrable sans Docker. Reportée en phase 5, où elle viendra
**s'ajouter** au filtrage service — pas le remplacer.

## Conséquences

- Une requête qui oublie le filtre est une faille. Il n'existe pas de filet base
  de données aujourd'hui : c'est la principale faiblesse assumée.
- Elle est compensée par le test : `apps/api/tests/test_tenant_isolation.py`
  couvre lecture, écriture, suppression, export, référence croisée, journal
  d'audit et falsification de jeton.
- **Règle de non-régression :** toute nouvelle ressource appartenant à un tenant
  doit recevoir un test « l'organisation B reçoit 404 » dans ce fichier.
- Les contraintes d'unicité incluent `organization_id` dès qu'une collision
  entre tenants est plausible (référence de projet, nom de bibliothèque).
