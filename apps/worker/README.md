# apps/worker — toujours sans exécuteur, et voici ce qui tourne à la place

**Ce répertoire ne contient aucun code, et ce n'est plus un simple « réservé ».**
Le premier traitement hors requête existe ; il n'a simplement pas la forme d'un
démon, parce qu'il n'y a **rien à dépiler**.

## Ce qui est faux dans ce que ce fichier disait avant

Il annonçait que « le modèle de données prévoit déjà `ProcessingJob` ». C'est
inexact : il n'existe ni modèle ni table de ce nom — seulement une mention dans
`docs/DATA_MODEL.md` parmi les tables PRÉVUES. Redis est bien démarré par
`infra/docker-compose.yml`, et personne ne le consomme.

## Ce qui tourne hors requête, aujourd'hui

| Pièce | Rôle |
| --- | --- |
| `apps/api/src/metreo_api/services/travail_documentaire.py` | L'unité de travail : prendre une étape, l'exécuter, consigner son issue. Deux portes — une pour une requête HTTP, qui reste dans sa transaction unique ; une pour un processus séparé, qui valide la prise pour qu'un second la voie |
| `scripts/lire_un_plan.py` | La porte d'entrée : UNE révision nommée, un délai armé par le processus lui-même, et il s'arrête |

L'état par étape vit dans `DocumentStepRun`, dont la clé d'idempotence est
`(organization_id, revision_id, step, pipeline_version, prompt_version,
model_version)`. Une étape déjà réussie n'est pas rejouée ; une étape en échec
se relance par `retry_failed_step_run`, qui incrémente la tentative **sans
changer cette clé** — bouger un numéro de version créerait une seconde ligne et
perdrait l'historique.

## Pourquoi pas un démon

Trois raisons, dans cet ordre.

1. **Il n'y a pas de file.** Un démon qui tourne en boucle sur une table
   inexistante est un faux positif dans l'architecture : il donnerait à lire
   qu'un travail asynchrone existe.
2. **Le délai ne s'arme que depuis un processus.** Mesuré : une seule entité de
   hachure a tenu un rendu 10,8 s, et une hachure plus large fait tuer le
   processus par le noyau sans qu'aucune exception ne soit levée. Aucune borne
   interne n'interrompt une boucle déjà commencée ; seul un signal posé par le
   fil principal d'un processus le fait — ce qu'un script est et qu'un serveur
   n'est pas.
3. **Un processus par fichier est la règle posée** par
   `docs/adr/0007-lecture-de-plans.md` §5, pour que l'analyse d'un plan reçu
   n'ait ni réseau, ni accès à la base d'un autre tenant, ni durée illimitée.

## Ce que ce répertoire accueillera

Un exécuteur, quand une file existera : une table de travaux, une prise
atomique, une reprise après panne et une observabilité par étape. Les trois
sont une tranche à part entière, et le contrat attendu ne change pas
(`docs/ARCHITECTURE.md`) : observable, relançable, idempotent, et corrélé à la
requête d'origine par `X-Request-Id`.
