# Valider Metreo sur vos plans : ce qu'il faut, et le tableau à remplir

> **Ce document est une procédure, pas un constat.** Il dit exactement quels
> fichiers fournir, quelles cotes relever, comment conduire l'essai et sous
> quelle forme rendre le résultat. Il ne contient aucun chiffre mesuré sur un
> plan réel. Un premier essai a eu lieu, hors dépôt, par des **pointages
> automatisés** sur un dossier d'exécution réel (procédure et harnais dans
> `docs/ESSAI_GUIDE_PLANS_REELS.md`, résultats dans l'espace privé de la
> machine d'essai) ; il ne remplace pas l'essai **par une personne** que ce
> document décrit, et n'établit pas la justesse.
>
> **Les plans réels ne sont jamais dans le dépôt.** Ils vivent dans l'espace
> privé de la machine d'essai (`/root/.metreo-essai/plans/`), et ce document
> existe pour que l'essai soit possible **sans nouvelle discussion** le jour où les
> fichiers seront là.
>
> **Leur absence ne bloque rien d'autre.** Tout ce qui pouvait être éprouvé
> sans eux l'a été, sur des fixtures fabriquées dont la géométrie est connue :
> voir le § 6 pour ce que cela couvre, et le § 5 pour ce que cela ne couvre
> pas.
>
> **Confidentialité.** Aucun plan réel n'entre dans ce dépôt, et aucun ne part
> vers un service tiers. La règle et les trois voies de dépôt possibles sont
> dans `docs/PLANS_REELS.md` ; ce document ne les répète pas.
>
> **Pour conduire l'essai, lisez d'abord `docs/ESSAI_GUIDE_PLANS_REELS.md`** :
> il enchaîne les sept gestes du PDF au devis, en une page, et ajoute la
> comparaison avec le DXF du même dessin. Ce document-ci est la MÉTHODE
> chiffrée qu'il applique.

---

## 1. Les trois niveaux de preuve, à ne jamais mélanger

Ce document ne parle que du deuxième. Les trois sont nommés ici parce que la
confusion entre eux est le risque principal de lecture.

| Niveau | Sur quoi | Ce qu'il établit | Où il vit |
| --- | --- | --- | --- |
| **Fixtures** | `fixtures/plans/plan_batiment.pdf`, dont la géométrie est écrite par la fabrique | que la **chaîne de calcul** est juste : écran, repère, calibration, formule, décision | `apps/api/tests/`, `apps/web/e2e-premier-devis/` — à chaque livraison |
| **Plans réels** | vos fichiers, hors dépôt | que Metreo **mesure juste sur un vrai dessin**, pointé par une personne | **ce document** — rien n'est fait |
| **Préproduction** | le VPS, l'image Docker, la base PostgreSQL | que le logiciel **tourne** et se remet en arrière | `docs/MISE_EN_LIGNE_LECTURE_DE_PLANS.md`, `scripts/epreuve_retour_arriere.py` |

Une mesure juste sur une fixture ne dit **rien** sur un plan d'exécution :
l'écart entre les deux est l'erreur de pointage d'un humain sur un dessin réel,
plus tout ce que la fixture ne reproduit pas — un format A0, une page
légèrement déformée, deux échelles sur la même feuille, un trait épais dont on
ne sait pas quel bord coter.

---

## 2. Les fichiers nécessaires

Par ordre de ce qu'ils débloquent. **Le premier suffit à commencer** ; les
suivants élargissent ce qui peut être conclu.

| # | Fichier | Indispensable ? | Ce qu'il permet, et rien de plus |
| --- | --- | --- | --- |
| 1 | **Un PDF d'exécution**, une page, format réel (A0/A1), avec son **cartouche et son échelle déclarée** | **oui** | tout le § 3. C'est le minimum vital |
| 2 | **Le DXF du MÊME plan et de la MÊME révision** | fortement souhaité | lire les cotes dans le fichier plutôt que de les relever sur place : la référence devient exacte au lieu d'être déclarée. C'est aussi le seul moyen de comparer les deux lecteurs sur un sujet identique |
| 3 | **Un second plan, d'un autre bureau d'études** | souhaité | distinguer « ça marche » de « ça marche chez eux » : deux plans d'un même bureau partagent leurs conventions |
| 4 | **Un plan portant deux échelles** sur la même feuille (plan + détail) | souhaité | c'est le seul moyen d'éprouver l'hypothèse **H10**, aujourd'hui non vérifiée : une calibration sans zone s'applique à toute la page, et un détail au 1:20 mesuré avec l'échelle d'un plan au 1:50 donne une valeur **fausse de 2,5× et parfaitement plausible** |
| 5 | **Un plan scanné** (numérisé, sans texte extractible) | souhaité | aucun des quatre fichiers déjà fournis ne l'était. Le diagnostic « probablement scanné » n'a donc jamais été éprouvé sur un vrai scan — seulement sur une fixture fabriquée pour cela |
| 6 | **Un plan dont le métré est déjà validé** | optionnel | la seule référence qui permette de parler de l'ensemble d'un métré, et non de trois cotes |

