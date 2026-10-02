# Travailler sur des plans réels sans les commiter

Un plan de client est une pièce confidentielle d'un dossier d'étude de prix,
souvent sous clause du cahier spécial des charges. Ce document dit où il peut
vivre, où il ne peut pas, et quoi fournir pour faire progresser la lecture.

## La règle, en une ligne

**Aucun plan réel n'entre dans ce dépôt, et aucun ne sort vers un service
tiers.** Les deux interdits sont indépendants : le second vaut même pour un
service certifié, parce qu'il ajoute un sous-traitant à déclarer pour chaque
organisation cliente et une rétention hors de notre contrôle.

Ce que le dépôt contient, et peut contenir : des fichiers **fabriqués**, dans
`fixtures/plans/`, qui reproduisent un cas précis — une unité absente, une cote
sentinelle, un cycle de blocs. Chacun tient en quelques lignes et se relit.

Ce que le dépôt ne contiendra jamais : un plan d'exécution, même anonymisé.
Anonymiser un plan est illusoire — la géométrie d'un bâtiment l'identifie.

## Où déposer un plan pour qu'il soit analysé

Trois voies, de la plus simple à la plus durable.

**1. Dépôt direct dans la conversation.** Le fichier est copié dans un espace
de travail de session, hors du dépôt, en droits `700`. Il disparaît avec la
session. C'est la voie des premiers essais, et elle suffit à l'analyse.
L'empreinte SHA-256 est relevée à l'arrivée, pour que l'on parle du même
fichier d'un échange à l'autre.

**2. Volume privé du serveur de préproduction.** Un répertoire hors du clone,
par exemple `/opt/metreo-plans/`, en droits `700`, appartenant au compte qui
fait tourner l'application. Le dépôt n'y touche pas, la sauvegarde chiffrée le
couvre si on l'y ajoute explicitement. C'est la voie d'un corpus qui doit
survivre à une session.

**3. Dépôt par l'application elle-même**, quand le parcours sera livré : le
plan devient une révision de document, avec son original immuable, son
empreinte et son organisation. C'est la seule voie qui porte la traçabilité
complète, et celle vers laquelle tout converge.

## Quoi fournir, et dans quel ordre

Un petit échantillon représentatif vaut mieux qu'un versement massif. L'ordre
ci-dessous suit ce qui débloque le plus de travail par fichier fourni.

| # | Ce qu'il faut | Pourquoi |
| --- | --- | --- |
| 1 | **Un même plan en DXF *et* en PDF** | C'est le seul moyen de comparer les deux lecteurs sur un sujet identique. Sans cette paire, la comparaison demandée n'est pas mesurable |
| 2 | Un plan dont vous connaissez **deux ou trois cotes réelles** | Recaler l'échelle et prouver que la mesure retombe sur la bonne valeur. Trois cotes suffisent : une courte, une longue, une oblique |
| 3 | Un plan **numérisé**, s'il en existe | Aucun des quatre plans fournis ne l'est : la chaîne OCR et l'avertissement d'échelle n'ont donc rien à éprouver |
| 4 | Un plan dont le **métré est déjà validé** | La référence contre laquelle mesurer les progrès. À défaut, une annotation faite à l'écran fera la référence |
| 5 | Deux ou trois plans d'un **autre projet**, d'un autre bureau d'études | Les plans d'un même bureau partagent leurs conventions de calques. Un second bureau est ce qui distingue « ça marche » de « ça marche chez eux » |

## Ce qui reste hors du dépôt, et comment on le prouve

Les analyses faites sur vos plans produisent des chiffres — nombre de
cotations, unité, médiane des mesures. **Ces chiffres-là peuvent être
commités**, dans un document ou un message de commit : ils ne reconstituent
aucun ouvrage.

Ce qui ne peut pas l'être : une coordonnée, un nom de projet, une adresse, un
extrait de cartouche, une capture d'écran du plan. Un test qui aurait besoin
d'une de ces valeurs est un test à réécrire sur une fixture fabriquée.

Le contrôle `scripts/check_skills.py` refuse déjà les données volatiles dans la
documentation. Il ne sait pas reconnaître un plan : c'est à la relecture de le
faire.
