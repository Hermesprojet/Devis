# scripts — outils d'exploitation et garde-fous

Ce fichier annonçait un répertoire « vide volontairement », en réservant la
place pour « la purge RGPD » et quelques autres. Il en contient sept, dont
cette purge. Un README qui décrit l'état d'hier est pire qu'un README absent :
on le croit.

Le principe qu'il posait tient toujours : **un script d'enveloppe qui relaie
une commande d'une ligne serait une indirection de plus à maintenir.** Les
gestes courants restent donc des commandes, documentées dans `README.md` et
`docs/TESTING.md` :

```bash
alembic -c alembic.ini upgrade head       # migrations
python -m metreo_api.seed [--reset]       # jeu de démonstration fictif
pytest / ruff / mypy / npm run build      # qualité
```

Ce qui vit ici fait quelque chose qu'une ligne ne fait pas.

## Outils d'exploitation

| Script | Ce qu'il fait |
| --- | --- |
| `purger_organisation.py` | Enregistre une décision de conservation, puis détruit une organisation — la **seule** porte, car ni la purge ni la décision ne passent par HTTP |
| `migration_roundtrip.py` | Crée sa propre base, y joue montée / descente / remontée, la détruit |
| `lire_un_plan.py` | Lit UN plan DXF déposé, hors requête HTTP : son constat, son image et ses mesures proposées. Il n'y a pas de file d'attente — ce script traite une révision nommée, et s'arrête. Sa raison d'être est le délai : l'API analyse dans la requête et refuse donc au-delà de `METREO_PLAN_SYNC_MAX_BYTES`, là où personne n'attend ici |
| `essai_sur_plans_reels.py` | L'instrument de l'essai sur plans réels, contre la pile locale de `ops/essai_local.sh` : chantier, client et prix **fictifs**, dépôt et lecture, aides au pointage, constat DXF avec recensement indépendant, compte rendu attendu / mesuré / écart. Il ne parle qu'à une API sur la boucle locale et refuse d'écrire dans le dépôt |

`purger_organisation.py` montre ce qu'il va détruire avant de le faire et
n'agit que sur `--confirmer`. Il ne peut pas passer outre les refus du service :
sans décision de conservation en vigueur, sans motif pris dans la liste fermée,
ou avec un devis encore dans sa durée, il s'arrête. Voir
`docs/adr/0006-conservation-et-effacement.md`.

## Garde-fous

Appelés par le `Makefile` et par la CI. Ils ne réparent rien : ils refusent.

| Script | Ce qu'il refuse |
| --- | --- |
| `check_disposable_database.py` | Une URL qui ne nomme pas une base jetable — la suite y crée et détruit un schéma par test |
| `check_clean_install.py` | Un environnement vierge qui ne démarrerait pas depuis les seuls manifestes |
| `check_skills.py` | Un skill dont le frontmatter, les chemins cités ou les données volatiles ont dérivé |
| `schema_drift_gate.py` | Un schéma migré qui ne correspond plus aux modèles, ou deux têtes Alembic |
| `verify_dependency_closure.py` | Un environnement dont les versions installées n'honorent pas les manifestes |
| `verifier_provenance_du_candidat.py` | Un candidat portant un commit qu'aucune branche fusionnée n'emporte |
| `_url_safety.py` | *(module partagé)* Analyse une URL de base sans jamais journaliser son mot de passe |

`verifier_provenance_du_candidat.py` n'est pas appelé par le `Makefile` : il
lit l'historique de plusieurs branches distantes, ce qu'un arbre de travail
quelconque ne porte pas. Il se lance à la main, avant de proposer un candidat.

Il compare les `patch-id` des commits du candidat à ceux des branches de PR. Un
commit qui n'est dans aucune ne se perd que si la branche qui le porte n'est pas
fusionnée — le script situe donc le candidat par rapport à la branche que la
demande de fusion unique amène sur `main`, et ne parle de perte que dans ce cas.
Il ne décide pas la mise en ligne : ce qui ferme l'écart entre « éprouvé » et
« en ligne » est la comparaison de contenu de
`docs/FICHE_DE_DECISION_MISE_EN_LIGNE.md`.

Mesuré sur le candidat `2d18299` : 70 commits hors fusion, dont 9 écrits sur les
branches de candidat elles-mêmes, que la fusion emporte. Avant correction de son
candidat par défaut et de sa liste de branches, il en annonçait 31 perdus — dont
22 à tort. Un contrôle qui crie à tort cesse d'être lu.

## Règle

Un script qui touche à des données **montre d'abord, agit ensuite**, et laisse
une trace de ce qu'il a fait. Un script qui contrôle sort en échec avec la
raison en clair, jamais en corrigeant silencieusement.
