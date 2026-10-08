# Les cotes de référence : la fiche à remplir

Ce document est une **demande**, pas un rapport. Il décrit ce qu'il faut me
fournir pour que la phrase « Metreo mesure juste » cesse d'être une opinion.

> **Pourquoi elle est nécessaire.** Metreo mesurera bientôt un PDF. Pour dire
> que cette mesure est juste, il faut une vérité **indépendante de Metreo**.
> Sans elle, « vérifié » voudrait dire « Metreo est d'accord avec Metreo » —
> ce qui reste vrai même quand tout est faux.

---

## 1. Ce qu'il faut, en une phrase

**Une paire DXF + PDF du même plan et de la même révision**, et **trois cotes**
relevées à la main dessus : une **courte**, une **longue**, une **oblique**.

C'est tout. Le reste de ce document explique pourquoi chacun de ces mots
compte, et ce qu'il faut noter exactement.

---

## 2. Pourquoi une paire, et pourquoi la même révision

| Le fichier | Ce qu'il apporte |
| --- | --- |
| **DXF** | ses cotations sont lisibles **par la machine**, et son unité de dessin est déclarée dans le fichier. Metreo les lit sans aucune calibration, et sans aucun clic. C'est une vérité de référence gratuite et exacte. |
| **PDF** | il n'a **aucune unité**. Ses coordonnées sont des points PostScript, qui ne disent rien de l'ouvrage. Sa mesure passe obligatoirement par une calibration humaine — et c'est précisément ce qu'on veut éprouver. |

La paire permet donc de comparer **la mesure calibrée à la mesure lue**, sur le
même ouvrage, au même endroit.

### La même révision n'est pas un détail de rangement

Si les deux fichiers viennent de deux révisions différentes et qu'un écart
apparaît, **il devient impossible de savoir d'où il vient** : d'une calibration
imprécise, ou d'un mur qui a bougé entre les deux versions. L'exercice ne
prouve alors plus rien, et il coûte exactement le même travail.

C'est pour cela que la fiche demande l'**indice de révision relevé sur chacun
des deux fichiers**, et non une fois pour les deux : c'est la seule façon de
constater qu'ils concordent, au lieu de le supposer.

---

## 3. Pourquoi trois cotes, et pourquoi celles-là

Chacune attrape une erreur que les deux autres laissent passer.

### La cote **courte** — elle attrape l'imprécision du pointage

Une calibration se pose en désignant deux points. L'erreur de pointage est à
peu près **constante** : quelques pixels, quel que soit l'ouvrage visé. Son
poids **relatif** est donc maximal sur la plus petite cote.

Une erreur de 40 mm sur une cote de 5 000 mm, c'est 0,8 % : invisible. La même
erreur sur une cote de 400 mm, c'est 10 % : un linteau, une allège, une
réservation deviennent faux.

### La cote **longue** — elle attrape la déformation de la page

Elle doit **traverser le plan**, d'un bord à l'autre autant que possible. Une
page étirée à l'export, un « ajuster à la page » resté coché, une mise à
l'échelle non uniforme : tout cela produit une erreur qui **croît avec la
distance**, et qui reste sous le bruit sur une cote courte.

### La cote **oblique** — c'est la décisive

Si l'horizontale et la verticale n'ont pas le même facteur — page étirée dans
une seule direction, calibration posée sur un seul axe — alors une cote
horizontale **et** une cote verticale peuvent être justes toutes les deux, et
la géométrie être fausse quand même.

Une cote oblique mélange les deux axes. Elle ne peut pas être satisfaite par un
facteur juste sur un axe et faux sur l'autre : c'est le seul des trois contrôles
qui vérifie que le plan est **semblable** à l'ouvrage, et pas seulement étiré
dans la bonne proportion sur une direction.

