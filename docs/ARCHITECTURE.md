# Architecture

## Principe directeur

**Le calcul de l'argent vit dans un paquet qui ne connaît ni la base de données,
ni HTTP, ni l'IA.** Tout le reste est de la plomberie autour.

`packages/domain` est du Python pur, sans dépendance, déterministe et testable
seul en 0,1 s. Il est la seule autorité sur la monnaie, les unités et les prix.
L'API l'appelle ; l'API ne recalcule jamais rien elle-même.

## Vue d'ensemble

```text
┌──────────────────┐        HTTPS + Bearer         ┌───────────────────────────┐
│  apps/web        │ ────────────────────────────► │  apps/api  (FastAPI)      │
│  Next.js 15      │ ◄──────────────────────────── │  monolithe modulaire      │
│  App Router, TS  │        JSON / CSV / HTML      │                           │
└──────────────────┘                               │  routers/   ← HTTP        │
                                                   │  services/  ← règles      │
                                                   │  models.py  ← SQLAlchemy  │
                                                   │  security/  ← tenant+RBAC │
                                                   └───────┬───────────────────┘
                                                           │ appelle
                                                           ▼
                                              ┌────────────────────────────┐
                                              │ packages/domain            │
                                              │  money.py    Decimal       │
                                              │  units.py    conversions   │
                                              │  pricing.py  composants    │
                                              │  estimate.py agrégation    │
                                              │  (aucun I/O, aucun import  │
                                              │   de framework)            │
                                              └────────────────────────────┘
                                                           │
                          ┌────────────────────────────────┴──────────┐
                          ▼                                           ▼
                 ┌──────────────────┐                        ┌─────────────────┐
                 │ PostgreSQL 16    │                        │ Redis           │
                 │ (+PostGIS, ph.3) │                        │ files (phase 2) │
                 └──────────────────┘                        └─────────────────┘

                 ┌──────────────────────────────────────────────────┐
                 │ apps/worker — réservé phase 2                    │
                 │ OCR, extraction, conversions CAO, exports lourds │
                 │ (répertoire présent, aucun code : rien ne prétend │
                 │  fonctionner)                                    │
                 └──────────────────────────────────────────────────┘
```

## Pourquoi un monolithe modulaire

Le produit final touche quinze domaines métier. Les découper en services
maintenant coûterait des transactions distribuées sur des opérations qui sont
naturellement transactionnelles — geler une estimation écrit une version, un
instantané et un événement d'audit, en une fois.

Les frontières sont donc **des modules, pas des processus** :

| Module | Emplacement | Responsabilité |
| --- | --- | --- |
| Identité et autorisations | `security/auth.py`, `security/roles.py` | Résoudre l'appelant, appliquer les permissions |
| Accès tenant | `services/tenant.py` | Aucune ligne métier ne se lit sans filtre d'organisation |
| Paramètres régionaux | `models.RegionProfile` | Packs pays/région versionnés |
| Projets | `routers/projects.py` | Dossiers d'appel d'offres |
| Bibliothèque de prix | `services/price_import.py`, `services/composites.py` | Prix, versions, imports en deux temps, sous-détails |
| Métré | `routers/boq.py` | Bordereau, quantités, statuts |
| Étude de prix | `services/estimating.py` | Traduction base ↔ moteur, gel, instantanés |
| Exports | `services/exports.py` | CSV et aperçu de devis |
| Audit | `services/audit.py` | Journal chaîné append-only |

Ce qui doit devenir un service asynchrone le deviendra sans changer de
contrat : les opérations longues de la phase 2 sont déjà pensées comme des
travaux (`ProcessingJob` au modèle de données, `apps/worker` réservé).

## Flux : calculer une version d'estimation

```text
GET /api/v1/estimates/{id}/versions/{vid}/computation
   │
   ├─ security.auth.current_context      → qui appelle, pour quelle organisation
   ├─ services.tenant.get_owned          → l'estimation appartient-elle au tenant ?
   │
   ├─ version.status == "frozen" ?
   │     oui → estimating.recompute_from_snapshot(version.snapshot)
   │            (aucune lecture des tables de prix : le passé ne bouge pas)
   │     non → estimating.build_line_specs(...)     lignes + prix résolus
   │           estimating.inputs_from_specs(...)    validation ligne par ligne
   │           domain.compute_estimate(...)         calcul déterministe
   │
   ├─ estimating.totals_for_display(..., include_internal = context.can(COST_READ))
   │     retire déboursé sec, prix de revient, composants et chaîne de marge
   │     si l'appelant n'a pas la permission
   │
   └─ 200 { version, result }        ou 422 { code: "unpriceable_lines", problems: [...] }
```

Deux détails comptent :

1. **Une version gelée se relit, elle ne se recalcule pas depuis la base.**
   L'instantané contient les entrées *et* le résultat ; `recompute_from_snapshot`
   rejoue le moteur sur les seules entrées de l'instantané, ce qui rend le
   résultat vérifiable sans faire confiance au total stocké.
2. **Une ligne incalculable est attribuée à sa ligne.** Chaque ligne est chiffrée
   isolément d'abord ; une conversion refusée renvoie le poste concerné, pas un
   échec global anonyme.

## Flux : import de prix en deux temps

