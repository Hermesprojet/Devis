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

---

# L'orthographe des nombres : quatre surfaces, deux écritures

**Ce chapitre ne parle pas d'arrondi.** Aucune valeur n'y change, aucune
décimale n'y est décidée, et le `snapshot_sha256` d'un devis gelé n'en dépend
pas. Il parle de la façon d'ÉCRIRE un nombre que le moteur a déjà arrêté.

## Le constat

Les captures du parcours montraient, pour une seule et même quantité :

| Où | Ce qui s'affichait |
| --- | --- |
| L'aperçu d'une reprise de mesure | `6,0200 m` |
| La ligne du bordereau | `6.02` |
| Le PDF du devis | `6.02` |
| Le statut de cette ligne | `proposed` |

Quatre écritures pour un seul chiffre, et un mot anglais sur un écran
autrement entièrement français. Un métreur belge qui relit son bordereau ne
peut pas dire si `6.02` et `6,0200` sont le même nombre — ils le sont.

## Les quatre surfaces d'un devis, et ce que chacune porte

Un même devis se lit à quatre endroits, et ils n'ont pas le même lecteur :

| Surface | Lecteur | Écriture |
| --- | --- | --- |
| L'écran (bordereau, étude, devis public, tableau des devis) | une personne | **belge** : virgule, espace fine insécable U+202F |
| Le PDF remis au client | une personne | **belge**, avec l'espace insécable ordinaire U+00A0 |
| L'aperçu HTML imprimable | une personne | **belge**, U+202F |
| Le CSV | **une machine** — un tableur, un outil, la répétition de préproduction | **canonique** : point décimal, aucun séparateur de milliers |

**Le CSV reste à l'orthographe machine, et c'est délibéré.** Son en-tête dit
qu'il doit « tenir tout seul, détaché de l'application » ; `ops/parcours_devis.py`
le relit pour vérifier qu'un devis restauré porte les mêmes nombres, et
`money.to_decimal` ne sait pas relire une virgule précédée d'une espace
insécable — vérifié : `to_decimal("5 620,00")` lève `InvalidOperation`. Changer
l'écriture du CSV n'est pas une correction de présentation, c'est un changement
de format d'échange : il appartient au propriétaire de le demander, et il
demanderait sa propre migration de lecture.

**Un nombre écrit à la belge est un cul-de-sac.** Il ne doit jamais atteindre
un instantané, une base, un calcul ni un export machine. C'est pourquoi la
transcription se fait au tout dernier moment, à l'endroit du rendu, et jamais
à la source.

## Où la transcription se fait, et pourquoi

`services/lisible.nombre_francais_tel_quel` côté serveur, et
`apps/web/src/lib/nombres.ecrireEnFrancais` côté écran, font exactement le même
geste : point remplacé par une virgule, milliers groupés. **Ni l'une ni l'autre
n'arrondit, ne choisit une décimale, ni ne passe par un flottant.**

La règle du dépôt — « un nombre destiné à être LU est rendu par le serveur, et
l'écran ne le recalcule pas » — vise les deux arrondis qui finiraient par
diverger d'un chiffre. Une transcription ne décide rien : elle ne peut pas
diverger. Ce qui reste rendu par le serveur est tout ce qui se DÉCIDE :

- le nombre de décimales d'une **mesure**, qui vient de son incertitude
  (`quantite_lisible`, `incertitude_lisible`) ;
- l'écriture d'une **quantité de bordereau** (`quantite_de_document_lisible`,
  champ `quantity_lisible`) : le nombre canonique du moteur, transcrit, sans
  zéro ajouté ni décimale retirée — c'est celui que l'étude affiche et que le
  PDF imprime ;
- le symbole d'une **unité** : « m² » là où le code dit « m2 ».

## Les deux mondes, et le seul endroit où ils se touchent

`quantite_lisible` sert le monde de la **mesure** : « 6,0200 m » dit jusqu'où
la cote est connue, et ses zéros ne sont pas décoratifs.
`quantite_de_document_lisible` sert le monde du **document** : son nombre sera
multiplié par un prix unitaire et imprimé sur un devis.

Les deux se touchent à un seul endroit : **l'aperçu d'une reprise**, qui
annonce ce qu'une ligne de bordereau portera. Il emploie désormais la règle du
document. Montrer « 6,0200 m » puis écrire « 6,02 m » était annoncer autre
chose que ce qu'on fait.

> **Un défaut trouvé en chemin.** L'aperçu appliquait au nombre **corrigé par
> un humain** l'incertitude calculée par la machine, alors que l'écran de
> mesure s'en abstient délibérément pour ce même nombre : une valeur relevée au
> décamètre n'hérite pas de la finesse du pixel. Les deux routes voisines
> rendaient deux orthographes. La question ne se pose plus ici, puisque
> l'aperçu ne tire plus ses décimales de l'incertitude.

## Les statuts