> **Si vous pouvez choisir** : prenez la courte plutôt **horizontale**, la
> longue plutôt **verticale** (ou l'inverse), et l'oblique franchement en
> diagonale. Les trois couvrent alors x, y, et leur accord.

---

## 4. Ce qu'une mesure par clic peut valoir — et pourquoi cela décide de la méthode

Mesuré sur vos quatre plans, à la résolution de l'aperçu pleine page
(2 000 pixels sur le grand côté) :

| Plan | Format | mm de **papier** par pixel | … soit en mm d'**ouvrage** à 1:20 / 1:50 / 1:100 |
| --- | --- | --- | --- |
| `coupe-1-1` | 1480 × 850 mm | 0,740 | 14,8 / 37,0 / 74,0 |
| `elevation-nord` | 1690 × 850 mm | 0,845 | 16,9 / 42,2 / 84,5 |
| `etage-1` | 1189 × 914 mm | 0,594 | 11,9 / 29,7 / 59,4 |
| `etage-3` | 1189 × 914 mm | 0,594 | 11,9 / 29,7 / 59,4 |

**Un seul pixel vaut donc 12 à 42 millimètres d'ouvrage.** Une calibration
posée par deux clics sur l'aperçu pleine page porte une incertitude de l'ordre
de **80 mm à 1:50**, avant toute autre source d'erreur. Ce n'est pas un métré.

Sur une **tuile agrandie** (512 pixels pour une zone de quelques centimètres de
papier), le même pixel vaut **0,6 à 6 mm**. Vingt fois mieux.

> **Conséquence, et elle est contraignante** : la calibration et le pointage
> des mesures se feront **sur une vue agrandie**, jamais sur l'aperçu pleine
> page. L'aperçu sert à **trouver** la zone ; il ne sert pas à **pointer**.
> Ce n'est pas un choix d'ergonomie, c'est ce que le calcul ci-dessus impose.

C'est aussi ce qui fixe le **plancher de tolérance** : aucune comparaison ne
peut exiger mieux que la résolution à laquelle le point a été posé.

---

## 5. La fiche

### 5.1 Le plan, une fois

| Donnée | Votre réponse | Comment la relever |
| --- | --- | --- |
| Nom du plan | | tel qu'écrit au cartouche |
| Fichier DXF | | nom exact du fichier |
| Fichier PDF | | nom exact du fichier |
| **Indice de révision lu sur le DXF** | | dans le cartouche, à l'ouverture du DXF |
| **Indice de révision lu sur le PDF** | | dans le cartouche du PDF |
| Les deux concordent ? | ☐ oui ☐ non | **si non, la paire n'est pas exploitable** |
| Échelle écrite au cartouche | ex. 1:50 | |
| Format papier annoncé | ex. A0 | |
| Page du PDF concernée | ex. 1 | si le PDF en a plusieurs |
| Le texte du PDF est-il sélectionnable ? | ☐ oui ☐ non | ouvrez le PDF, essayez de sélectionner une cote à la souris. **Non** = plan scanné ou aplati : les textes ne seront pas extraits, et seule la mesure par clic restera possible |
| Export « ajusté à la page » ? | ☐ oui ☐ non ☐ je ne sais pas | si oui, l'échelle du cartouche **ne vaut plus** pour le PDF |

### 5.2 Les trois cotes

Pour **chacune** des trois, relevez :

| | Cote **courte** | Cote **longue** | Cote **oblique** |
| --- | --- | --- | --- |
| **Valeur écrite sur le plan** | | | |
| **Unité** (mm, cm, m — écrite, pas supposée) | | | |
| Orientation | ☐ horiz. ☐ vert. ☐ oblique | ☐ horiz. ☐ vert. ☐ oblique | ☐ horiz. ☐ vert. ☐ oblique |
| **Où elle se trouve** : un texte voisin que je peux chercher dans le PDF | | | |
| … et de quel côté de ce texte | ex. « 15 cm au-dessus » | | |
| Calque, si visible dans le DXF | | | |
| Identifiant (« handle ») de la cotation dans le DXF, si votre logiciel l'affiche | | | *facultatif, mais c'est le repère le plus sûr* |

### 5.3 Ce que vous attendez comme tolérance

| Question | Votre réponse |
| --- | --- |
| Au-delà de quel écart une mesure doit-elle rester « à vérifier » ? | ☐ en % de la valeur : ____ % ☐ en mm absolus : ____ mm ☐ les deux |

**Je ne remplis pas cette ligne à votre place**, et c'est volontaire : un seuil
de tolérance est une décision de métier, pas une constante technique. Un
terrassement et un châssis n'ont pas la même exigence.

Ce que je peux vous donner pour décider, ce sont les deux planchers mécaniques
mesurés plus haut : **± 0,6 à 6 mm** si le pointage est fait sur une tuile
agrandie, **± 12 à 42 mm par clic** s'il est fait sur l'aperçu. Toute tolérance
plus serrée que le plancher serait invérifiable.

---

## 6. Ce que je ferai de la fiche

1. **Lire le DXF** et relever ce que Metreo y trouve pour vos trois cotes —
   sans calibration, sans clic : l'unité est dans le fichier.
2. **Calibrer le PDF** selon la procédure, puis mesurer les mêmes trois cotes.
3. **Comparer les trois valeurs** — la vôtre, celle du DXF, celle du PDF
   calibré — et donner pour chacune l'écart **absolu** et **relatif**.
4. **Dire ce qui dépasse votre tolérance**, et pourquoi.

Les trois comparaisons se lisent ensemble :

| Ce qu'on observe | Ce que cela désigne |
| --- | --- |
| DXF ≠ votre relevé | le lecteur DXF, ou une erreur de relevé — à trancher avant tout le reste |
| DXF = votre relevé, PDF ≠ les deux | la **calibration** du PDF |
| écart faible sur la longue, fort sur la courte | l'**imprécision de pointage** |
| écart qui croît avec la longueur | la **page est déformée** |
| horizontale et verticale justes, **oblique fausse** | les deux axes n'ont pas le même facteur |

---

## 7. Comment me transmettre tout cela sans exposer vos plans

**Les fichiers ne quittent pas votre machine et n'entrent pas dans le dépôt.**
C'est la règle depuis le début, et elle ne change pas ici.

- **La fiche remplie est du contenu de plan** : des valeurs, des noms de
  calque, des textes de cartouche. Elle ne se commite donc pas non plus.
  Transmettez-la dans la conversation, ou remplacez les textes d'ancrage par
  des repères neutres si vous préférez la garder par écrit.
- **Les fichiers DXF et PDF** restent chez vous. Je les lis dans un répertoire
  de travail temporaire, hors du dépôt, exactement comme pour les quatre plans
  déjà mesurés — dont rien n'a été commité.
- **Aucun envoi vers un service tiers.** Tout le traitement est local :
  `ezdxf` et PDFium tournent dans le conteneur, sans réseau.

Ce que ce document-ci contient, en revanche, ne porte **aucune donnée de
plan** : c'est un formulaire vide, et il a sa place dans le dépôt.

---

## 8. Ce que cette fiche ne prouvera pas

Elle vaut pour **un** plan, **une** révision, **trois** cotes. Elle attrape une
calibration fausse, une page déformée, une anisotropie. Elle ne dit rien de :

- la justesse sur un **autre** plan, à une autre échelle ou dans un autre
  logiciel d'export ;
- les cotes que Metreo **n'a pas vues** — un plan scanné n'en livre aucune ;
- la question qui vient juste après, et qui n'est pas une question de
  précision : **un nombre lu sur un plan n'est pas forcément une cote.** Sur
  l'un de vos plans, 860 fragments de texte sur 4 351 sont des nombres d'au
  moins trois chiffres, et le premier d'entre eux est le code postal du
  cartouche. Aucune tolérance ne corrige cela ; seule une personne qui regarde
  **ce que le nombre cote** le peut.

Trois cotes bien choisies valent mieux que trente mal choisies : elles sont
exploitables le jour où vous les remettez.