```text
POST .../imports/preview   (multipart)      POST .../imports/{batch}/commit
   │                                            │
   ├─ détection encodage + séparateur           ├─ refus si confirm != true
   ├─ correspondance des colonnes FR/NL/EN      ├─ refus si version publiée
   ├─ validation ligne par ligne                ├─ stratégie create/replace/ignore/merge
   ├─ détection des doublons                    ├─ écriture réelle dans price_items
   ├─ écriture dans import_batches / _rows      └─ audit
   └─ 200 rapport complet
        AUCUNE écriture dans la bibliothèque
```

Le fichier est stocké en zone de préparation avec son SHA-256. L'utilisateur
voit les erreurs avant que quoi que ce soit ne soit écrit — c'est le scénario
d'acceptation n° 2, et c'est testé.

## Frontières que le code fait respecter

| Règle | Mécanisme |
| --- | --- |
| L'IA n'écrit jamais dans une table approuvée | Aucun code IA n'existe. Le modèle prévoit `ExtractionProposal` → `ValidationDecision` (phase 2) : la proposition et la donnée validée sont deux tables. |
| Aucun handler ne lit un `organization_id` de la requête | `current_context` le tire du jeton ; `get_owned` filtre. Une ressource d'un autre tenant renvoie **404**, pas 403. |
| Aucune arithmétique monétaire en SQL | Le type `Amount` sérialise, il n'agrège pas. Toutes les sommes passent par `Money`. |
| Une version publiée ne se modifie plus | `_refuse_if_published` dans `routers/pricebooks.py` |
| Une quantité approuvée ne bouge pas silencieusement | `routers/boq.py` exige `override_approved` **et** un motif, puis repasse la ligne en `verified` |
| Le PDF client ne révèle pas les coûts | `exports.quote_html(include_internal=...)`, piloté par un réglage d'entreprise **et** une permission |

## Choix de la pile

| Couche | Choix | Pourquoi pas autre chose |
| --- | --- | --- |
| Domaine | Python pur, `Decimal` | Aucun ORM ni framework ne doit pouvoir influencer un montant. |
| API | FastAPI + Pydantic v2 | Contrats validés des deux côtés et OpenAPI dérivé du code, pas maintenu à côté. |
| ORM | SQLAlchemy 2.0 typé + Alembic | Migrations obligatoires ; `render_as_batch` pour rester compatible SQLite. |
| Base | PostgreSQL 16 (+PostGIS) | Recherche plein texte native pour la phase 2, géométries pour la phase 3, `pgvector` seulement si un cas RAG le justifie. |
| Web | Next.js 15, App Router, TypeScript strict | Rendu rapide de grilles denses, i18n prête, aucune dépendance UI lourde. |
| File d'attente et cache | Redis | Files de traitement de la phase 2 et cache technique — **pas** un stockage de fichiers, qui relèvera d'un `ObjectStore` compatible S3 (ADR 0003). Démarré par Compose, non utilisé aujourd'hui. |

Aucune bibliothèque de composants, aucun client d'état global, aucun ORM côté
web : le MVP n'en a pas besoin et chaque dépendance est une surface à maintenir.

## Observabilité

- Logs JSON (`logging_config.py`) avec `request_id` propagé par un middleware —
  repris de l'en-tête `X-Request-Id` ou engendré, renvoyé dans la réponse et
  inscrit dans chaque événement d'audit de la requête — et
  renvoyé dans l'en-tête `X-Request-Id`.
- Aucun corps de document, aucun jeton, aucun secret dans les logs.
- OpenTelemetry n'est pas branché ; le point d'accroche est le middleware de
  corrélation, qui existe déjà.

## Dégradation contrôlée

Le cahier des charges exige que l'édition d'un devis reste possible si l'IA ou
une intégration externe tombe. C'est structurellement vrai ici : **rien dans le
chemin de calcul n'appelle un service externe.** Le point de contrôle
`/api/v1/health` expose `ai_enabled`, et un test d'acceptation parcourt tout le
flux avec l'IA désactivée.

## `NEXT_PUBLIC_API_URL` : une valeur de construction, pas d'exécution

Next inscrit toute variable `NEXT_PUBLIC_*` **dans le JavaScript livré** au
moment du build. La conséquence est contre-intuitive et vaut d'être écrite :
définir `NEXT_PUBLIC_API_URL` sur un conteneur déjà construit n'a **aucun
effet**, le code compilé porte encore l'ancienne valeur.

Deux stratégies existaient. Nous retenons la première :

1. **URL injectée au build**, par `ARG NEXT_PUBLIC_API_URL` dans
   `infra/web.Dockerfile`. Une image est construite par environnement :

   ```bash
   docker build --build-arg NEXT_PUBLIC_API_URL=https://api.exemple.be/api/v1 \
     -f infra/web.Dockerfile -t metreo-web:prod .
   ```

2. API et front sous la même origine, avec un `rewrite` Next servant de proxy.
   Écartée pour l'instant : elle ferait transiter le jeton par le serveur Next,
   qui n'a aucune raison de le voir, et le ferait apparaître dans ses journaux.
   À reconsidérer si un déploiement impose une origine unique.

Le `docker-compose.yml` passe donc la valeur sous `build.args` et non sous
`environment`, qui laissait croire qu'un redémarrage suffisait à changer d'API.