**Ce qu'il ne faut PAS fournir** : un plan anonymisé en croyant que cela lève
l'interdit de commit. La géométrie d'un bâtiment l'identifie ; l'interdit porte
sur le fichier, pas sur son cartouche.

---

## 3. Les références à relever

**Trois cotes, et elles ne sont pas interchangeables.** Chacune attrape une
hypothèse différente, et deux d'entre elles ne peuvent pas être remplacées par
un facteur juste sur un seul axe.

| Cote | Ce qu'elle doit être | L'hypothèse qu'elle éprouve |
| --- | --- | --- |
| **A — courte** | 500 à 1 500 mm d'ouvrage, par exemple l'épaisseur d'un mur ou la largeur d'une baie | l'**imprécision du pointage** : c'est la cote où l'erreur relative est la plus grande, donc celle qui révèle si le ± annoncé est honnête |
| **B — longue, traversant la feuille** | la plus grande cote du plan, d'un bord à l'autre | **H2**, la page non déformée. Un PDF exporté « ajusté à la page » ou réimprimé porte une erreur qui **croît avec la distance** et reste invisible sur une cote courte |
| **C — oblique** | une diagonale, un rampant, une cote qui n'est ni horizontale ni verticale | **H3**, l'isotropie. **C'est la décisive** : un facteur juste en horizontale ET en vertical peut encore donner une géométrie fausse, et aucune cote droite ne le montrerait |

**Et une cote de calibration, distincte des trois.** Elle doit être :
- **longue** — l'incertitude du facteur vaut `√2 · ε / d` : plus la base est
  courte, plus tout ce qui en descend est incertain ;
- **lisible au cartouche ou relevée sur place**, pas déduite du rapport
  d'échelle : un PDF exporté « ajusté à la page » rend ce rapport faux ;
- **pointée dans la loupe**, jamais sur l'aperçu. Mesuré sur quatre plans
  réels : un pixel d'aperçu pleine page vaut **12 à 42 mm d'ouvrage**, contre
  0,6 à 6 mm dans la loupe.

**Pour chaque cote, notez :**

1. ce qu'elle cote, en une phrase qu'un tiers comprend (« épaisseur du mur de
   façade, axe en axe, au niveau de la baie 3 ») ;
2. sa valeur, et **d'où elle vient** : lue dans le DXF, lue au cartouche du
   PDF, ou relevée sur place — et à quel instrument ;
3. la page et, grossièrement, où elle se trouve sur la feuille (« angle bas
   gauche », « axe central »).

Le troisième point compte : une cote près d'un **bord** tombe dans une loupe
rognée, et c'est un cas que la fixture ne reproduit pas.

---

## 4. Le tableau attendu / mesuré / écart

À remplir tel quel. Les colonnes grisées sont celles que **Metreo** affiche ;
les autres viennent de vous.

### 4.1 Identification de l'essai

| Champ | Valeur |
| --- | --- |
| Date de l'essai | |
| SHA du logiciel essayé | |
| Fichier (nom interne, **jamais le nom du client**) | |
| Empreinte SHA-256 du fichier | |
| Format de la page, en millimètres | |
| Échelle déclarée au cartouche | |
| Cote de calibration : ce qu'elle cote | |
| Cote de calibration : valeur réelle, et sa source | |
| Facteur affiché par Metreo (« N mm par point ») | |

### 4.2 Les mesures

| Cote | Attendu (mm ou m²) | Source de l'attendu | Mesuré par Metreo | ± affiché | Écart absolu | Écart relatif | Dans le ± ? | Fiabilité annoncée |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A — courte | | | | | | | | |
| B — longue | | | | | | | | |
| C — oblique | | | | | | | | |
| D — une surface fermée | | | | | | | | |