Trois listes d'états bornées par le serveur s'affichaient en anglais :
`BoqItem.status`, le statut d'un chantier et celui d'une version de
bibliothèque. Elles sont traduites dans `apps/web/src/lib/i18n.ts`, sous des
clés préfixées — `boq.status.*`, `projects.status.*`,
`priceBook.versionStatus.*`. Le préfixe n'est pas cosmétique : `proposed` et
`rejected` appartiennent à DEUX énumérations différentes — celle d'une ligne de
bordereau (`proposed`, `verified`, `approved`, `rejected`) et celle d'une
proposition de plan (`proposed`, `accepted`, `corrected`, `rejected`). Un
dictionnaire unique traduirait l'une par l'autre.

**La valeur stockée ne change pas.** C'est le libellé qui est traduit, jamais
le code : la contrainte `ck_boq_item_status` borne toujours les mêmes quatre
chaînes, et l'API les rend telles quelles.

## Ce que cette passe ne change pas

- Aucune valeur stockée, aucune empreinte, aucun total.
- Aucune règle d'arrondi métier : la `RoundingPolicy` de l'entreprise décide
  toujours seule, et la transcription n'y touche pas.
- **Les devis déjà émis.** Leur PDF est lu sur le volume, octet pour octet, et
  son `pdf_sha256` le prouve. Un devis émis avant cette passe garde son
  document tel qu'il a été remis — c'est la même réserve que pour l'arrondi,
  et pour la même raison : on ne réécrit pas un document déjà entre les mains
  d'un client.

## Ce qui reste ouvert, et qui est une décision de chiffrage

**Une quantité reprise d'un plan porte dix décimales.** La mesure est quantisée
à dix décimales à son calcul, la conversion d'unité en produit autant, et la
colonne `boq_items.quantity` les conserve. Pour une cote ronde — 6 020 mm
corrigée à la main — cela ne se voit pas. Pour une cote **mesurée**, qui ne
tombe jamais juste, cela se voit.

**L'essai sur un plan réel l'a montré, et c'était le moment prévu pour le
voir.** Sur le plan d'étage d'un immeuble, au 1/50, la dalle d'un balcon,
pointée à ses quatre coins puis confirmée, a donné une ligne de bordereau de
6,3787950927 m². Ce que chaque surface en écrivait :

| Où | Avant la correction | Après |
| --- | --- | --- |
| La mesure, à l'écran de lecture | `6,379 m²` — décimales tirées du ± 0,041 m² | inchangé : c'est le monde de la mesure |
| L'aperçu de la reprise, « quantité qui sera écrite » | `6,378795 m²` — plafonné à six décimales | `6,3787950927 m²` |
| La ligne du bordereau | `6,378795 m²` | `6,3787950927 m²` |
| L'étude de prix | `6,3787950927` | inchangé |
| Le PDF remis au client | `6,3787950927` | inchangé |

**Ce qui a été corrigé, et ce qui ne l'a pas été.** L'aperçu annonçait un
nombre que la ligne ne portait pas, et l'écran disait autre chose que le
document. Ce n'était pas une question d'arrondi : l'écriture du bordereau
plafonnait à six décimales et complétait à deux, quand l'étude et le PDF
transcrivent le texte du moteur. Les quatre surfaces du document écrivent
désormais le même texte — `test_une_quantite_a_dix_decimales_s_ecrit_pareil_de_l_apercu_au_pdf`.
**Aucune valeur n'a changé** : ni la quantité stockée, ni le montant, ni
l'empreinte d'un devis gelé.

**La décision, elle, reste à prendre, et elle est maintenant visible avant
l'émission** : un devis qui imprime « 6,3787950927 m2 » n'est pas présentable.
Trois sorties, et aucune n'est de la présentation :

1. **Quantiser la quantité au moment de la reprise**, à la précision que son
   incertitude justifie — c'est-à-dire au nombre que la personne a vu et
   confirmé : 6,379 m² ± 0,041. C'est la règle que `lisible.decimales_utiles`
   applique déjà à l'affichage d'une mesure, portée à la valeur reprise.
   Défendable — *une quantité n'est jamais connue mieux que son incertitude*,
   et la provenance dit déjà « confirmée : 6,379 m² » — mais elle change le
   nombre qui chiffre le devis.
2. **Arrondir à l'impression**, comme on arrondit un montant. Il faudrait alors
   vérifier que le document s'additionne toujours de tête, ce qui est la raison
   d'être de tout ce qui précède.
3. **Laisser la personne trancher**, ce que le produit permet déjà sans
   règle nouvelle : au lieu de *Confirmer*, *Corriger* la mesure en saisissant
   la valeur retenue — 6,38 — avec son motif. La ligne porte alors ce nombre,
   et le devis l'imprime tel quel.

La troisième marche aujourd'hui, à la main, mesure par mesure. Les deux
premières sont des règles, et une règle de chiffrage appartient à l'entreprise.
