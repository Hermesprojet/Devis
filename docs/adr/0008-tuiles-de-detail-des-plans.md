# ADR 0008 — Préparer et mettre en cache les tuiles de détail d'un plan

- **Statut** : accepté
- **Date** : 2026-10-06
- **Étend** : l'ADR 0007 (lecture de plans), qui décide des bibliothèques et de
  l'isolation, et l'ADR 0003 (stockage documentaire), qui décide que l'original
  est immuable et que les dérivés vivent sur le volume
- **Porte sur** : `apps/api/src/metreo_api/rendu_tuile.py`,
  `apps/api/src/metreo_api/services/tuiles.py`,
  `apps/api/src/metreo_api/services/rendu_pdf.py`

## Contexte

### Le problème se voit à l'écran, et il est chiffrable

L'aperçu d'une page de plan est rendu à 2 000 pixels sur le grand côté. Sur les
quatre plans réels du donneur d'ordre — quatre A0, conservés hors du dépôt —
cela donne :

| Mesuré sur l'aperçu à 2 000 px | Valeur |
| --- | --- |
| Papier par pixel | 0,594 à 0,845 mm |
| Ouvrage par pixel, aux échelles 1:20 à 1:50 | 12 à 42 mm |
| Hauteur médiane d'un caractère | **2,1 à 2,5 px** (3,7 à 5,9 pt) |

**Un texte de deux pixels de haut ne se relit pas**, et un pointage à 42 mm près
n'est pas une calibration. L'aperçu répond donc à « où est l'information »,
jamais à « que dit-elle ». C'est la tuile de détail qui répond à la seconde
question. Mesuré sur la zone que la loupe de l'écran demande réellement — 5 %
de la page, rendue sur 512 pixels :

| Mesuré sur la tuile de la loupe | Valeur |
| --- | --- |
| Étendue réellement rendue | 6,4 % de la page |
| Papier par pixel | **0,149 à 0,211 mm** |
| Gain sur l'aperçu | **3,2 à 4,0 fois** |
| Hauteur d'une ligne de texte | 12 à 19 px |

Ces quatre nombres sont le résultat d'une correction, et le § « Le plafond de la
marge » ci-dessous dit laquelle : la première version rendait **1,47 à 1,54 mm
par pixel**, c'est-à-dire plus grossier que l'aperçu.

### Pourquoi la solution évidente est exactement la mauvaise

La solution évidente est un cache de **pages** en mémoire : charger la page une
fois, en rendre dix tuiles. Mesuré sur le code livré, un processus neuf parti
de 12 Mo résidents :

| Plan (première page) | Rendu en direct | RSS après, document fermé |
| --- | --- | --- |
| `coupe-1-1.pdf` (6,9 Mo) | 459 ms | 12 → 66 Mo (**+54**) |
| `elevation-nord.pdf` (2,7 Mo) | 143 ms | 66 → 66 Mo (+0) |
| `etage-1.pdf` (4,8 Mo) | 4 972 ms | 66 → 489 Mo (**+422**) |
| `etage-3.pdf` (4,3 Mo) | 2 146 ms | 489 → 489 Mo (+0) |

Trois faits en sortent, et ils décident de tout :

1. **Le coût est dans le chargement de la page**, pas dans le rendu : la même
   tuile, page déjà chargée, se rend en 2 à 34 ms.
2. **La mémoire n'est pas rendue** à la fermeture du document. 489 Mo restent
   résidents, et le plan suivant n'y ajoute rien — il réutilise ce qui est déjà
   réservé. Ce n'est donc pas une fuite, c'est un **plancher définitif** : tout
   travailleur qui a rendu un A0 dense pèse un demi-gigaoctet jusqu'à sa mort.
3. **Le coût ne suit pas la taille du fichier** mais la densité du dessin : le
   plus lourd des quatre sur le disque est le moins cher des deux en mémoire.
   Aucune borne utile ne peut donc être déduite de l'octet déposé.

Un cache de pages multiplierait donc le plancher par le nombre de pages
gardées. Trois pages denses dans un travailleur d'API épuisent un conteneur
ordinaire — et le plafond de `max_upload_bytes` (25 Mio) n'y change rien, vu le
point 3.

## Décision

### 1. Le rendu d'une tuile se fait dans un processus séparé, qui meurt aussitôt

`python -m metreo_api.rendu_tuile` rend **une** tuile et se termine. Un
processus qui se termine rend tout ce qu'il a réservé, y compris ce que PDFium
ne rend pas. Mesuré, le même travail que ci-dessus, cette fois hors processus :

