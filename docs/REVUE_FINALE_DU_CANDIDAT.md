# Revue du candidat, et ce que la correction a changé

**Deux versions, et il faut les distinguer d'emblée.**

| | |
| --- | --- |
| **Version de référence**, inchangée | `daeb7f0d7c2a12d47e1b941c0d79932a1e20d464`, branche `claude/candidat-complet` (#90, brouillon) |
| **Candidat corrigé** | branche `claude/candidat-corrige`, bâtie sur `claude/new-session-jdj11s` — SHA en tête de `docs/FICHE_DE_DECISION_MISE_EN_LIGNE.md` |

`daeb7f0` n'a pas bougé d'un octet. Les corrections vivent sur une branche
dédiée, et le candidat corrigé est une seconde branche qui l'assemble : la
comparaison entre les deux reste donc possible, commit par commit.

**Ce que relire veut dire ici.** Les douze ateliers de CI étaient verts sur
`daeb7f0`, et sa suite d'API rejouée donnait 1 605 réussites. Cette revue n'a
donc pas refait ce que les tests font : elle a cherché ce qu'aucun test ne
regarde — les contradictions entre deux écrans, les nombres affichés, les
hypothèses qu'une formule porte sans le dire, et les chemins que le parcours de
livraison ne traverse pas.

**La distinction à tenir dans tout ce qui suit.** Le prototype réalise une
**mesure assistée** : une personne déclare une échelle, pointe des points, et le
programme rend un nombre avec son incertitude propagée. Sa **justesse sur vos
plans reste à établir** — c'est l'objet de `docs/COTES_DE_REFERENCE.md`, et rien
de ce qui suit ne l'établit.

---

## A. Les trois défauts bloquants — corrigés

### A1. L'écran d'un PDF commençait par annoncer qu'aucune mesure n'était possible

**Ce qui s'affichait.** Sur chaque plan PDF, au-dessus de l'écran de mesure :

> Aucune mesure n'est exploitable : le plan ne déclare pas son unité de dessin,
> et une longueur sans unité ne veut rien dire.

Et juste en dessous, l'écran PDF, ses trois outils, et un tableau de mesures
marquées « mesurable ». La page se contredisait, et le bandeau était ce qu'on
lisait en premier. Avec lui venaient quatre champs DXF — `$INSUNITS`, version
DXF, feuilles, entités par type — affichés en tirets sur un PDF.

**Pourquoi.** `LecturePlan.tsx` rendait `ConstatDuPlan` sans condition, avant de
choisir entre l'écran DXF et l'écran PDF. Le serveur, lui, n'a jamais menti :
`mesurable` vaut faux pour un PDF parce qu'un PDF ne porte aucune unité de
dessin. C'était la TRADUCTION à l'écran qui était fausse — écrite pour un DXF
sans `$INSUNITS`, où elle est juste.

**Ce qui a été fait.**

1. **Le constat DXF ne s'affiche plus que pour un DXF.**
2. **L'introduction est propre à chaque format.** Celle du PDF dit le geste :
   « Un PDF ne porte aucune unité de dessin : Metreo ne peut rien y mesurer
   seul. Vous déclarez l'échelle sur une cote que vous connaissez, puis vous
   POINTEZ ce qu'il faut mesurer. » L'ancienne phrase unique — « Aucune valeur
   n'est convertie : elles sont affichées dans l'unité du document » — décrivait
   un DXF et laissait croire que l'unité d'une mesure PDF venait du fichier.
3. **Un fil d'état en trois étapes** — déclarer l'échelle, pointer et mesurer,
   décider — déduit de la base et non d'un compteur local, avec l'étape en cours
   mise en avant et ce qu'elle attend. L'écran montrait des outils et un badge,
   et laissait deviner que l'un conditionnait l'autre.
4. **Deux phrases d'explication à leur place** : pourquoi on pointe dans la
   loupe et non sur l'aperçu, et le fait que la loupe se déplace sans perdre les
   points posés — ce qui est la seule façon de calibrer sur une cote plus longue
   qu'elle.
5. **Une contradiction de plus, trouvée en relisant les captures** :
   l'en-tête affichait « Textes lus : aucun — plan probablement scanné » pendant
   que le bas de l'écran annonçait « 4 textes affichés sur 4 lus ». L'une parlait
   du SEUIL d'extraction, l'autre du nombre lu. Le compte est désormais affiché,
   et la réserve posée à côté.

**Éprouvé par** : `apps/web/captures/parcours-pdf.spec.ts`, qui échoue si
`plan-constat` apparaît sur un PDF ou si l'introduction ne parle pas de pointage.

### A2. Les valeurs étaient affichées à dix décimales

**Ce qui s'affichait**, relevé sur les captures :

| Avant | Après |
| --- | --- |
| `4180.6822810844 mm` | `6 006,3 mm` |
| `± 26.3682553403 mm` | `± 2,3 mm` |
| `16.2046806767 m2` | `24,060 m²` |
| `555.8912386702 mm par point` | `25,02 mm par point` |

Dix décimales pour une valeur dont l'incertitude valait vingt-six millimètres :
le dixième de picomètre écrit avec la même autorité que le mètre. Avec, dans la
même cellule, un point décimal anglo-saxon à côté de la virgule que
l'utilisateur venait de taper.

**Ce qui a été fait.**

- **Un module `lisible.py`, côté serveur.** La règle d'affichage est celle de la
  métrologie : l'incertitude garde deux chiffres significatifs, et la valeur
  s'arrête à la même décimale. Elle vit côté serveur pour la même raison que
  `facteur_lisible` — deux arrondis, un en Python et un en TypeScript,
  finiraient par diverger d'un chiffre. **La précision de calcul ne change
  pas** : `Amount` quantise toujours à dix décimales, et l'API rend la valeur
  exacte à côté de sa version lisible.
- **Virgule décimale et espace insécable étroit** (U+202F) entre les milliers,
  écrits à la main plutôt que par `locale` : la locale d'un conteneur dépend de
  l'image, et un nombre qui change d'écriture selon la machine est exactement ce
  qu'on ne veut pas.
- **`m²` et `m³`** là où le code dit `m2` et `m3`.
- **La valeur corrigée est validée comme une quantité.** `after_value` restait
  un dictionnaire libre : « 3,8 m environ » partait en base et revenait à
  l'écran. Sept frappes plausibles sont désormais refusées par un 422 nommé —
  valeur absente, non numérique, nulle, négative, unité différente de celle de
  la mesure. La virgule belge est acceptée et **normalisée avant l'écriture** :
  le premier lecteur qui oublierait de la convertir lirait zéro.
- **L'unité est affichée et non saisissable** à côté du champ de correction : on
  corrige un nombre, pas une dimension.
- **Deux colonnes au lieu d'une** : « Mesure calculée » et « Valeur retenue ».
  Les fondre laissait croire qu'une proposition machine était déjà une quantité,
  et qu'une correction remplaçait la mesure. Ni l'un ni l'autre n'est vrai.
- **Le motif de la décision est conservé et affiché** à côté d'elle. « 3,80 m »
  ne vaut que si l'on sait, dans six mois, d'où ce nombre vient.
- **Une mesure rejetée ne peut pas alimenter un bordereau**, et l'écran le dit.
  `reprenables()` tient la règle en un seul endroit : seules les décisions
  « confirmée » et « corrigée » retiennent une valeur ; un rejet et une mesure
  non tranchée n'en retiennent aucune. Aucun code ne reprend encore de mesure
  dans un bordereau — la fonction est donc en avance sur son appelant, et c'est
  assumé : une promesse de produit sans point d'application se perd au premier
  développeur qui ne l'a pas lue.

**Éprouvé par** : `test_lisible.py` (30 tests), `test_decision_sur_une_mesure.py`
(20 tests), et le parcours de captures.

### A3. Une purge d'organisation ne détruisait pas les plans — et, en vérité, échouait

**Ce que la vérification a trouvé, et qui est pire que ce qui était signalé.**
Le rapport précédent disait que les ORIGINAUX survivaient à une purge. En
écrivant le test avec des fichiers factices, le constat a été autre :

```
sqlite3.IntegrityError: published document revision is immutable
[SQL: DELETE FROM organizations WHERE organizations.id = ?]
```

**La purge échouait entièrement.** Pas « laissait des fichiers » : échouait.
Toute organisation ayant déposé un document — un plan, un métré, n'importe quoi
— était indestructible, et l'écrit de purge restait en `executing` sans que rien
ne soit détruit.

**Pourquoi.** `20260828_0003` pose un déclencheur d'immuabilité sur
`document_revisions` : une révision publiée ne peut être ni modifiée ni
supprimée. La règle est juste et doit le rester — c'est elle qui fait qu'une
citation rouverte retrouve le texte qu'elle citait. Mais elle ne prévoyait
aucune exception, et la suppression d'une organisation cascade jusque-là.

**Ce qui a été fait** — migration `a4b5c6d70809` :

- la **modification** d'une révision publiée reste refusée, toujours ;
- la **suppression** devient possible à une seule condition, celle que
  `20260831_0004` avait déjà posée pour les devis émis : il existe pour cette
  organisation une purge en `executing` dont `authorized_until` n'est pas
  dépassé, **selon l'horloge de la base**. Même porte, même clé ;
- les **originaux sont inscrits au registre**, avant leurs dérivés —
  `retirer_les_fichiers` parcourt le registre dans l'ordre, et une interruption
  doit laisser des dérivés sans original plutôt que l'inverse.

Ce qui aurait été plus simple et qui serait faux : retirer le déclencheur de
suppression. Il ne protège pas d'une purge, il protège d'un `DELETE` ordinaire —
celui d'un script de maintenance ou d'une migration maladroite. Le retirer pour
faire passer la purge reviendrait à ouvrir la porte à tout le monde pour laisser
entrer une seule personne.

**Éprouvé par** : `test_purge_des_plans.py` (7 tests), qui recensent les
**fichiers réellement présents** sous la racine de stockage, avant et après — un
test qui compterait des lignes passerait exactement comme le code passait. Ils
couvrent plusieurs révisions du même document, un plan jamais analysé (le cas le
plus fréquent, et celui qu'on oublie), et **l'isolation entre organisations** :
purger A ne nomme ni ne touche aucun fichier de B.

---

## B. Les écarts d'incertitude — corrigés

### B1. Sur une page en portrait, l'incertitude annoncée était 1,41 fois trop petite

L'écran déclarait `resolution_du_pointage = largeur_de_la_page × 0,05 / 512`,
tandis que `rendu_pdf.tuile` ajuste son facteur sur le **plus grand côté** de la
zone. Les deux coïncident en paysage — vos quatre plans le sont tous — et
divergent de 1,41 en portrait, **dans le mauvais sens** : l'écran annonçait une
mesure plus sûre qu'elle ne l'est.

Corrigé en prenant le plus grand côté. **Et éprouvé**, ce qui demandait une
fixture que le dépôt n'avait pas : le même plan, sur une page plus haute que
large. `test_le_portrait_n_annonce_pas_une_mesure_plus_sure_que_le_paysage`
échoue si la règle redevient « la largeur ».

### B2. L'incertitude d'un tracé était calculée comme s'il n'avait que deux sommets

`du_trace` valait `√2·ε / L` : la formule d'un segment à deux extrémités,
appliquée telle quelle à une polyligne de vingt sommets. Un relevé de façade en
vingt clics était annoncé aussi sûr qu'un segment droit de même longueur.

**La correction n'est pas un `√N` forfaitaire**, qui aurait puni un tracé lisse
pour une erreur qu'il ne commet pas. La sensibilité est calculée sommet par
sommet, par la dérivée exacte :

> **σ_L = ε · √( Σᵢ |û(i−1) − û(i)|² )**

Ce qui donne, et c'est ce qui la rend juste : `√2` exactement pour deux points —
l'ancienne formule était correcte dans ce cas et le reste ; **zéro** pour un
sommet intérieur aligné, parce que glisser un point le long d'une droite ne
change pas la longueur ; et une croissance en racine du nombre de sommets pour
un tracé anguleux.

Pour l'aire, le périmètre a été remplacé par la dérivée de la formule du lacet,
qui ne dépend que des deux voisins d'un sommet. Mesuré : sur une forme en L de
2 000 points carrés pointée à un demi-point près, l'approximation annonçait
**0,3 %** là où la propagation exacte donne **1,9 %**.

Le facteur, lui, reste **exactement doublé** sur une aire : elle vaut `k²·A`.

**Éprouvé par** : quatre tracés (2, 5, 20, 50 sommets) et un contrôle de la
croissance en racine. L'hypothèse **H8** de `docs/PRECISION_DES_MESURES.md`
prescrivait exactement cette expérience ; elle est levée.

### B3. L'écran PDF ne masquait pas les commandes de décision

Un compte `buyer` ou `viewer` voyait « Confirmer », « Corriger » et
« Rejeter », que le serveur refusait ensuite par un 403. L'écran DXF le faisait
déjà ; `permissions.ts` affirmait même que c'était le cas pour les deux. Le
drapeau `peutValider` est maintenant passé à l'écran PDF, et les trois commandes
sont masquées — non grisées — avec la phrase qui explique l'absence.

---

## C. La démonstration : une géométrie, et non des textes isolés

Le parcours de captures travaillait sur `plan_cote.pdf`, qui ne porte que des
textes. La calibration y agrandissait « 00 » et la surface mesurait un fragment
de « Coupe A-A ». Les gestes étaient les bons, le résultat ne voulait rien dire.

`plan_batiment.pdf` porte, sur 420 × 320 points : une **ligne de cote** de
200 points avec ses deux traits de rappel — sans eux, « cliquez les deux
extrémités » n'est pas une consigne exécutable — et une **pièce fermée** de
240 × 160 points. L'échelle du dessin est déclarée dans la fabrique : un point
vaut 25 mm. La cote fait donc 5 000 mm, la pièce 6 000 × 4 000 mm, sa surface
**24,00 m²** — jamais écrits sur le dessin, jamais lus par le lecteur, et
publiés dans `plan_batiment.json` pour que le parcours de navigateur puisse les
comparer sans recopier un seul chiffre.

**Ce que le parcours relève, en pilotant l'application réelle :**

| Attendu | Rendu par Metreo | Écart |
| --- | --- | --- |
| façade 6 000 mm | **6 006,3 mm ± 2,3 mm** | **0,11 %** |
| séjour 24,00 m² | **24,060 m² ± 0,016 m²** | **0,25 %** |

Et il **échoue** au-delà de 1 % sur la longueur, 2 % sur la surface. Ce n'est
plus une démonstration, c'est une vérification.

La calibration s'y fait en **deux positions de loupe** : la cote fait 200 points
et la loupe en couvre 21, donc ses deux extrémités n'entrent pas dans la même
fenêtre. C'est le geste réel sur un plan de grand format, et l'écran l'annonce
désormais.

---

## D. L'espace de mesure

Les captures montraient un aperçu, puis une très grande loupe EN DESSOUS, puis
les formulaires, puis le tableau des résultats encore plus bas. Nommer une
mesure obligeait à quitter le dessin des yeux ; vérifier un tracé, à remonter.

- **Deux colonnes** : le plan à gauche, ce sur quoi on agit à droite. La colonne
  de gauche est **collante** — le plan reste à l'écran pendant qu'on agit et
  pendant qu'on relit la liste. Une seule colonne sous 960 px, où deux
  rendraient la loupe trop étroite pour qu'on y pointe.
- **La mesure créée ou sélectionnée affiche son tracé** : la ligne en évidence
  sur l'aperçu ET dans la loupe, et **ses sommets marqués**. Afficher le trait
  ne suffit pas à vérifier ce qui a été pointé — deux extrémités mal posées
  donnent le même trait, décalé. Les sommets sont ce qui permet de dire « ce
  point-là n'est pas sur l'angle du mur ».
- **Un panneau « ce que je vérifie »** à côté du dessin, qui répète la valeur
  calculée et la valeur retenue. La redondance est le but : vérifier un tracé
  demande d'avoir les deux sous les yeux.

---

## E. La voie de retour arrière A, mesurée

Elle était classée « la plus réversible » sur un raisonnement.
`scripts/epreuve_retour_arriere.py` la joue maintenant pour de bon : il monte le
schéma neuf, y dépose un plan, le calibre, le mesure, tranche — puis fait jouer
**l'API de `origin/main` (39ad8d0)** sur cette même base, **sans toucher aux
migrations**.

**Seize parcours sur dix-neuf fonctionnent.** Se connecter, lister chantiers,
clients et bibliothèque, ouvrir le chantier créé par la version neuve, lister
ses documents, **télécharger l'original du plan déposé pendant l'essai**, lire
le journal d'audit, créer un client, un chantier, déposer un document — tout
passe. La chaîne d'audit écrite par la version neuve **reste valide** sous
l'ancien code (`valid: True`, 12 maillons). Les données de l'essai — 1
proposition, 1 calibration, 1 décision — **sont toujours en base**.

Les trois exceptions sont celles qu'on attend : `GET …/plan` et `…/plan/mesures`
rendent **404**, ces routes n'existant pas dans l'ancienne version ; et relancer
les migrations sort en **255**, `Can't locate revision identified by
'a4b5c6d70809'` — ce qui est précisément la raison d'être de cette voie.

Le détail est dans `docs/MISE_EN_LIGNE_LECTURE_DE_PLANS.md`, section « Ce que
l'ancienne API fait VRAIMENT sur le schéma neuf ».

---

## F. Ce qui est validé, et par quoi — les trois niveaux séparés

### F1. Validé par les tests du dépôt

Tout ce qui précède, plus les propriétés déjà couvertes : isolation par
organisation (404 et jamais 422), registre transactionnel, séparation
`DOCUMENT_WRITE` / `DOCUMENT_VALIDATE`, tête de migration unique, plafonds du
cache de tuiles, politique de refus total sur les en-têtes d'image, absence de
secret commité.

**La justesse de la chaîne de mesure est validée sur une géométrie connue** —
0,11 % et 0,25 % d'écart — et c'est une propriété arithmétique, pas
métrologique.

### F2. Sur vos plans réels : rien

**Les quatre plans ne sont pas accessibles dans cet environnement.** Le
conteneur de cette session a été recréé, et le dossier privé qui les portait
n'existe plus ; `/tmp/claude-0/prive/` est absent. Ce qui reste du travail
d'octobre est une copie de la fixture fabriquée, pas un plan.

**Ce qu'il faudrait pour cette validation**, précisément :

1. les quatre fichiers remis à nouveau dans cette session, **hors du dépôt** —
   ils ne seront ni commités ni transmis à un tiers ;
2. **au moins trois cotes de référence par plan**, relevées par vous ou lues au
   cartouche, avec pour chacune : la page, une description de l'endroit
   (« cote 5000 de la façade sud, entre les deux traits de rappel »), la valeur
   et son unité. Une seule cote ne distingue pas une erreur d'échelle d'une
   erreur de pointage ;
3. idéalement, **une cote longue et une cote courte sur le même plan** — c'est
   ce qui montre si l'erreur suit la longueur ou non — et **une cote oblique**,
   qui est la seule à éprouver l'isotropie des deux axes.

`docs/COTES_DE_REFERENCE.md` (PR #88) décrit la fiche à remplir. Tant qu'elle
est vide, « Metreo mesure juste sur vos plans » reste une opinion — la mienne
comprise.

### F3. En préproduction : rien

**Aucun déploiement, aucune publication d'image, aucune modification du VPS.**
Les dernières observations du serveur datent du **16 septembre 2026**, et rien
depuis cette date n'est une observation.

Ce qui a été éprouvé de la mise en ligne l'a été **hors machine** : la voie A
sur deux processus Python et un fichier SQLite, le refus de migrer de
l'ancienne image, le comportement du schéma. Ce qui ne l'est pas : le
comportement de `docker compose` derrière le proxy, les déclencheurs
PostgreSQL de la migration `a4b5c6d70809` — SQLite et PostgreSQL n'ont pas les
mêmes, et seule la CI les éprouve sur un vrai PostgreSQL — et la restauration
d'une sauvegarde réelle.

---

## G. Ce qui reste ouvert

| | Pourquoi ce n'est pas corrigé ici |
| --- | --- |
| Pas de pré-génération des tuiles, pas de purge LRU | Le cache est borné par deux plafonds, jamais rafraîchi. Une tranche à part, avec sa propre mesure |
| Le cache de tuiles est **local à une instance** | Deux conteneurs d'API ne le partagent pas. Sans objet tant qu'il n'y en a qu'un |
| Un seul navigateur éprouvé : Chromium | Ajouter Firefox et WebKit double le temps de la CI pour un parcours qui n'utilise rien d'exotique |
| La route de tuile ne vérifie pas que la révision est un PDF | Sur un DXF, le fils échoue et l'API rend un 422 `rendu_impossible`. Correct, mais le message ne dit pas la vraie cause |
| Une calibration de ZONE peut diverger de ce que l'en-tête affiche | Inatteignable par l'écran, qui n'envoie jamais de `zone`. Chaque ligne de mesure affiche le motif de la calibration **réellement utilisée** : la provenance par mesure est juste |
| H7 — le biais de pointage | La quadrature suppose une erreur aléatoire. Un biais systématique n'apparaît nulle part, et se mesure par 5 à 10 répétitions de la même cote — sur vos plans |
| H11 — l'indépendance entre deux mesures | Le terme du facteur est **le même** pour toutes les mesures d'une page : il est totalement corrélé. Un total de 20 murs n'a pas une incertitude en √20 mais en 20. À traiter quand les mesures remonteront dans un métré |
