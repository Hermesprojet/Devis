# Arrondi : les documents ne s'additionnent pas

Portée : le devis remis au client — export CSV et aperçu imprimable — et les
totaux que l'API renvoie. Ce document constate un défaut mesuré, expose ses
conséquences et pose la décision. **Il ne la tranche pas** : les deux
conventions possibles changent des montants imprimés.

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

## La décision à prendre

Trois conventions existent, et elles ne donnent pas le même devis.

### A — Le total est la somme des lignes imprimées

Le document devient cohérent par construction : `Total HT` est la somme des
totaux de ligne tels qu'imprimés, la TVA se calcule sur cette base imprimée, et
`Total TTC` est la somme des deux montants imprimés.

C'est la convention de la facturation : sur une facture, les nombres imprimés
doivent s'additionner, et la TVA porte sur la base telle qu'énoncée.

Conséquence : le total imprimé s'écarte du total exact, d'autant plus que le
bordereau est long. `total_selling_price_ht` (brut) reste la valeur de
référence pour le calcul interne et pour le gel.

Cette convention règle les quatre identités d'un coup, à condition d'aller
jusqu'au bout : la TVA doit se calculer sur la base **imprimée**, pas sur la
somme exacte. Sur les trois forfaits ci-dessus, elle donnerait Total HT
`300,03`, TVA `63,01`, Total TTC `363,04` — au lieu de `300,02` / `63,01` /
`363,03`. Le TTC change de un centime : c'est le prix de la cohérence.

Question ouverte que cette convention pose et ne referme pas : faut-il calculer
la TVA sur la base imprimée globale, ou additionner les TVA de ligne imprimées ?
Les deux divergent (`63,01` contre `63,00` sur le même exemple), et le choix
relève du traitement fiscal, pas de la technique.

### B — Une ligne d'écart d'arrondi

Le total reste l'arrondi de la valeur exacte, et le document porte une ligne
supplémentaire, visible, qui absorbe la différence.

Honnête, et courant en comptabilité. Mais une ligne « écart d'arrondi » sur un
devis d'appel d'offres se remarque, et se discute.

Cette convention règle la première identité et la troisième. Elle ne règle pas
la quatrième : la TVA imprimée reste celle de la somme exacte, et la base
imprimée reste, elle, la somme exacte arrondie. Il y faudrait une seconde ligne
d'écart, sur la TVA.

### C — Ne rien changer

Le défaut reste, et il faut savoir qu'il est là. C'est l'état actuel, et c'est
ce que les tests associés verrouillent.

Choisir C, c'est accepter qu'un devis de cinq cents postes se contredise d'un
euro et demi, et qu'un devis puisse annoncer une TVA qui n'est pas celle de sa
base. Sur un devis, un client conteste ; sur la facture qui en découle, c'est
l'administration.

## En attendant

`apps/api/tests/test_quote_rounding_coherence.py` reproduit les quatre
identités et **borne l'écart** de chacune. Ce test ne dit pas que le comportement est juste :
il dit qu'il est celui-ci, et qu'il ne peut pas s'aggraver sans que la suite le
signale. Si la convention est tranchée, ce test doit être supprimé et remplacé
par les identités correspondantes ; son message d'échec le rappelle.

Ce qui n'est **pas** en cause : le gel. `snapshot_sha256` porte sur les valeurs
non arrondies, identiques sur les deux moteurs (voir la PR sur l'écriture
canonique). Un devis gelé reste comparable à lui-même.