| Plan | Durée, appel du fils compris | RSS du parent | Pic du fils |
| --- | --- | --- | --- |
| `coupe-1-1.pdf` | 461 ms | 11 Mo | 74 Mo |
| `elevation-nord.pdf` | 220 ms | 11 Mo | — |
| `etage-1.pdf` | 5 286 ms | 11 Mo | 510 Mo |
| `etage-3.pdf` | 2 661 ms | 11 Mo | — |

**Le parent reste à 11 Mo du premier au dernier rendu**, contre 489 Mo pour le
même travail en direct. Le fils le plus lourd a bien culminé à 510 Mo — et les
a rendus en mourant.

Le fils est invoqué par `-m` sur `sys.executable`, le **même** interpréteur que
le parent : c'est ce qui garantit le même environnement virtuel et les mêmes
versions, y compris dans l'image où l'API tourne sous un utilisateur non
privilégié.

### 2. Le cache porte sur le résultat, jamais sur ce qui l'a produit

La tuile produite est écrite sur le volume, à côté de l'aperçu et du constat,
sous une clé déterministe. Mesuré :

| Demande | Durée |
| --- | --- |
| Première, pour les quatre plans | 226 à 5 376 ms |
| Seconde, depuis le volume | **0,3 à 0,5 ms** |

C'est ce qui rend praticable le relevé d'une liste de mesures : la première
ouverture d'une zone paie, les suivantes non. Et c'est le seul cache possible,
parce que l'objet mis en cache pèse 92 Ko au lieu de 422 Mo.

**La clé est arrondie au millième de page** (`PRECISION_DE_LA_CLE = 3`). Sans
arrondi, deux clics à un pixel d'écart produiraient deux tuiles distinctes et
le cache ne servirait jamais. Au millième — deux pixels sur un aperçu de deux
mille — deux demandes voisines partagent leur tuile, et le décalage reste
invisible puisque la tuile porte une marge de huit fois la hauteur du texte.

L'organisation et la révision sont dans le **chemin**, pas dans l'empreinte :
une collision d'empreinte afficherait une mauvaise zone du même document, ce
qui se verrait immédiatement, et jamais le document d'un autre tenant.

### 3. Le plafond de la marge, et le défaut qu'il ferme

Une tuile ne montre pas seulement la zone demandée : elle l'élargit d'une marge
de **huit fois sa hauteur**, pour que le propriétaire voie l'ouvrage coté et
pas seulement le nombre. Sans marge, la tuile d'un « 1040 » de 11 points montre
« - 1040 E » et pas un trait du dessin — le propriétaire lit la cote sans
pouvoir juger CE QU'ELLE cote, ce qui est précisément la question qu'on lui
pose.

Huit fois la hauteur convient à la boîte d'un texte, qui fait deux millièmes de
page. **Mais la zone n'est pas toujours une boîte de texte.** La loupe de
l'écran demande 5 % de la page : huit fois 5 % font 40 % de marge de chaque
côté, et la tuile rendait alors 51 à 67 % × 85 % de la page sur 512 pixels.

| Zone demandée | Étendue rendue | Papier par pixel | Verdict |
| --- | --- | --- | --- |
| Boîte d'un texte (0,3 %) | 5,1 à 6,4 % | 0,149 à 0,179 mm | conforme à l'intention |
| Loupe de l'écran (5 %) — **avant** | 51 à 67 % × 85 % | **1,47 à 1,54 mm** | **plus grossier que l'aperçu** |
| Loupe de l'écran (5 %) — après | 6,4 % | 0,149 à 0,211 mm | 3,2 à 4,0 fois l'aperçu |

Cliquer pour agrandir rendait donc l'image **plus grossière**, et rien ne le
disait. La marge est désormais plafonnée par ce que la tuile doit encore
apporter : `GAIN_MINIMAL_SUR_L_APERCU = 4`, c'est-à-dire que la tuile rend au
moins quatre fois plus de pixels par point que l'aperçu **de la même page**.

Le plafond est calculé par page, et non écrit en fraction : `facteur_de_rendu`
ne réduit pas une page plus petite que 2 000 points, donc 6,4 % d'une page de
300 points — une fixture — serait dix-neuf points, un plafond absurde qui
supprimerait toute marge là où elle ne coûte rien. La référence est la finesse
de l'aperçu de cette page-là.

Et il borne la **marge**, pas la demande : une zone déjà plus large que le
plafond garde sa largeur et ne reçoit simplement aucune marge. Rétrécir la zone
demandée montrerait autre chose que ce que l'appelant a désigné — et sur une
mesure, « autre chose » veut dire un autre endroit du plan.

