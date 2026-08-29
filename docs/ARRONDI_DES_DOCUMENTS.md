# Arrondi : le devis remis au client s'additionne

Portée : le devis remis au client — export CSV et aperçu imprimable — et les
totaux que l'API renvoie. Ce document enregistre un défaut mesuré, la
convention retenue pour le corriger, et ce que ce choix coûte.

Le constat, la cause et l'ampleur ci-dessous décrivent l'état **avant**
correction.

## Le constat

Sur le devis du jeu de démonstration, huit postes chiffrés :

| Ce que le document imprime | Montant |
| --- | --- |
| somme des huit totaux de ligne | `99 097,07` |
| **Total HT** | `99 097,08` |
| TVA 21 % | `20 810,39` |
| **Total TTC** | `119 907,46` |

Quatre identités que le lecteur vérifie de tête sont en cause. Sur ce devis,
trois sont fausses :

    somme des lignes        ≠  Total HT           écart  0,01
    somme des TVA de ligne  ≠  TVA imprimée       écart  0,01
    Total HT + TVA          ≠  Total TTC          écart  0,01
    99 097,08 + 20 810,39 = 119 907,47, or le devis imprime 119 907,46

La quatrième — *la TVA imprimée est la TVA de la base imprimée* — se vérifie
ici (`99 097,08 × 21 % = 20 810,39`), mais ce n'est pas une propriété : voir
« La quatrième identité » plus bas.

Le même écart apparaît dans l'export CSV et dans l'aperçu HTML, qui est le
document effectivement remis. La TVA poste par poste n'apparaît pas au CSV ;
elle apparaît dans le calcul que l'API rend à l'interface web.

## La cause

`EstimateResult.to_dict` arrondit **chaque** montant indépendamment, à partir
de la valeur non arrondie :

    total_selling_price_ht  =  arrondi(Σ lignes non arrondies)
                            ≠  Σ arrondi(ligne)

    total_ttc               =  arrondi(HT non arrondi + taxes non arrondies)
                            ≠  arrondi(HT) + arrondi(taxe)

    TVA totale              =  arrondi(Σ taxes de ligne non arrondies)
                            ≠  Σ arrondi(taxe de ligne)

Rien n'est faux au centime près pris isolément : chaque nombre est le bon
arrondi de sa propre valeur exacte. C'est leur mise côte à côte sur une même
page qui produit la contradiction.

## L'ampleur

L'écart n'est pas borné à un centime : il croît avec le nombre de postes.
Mesuré sur des postes forfaitaires dont les montants portent
systématiquement une fraction de centime :

| Postes | Somme des lignes | Total imprimé | Écart HT |
| ---: | ---: | ---: | ---: |
| 8 | 838,60 | 838,58 | 0,02 |
| 50 | 6 298,75 | 6 298,60 | 0,15 |
| 200 | 40 201,00 | 40 200,40 | 0,60 |
| 500 | 175 502,50 | 175 501,00 | **1,50** |

Un bordereau de voirie de cinq cents postes est une taille ordinaire. Le devis
s'y contredit d'un euro cinquante — un montant qu'un maître d'ouvrage relève.

L'écart sur la TVA croît de la même façon, avec un cran de retard parce que la
TVA d'un poste est déjà un montant arrondi :

| Postes | Σ TVA de ligne | TVA imprimée | Écart TVA |
| ---: | ---: | ---: | ---: |
| 8 | 168,04 | 168,04 | 0,00 |
| 200 | 4 221,11 | 4 221,11 | 0,00 |
| 500 | 10 631,53 | 10 631,51 | −0,02 |
| 2 000 | 44 101,10 | 44 101,05 | −0,05 |

Un balayage de 5 160 configurations — de 2 à 259 postes, cinq prix unitaires
porteurs de fractions de centime, quatre progressions — donne les pires écarts
suivants :

| Identité | Pire écart mesuré |
| --- | ---: |
| somme des lignes vs Total HT | `1,29` |
| Σ TVA de ligne vs TVA imprimée | `−1,03` |
| TVA imprimée vs TVA de la base imprimée | `0,01` |
| Total HT + TVA vs Total TTC | `−0,01` |

Les deux premières ne sont bornées que par la taille du bordereau. Les deux
dernières restent au centime : elles portent sur deux nombres, pas sur une
somme de *n* nombres.

## La quatrième identité

Sur le devis de démonstration, `TVA imprimée = 21 % du Total HT imprimé`. C'est
une coïncidence de ce jeu de données, pas une propriété : **trois postes
suffisent à la casser**. Trois forfaits à `100,005`, `100,0083` et `100,0116` :

