# Stratégie de tests

## Pyramide

| Niveau | Où | Nombre | Durée | Ce qu'il protège |
| --- | --- | --- | --- | --- |
| Domaine | `packages/domain/tests` | 61 | ~0,1 s | L'exactitude des montants, unités et conversions |
| Intégration API | `apps/api/tests` | 86 | ~27 s | Isolation, permissions, contrats HTTP, migrations |
| Web | `apps/web` | typage + build | ~30 s | Contrats TypeScript, compilation |
| Parcours navigateur | — | manuel | — | Vérifié au navigateur, **pas encore en CI** (dette identifiée) |

Le niveau domaine est délibérément le plus fourni : c'est là que vit l'argent, et
il n'a besoin d'aucune infrastructure.

## Ce qui n'est pas simulé

Les tests d'API tournent **à travers les migrations Alembic réelles**, pas
`create_all`. Une migration qui ne reproduit pas les modèles fait échouer la
suite au lieu de passer inaperçue jusqu'à la production.

Le seed est le même code que la démonstration. Si le jeu de démonstration casse,
la suite casse.

## Scénarios d'acceptation

Les seize scénarios du cahier des charges, et leur état :

| # | Scénario | État | Test |
| --- | --- | --- | --- |
| 1 | B ne lit aucune donnée de A, même en appelant l'API | ✅ | `test_tenant_isolation.py` (13 tests) |
| 2 | 5 prix valides + 2 lignes fautives : erreurs avant écriture, import confirmé crée exactement 5 lignes | ✅ | `test_price_import.py` |
| 3 | Poste « excavation de terres » de 120 m³, détail visible et reproductible | ✅ | `test_pricing.py`, `test_estimating.py` |
| 4 | m³ → tonne refusé sans densité sourcée | ✅ | `test_units.py`, `test_estimating.py` |
| 5 | Poste sans prix signalé et bloquant si la règle l'exige | ✅ | `test_estimating.py` |
| 6 | Le gel crée une version immuable avec sa bibliothèque | ✅ | `test_estimating.py` |
| 7 | Une modification ultérieure d'un prix ne change pas la version gelée | ✅ | `test_estimating.py` |
| 8 | L'export reprend référence, version, date, unités, montants, hypothèses | ✅ | `test_estimating.py` |
| 9 | Les modifications importantes apparaissent dans l'audit | ✅ | `test_audit.py` |
| 10 | Le système reste utilisable si l'IA est désactivée | ✅ | `test_platform.py` |
| 11 | Un PDF scanné est traité en arrière-plan, état visible | ⛔ | Phase 2 |
| 12 | Une clause extraite renvoie à la bonne page/zone | ⛔ | Phase 2 |
| 13 | Confiance trop basse ⇒ aucune donnée approuvée | ⛔ | Phase 2 |
| 14 | Une instruction malveillante dans un PDF ne change rien | ⛔ | Phase 2 |
| 15 | Aucun message ne part sans confirmation explicite | ⛔ | Phase 4 |
| 16 | Une offre dans une autre unité n'est comparée qu'après conversion tracée | ⛔ | Phase 4 |

Les scénarios 11 à 16 portent sur des fonctions non implémentées. Ils ne sont
pas « en échec » : ils n'ont pas de sujet.

## Écrire un test ici

**Nom.** Le nom énonce la règle, pas la mécanique :
`test_volume_to_mass_is_refused_without_density`, pas `test_convert_2`.

**Un fait par test.** Un test qui vérifie six choses ne dit pas laquelle a cassé.

**Nombres recalculés à la main.** Les tests du moteur recalculent la valeur
attendue depuis les entrées dans le corps du test, plutôt que de figer une sortie
observée. Un littéral copié depuis une exécution ne prouve rien — c'est ce qui a
révélé une erreur de raisonnement pendant le développement de la chaîne de marge.

**Sécurité : le chemin de l'attaque, pas celui de l'UI.** Les tests d'isolation
appellent l'API directement avec un jeton valide et un identifiant appartenant à
quelqu'un d'autre.

## Commandes

```bash
python -m pytest packages/domain/tests -q      # domaine
cd apps/api && python -m pytest -q             # API
cd apps/api && python -m pytest -q -k isolation

ruff format --check packages/domain apps/api/src apps/api/tests
ruff check       packages/domain apps/api/src apps/api/tests
mypy             packages/domain/src/metreo_domain apps/api/src/metreo_api

cd apps/web && npm run typecheck && npm run build
```

Sur PostgreSQL :

```bash
docker compose -f infra/docker-compose.yml up -d db
export METREO_DATABASE_URL="postgresql+psycopg://metreo:metreo@localhost:5432/metreo"
cd apps/api && python -m pytest tests/test_tenant_isolation.py -q
```