**Le défaut n'a pas été vu par relecture, et il n'aurait pas pu l'être** : la
première mesure de la finesse de la tuile recalculait la marge au lieu de
l'observer, et retrouvait donc le chiffre qu'elle voulait trouver. Le test de
régression compare les deux `pixels_par_point` que le code lui-même rend, et le
script de mesure intercepte les arguments passés à PDFium.

### 4. Quatre limites de ressources, chacune avec son chiffre

| Limite | Valeur | Ce qu'elle arrête, et pourquoi ce chiffre |
| --- | --- | --- |
| `PLAFOND_MEMOIRE` (`RLIMIT_AS` du fils) | 1 536 Mio | Un fichier forgé pour épuiser la machine tue **le fils**, pas le conteneur. Trois fois le pic mesuré (510 Mo) : assez pour une page plus dense que tout ce qui a été vu, pas assez pour une page qui n'en finit pas. Posée **avant** d'importer PDFium — une bibliothèque C qui a déjà réservé son arène ne la rendra pas, et `setrlimit` ne vaut que pour les allocations futures. |
| `DELAI_MAXIMAL_EN_SECONDES` | 60 | Un fichier qui fait boucler PDFium : un processus bloqué retient son demi-gigaoctet indéfiniment. Dix fois le pire mesuré (5,3 s). |
| `PLAFOND_D_OCTETS_PAR_REVISION` | 64 Mio | Le volume. Mesuré sur une grille de cent zones, une tuile pèse 26 à 246 Ko, **médiane 92 Ko** : cinq cents tuiles font 45 Mo au poids médian et **120 Mo au pire**, pour un original qui en pèse 5. C'est donc l'octet qui borne, pas le nombre. Soixante-quatre mébioctets : une dizaine de fois l'original, l'ordre de grandeur des aperçus pleine page d'un document de dix pages. |
| `PLAFOND_DE_TUILES_PAR_REVISION` | 500 | Garde-fou bon marché devant le précédent : il s'évalue sans interroger la taille de chaque fichier. |

**Atteindre un plafond de cache ne refuse pas la mesure.** Le rendu continue de
fonctionner, il n'est simplement plus mis en cache, et l'événement
`cache_de_tuiles_plein` est journalisé avec le compte et les octets — un volume
qui se remplit doit être visible avant d'être plein. Le journal ne porte, ici
comme ailleurs, **rien du contenu du plan**.

### 5. Les tuiles sont des dérivés, et la purge doit les connaître

