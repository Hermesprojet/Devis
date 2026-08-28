# ADR 0001 — Pile technique

- **Statut** : accepté
- **Date** : 2026-08-20

## Contexte

Le dépôt était vide. Le cahier des charges propose une pile de référence
(monorepo, Next.js, FastAPI, PostgreSQL, workers, S3, Redis) et demande de
justifier tout écart. Il faut par ailleurs que le produit démarre **sans aucune
clé payante ni service externe**.

## Décision

| Couche | Choix | Écart au cahier des charges |
| --- | --- | --- |
| Monorepo | `apps/` + `packages/` + `infra/` + `docs/` + `fixtures/` | Aucun |
| Domaine | Paquet Python pur, sans dépendance : `packages/domain` | Ajout : le cahier des charges ne l'imposait pas |
| API | FastAPI, Pydantic v2, SQLAlchemy 2.0, Alembic | Aucun |
| Base | PostgreSQL 16 + PostGIS, **SQLite accepté en développement et en test** | Écart assumé |
| Web | Next.js 15 (App Router), React 19, TypeScript strict, **aucune bibliothèque UI** | Écart : pas de dépendance de composants |
| Files | Redis prévu, non utilisé aujourd'hui | Reporté phase 2 |
| Workers | `apps/worker` réservé, vide | Reporté phase 2 |
| Stockage objet | Non branché | Reporté phase 2 |
| Observabilité | Logs JSON + identifiant de corrélation ; OpenTelemetry non branché | Partiel |

## Justification des écarts

**Un paquet domaine séparé.** Le cahier des charges impose que les calculs
soient déterministes et testés. Le meilleur moyen de le garantir est de rendre
structurellement impossible d'appeler la base ou un LLM depuis le code de
calcul : `packages/domain` n'a aucune dépendance, donc rien à appeler. Sa suite
de tests s'exécute en 0,1 s, ce qui la rend utilisable à chaque frappe.

**SQLite en développement et en test.** Un contributeur doit pouvoir cloner et
lancer les tests sans Docker. Le risque de divergence est traité de trois
façons : un type `Amount` qui garantit l'exactitude décimale sur les deux
moteurs, des tests exécutés **à travers les migrations réelles**, et un job CI
qui rejoue migrations, seed et tests d'isolation sur PostgreSQL. `staging` et
`production` refusent SQLite au démarrage.

**Pas de bibliothèque UI.** Le produit affiche des grilles denses et des
formulaires. Une bibliothèque de composants apporterait des mégaoctets, un
système de thème et un cycle de mise à jour, pour des tableaux et des `<input>`.
Le CSS tient en un fichier, avec des variables, un contraste conforme et un
focus visible. À réévaluer quand un vrai composant complexe (éditeur type
tableur, visionneuse de plans) sera nécessaire.

**Redis et workers reportés.** Aucune opération de la phase 1 ne dépasse la
seconde. Démarrer un worker qui ne traite rien serait un faux positif dans
l'architecture. Le modèle prévoit `ProcessingJob` et le répertoire est réservé.

## Conséquences

- Toute règle de calcul ajoutée ailleurs que dans `packages/domain` est un
  défaut de revue.
- Toute construction SQL doit fonctionner sur les deux moteurs, ou être
  explicitement conditionnée au dialecte (aujourd'hui : uniquement le type
  `Amount`).
- L'ajout d'une dépendance frontend demande une justification en revue.