### 4.3 La répétition — c'est elle qui dit si le ± vaut quelque chose

**Cinq pointages indépendants de la cote A**, la loupe refermée et rouverte
entre chacun. Sans cette répétition, un écart unique ne se distingue pas d'un
coup de chance.

| Pointage | Mesuré | ± affiché |
| --- | --- | --- |
| 1 | | |
| 2 | | |
| 3 | | |
| 4 | | |
| 5 | | |
| **Moyenne** | | |
| **Écart-type observé** | | |

Deux chiffres se lisent ensuite :

- **moyenne − attendu** : c'est le **biais**. Il ne s'annule pas en répétant,
  et il n'apparaît **nulle part** dans le ± affiché — c'est l'hypothèse **H7**,
  et c'est le seul essai qui la lève ;
- **écart-type observé ÷ ± affiché** : si ce rapport dépasse 1, le ± est trop
  étroit et se présente comme plus sûr qu'il n'est. Sur la fixture du dépôt, ce
  rapport vaut **0,22** — le ± y est 4,6 fois plus large que la dispersion
  constatée. Rien ne dit qu'il en sera de même sur un vrai dessin : la
  quantification en pixels n'est pas la main d'un métreur.

### 4.4 Les cas de bord, un par ligne

| Cas | Ce qui est attendu | Ce qui s'est passé |
| --- | --- | --- |
| Une cote **près d'un bord** de la feuille | la loupe est rognée ; la mesure reste juste | |
| Le navigateur **redimensionné** entre deux pointages | la résolution est relue, l'incertitude s'ajuste | |
| Une page en **portrait** | l'incertitude n'est pas annoncée plus petite qu'en paysage | |
| Un **détail à une autre échelle** sur la même feuille | **non couvert** : la mesure sera fausse sans réserve (H10) | |
| Une cote **plus longue que la loupe** | la loupe se déplace entre les deux clics, les points survivent | |

### 4.5 Votre tolérance

**Cette ligne n'est pas remplie à votre place, et c'est délibéré.**

> Au-delà de quel écart une mesure doit-elle rester « à vérifier » plutôt que
> d'être présentée comme mesurable ? ⟶ **…………**

Le garde-fou actuel est de **2 % d'incertitude relative**
(`SEUIL_D_INCERTITUDE`, dans `apps/api/src/metreo_api/services/mesures_pdf.py`).
Le code dit lui-même, dans le commentaire qui précède la constante, que **ce
n'est pas une tolérance métier**. Un chiffre inventé ici deviendrait une règle
de chiffrage que personne n'a décidée.

---

## 5. Ce que cet essai lèvera, et ce qu'il ne lèvera pas

Les hypothèses sont celles du § 2.11 de `docs/PRECISION_DES_MESURES.md`.

| Hypothèse | Levée par cet essai ? |
| --- | --- |
| **H1** la cote de calibration saisie est juste | **oui** — c'est l'objet de la paire DXF + PDF |
| **H2** la page n'est pas déformée | **oui** — la cote B |
| **H3** les deux axes ont la même échelle | **oui** — la cote C |
| **H7** l'erreur de pointage est centrée | **oui** — la répétition du § 4.3 |
| **H10** une page ne porte qu'une échelle | **oui, si** le fichier n° 4 est fourni |
| **H12** le nombre pointé est bien une cote de l'ouvrage | **non**, et rien ne le lèvera : seule une personne qui regarde ce que le nombre cote le peut. C'est pourquoi la validation humaine n'est pas optionnelle |
| **H11** les incertitudes de deux mesures sont indépendantes | **non** — c'est un défaut connu du modèle, pas une inconnue : le terme de facteur est **totalement corrélé** entre toutes les mesures d'une page. Un total de 20 murs n'a pas une incertitude en √20 mais en 20, et aucune agrégation n'est livrée aujourd'hui |
| **H4, H5, H6** ε déclaré = ε réel | **déjà levées**, et sans vos plans — voir § 6 |
| **H8** l'erreur de tracé croît avec le nombre de sommets | **déjà levée** — voir § 6 |
| **H9** mesure et calibration pointées à la même finesse | **déjà levée** — voir § 6 |

---

## 6. Ce qui a été fait sans vos plans

Pour que la liste ci-dessus se lise sans croire que tout attend vos fichiers.