Une tuile n'a aucune ligne en base : sa clé se calcule depuis la révision. Le
jour où la révision disparaît, plus rien ne saurait quels fichiers lui
appartenaient. `cles_des_tuiles()` énumère donc les tuiles d'une révision pour
que `conservation.py` les inscrive, au même titre que l'aperçu et le constat
(ADR 0006, §4 : lignes d'abord, fichiers ensuite).

### 6. Ce que l'écran reçoit, et ce qu'il en fait

La tuile servie porte sa largeur, sa hauteur et **la hauteur que la zone
demandée atteint dans l'image**, en pixels. Ce dernier nombre n'est pas
décoratif : quand la zone est la boîte d'un texte — le cas d'une mesure qu'on
va relire — c'est la hauteur de ce texte, et en deçà de 16 px la cote reste
difficile à relire ; l'écran doit pouvoir le dire au lieu d'afficher un flou
sans un mot.

Il s'appelait « hauteur du texte », et c'était faux dès que l'appelant demandait
autre chose qu'une boîte de texte : sur la loupe, il valait 201 à 308 pixels —
la hauteur de la loupe — là où le texte en faisait douze à dix-neuf. Un champ
mal nommé est un champ qui finira par être lu de travers ; celui-ci ne l'était
encore par aucun écran, et le nom a été corrigé avant qu'il le soit.

Il est perdu quand la tuile vient du cache — il n'est pas dans le PNG — et
l'écran le reçoit donc à la **première** demande, qui est celle où il décide
quoi afficher.

## Conséquences

**Acquis.**

- Un travailleur d'API ne grossit pas en servant des plans. Le plancher de
  489 Mo n'existe plus dans le processus qui répond aux requêtes.
- La deuxième consultation d'une zone est instantanée (0,3 ms), ce qui rend le
  relevé d'une série de mesures utilisable.
- Un PDF hostile dispose de 60 secondes et de 1,5 Gio dans un processus
  jetable, et d'aucun accès à la base ni au réseau.

**Prix accepté.**

- **La première tuile d'une zone coûte jusqu'à 5,3 secondes.** C'est long, et
  c'est annoncé à l'écran avec sa cause, plutôt que subi. L'écran ne demande
  jamais de tuile de sa propre initiative : seul un clic en déclenche une.
- Un `fork`/`exec` par tuile. Négligeable devant les 220 ms du cas le plus
  rapide, et c'est précisément ce `exec` qui rend la mémoire.
- Le cache est **sur le volume local**, donc par instance. Deux instances
  rendront la même tuile une fois chacune. Acceptable au stade actuel (une
  seule instance d'API), et à reprendre le jour où il y en aura plusieurs — la
  clé est déjà déterministe et indépendante de l'instance, donc le partage ne
  demandera qu'un stockage partagé, pas une nouvelle conception.

**Non résolu, et assumé.**

- **Aucune préparation anticipée.** Rien ne rend les tuiles à l'avance, au
  dépôt du document. C'est délibéré : on ne sait pas quelles zones seront
  consultées, une page fait 400 tuiles de 5 %, et en rendre 400 coûterait
  jusqu'à 35 minutes et 100 Mo pour un document dont trois zones seront
  regardées. Si l'usage montre que les mêmes zones reviennent — le cartouche,
  l'index des cotes — c'est **cette liste-là** qu'il faudra préparer, et elle
  s'observera dans les journaux avant d'être codée.
- **Aucune purge des tuiles à l'usage.** Elles disparaissent avec leur
  révision, et les plafonds les empêchent de croître sans fin, mais rien ne
  retire la tuile d'une zone qu'on ne regardera plus. Un `LRU` est la suite
  naturelle ; il n'est pas écrit.

## Alternatives écartées

| Écartée | Pourquoi, avec le chiffre |
| --- | --- |
| **Cache de pages PDFium en mémoire** | Le plancher de 422 Mo par page dense n'est pas rendu. Trois pages tiennent le conteneur entier. C'est la mesure qui écarte l'option, pas une préférence. |
| **Rendre les tuiles dans le processus d'API, sans cache** | Même plancher, et il s'installe dans le processus qui doit répondre à toutes les autres requêtes. |
| **Un aperçu beaucoup plus grand au lieu de tuiles** | Mesuré : pour amener le texte médian au seuil de lecture de 8 px, il faut un grand côté de **6 480 à 7 590 px**, soit un bitmap RGB de **63 à 124 Mo** avant compression ; pour 12 px, 9 720 à 11 385 px et 143 à 278 Mo. Un tel PNG est intransportable, et il faudrait le rendre en entier pour en regarder 5 %. |
| **Un travailleur de rendu permanent (file Redis)** | Reporte le plancher sur le travailleur au lieu de le supprimer : un travailleur permanent qui a vu dix plans denses pèse ce que pèse le pire d'entre eux, pour toujours. Il faudrait de toute façon le recycler après N tâches — c'est-à-dire refaire, en plus compliqué, ce que fait un processus jetable. |
| **Rendre côté navigateur (PDF.js)** | Enverrait le plan complet au client pour qu'il en affiche 5 %, et déplacerait la géométrie — donc la mesure — hors du serveur, là où elle ne serait plus déterministe ni vérifiable. L'ADR 0007 a déjà tranché que la mesure appartient au serveur. |
| **Déléguer le rendu à un service tiers** | Le plan d'un client ne sort pas. Contrainte explicite du donneur d'ordre, et antérieure à cette décision. |

## Comment reproduire les mesures

Les chiffres de cette décision viennent de deux scripts, exécutés sur les
quatre plans réels du donneur d'ordre. **Ces plans ne sont pas dans le dépôt**
et n'y entreront pas : ils sont des documents de chantier d'un client.

| Mesure | Script |
| --- | --- |
| Coût en direct, hors processus, et effet du cache | `scripts/mesures/mesurer_le_budget_des_tuiles.py <dossier> A\|B\|C` |
| Poids d'une tuile sur une grille de 5 × 5 zones | `scripts/mesures/mesurer_le_poids_des_tuiles.py <dossier>` |
| Étendue réellement rendue, par interception de l'appel à PDFium | `scripts/mesures/mesurer_l_etendue_d_une_tuile.py <dossier>` |

Les deux prennent en argument un dossier de PDF **fourni par l'exploitant** :
le dépôt porte les scripts, jamais les plans.

Chaque section de la première **doit** s'exécuter dans un processus neuf : la
mesure porte sur la mémoire résidente, et une section qui a déjà chargé une
page fausse la suivante. C'est l'erreur qui a été commise au premier essai, où
la section « hors processus » affichait un parent à 489 Mo — hérités de la
section précédente, pas du travail mesuré.

Les mêmes faits, sans les plans réels, sont vérifiés en continu par
`scripts/verifier_image_de_lecture.py`, qui tourne **dans l'image construite**
en CI sur des fixtures fabriquées par `scripts/fabriquer_pdf_de_test.py`.