| | Montant |
| --- | ---: |
| chaque ligne imprime | `100,01` HT, `21,00` de TVA |
| somme des trois lignes | `300,03` |
| **Total HT imprimé** | `300,02`  (l'exact vaut `300,0249`) |
| **TVA imprimée** | `63,01` |
| 21 % du Total HT imprimé | `63,00` |
| somme des trois TVA de ligne | `63,00` |

Le pied du devis annonce donc une TVA de `63,01` sur une base de `300,02`.

C'est la plus lourde des quatre. Les trois autres sont des contradictions de
présentation : le lecteur voit deux nombres qui ne s'accordent pas. Celle-ci
touche à l'énoncé fiscal — un document belge doit annoncer une TVA qui soit
celle de la base qu'il annonce. Une convention qui règle les trois premières
sans régler celle-ci ne suffit donc pas.

## Ce que l'ADR disait

`docs/adr/0004-pricing-engine.md`, section 2, justifie l'arrondi de
présentation ainsi :

> L'API expose les deux : `total_selling_price_ht` (brut) et
> `total_selling_price_ht_display` (arrondi selon la politique de **cette**
> version). Sinon chaque client refait l'arrondi à sa façon et les totaux
> divergent.

L'objectif est donc bien que les totaux ne divergent pas. Ils divergent, à
l'intérieur d'un seul document.

## La décision prise : option A

**Le document remis au client s'additionne exactement.**

    Total HT       = somme des totaux de ligne imprimés et inclus dans le total
    TVA d'un taux  = arrondi(taux x base taxable IMPRIMÉE de ce taux)
    Total TTC      = Total HT imprimé + TVA imprimées

C'est la convention de la facturation : sur une facture, les nombres imprimés
doivent s'additionner, et la TVA porte sur la base telle qu'énoncée.

Sur le devis de démonstration, le Total HT imprimé passe de `99 097,08` à
`99 097,07`, et le TTC de `119 907,46` à `119 907,47`. Sur les trois forfaits
du contre-exemple : Total HT `300,03`, TVA `63,01`, TTC `363,04`.

### Ce que ça coûte

Le total imprimé s'écarte du total exact, d'autant plus que le bordereau est
long. C'est le prix assumé de la cohérence : un lecteur qui additionne trouve
ce que le document annonce.

`total_selling_price_ht_raw` et `total_ttc_raw` restent les valeurs non
arrondies. Le calcul interne, le stockage et l'empreinte des instantanés
s'appuient dessus.

### Ce qui ne suit pas, et ne peut pas suivre

**La somme des TVA imprimées poste par poste n'égale pas la TVA du pied.** La
TVA porte sur la base d'un taux, pas sur chaque ligne prise isolément : c'est
le traitement fiscal, et c'est ce que la ligne « TVA 21 % » énonce. Le
document n'imprime d'ailleurs pas de colonne TVA par poste.

### Les devis déjà gelés

Un devis figé avant ce changement **affichera** désormais les montants de la
nouvelle convention : `recompute_from_snapshot` rejoue le moteur sur les
*entrées* de l'instantané, et c'est la mise en forme qui change. Un Total HT
peut donc bouger d'un centime entre hier et aujourd'hui sur un devis gelé.

Ce qui ne bouge pas :

| | |
| --- | --- |
| `snapshot_sha256` | l'empreinte porte sur l'instantané **stocké**, que rien ne réécrit |
| `total_selling_price_ht`, `total_ttc` en base | valeurs **brutes**, non arrondies |
| `total_selling_price_ht_raw` | la somme exacte, inchangée |

Un devis gelé reste donc comparable à lui-même et vérifiable.
`apps/api/tests/test_quote_arithmetic.py` le prouve : il gèle une version, la
relit depuis son instantané, vérifie les quatre identités sur ce relu, et
vérifie que l'empreinte et les valeurs brutes n'ont pas bougé.

**Un devis déjà remis à un client sur papier ou en PDF n'est pas rétroactivement
corrigé** : le fichier qu'il détient garde ses anciens nombres. C'est une
différence d'un centime sur un petit bordereau, jusqu'à un peu plus d'un euro
sur cinq cents postes. À signaler au commerce avant la mise en production.

### Les options et lignes exclues

Sémantique inchangée : une option est chiffrée et reste hors du Total HT, donc
hors de la base taxable. `options_total_ht` continue de l'exposer à part.

## Les options qui n'ont pas été retenues

### B — Une ligne d'écart d'arrondi

Le total reste l'arrondi de la valeur exacte, et le document porte une ligne
supplémentaire qui absorbe la différence. Honnête, et courant en comptabilité,
mais une ligne « écart d'arrondi » sur un devis d'appel d'offres se remarque et
se discute — et il en aurait fallu une seconde sur la TVA.

### C — Ne rien changer

Aurait laissé un devis de cinq cents postes se contredire d'un euro et demi, et
un devis annoncer une TVA qui n'est pas celle de sa base. Sur un devis, un
client conteste ; sur la facture qui en découle, c'est l'administration.

## Ce qui n'est pas en cause

Le gel. `snapshot_sha256` porte sur les valeurs non arrondies, identiques sur
les deux moteurs (voir la PR sur l'écriture canonique). Un devis gelé reste
comparable à lui-même.