| Ce qui est établi | Comment, et où |
| --- | --- |
| La chaîne retombe sur **6 000 mm** et **24,00 m²** d'une géométrie connue, **par le navigateur**, à chaque livraison | `apps/web/e2e-premier-devis/suite-mesure-juste-pdf.spec.ts` |
| Le pointage est **impossible** tant que l'image affichée n'est pas celle de la page, de la révision et de la zone demandées | `apps/web/e2e-premier-devis/suite-pointage-sur-loupe.spec.ts` — chargement lent, déplacements rapides, échec de rendu |
| La tuile **déclare la zone qu'elle couvre vraiment**, et cette zone survit au cache | `apps/api/tests/test_tuiles.py`, `test_lecture_pdf.py` |
| **H4, H5, H6 levées** : ε est mesuré sur la taille réellement affichée et la zone réellement rendue, et non calculé depuis 512 pixels et la largeur de page | `LecturePdf.tsx`, fonction `resolutionAffichee` |
| **H9 levée** : une mesure déclare la résolution de son propre pointage | `schemas.MesureCreate`, champ `resolution_du_pointage` |
| **H8 levée** : l'incertitude de tracé est calculée sommet par sommet | `mesures_pdf._sensibilite_d_une_longueur`, `_sensibilite_d_une_aire` |
| **Douze pointages indépendants** sur la fixture encadrent la cote connue, et la dispersion observée est 4,6 fois plus étroite que le ± annoncé | `apps/api/tests/test_mesure_juste_sur_un_plan.py` |
| Une mesure **confirmée ou corrigée** alimente une ligne de bordereau, puis le devis, puis le PDF ; une mesure **rejetée ou non tranchée** est refusée | `apps/api/tests/test_reprise_d_une_mesure.py` |

---

## 7. Conduire l'essai, pas à pas

1. Déposer le PDF selon l'une des trois voies de `docs/PLANS_REELS.md` — en
   pratique, la voie 3 : par l'application, qui en fait une révision de
   document avec son original immuable et son empreinte.
2. Ouvrir l'écran de lecture, lancer l'analyse, **relever ce que l'en-tête
   annonce** : nombre de pages, fragments et caractères extraits, tracés
   vectoriels, échelle déclarée. Ces chiffres entrent au § 4.1.
3. Choisir l'outil « Déclarer l'échelle », cliquer sur l'aperçu à l'endroit de
   la cote de calibration, puis pointer ses **deux extrémités dans la loupe**.
   Si la cote est plus longue que la loupe : pointer la première extrémité,
   déplacer la loupe, pointer la seconde — les points survivent au
   déplacement, et l'écran le dit.
4. Saisir la distance réelle, son unité, et **le motif** : « cote 7500 lue au
   cartouche », « relevé au décamètre le 12/03 ». Sans motif, la mesure qui en
   descend n'est pas auditable.
5. Mesurer A, B, C et D. Pour chacune : noter la valeur affichée, le ± et la
   fiabilité annoncée **avant** de trancher.
6. Pour chaque mesure, cliquer « Voir le tracé » et **vérifier sur l'image**
   que les sommets sont sur les points visés. Un nombre juste posé sur le
   mauvais dessin reste un nombre faux.
7. Trancher : confirmer, corriger avec un motif, ou rejeter avec un motif.
8. Refaire la cote A **cinq fois**, la loupe refermée entre chaque (§ 4.3).
9. Jouer les cas de bord du § 4.4.
10. Remplir le tableau et le rendre. **Les chiffres du tableau peuvent être
    commités** — un écart en millimètres ne reconstitue aucun ouvrage. Le
    fichier, son nom de client, une coordonnée ou une capture du plan ne
    peuvent pas l'être.

---

## 8. Ce qui sera conclu, et sous quelle forme

Trois phrases, et pas davantage, parce que trois cotes ne portent pas plus :

1. **« Sur ce plan, à cette échelle, et pointé dans la loupe, Metreo mesure à
   X % près »** — où X est le plus grand des trois écarts relatifs.
2. **« Le ± affiché couvre / ne couvre pas l'écart réellement commis »** — par
   comparaison de la colonne « Dans le ± ? ».
3. **« Le pointage est / n'est pas biaisé »**, et de combien — par la moyenne
   du § 4.3.

Ce qui ne sera **pas** conclu, quel que soit le résultat : que Metreo mesure
juste sur **vos plans** au pluriel. Un plan essayé est un plan essayé. C'est le
fichier n° 3 — un second bureau d'études — qui commence à répondre à cette
question, et il faudra plus d'un essai.
