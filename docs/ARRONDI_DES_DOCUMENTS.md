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

Deux identités que le lecteur vérifie de tête sont fausses :

    somme des lignes  ≠  Total HT          écart  0,01
    Total HT + TVA    ≠  Total TTC         écart  0,01
    99 097,08 + 20 810,39 = 119 907,47, or le devis imprime 119 907,46

Le même écart apparaît dans l'export CSV et dans l'aperçu HTML, qui est le
document effectivement remis.

## La cause

`EstimateResult.to_dict` arrondit **chaque** montant indépendamment, à partir
de la valeur non arrondie :

    total_selling_price_ht  =  arrondi(Σ lignes non arrondies)
                            ≠  Σ arrondi(ligne)

    total_ttc               =  arrondi(HT non arrondi + taxes non arrondies)
                            ≠  arrondi(HT) + arrondi(taxe)

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

### B — Une ligne d'écart d'arrondi

Le total reste l'arrondi de la valeur exacte, et le document porte une ligne
supplémentaire, visible, qui absorbe la différence.

Honnête, et courant en comptabilité. Mais une ligne « écart d'arrondi » sur un
devis d'appel d'offres se remarque, et se discute.

### C — Ne rien changer

Le défaut reste, et il faut savoir qu'il est là. C'est l'état actuel, et c'est
ce que le test associé verrouille.

## En attendant

`apps/api/tests/test_quote_rounding_coherence.py` reproduit les deux identités
fausses et **borne l'écart**. Ce test ne dit pas que le comportement est juste :
il dit qu'il est celui-ci, et qu'il ne peut pas s'aggraver sans que la suite le
signale. Si la convention est tranchée, ce test doit être supprimé et remplacé
par les identités correspondantes ; son message d'échec le rappelle.

Ce qui n'est **pas** en cause : le gel. `snapshot_sha256` porte sur les valeurs
non arrondies, identiques sur les deux moteurs (voir la PR sur l'écriture
canonique). Un devis gelé reste comparable à lui-même.
