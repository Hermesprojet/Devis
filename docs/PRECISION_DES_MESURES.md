# Ce que vaut une mesure prise sur un PDF

> **Lu sur** : `/home/user/Devis`, branche `claude/corrections-du-candidat`.
>
> **Aucun SHA n'est recopié dans ce document.** Un numéro de commit écrit à la
> main se périme au commit suivant, en silence, et c'est arrivé une fois ici.
> Le SHA de la livraison est celui que le message de livraison porte ; ce
> document décrit la branche.
>
> **Épreuves relancées pour ce document** :
>
> ```
> .venv/bin/python -m pytest apps/api/tests/test_mesures_pdf.py \
>   apps/api/tests/test_calibration_de_plan_api.py \
>   apps/api/tests/test_mesure_juste_sur_un_plan.py -q
> ```
>
> La même commande lancée avec le `pytest` du `PATH` ne se rejoue pas : elle
> échoue avant de collecter, sur `ImportError while loading conftest … No module
> named 'fastapi'`, parce que ce `pytest` n'est pas celui de l'environnement du
> projet. Il faut donc passer par le `python` du venv, ce qui est aussi la
> convention écrite du dépôt (`docs/TESTING.md` et `README.md` écrivent tous
> deux `python -m pytest`, venv activé).
>
> Et, par le navigateur, à chaque livraison :
>
> ```
> cd apps/web && npx playwright test --config=playwright.premier-devis.config.ts
> ```
>
> Ce document sépare ce qui est **vérifié sur des fixtures fabriquées** de ce
> qui reste **inconnu sur vos plans**. Il présente les incertitudes avec leurs
> hypothèses. Il dit, en § 2.0, ce que le « ± » affiché signifie exactement, et
> en § 3 ce que le nombre 0,9 est et n'est pas.
>
> **Renvois.** Les renvois de ce document désignent un fichier et un
> **symbole** — fonction, constante, test, champ, section — et non un numéro de
> ligne : un numéro se périme au commit suivant, un nom de symbole non.

---

## 1. Ce qui est vérifié, et sur quoi

### 1.1 Deux propriétés qu'il ne faut pas confondre

|  | Propriété **arithmétique** | Propriété **métrologique** |
| --- | --- | --- |
| Ce qu'elle affirme | *le calcul est juste* — 150 points déclarés à 7 500 mm donnent bien 50 mm par point, et 75 points donnent bien 3 750 mm | *le nombre correspond à l'ouvrage* — ce mur fait bien 3 750 mm |
| Ce qu'il faut pour l'éprouver | une fixture dont on connaît la réponse d'avance | une **vérité indépendante de Metreo** : une cote relevée à la main sur le chantier, ou lue dans le DXF du même plan |
| État dans le dépôt | **éprouvée**, et pas seulement par des invariants : **sept** tests comparent la mesure rendue à une valeur attendue (§ 1.3) | **aucune épreuve.** Le dépôt ne contient aucune comparaison entre une mesure Metreo et une dimension connue **indépendamment de Metreo** |

Le parcours navigateur l'écrit lui-même
(`apps/web/e2e-premier-devis/suite-plan-pdf-mesures.spec.ts`, en-tête du
fichier, § « Ce qui n'est pas vérifié ici, et pourquoi ») :

> « La **JUSTESSE** du nombre mesuré ne l'est pas : un clic de souris sur un
> rendu ne vaut pas une référence, et c'est précisément ce que
> `docs/COTES_DE_REFERENCE.md` sert à établir. »

**Et c'est ce qui a changé le 7 octobre 2026.** Un second scénario navigateur,
`apps/web/e2e-premier-devis/suite-mesure-juste-pdf.spec.ts`, mesure par le
navigateur une géométrie dont les dimensions sont connues **sans passer par
Metreo**, et **échoue si l'écran s'en écarte** de plus de 1 % en longueur ou 2 %
en surface. La colonne de droite du tableau ci-dessus reste vraie — une fixture
n'est pas un plan d'exécution — mais « aucune comparaison » ne l'est plus : il
y en a une, et elle tombe à chaque livraison.

### 1.2 Les fixtures : ce sur quoi on mesure

| Fixture | Nature | Où |
| --- | --- | --- |
| `PAGE = (0.0, 0.0, 200.0, 100.0)` et `CENT_POINTS_POUR_CINQ_METRES` | **fabriquée**, en mémoire, valeurs rondes exprès ; `scripts/fabriquer_pdf_de_test.py` la commente à ses constantes `LARGEUR_REFERENCE` / `HAUTEUR_REFERENCE` (« Petite exprès — une page A0 rendrait le calcul d'inversion illisible ») | `apps/api/tests/test_mesures_pdf.py`, constantes de module `PAGE` et `CENT_POINTS_POUR_CINQ_METRES` |
| La page de 300 × 220 points du parcours API | **fabriquée** par `scripts/fabriquer_pdf_de_test.py`, fonctions `page_avec_plusieurs_textes` et `plan_de_deux_pages` (paramètres par défaut `largeur = 300.0` / `hauteur = 220.0`) | `apps/api/tests/test_calibration_de_plan_api.py`, constantes de module de la page de référence |
| `fixtures/plans/plan_cote.pdf` (parcours navigateur) | **fabriquée**, et non commitée (`.gitignore`, entrée `fixtures/plans/plan_cote.pdf`) | produite par `scripts/fabriquer_plans_de_test.py` |
| `fixtures/plans/plan_batiment.pdf` — **une ligne de cote et une pièce fermée** | **fabriquée**, non commitée, et surtout : **ses dimensions sont connues sans passer par le lecteur**. La cote fait 5 000 mm, la pièce 6 000 × 4 000 mm, sa surface 24,00 m² — posés en points de papier dans `fabriquer_pdf_de_test`, convertis par une échelle déclarée, et écrits dans `plan_batiment.json` | `apps/api/tests/test_mesure_juste_sur_un_plan.py` et `apps/web/captures/parcours-pdf.spec.ts` |
| Le même plan **en portrait** | **fabriquée**, en mémoire. Elle existe parce que les quatre plans du propriétaire sont tous en paysage, et qu'un défaut de résolution de pointage ne se voit que sur une page plus haute que large | `plan_de_batiment_en_portrait()` |
| Les quatre plans du propriétaire | **réels, et hors du dépôt**, par la règle de `docs/PLANS_REELS.md` | constats datés reportés dans `docs/adr/0008-tuiles-de-detail-des-plans.md` |

Ces quatre plans ne sont **pas des A0**. L'ADR 0008 les décrit comme des
« **grands formats de 1 189 à 1 690 mm de grand côté** »
(`docs/adr/0008-tuiles-de-detail-des-plans.md`, § « Le problème se voit à
l'écran, et il est chiffrable »), et la fiche de cotes de référence (§ 4, voir
§ 5 ci-dessous) donne le détail :
`coupe-1-1` 1 480 × 850 mm, `elevation-nord` 1 690 × 850 mm,
`etage-1` et `etage-3` 1 189 × 914 mm. Un A0 mesure 841 × 1 189 mm : aucun des
quatre n'en est un. **Plusieurs commentaires du code disent pourtant « A0 »**
— voir § 6.

> **Où lire la fiche de cotes de référence.** `docs/COTES_DE_REFERENCE.md`
> **n'existe pas sur cette branche** : il arrive par la **PR #88** (branche
> `claude/cotes-de-reference`). Les chiffres qui en viennent — le tableau des
> quatre dimensions ci-dessus (son § 4) et le décompte des fragments numériques
> du § 2.11 H12 (son § 8) — ne sont donc pas vérifiables depuis cette branche
> prise seule. Pour les lire sans attendre la fusion :
> `git show origin/claude/cotes-de-reference:docs/COTES_DE_REFERENCE.md`.
>
> **La procédure d'essai, elle, est sur cette branche** :
> `docs/VALIDATION_SUR_PLANS_REELS.md`. Elle ne dépend d'aucune PR en attente,
> et c'est elle qu'il faut suivre le jour où les plans seront accessibles.

**Conséquence directe : aucun test du dépôt ne s'exécute sur un plan réel.**

**Ce qui a changé le 7 octobre 2026**, et qui ne remplace pas vos plans : il
existe désormais une fixture qui porte une GÉOMÉTRIE et non des textes isolés,
et dont les dimensions sont connues d'avance. `test_mesure_juste_sur_un_plan.py`
calibre sur sa ligne de cote, mesure la façade et le contour de la pièce, et
**compare à 6 000 mm et 24,00 m²** — des nombres que Metreo n'a pas produits.
Le parcours de navigateur fait le même trajet et relève **6 006,3 mm** et
**24,060 m²**, soit 0,11 % et 0,25 % d'écart.

Cela éprouve la chaîne complète — repère, calibration, calcul, affichage — sur
une géométrie connue. Cela ne dit **toujours rien** de l'écart entre ce que
Metreo rend et ce que portent VOS plans : cet écart-là est celui du pointage
d'un humain sur un vrai dessin, et il se mesure sur de vraies cotes.

### 1.3 Les tests qui comparent une mesure à une valeur attendue

Il en existe, contrairement à ce qu'une lecture rapide laisse croire. Les
voici, nommés, avec ce qu'ils établissent **et** ce qu'ils n'établissent pas.

**Dans le module pur** (`apps/api/tests/test_mesures_pdf.py`) :

| Test | Assertion | Ce qu'il compare |
| --- | --- | --- |
| `test_the_factor_is_the_real_distance_over_the_pointed_one` | `facteur(...) == Decimal("50")` | 100 points pour 5 000 mm → 50 mm/point, **égalité exacte** |
| `test_a_segment_is_measured_in_the_unit_that_was_calibrated` | `mesure.valeur == Decimal("2500")` | 50 points à 50 mm → 2 500 mm, **égalité exacte** |
| `test_a_polyline_adds_its_segments` | `== Decimal("5000")` | 50 + 50 points → 5 000 mm |
| `test_a_square_has_the_area_it_should` | `== Decimal("6.25")` | 50 × 50 points à 50 mm → 6,25 m² |
| `test_a_triangle_has_half_the_area_of_its_rectangle` | `== Decimal("3.125")` | la moitié du précédent |

**Dans le parcours API complet**
(`apps/api/tests/test_calibration_de_plan_api.py`), c'est-à-dire à travers
HTTP, la conversion écran → page, la base et la sérialisation :

| Test | Assertion | Tolérance |
| --- | --- | --- |
| `test_a_segment_is_measured_with_its_provenance` | `abs(valeur − 3750) < TOLERANCE_EN_MM` | `TOLERANCE_EN_MM = Decimal("0.01")`, soit **un centième de millimètre** |
| `test_a_closed_surface_is_measured_in_square_metres` | `abs(valeur − 20.625) < Decimal("0.001")` | **un millième de m²** |

Le fichier dit lui-même pourquoi il tolère plutôt que d'exiger l'égalité (dans
le commentaire qui précède `TOLERANCE_EN_MM`) : « les points traversent le
repère de l'écran en flottants : un quart de page ressort à 3 749,999999996
plutôt qu'à 3 750 ».

**Ce que ces sept tests établissent.** Que la chaîne entière — clic normalisé,
rotation d'affichage, boîte de page, facteur, formule du lacet, conversion
d'unité, écriture et relecture en base — **conserve l'arithmétique**, à un
centième de millimètre près sur 3 750 mm, soit **2,7 ppm**. C'est une garantie
réelle, et elle n'est pas rien : elle exclut une faute de signe, une inversion
d'axe, une projection sur un seul axe, une perte de décimales en base.

**Ce qu'ils n'établissent pas.** La valeur attendue, 3 750 mm, est calculée à
partir de `DISTANCE_DE_CALIBRATION = "7500"`, **que le test déclare
lui-même**. Aucune mesure n'est confrontée à un ouvrage, ni à une cote lue
ailleurs que dans Metreo. La formulation de la fiche de cotes de référence est
juste : sans vérité indépendante, « vérifié » voudrait dire « Metreo est
d'accord avec Metreo ».

C'est la différence exacte entre les deux colonnes du § 1.1 : ces tests portent
la propriété **arithmétique**, aucun ne porte la propriété **métrologique**.

### 1.4 Les autres propriétés éprouvées

Toutes arithmétiques ou comportementales. Fixtures fabriquées dans tous les cas.

| Propriété éprouvée | Test qui la porte (`test_mesures_pdf.py`) |
| --- | --- |
| Écran → page est l'inverse exact du lecteur, sur 4 rotations et 5 points dont les coins | `test_the_screen_to_page_conversion_is_the_exact_inverse_of_the_reader` |
| Une boîte de page qui ne commence pas à zéro est rendue en absolu | `test_a_page_whose_box_does_not_start_at_zero_is_handled` |
| Une rotation impossible est refusée, pas devinée | `test_an_impossible_rotation_is_refused_rather_than_guessed` |
| Le facteur ne dépend pas de la direction (oblique 60-80 = horizontale 100) | `test_the_factor_does_not_depend_on_the_direction_of_the_calibration` |
| Sous 10 points, refus — **et à 10 points exactement, acceptation** | `test_a_calibration_too_short_to_determine_anything_is_refused` et `test_a_calibration_at_the_threshold_is_accepted` |
| Distance ≤ 0 refusée ; unité non linéaire (`m2`) refusée | `test_a_non_positive_real_distance_is_refused` et `test_a_unit_that_is_not_a_length_is_refused` |
| Un point seul, ou deux points confondus : refus, jamais zéro | `test_a_single_point_measures_nothing_and_says_so` et `test_two_identical_points_are_refused_rather_than_measured_as_zero` |
| L'aire ne dépend ni du sens de tracé ni du sommet de départ | `test_the_area_does_not_depend_on_the_drawing_direction` et `test_the_area_does_not_depend_on_which_corner_was_clicked_first` |
| **5 m et 5 000 mm donnent la même aire** — le facteur vers le m² sort de `units.py` | `test_the_same_shape_calibrated_in_metres_or_millimetres_has_the_same_area` |
| Moins de 3 points, ou 3 points alignés : refus | `test_fewer_than_three_points_enclose_nothing` et `test_three_collinear_points_enclose_nothing_and_it_is_said` |
| Un contour qui se recoupe est **signalé, pas corrigé** | `test_a_contour_that_crosses_itself_is_flagged_not_corrected` |
| Un carré et un L ne sont **pas** signalés comme se recoupant (contre-exemple) | `test_a_simple_contour_is_not_flagged_as_crossing_itself` — il nomme désormais la réserve au lieu d'exiger une liste vide : le L porte « incertitude_elevee », et c'est juste |
| L'incertitude est rendue dans l'unité de la valeur, et concorde avec la relative | `test_the_uncertainty_is_given_in_the_same_unit_as_the_value` |
| Pointer deux fois plus fin donne une incertitude deux fois moindre | `test_a_finer_pointing_gives_a_proportionally_smaller_uncertainty` |
| Calibrer sur 20 points plutôt que 200 : **même valeur**, incertitude plus grande | `test_calibrating_on_a_short_span_poisons_every_measurement` |
| Le FACTEUR pèse exactement deux fois plus sur une aire que sur une longueur | `test_an_area_is_twice_as_uncertain_as_the_length_it_comes_from` — sur un carré, les deux termes de tracé sont eux aussi dans un rapport de 2, et l'égalité est donc exacte. Elle ne l'est pas sur une forme quelconque, et ne doit pas l'être |
| Au-delà du seuil la mesure est **conservée** et réservée ; en deçà elle ne porte **aucune** réserve | `test_an_uncertain_measurement_stays_to_be_verified_and_names_why` et `test_a_well_pointed_measurement_carries_no_reserve` |
| Une résolution de pointage non déclarée vaut `RESOLUTION_PAR_DEFAUT_EN_POINTS` et bascule en « à confirmer » | `test_an_undeclared_pointing_resolution_is_treated_pessimistically` — ses **deux** assertions : `resolution_du_pointage == RESOLUTION_PAR_DEFAUT_EN_POINTS`, puis `mesure.fiabilite == "a_confirmer"` |

Et côté serveur, une propriété qui conditionne tout le modèle d'incertitude :

| Propriété éprouvée | Test |
| --- | --- |
| **La tuile rend exactement la zone demandée**, sur les deux axes, à un point près | `apps/api/tests/test_lecture_pdf.py`, `test_a_tile_renders_exactly_the_zone_it_was_asked_for` |
| Une tuile de la taille de la loupe est plus fine que l'aperçu | `apps/api/tests/test_lecture_pdf.py`, `test_a_tile_of_a_loupe_sized_zone_is_finer_than_the_preview` |

Le premier est important : son commentaire rappelle qu'une marge de rendu avait
fait croire à l'écran que l'image couvrait la zone demandée, et que « les trois
étaient faux du même facteur » — clics, points dessinés et
`resolution_du_pointage`. Il éprouve la zone rendue **sur une page en paysage
de 300 × 220 points** ; il n'éprouve ni une page en portrait, ni le plafond
d'agrandissement (§ 2.11, H4).

### 1.5 Ce que le parcours navigateur vérifie

`suite-plan-pdf-mesures.spec.ts` couvre dépôt, aperçu, textes situés,
changement de page, loupe, calibration, mesure, correction. Sur la **valeur**,
ses deux assertions sont `toMatch(/\d/)` et `toContain('mm')` : le nombre
existe et porte son unité. Rien de plus, et c'est assumé (en-tête du fichier,
§ « Ce qui n'est pas vérifié ici, et pourquoi »).

Côté API, `test_a_segment_is_measured_with_its_provenance` ajoute
`Decimal(mesure["incertitude"]) > 0` avec le motif « aucune mesure n'est
exacte », et `test_a_coarse_pointing_keeps_the_measurement_to_be_verified`
vérifie qu'un pointage grossier dégrade en `a_confirmer`.

---

## 2. Le modèle d'incertitude, formules recopiées du code

Fichier : `apps/api/src/metreo_api/services/mesures_pdf.py`.

### 2.0 Ce que « ± » signifie, exactement

L'écran affiche « 6 001,2 ± 2,3 mm ». Trois lectures de ce « ± » sont possibles,
et elles n'engagent pas la même chose. Voici laquelle est la bonne.

> **Le « ± » affiché est une incertitude TYPE, à k = 1, propagée au premier
> ordre depuis une seule grandeur d'entrée : la résolution du pointage.**

Déplié, cela veut dire quatre choses, et chacune se vérifie dans le code.

**1. C'est un écart-type, pas une borne.** `Mesure.incertitude` est le produit
de `Mesure.incertitude_relative` par la valeur, et la relative est une somme
**en quadrature** — `math.sqrt(du_facteur² + du_trace²)`, dans `longueur` et
dans `aire`. Une somme en quadrature est l'opération des écarts-types ; une
borne s'additionnerait en valeur absolue. **Il n'y a donc aucune garantie que
la vérité tombe dans `valeur ± incertitude`**, et il n'y en a pas davantage à
deux ou trois fois cet écart : seule une distribution d'erreur, qui n'est pas
établie, donnerait un taux de couverture.

**2. Aucun facteur d'élargissement n'est appliqué.** Pas de k = 2, pas de 95 %.
Qui veut un intervalle plus prudent multiplie lui-même. Le test
`test_douze_pointages_independants_encadrent_tous_la_cote_connue`
(`apps/api/tests/test_mesure_juste_sur_un_plan.py`) vérifie la couverture à
**k = 2**, parce que c'est le seul niveau qu'un test peut exiger sans supposer
une loi de probabilité.

**3. Une seule grandeur d'entrée y contribue : ε, la résolution du pointage**,
en points de page par pixel affiché. Elle est **mesurée** sur l'image réellement
affichée (§ 2.7). Tout le reste est traité comme exact : l'échelle déclarée, la
planéité de la feuille, l'isotropie des axes, le fait que le trait pointé soit
bien la cote visée. **Les douze hypothèses du § 2.11 sont hors du ±**, et une
mesure peut être fausse d'un facteur 2 avec un ± de 0,04 % — c'est exactement
le cas si l'échelle saisie est fausse.

**4. ε est pris comme l'écart-type du pointage, ce qui est un choix
conservateur.** Un clic ne rend pas une position continue : il rend un pixel.
Si l'erreur de pointage n'était que cette quantification, uniforme sur un pixel
de largeur ε, son écart-type vaudrait ε/√12 ≈ 0,29 ε, et le modèle serait
**pessimiste d'un facteur 3,5**. Mesuré sur la fixture, douze pointages
indépendants donnent un rapport de **4,6** entre le ± annoncé et la dispersion
observée (§ 2.9bis). Cette marge est volontaire : elle couvre en partie la main
de l'opérateur, qui ne vise pas au pixel. Elle ne couvre **rien** d'un biais.

**Ce que le ± ne dit pas, et qu'il faut dire à côté.** Un biais — cliquer
toujours le bord extérieur d'un trait épais — ne se voit pas à l'écart-type et
ne s'annule pas en répétant. C'est l'hypothèse **H7**, et seule une répétition
sur un vrai dessin la lève : la procédure est au § 4.3 de
`docs/VALIDATION_SUR_PLANS_REELS.md`.

### 2.1 Les quatre constantes

| Constante | Valeur | Ce qu'elle est |
| --- | --- | --- |
| `CALIBRATION_MINIMALE_EN_POINTS` | `10.0` | un **refus**, pas une réserve. Dix points PostScript valent **3,5278 mm de papier** |
| `SEUIL_D_INCERTITUDE` | `Decimal("0.02")` | garde-fou. Le code dit lui-même, dans le commentaire qui précède la constante, que **ce n'est pas une tolérance métier** et que le propriétaire n'a pas fixé la sienne |
| `RESOLUTION_PAR_DEFAUT_EN_POINTS` | `1.0` | défaut pessimiste : qui ne déclare rien obtient le doute |
| `DECIMALES` | `Decimal("0.0000000001")` | dix décimales, comme `Amount` en base |

### 2.2 Le facteur

Dans `mesures_pdf.py`, fonction `facteur` :

```python
return (calibration.distance_reelle / Decimal(str(ecart))).quantize(
    DECIMALES, rounding=ROUND_HALF_UP
)
```

avec, pour `ecart` (propriété `Calibration.ecart_en_points`) :

```python
return math.hypot(self.second.u - self.premier.u, self.second.v - self.premier.v)
```

et le refus en deçà du plancher, en tête de `facteur` :
`if ecart < CALIBRATION_MINIMALE_EN_POINTS:`.

### 2.3 L'incertitude relative du facteur — la formule centrale

Dans `mesures_pdf.py`, fonction `_incertitude_relative_du_facteur` :

```python
epsilon = max(calibration.resolution_du_pointage, 0.0)
return Decimal(str(math.sqrt(2) * epsilon / calibration.ecart_en_points))
```

Soit :

> **ε_k / k = √2 · ε / d**

où `d` est l'écart de calibration **en points de page** et `ε` la résolution de
pointage **en points de page par pixel**.

### 2.4 Une longueur

Dans `mesures_pdf.py`, fonction `longueur` :

```python
valeur = k * Decimal(str(total_en_points))
…
du_facteur = _incertitude_relative_du_facteur(calibration)
du_trace = Decimal(
    str(
        _sensibilite_d_une_longueur(points)
        * calibration.resolution_du_pointage
        / total_en_points
    )
)
relative = Decimal(str(math.sqrt(float(du_facteur) ** 2 + float(du_trace) ** 2)))
```

> **σ_L / L = √[ (√2·ε/d)² + (S_L·ε/L_pts)² ]**, où
> **S_L = √( Σᵢ |û(i−1) − û(i)|² )**

`L_pts` est la longueur tracée en points, et `S_L` la **sensibilité du tracé** :
la racine de la somme des carrés des gradients, sommet par sommet, où `û(i)`
est le vecteur unitaire du segment `i`.

**Cette forme a remplacé un `√2` constant**, et le changement n'est pas
cosmétique. L'ancien terme était celui d'un segment à DEUX extrémités, appliqué
tel quel à une polyligne de vingt sommets : un relevé de façade en vingt clics
était annoncé aussi sûr qu'un segment droit de même longueur, alors qu'il porte
vingt erreurs de pointage. `S_L` dit trois choses qu'un `√N` forfaitaire ne
dirait pas :

- **deux points** : les deux gradients valent 1, la somme vaut 2, et on retrouve
  exactement `√2`. L'ancienne formule était juste dans ce cas, et le reste ;
- **des points ALIGNÉS** : les deux vecteurs d'un sommet intérieur sont égaux,
  leur différence est nulle, et le sommet n'ajoute rien. C'est physiquement
  vrai — glisser un point le long d'une droite ne change pas la longueur ;
- **un tracé anguleux** : à angle droit chaque sommet intérieur apporte `√2`, et
  la somme croît bien avec le nombre de sommets.

Un `√N` forfaitaire aurait puni un tracé lisse — celui qui suit une courbe, où
les sommets se compensent presque — pour une erreur qu'il ne commet pas.

### 2.5 Une aire

Dans `mesures_pdf.py`, fonction `aire` :

```python
aire_en_points = abs(double_aire) / 2.0
…
du_facteur = _incertitude_relative_du_facteur(calibration)
du_trace = Decimal(
    str(
        _sensibilite_d_une_aire(points)
        * calibration.resolution_du_pointage
        / aire_en_points
    )
)
relative = Decimal(str(math.hypot(2 * float(du_facteur), float(du_trace))))
…
facteur_en_metres = distance_en_metres / Decimal(str(calibration.ecart_en_points))
en_metres_carres = (facteur_en_metres * facteur_en_metres) * Decimal(str(aire_en_points))
```

> **σ_A / A = √[ (2·√2·ε/d)² + (S_A·ε/A_pts)² ]**, où
> **S_A = √( Σᵢ |∂A/∂Pᵢ|² )** et **∂A/∂Pᵢ = ½·( v(i+1) − v(i−1), u(i−1) − u(i+1) )**

**Les deux termes ne se doublent pas de la même façon, et c'est le correctif.**

Le **facteur** est bien doublé : une aire vaut `k²·A`, donc une erreur relative
de 1 % sur `k` en fait 2 % sur l'aire. Le **tracé**, non : son erreur se propage
par la dérivée de la formule du lacet, sommet par sommet, et non par le
périmètre. L'approximation par le périmètre valait pour un carré et se trompait
sur tout le reste — mesuré, sur une forme en L de 2 000 points carrés pointée à
un demi-point près, elle annonçait **0,3 %** là où la propagation exacte donne
**1,9 %**.

La dérivée ne dépend que des deux VOISINS d'un sommet : un sommet dont les
voisins sont proches pèse peu sur l'aire, un sommet qui sépare deux côtés longs
pèse beaucoup. C'est ce que le périmètre ne savait pas dire.

### 2.6 Le verdict

Dans `mesures_pdf.py`, fonction `_verdict` :

```python
absolue = (valeur * relative).quantize(DECIMALES, rounding=ROUND_HALF_UP)
if relative > SEUIL_D_INCERTITUDE:
    reserves.append("incertitude_elevee")
fiabilite: Fiabilite = "a_confirmer" if reserves else "mesurable"
```

**Toute** réserve dégrade, pas seulement celle d'incertitude.

### 2.7 D'où vient ε — et pourquoi il est désormais MESURÉ

`apps/web/src/components/LecturePdf.tsx`, fonction `resolutionAffichee`.

```ts
function resolutionAffichee(
  image: HTMLImageElement,
  zone: [number, number, number, number],
  largeurDeLaPage: number,
  hauteurDeLaPage: number,
): number | null {
  const boite = image.getBoundingClientRect()
  if (boite.width <= 0 || boite.height <= 0) return null
  const pointsEnLargeur = (zone[2] - zone[0]) * largeurDeLaPage
  const pointsEnHauteur = (zone[3] - zone[1]) * hauteurDeLaPage
  return Math.max(pointsEnLargeur / boite.width, pointsEnHauteur / boite.height)
}
```

Soit, en notant **Z** le côté de la zone RÉELLEMENT rendue en points de page et
**W** le côté de l'image RÉELLEMENT affichée en pixels CSS :

> **ε = max( Z_largeur / W_largeur , Z_hauteur / W_hauteur )**

**La version précédente le SUPPOSAIT, et de trois façons fausses à la fois.**
Elle calculait `largeur_de_page × 0,05 ÷ 512` :

| # | Ce qui était supposé | Ce qui est vrai | Sens de l'erreur |
| --- | --- | --- | --- |
| 1 | la tuile fait 512 px | un bitmap se compte en pixels **entiers** : une zone de 21 × 16 pt au facteur 24,381 revient en **511 × 389 px**, soit 20,959 × 15,955 pt | la zone est plus petite de **0,2 %** en x et **0,28 %** en y — et le clic était rapporté à la zone *demandée* |
| 2 | l'image est affichée à sa taille naturelle | le CSS lui donne `width: 100 %` : **417,6 px CSS** pour 511 px de bitmap dans la disposition à deux colonnes | ε sous-estimé d'un facteur 1,22 : l'incertitude était annoncée **plus petite qu'elle n'est** |
| 3 | la page est en paysage | ε était pris sur la **largeur** seule, alors que le rendu ajuste son facteur sur le **plus grand côté** de la zone | sur une page en portrait, erreur de **1,41** dans le sens optimiste |

Les trois disparaissent en lisant la zone et la taille au lieu de les déduire.
**Et la zone lue n'est pas celle demandée** : le serveur la déclare, dans
l'en-tête HTTP `X-Metreo-Zone` **et** dans un bloc `tEXt` du PNG lui-même
(`rendu_pdf.CLE_DE_LA_ZONE`), pour qu'elle survive au cache de tuiles. Une
tuile en cache sans sa zone est **re-rendue** plutôt que servie avec une
géométrie inconnue (`tuiles._zone_du_png`, journal `tuile_sans_zone_rerendue`).

Si l'en-tête manque — serveur plus ancien, proxy filtrant — l'écran retombe sur
la zone demandée **et le dit** : bandeau `pdf-zone-non-declaree`. Un pointage
décalé ne reste pas sans explication.

### 2.8 Le résultat central : l'incertitude propagée est **sans échelle**

Reportons ε dans la formule du § 2.3, en écrivant la base de calibration comme
une **fraction f du côté de la zone rendue** (d = f · Z) :

> **ε_k / k = √2 · (Z/W) / (f · Z) = √2 / (W · f)**

**Z disparaît.** L'incertitude relative du facteur ne dépend **ni de la taille
de la page, ni de l'échelle du plan, ni du nombre de millimètres d'ouvrage** :
elle ne dépend que de **W**, le nombre de pixels affichés, et de la **fraction
de la loupe** que couvre la base de calibration.

Même chose pour une longueur : en notant g la fraction couverte par le tracé,

> **σ_L / L = (√2 / W) · √( 1/f² + 1/g² )**

**Ce qui a changé par rapport à la version précédente de ce document** : le
dénominateur était écrit `512`, la taille **visée** du bitmap. C'est désormais
`W`, la taille **affichée**, qui dépend de la fenêtre du navigateur. Une
conséquence directe et contre-intuitive : **agrandir la fenêtre resserre
l'incertitude**, parce que le même dessin est pointé sur plus de pixels. C'est
vrai, et c'est pourquoi ε est relu à chaque clic plutôt que calculé une fois.

### 2.9 Le tableau reproductible au crayon

Toutes les entrées de la formule sont ici, et aucune autre n'est nécessaire.

**Entrée : √2 ÷ W.** Deux valeurs de W comptent, et il faut les distinguer :

| W | Ce que c'est | √2 / W |
| --- | --- | --- |
| **512 px** | la taille que le serveur **vise** (`rendu_pdf.COTE_TUILE`) | 0,2762 % |
| **511 px** | la taille qu'il **produit** sur une zone de 21 × 16 pt — pixels entiers | 0,2768 % |
| **417,6 px** | la taille **affichée** dans la disposition à deux colonnes, mesurée dans Chromium à 1 440 px de large | **0,3386 %** |

C'est la troisième ligne qui décide de ce que l'écran annonce. Il suffit ensuite
de diviser par f.

| Base de calibration, en fraction du côté de la zone rendue | √2 / (417,6 · f) |
| --- | --- |
| f = 1,00 — toute la loupe | **0,3386 %** |
| f = 0,90 | **0,3762 %** |
| f = 0,50 — à mi-largeur | **0,6772 %** |
| f = 0,25 | **1,3544 %** |
| f = 0,1693 | **2,0000 %** — le seuil est atteint |

**Les chiffres de la version précédente de ce document étaient optimistes d'un
facteur 1,22**, exactement le rapport 512 / 417,6 : ils décrivaient un écran
qui aurait affiché la tuile à sa taille naturelle.

### 2.9bis Les écarts réellement constatés, avant et après correction

**C'est le § le plus concret de ce document.** Le propriétaire a relevé sur les
captures un écart que le ± annoncé ne couvrait pas. Les trois causes ont été
mesurées et corrigées ; voici les deux états, sur la même fixture et le même
parcours navigateur (`suite-mesure-juste-pdf.spec.ts`, qui écrit ces écarts
dans son journal).

| Grandeur | Attendu | Avant correction | Écart | Après correction | Écart |
| --- | --- | --- | --- | --- | --- |
| Longueur de la façade | 6 000 mm | 6 006,3 ± 2,3 mm | **+0,105 %** | **6 001,2 mm** | **+0,020 %** |
| Surface du séjour | 24,000 m² | 24,060 ± 0,016 m² | **+0,250 %** | **24,012 m²** | **+0,050 %** |

**Cinq fois moins d'écart sur les deux grandeurs**, et surtout : avant, l'écart
de 6,3 mm **dépassait** le ± de 2,3 mm — la mesure se présentait comme plus
sûre qu'elle n'était. Après, l'écart de 1,2 mm est **contenu** dans le ±.

**Et une confirmation inattendue de H5**, obtenue en jouant le même scénario
sous deux configurations dont la seule différence est la **largeur de la
fenêtre** :

| Fenêtre | Longueur (6 000 mm attendus) | Surface (24,000 m² attendus) |
| --- | --- | --- |
| 1 280 px — `playwright.premier-devis.config.ts` | 6 001,2 mm (**+0,020 %**) | 24,012 m² (**+0,050 %**) |
| 1 440 px — `playwright.captures.config.ts` | **5 999,8 mm** (**−0,0033 %**) | **23,997 m²** (**−0,0125 %**) |

Une fenêtre plus large affiche la loupe sur plus de pixels, donc le même dessin
est pointé plus finement, donc l'écart diminue — d'un facteur six ici. **C'est
exactement ce que l'hypothèse H5 niait** en supposant la tuile pointée à sa
taille native de 512 pixels. Le signe de l'écart change aussi, ce qui est
cohérent avec une erreur de quantification et non avec un biais résiduel.

**Les trois causes, et la part de chacune.** Elles ont été isolées par un banc
de diagnostic qui imprime le point visé, la fraction cliquée, les boîtes de
l'enveloppe et de l'image, et ce que l'API a enregistré
(`apps/web/captures/diagnostic-pointage.spec.ts`).

| Cause | Mesure du défaut | Part de l'écart | Correction |
| --- | --- | --- | --- |
| **La bordure de 1 px** comptée dans la fraction cliquée. `.pdf-apercu` et `.pdf-loupe-image` portent chacune `border: 1px` ; le clic était rapporté à la **boîte de bordure** de l'enveloppe, pas à l'image. Mesuré dans Chromium : enveloppe `x=962,39 w=419,61`, image `x=963,39 w=417,61` | décalage de **+1 px** et erreur d'échelle de **0,3 %** | **dominante** : le facteur de calibration était faux de **+0,088 %**, lu comme « 25,02 mm par point » au lieu de 25,00 | `fractionDansLImage` prend la boîte de l'**image** (`<img>`), et rend `null` hors de celle-ci |
| **La troncature du bitmap**. La zone demandée de 21 × 16 pt revient en 511 × 389 px, donc couvre 20,959 × 15,955 pt | **−0,2 %** en x, **−0,28 %** en y, systématique et jamais compensée entre deux pointages | secondaire, et de **sens opposé** à la précédente | la tuile **déclare** la zone qu'elle couvre, et le clic est rapporté à **cette** zone |
| **ε supposé** depuis 512 px et la largeur de page | ε sous-estimé d'un facteur 1,22 en paysage, 1,73 en portrait | **aucune part de l'écart** — mais le ± annoncé était trop étroit, donc la mesure fausse passait pour sûre | `resolutionAffichee` (§ 2.7) |

**Vérification par les coordonnées enregistrées**, et non par le résultat : le
banc visait `x = 0,142857` et `0,714286` de la page ; l'API a enregistré
`0,1437245` et `0,7152466`. Soit **+0,0009 de la page sur les deux points**,
c'est-à-dire **+0,38 pt** — un décalage constant, la signature d'un repère
translaté d'un pixel, et non d'un bruit de pointage.

**Et la dispersion, mesurée.** Douze pointages indépendants de la même cote sur
la fixture, chacun sur une grille de pixels décalée
(`test_douze_pointages_independants_encadrent_tous_la_cote_connue`) :

| Grandeur | Valeur |
| --- | --- |
| Attendu | 6 000,000 mm |
| Moyenne des douze | 6 000,017 mm — **biais de +0,017 mm** |
| Étendue (min → max) | 5 999,590 → 6 000,820 mm |
| **Écart-type observé** | **0,49 mm** |
| **± annoncé** (moyenne) | **2,27 mm**, soit 0,0378 % |
| Rapport annoncé / observé | **4,6** |
| Pointages encadrant la vérité à 1 σ | **12 / 12** |

Le ± est donc **large**, et il l'est volontairement : ε est pris comme
l'écart-type du pointage alors que la seule quantification en pixels donnerait
ε/√12. Ce qui reste non couvert est le **biais**, ici négligeable parce que la
grille de pixels est centrée — rien ne dit qu'une main le soit.

### 2.10 Ce que le plancher de 10 points protège, et ce qu'il ne protège pas

Le plancher est en **points de papier** ; l'incertitude est en **fraction de la
loupe**. Les deux ne varient pas ensemble, et l'écart est grand.

La dernière colonne vaut √2 / (W · f) avec **W = 417,6 px affichés** (§ 2.9).
Elle était calculée avec 512 dans la version précédente de ce document, donc
**optimiste d'un facteur 1,22**.

| Page | Largeur (points) | Loupe = 5 % (points) | 10 points en fraction de la loupe | Incertitude du facteur **au minimum autorisé** |
| --- | --- | --- | --- | --- |
| Fixture du dépôt | 300,0 | 15,00 | **66,7 %** | **0,508 %** |
| A3 (420 mm) | 1 190,6 | 59,53 | 16,8 % | 2,016 % |
| `etage-1` / `etage-3` (1 189 mm) | 3 370,4 | 168,52 | **5,9 %** | **5,707 %** |
| `coupe-1-1` (1 480 mm) | 4 195,3 | 209,76 | 4,8 % | 7,103 % |
| `elevation-nord` (1 690 mm) | 4 790,6 | 239,53 | **4,2 %** | **8,112 %** |

Lecture : sur la fixture, le plancher interdit toute calibration dont le
facteur serait incertain à plus de 0,51 % — il **borde** le test. Sur
`elevation-nord`, le même plancher laisse passer une calibration dont le
facteur est incertain à 8,1 %. C'est alors le seuil de 2 % qui prend le relais
et bascule la mesure en `a_confirmer` ; le plancher, lui, ne refuse plus rien
d'utile. Sur un A3, le plancher est désormais **juste à la limite** du seuil :
une calibration au minimum autorisé y est refusée de justesse.

**Les tests de seuil s'exécutent donc sur une géométrie où le plancher est
seize fois plus protecteur que sur `elevation-nord`.** Ce n'est pas un défaut
du code — c'est une limite de ce que la fixture peut prouver.

### 2.11 Les hypothèses du modèle, et ce qu'elles coûtent

**Cinq des douze sont levées**, et elles sont barrées dans le tableau : H4, H5,
H6 et H9 le 7 octobre 2026, H8 la veille. Les sept restantes sont **codées
implicitement** et **non vérifiées sur un plan réel** ; la procédure qui les
lèverait est `docs/VALIDATION_SUR_PLANS_REELS.md`.

| # | Hypothèse | Où elle est faite | Ce qu'elle coûte si elle est fausse | Par quelle mesure on la lève |
| --- | --- | --- | --- | --- |
| **H1** | La cote de calibration saisie est **juste** | hors modèle : `distance_reelle` est une déclaration (`schemas.py`, champ `CalibrationCreate.distance_reelle`) | tout est faux du même facteur, **sans aucune réserve** : l'incertitude de 0,3 % reste affichée | relever la cote sur place, ou la lire dans le DXF du même plan |
| **H2** | La page **n'est pas déformée** : le facteur est constant partout | `mesures_pdf.facteur` rend **un** scalaire appliqué partout | une erreur qui **croît avec la distance**, invisible sur une cote courte | une **cote longue traversant le plan** (fiche § 3) |
| **H3** | Les deux axes ont la **même échelle** | `math.hypot` (dans `Calibration.ecart_en_points` et dans `longueur`) et la formule du lacet (dans `aire`) supposent un repère isotrope ; aucun contrôle n'existe | horizontale et verticale justes, **géométrie fausse quand même** ; aire fausse du produit des deux | une **cote oblique** (fiche § 3) — le seul contrôle qu'un facteur juste sur un axe ne peut pas satisfaire |
| **H4** | ~~La page est en **paysage**~~ — **levée le 7 octobre 2026** | `resolutionAffichee` prend le **plus grand** des deux rapports, axe par axe, sur la zone réellement rendue | l'hypothèse était fausse de **1,41** sur une page en portrait, dans le sens qui annonce une mesure plus sûre qu'elle n'est | `test_le_meme_plan_en_portrait_rend_les_memes_longueurs` et `test_le_portrait_n_annonce_pas_une_mesure_plus_sure_que_le_paysage` (`test_mesure_juste_sur_un_plan.py`) |
| **H5** | ~~La tuile est pointée à sa **taille native** de 512 pixels~~ — **levée le 7 octobre 2026** | `resolutionAffichee` lit `image.getBoundingClientRect()` : la taille **affichée**, pas la taille visée | l'hypothèse était fausse d'un facteur **1,22** dans la disposition à deux colonnes — 417,6 px affichés pour 511 px de bitmap — et dans le sens optimiste | la résolution est désormais **mesurée** à chaque clic ; le parcours navigateur vérifie la justesse du résultat (`suite-mesure-juste-pdf.spec.ts`), et c'est lui qui aurait échoué si ε était faux |
| **H6** | ~~La fenêtre de la loupe fait bien **5 %** de la page~~ — **levée le 7 octobre 2026** | ε est calculé depuis la zone que le **serveur déclare** avoir rendue (`X-Metreo-Zone`, et le bloc `tEXt` du PNG), rognage aux bords et troncature en pixels entiers compris | l'hypothèse était pessimiste près d'un bord et optimiste de 0,2 % partout ailleurs (la troncature) | `test_a_tile_says_which_zone_it_really_covers`, `test_the_tile_carries_its_zone_inside_the_png`, `test_the_real_zone_survives_the_cache` |
| **H7** | L'erreur de pointage est **aléatoire**, non systématique | la somme en quadrature (dans `longueur` et dans `aire`) ne vaut que pour des erreurs indépendantes et centrées | un biais constant — cliquer toujours le bord extérieur d'un trait épais — **ne s'annule pas** et n'apparaît nulle part dans le nombre rendu | mesurer la même cote 5 à 10 fois et comparer la dispersion observée à l'incertitude annoncée. Un biais se voit à la moyenne, pas à l'écart-type |
| **H8** | ~~L'erreur de tracé vaut √2·ε quel que soit le nombre de sommets~~ — **levée le 7 octobre 2026** | `du_trace` est désormais calculée sommet par sommet : `_sensibilite_d_une_longueur` et `_sensibilite_d_une_aire` | l'hypothèse était exacte pour un segment à 2 points et **optimiste d'un facteur allant jusqu'à 10** sur un tracé anguleux à 50 sommets. L'expérience prescrite ici a été jouée, et elle a confirmé le défaut | `test_l_incertitude_de_trace_croit_avec_le_nombre_de_sommets` (2, 5, 20, 50 sommets) et `test_la_croissance_suit_la_racine_du_nombre_de_sommets` |
| **H9** | ~~La mesure est pointée **à la même finesse** que la calibration~~ — **levée le 7 octobre 2026** | `schemas.MesureCreate` porte désormais `resolution_du_pointage` ; `calibration_de_plan.mesurer` l'utilise pour le terme de **tracé**, le facteur gardant celle de la calibration | l'hypothèse était vraie dans l'écran livré et **fausse pour tout autre client de l'API**, qui obtenait une incertitude sous-estimée | `test_a_measurement_may_declare_the_resolution_of_its_own_pointing` et `test_a_measurement_without_its_own_resolution_keeps_the_old_behaviour` (`test_calibration_de_plan_api.py`) |
| **H10** | Une page ne porte **qu'une seule échelle** | `calibration_de_plan._contient` : une calibration **sans zone** s'applique à toute la page | une page portant un plan au 1:50 **et** un détail au 1:20 donne une mesure fausse d'un facteur 2,5, **sans réserve** | éprouver sur un plan réel à deux échelles. Le mécanisme de zone existe et est testé (`test_calibration_de_plan_api.py`, test du refus hors zone), mais **rien n'oblige** à l'utiliser |
| **H11** | Les incertitudes de deux mesures sont **indépendantes** | non codée, mais supposée par quiconque additionne deux mesures | le terme `du_facteur` est **le même** pour toutes les mesures d'une page : il est totalement **corrélé**. Un total de 20 murs n'a pas une incertitude en √20, mais en 20 | à traiter quand les mesures remonteront dans un métré. Le module ne fait aucune agrégation aujourd'hui |
| **H12** | Le nombre pointé **est une cote de l'ouvrage** | hors modèle entièrement | la fiche de cotes de référence (§ 8) le dit : sur un des plans, **860 fragments sur 4 351** sont des nombres d'au moins trois chiffres, et le premier est le **code postal du cartouche**. Aucune tolérance ne corrige cela. *(Ce chiffre vient de `docs/COTES_DE_REFERENCE.md`, absent de cette branche — PR #88 ; voir l'encadré du § 1.2.)* | seule une personne qui regarde ce que le nombre cote le peut. C'est pourquoi la validation humaine n'est pas optionnelle |

Deux limites supplémentaires, assumées et écrites dans le code :

- le signalement d'un contour qui se recoupe ne traite **que le croisement
  franc** : deux segments colinéaires qui se chevauchent ne sont pas signalés
  (`mesures_pdf.py`, fonction `_se_recoupe`) ;
- le facteur 2 de l'aire est appliqué **aussi au terme de tracé** (dans
  `aire`). C'est un choix de modélisation, cohérent et testé, mais non éprouvé
  contre une dispersion réelle.

---

## 3. Ce que 0,9 n'est pas

### 3.1 Trois nombres à ne pas confondre

| Nombre | Où | Ce que c'est |
| --- | --- | --- |
| `CONFIANCE["mesurable"] = 0.9` | `calibration_de_plan.py`, table `CONFIANCE` | un **rang** entre deux classes nommées |
| `CONFIANCE_DE_LA_CITATION = Decimal("0.9")` | `calibration_de_plan.py`, constante `CONFIANCE_DE_LA_CITATION` | un champ de **provenance** sur l'emplacement cité |
| `incertitude` / `incertitude_relative` | `mesures_pdf.py`, champs de `Mesure` | la **seule** grandeur qui porte une information métrologique |

Le code le dit déjà, et il faut le reprendre tel quel (`calibration_de_plan.py`,
commentaire qui précède la table `CONFIANCE`) :

> « **Ces nombres ne sont pas des probabilités mesurées.** Ils ORDONNENT deux
> classes nommées, parce que la base exige un nombre dans [0,1]. Ce qui porte
> le sens est l'incertitude, qui est calculée et conservée dans `value`, dans
> la même unité que la mesure. »

Autrement dit : **0,9 ne veut dire ni « 90 % de chances d'être juste », ni
« ± 10 % »**. Il veut dire « classé `mesurable` plutôt que `a_confirmer` », et
la colonne `confidence` n'accepte qu'un nombre. 0,9 et 0,5 auraient pu être 2
et 1.

**`CONFIANCE_DE_LA_CITATION` ne porte même pas sur la valeur** : il porte sur
l'**emplacement**. Il répond à « sait-on où regarder ? », pas à « le nombre
est-il bon ? » (`calibration_de_plan.py`, commentaire de
`CONFIANCE_DE_LA_CITATION`).

### 3.2 Le compte exact des occurrences

```bash
grep -rn "CONFIANCE_DE_LA_CITATION" \
  --exclude-dir=.git --exclude-dir=.venv \
  --exclude-dir=__pycache__ --exclude-dir=.mypy_cache .
```

rend **cinq occurrences dans trois fichiers**, et il y a **deux constantes
distinctes qui portent le même nom** :

| Fichier, symbole | Contenu |
| --- | --- |
| `services/calibration_de_plan.py`, constante `CONFIANCE_DE_LA_CITATION` | `= Decimal("0.9")` — mesure **PDF** |
| `services/calibration_de_plan.py`, écriture de la citation | `confidence=CONFIANCE_DE_LA_CITATION,` — usage |
| `services/mesures_de_plan.py`, constante `CONFIANCE_DE_LA_CITATION` | `= Decimal("1")` — cotation **DXF** |
| `services/mesures_de_plan.py`, écriture de la citation | `confidence=CONFIANCE_DE_LA_CITATION,` — usage |
| `apps/api/tests/test_citation_de_plan.py`, docstring du test de la contrainte `confidence` | mention, **à propos de la valeur 1 du DXF** |

*(Les exclusions ne sont pas cosmétiques : sans elles, la même recherche
remonte aussi les `.pyc` et les caches mypy, et ne rend plus cinq lignes.)*

**Aucun test n'assertit la valeur 0,9.** La seule mention dans un test
concerne la constante **du DXF**, et porte sur un tout autre sujet : sous
SQLite, `confidence` est stockée en TEXTE, et `'1.0000000000' <= '1'` est faux
en comparaison de chaînes — la contrainte refusait la seule valeur certaine,
jusqu'au `CAST(confidence AS NUMERIC)` qui la corrige.

Conséquence pratique : **si quelqu'un remplace 0,9 par 0,99, rien ne tombe au
rouge.** C'est cohérent avec ce qu'est ce nombre — un rang, pas une mesure —
mais il faut le savoir.

### 3.3 Le 0,9 n'arrive même pas à l'écran

`schemas.MesureDePdf`, le modèle que l'écran relit, porte `valeur`, `unite`,
`incertitude`, `incertitude_relative`, `fiabilite`, `reserves`, `points`,
`cadre`, `calibration`, `decision`, `valeur_corrigee` — et **aucun champ
`confidence`**. Les deux 0,9 sont écrits en base, l'un sur la citation, l'autre
sur la proposition (`confidence=CONFIANCE[mesure.fiabilite]`, dans
`calibration_de_plan.py`), pour que la colonne existe et soit comparable au
reste du pipeline documentaire.

### 3.4 Pourquoi 0,9 ici et 1 pour une cote DXF

| | Cote lue dans un DXF | Mesure prise sur un PDF |
| --- | --- | --- |
| Confiance de la **citation** | `Decimal("1")` — `mesures_de_plan.py`, `CONFIANCE_DE_LA_CITATION` | `Decimal("0.9")` — `calibration_de_plan.py`, `CONFIANCE_DE_LA_CITATION` |
| Pourquoi | « un handle DXF désigne un objet sans ambiguïté : il n'y a pas de "peut-être cet objet" » (commentaire de la constante) | « une mesure de PDF désigne un endroit que quelqu'un a pointé à la souris, et ce pointage porte la même incertitude que la mesure » (commentaire de la constante) |
| Extracteur enregistré | `lecture_dxf@ezdxf-…` | `mesure_humaine@pdf` — `calibration_de_plan.py`, constante d'extracteur |
| Unité | **dans le fichier** (`$INSUNITS`) | **inexistante** : le PDF ne porte que des points PostScript |
| Nom de schéma | `mesure_de_plan` | `mesure_pdf` — distinct exprès (`calibration_de_plan.py`, commentaire du nom de schéma) |

### 3.5 L'ordre de lecture correct

1. **`incertitude`**, dans la même unité que la valeur (« 3 750 mm ± 4 mm ») —
   calculée depuis ε, pas supposée ;
2. **`reserves`** (`incertitude_elevee`, `contour_qui_se_recoupe`) — le fait nommé ;
3. **`fiabilite`** (`mesurable` / `a_confirmer`) — le classement ;
4. **`confidence`** = 0,9 ou 0,5 — le rang écrit en base, et rien d'autre.

`mesures_de_plan.py` le formule déjà, dans le commentaire qui précède sa table
de confiance : « un écran qui n'afficherait que "0,9" mentirait par omission :
la réserve est le fait, le nombre n'en est que le rang. »

---

## 4. Le tableau honnête

| Propriété | Vérifié sur fixture fabriquée | Inconnu sur un plan réel |
| --- | --- | --- |
| **Conversion écran ↔ page** | Oui — inverse exact du lecteur, 4 rotations, coins compris ; boîte décalée | Rien d'inconnu : propriété purement géométrique |
| **Facteur = distance ÷ écart** | Oui — égalité exacte à 50 mm/point (`test_the_factor_is_the_real_distance_over_the_pointed_one`) ; invariant par direction | **Si la cote saisie est fausse ou mal lue, tout l'est** — et rien ne le détecte (H1) |
| **Longueur d'un segment, d'une ligne brisée** | Oui — 2 500 mm exacts, 5 000 mm exacts ; et **3 750 mm à 0,01 mm près à travers l'API** (`test_a_segment_is_measured_with_its_provenance`) | **Aucune de ces valeurs n'a été comparée à une cote relevée hors de Metreo** |
| **Aire par la formule du lacet** | Oui — 6,25 m² exacts, triangle = moitié ; et **20,625 m² à 0,001 près à travers l'API** (`test_a_closed_surface_is_measured_in_square_metres`) | Idem : aucune surface confrontée à un métré validé |
| **Conversion vers le m²** | Oui — 5 m et 5 000 mm donnent la même aire : le facteur sort de `units.py` | Rien d'inconnu |
| **Refus d'une calibration < 10 points** | Oui, avec contre-exemple au seuil exact | **Le plancher ne borde presque plus rien sur un grand format** : 4,2 % de la loupe sur `elevation-nord` contre 66,7 % sur la fixture (§ 2.10) |
| **Refus d'unité non linéaire, de distance ≤ 0** | Oui | Rien d'inconnu |
| **Contour qui se recoupe** | Oui, avec contre-exemple carré + L | Les croisements **colinéaires** ne sont pas détectés — limite assumée (`mesures_pdf._se_recoupe`) |
| **L'incertitude est dans l'unité de la valeur** | Oui | Rien d'inconnu |
| **L'incertitude décroît avec un pointage plus fin** | Oui — rapport de 2 exactement (`test_a_finer_pointing_gives_a_proportionally_smaller_uncertainty`) | **L'étalonnage est désormais mesuré sur fixture** : douze pointages indépendants donnent σ observé = 0,49 mm pour un ± annoncé de 2,27 mm, soit un rapport de 4,6 (§ 2.9bis). Ce qui reste inconnu est le **biais** d'une main réelle (H7) |
| **Une calibration courte empoisonne tout** | Oui — même valeur, incertitude plus grande | Rien d'inconnu : propriété de la formule |
| **Une aire est 2× plus incertaine qu'une longueur** | Oui | Le facteur 2 s'applique aussi au terme de tracé : **choix de modélisation, non éprouvé** |
| **Le seuil de 2 % bascule en « à confirmer »** | Oui, avec contre-exemple | **2 % n'est pas votre tolérance.** Le code le dit (commentaire de `SEUIL_D_INCERTITUDE`) ; `docs/VALIDATION_SUR_PLANS_REELS.md` § 4.5 attend votre réponse |
| **Pointage non déclaré → pessimisme** | Oui (`test_an_undeclared_pointing_resolution_is_treated_pessimistically`) | Rien d'inconnu |
| **La tuile rend exactement la zone demandée** | Oui, sur les deux axes, à un point près (`test_a_tile_renders_exactly_the_zone_it_was_asked_for`) | Le plafond d'agrandissement reste non éprouvé ; le **portrait l'est désormais** (`test_le_portrait_n_annonce_pas_une_mesure_plus_sure_que_le_paysage`) |
| **ε = zone RENDUE ÷ taille AFFICHÉE** | La règle est éprouvée côté serveur (`test_mesure_juste_sur_un_plan.py`), et le parcours de livraison la joue dans un vrai navigateur sur une géométrie connue (`suite-mesure-juste-pdf.spec.ts`) | **Plus rien d'inconnu sur la formule** : H4, H5 et H6 sont levées le 7 octobre 2026, parce qu'il n'y a plus de formule — la zone est déclarée par le serveur et la taille lue dans le DOM (§ 2.7). Ce qui reste inconnu est la main qui pointe, pas le pixel |
| **Isotropie des deux axes** | **Non testable sur fixture** — la fixture est isotrope par construction | **Entièrement ouvert.** Levé par la cote oblique (H3) |
| **Page non déformée** | **Non testable sur fixture** | **Entièrement ouvert.** Levé par la cote longue (H2) |
| **Erreur aléatoire et non systématique** | **Non** — la quadrature la suppose | **Entièrement ouvert.** Un biais de pointage n'apparaît nulle part (H7) |
| **Erreur indépendante du nombre de sommets** | **Levée.** La sensibilité est calculée sommet par sommet, et quatre tracés (2, 5, 20, 50 sommets) l'éprouvent | Reste la question du BIAIS, qui n'est pas celle du nombre (H7) |
| **Calibration et mesure au même zoom** | **Plus supposé** — `MesureCreate` porte `resolution_du_pointage`, et le terme de tracé l'utilise (H9 levée) | Rien d'inconnu : un client qui ne déclare rien obtient le défaut **pessimiste** d'un point de papier par pixel |
| **Une seule échelle par page** | Le **refus hors zone** est testé (`test_calibration_de_plan_api.py`) | Rien n'oblige à poser une zone : une page à deux échelles donne une mesure fausse **sans réserve** (H10) |
| **Indépendance entre deux mesures** | **Non** — jamais agrégées | Le terme du facteur est **totalement corrélé** sur une page (H11) |
| **Le nombre pointé est bien une cote** | **Hors modèle** | 860 fragments numériques sur 4 351 sur un de vos plans, dont un code postal (H12 ; chiffre issu de la fiche de la PR #88) |
| **Confiance 0,9** | **Non** — aucun test ne l'assertit (§ 3.2) | **Sans objet** : ce n'est pas une précision, c'est un rang de provenance |

---

## 5. Ce qu'il faudrait pour lever l'inconnu

### 5.1 La procédure d'essai sur vos plans

Le document qui dit exactement quels fichiers fournir, quelles cotes relever et
sous quelle forme rendre le résultat est **`docs/VALIDATION_SUR_PLANS_REELS.md`**,
sur cette branche. Il porte :

- la **liste des fichiers**, par ordre de ce qu'ils débloquent — le minimum est
  un seul PDF d'exécution avec son cartouche ;
- les **trois cotes** à relever : une **courte** (elle attrape l'imprécision du
  pointage), une **longue** traversant la feuille (H2, la déformation), une
  **oblique** (H3, l'anisotropie — c'est la décisive, parce qu'elle ne peut pas
  être satisfaite par un facteur juste sur un seul axe) ;
- le **tableau attendu / mesuré / écart**, prêt à remplir, avec une section de
  **répétition** — cinq pointages de la même cote — qui est le seul essai
  levant H7 ;
- la ligne **« votre tolérance »**, laissée vide volontairement.

**Ce qui a changé depuis la version précédente de ce document.** Celle-ci
renvoyait à `docs/COTES_DE_REFERENCE.md`, qui vit sur `claude/cotes-de-reference`
(PR #88) et **n'est pas sur cette branche**. Le renvoi était donc pendant.
`VALIDATION_SUR_PLANS_REELS.md` reprend ce que la fiche demandait, y ajoute la
conduite de l'essai et les cas de bord, et **vit sur la branche qu'on livre**.
Les deux ne se contredisent pas ; si la #88 est fusionnée, la fiche reste le
document de référence sur le FORMAT des cotes, et celui-ci sur la PROCÉDURE.

**L'état de l'essai, en une ligne : il n'a pas eu lieu.** Les quatre plans
fournis lors d'un échange précédent vivaient dans un espace de session qui a
disparu avec le conteneur, et ils ne sont plus accessibles. Tout chiffre de ce
document vient donc d'une **fixture fabriquée**, jamais d'un plan réel.

### 5.2 Ce que les trois cotes lèvent, et ce qu'elles ne lèvent pas

| Hypothèse | Levée par les trois cotes ? |
| --- | --- |
| H1 cote de calibration juste | **Oui** — c'est l'objet même de la paire DXF + PDF |
| H2 page non déformée | **Oui** — la cote longue |
| H3 isotropie | **Oui** — la cote oblique |
| H7 biais de pointage | **Oui, par la RÉPÉTITION** — cinq pointages de la même cote, § 4.3 de la procédure. C'est le seul chiffre que le ± ne contient pas |
| H10 une seule échelle par page | **Oui, si** un plan à deux échelles est fourni |
| H12 le nombre est une cote | **Non**, et rien ne le lèvera : seule une personne qui regarde ce que le nombre cote le peut |
| H11 indépendance entre deux mesures | **Non** — c'est un défaut connu du modèle, pas une inconnue |
| H4, H5, H6 ε déclaré = ε réel | **Déjà levées**, et sans vos plans — § 5.3 |
| H8 nombre de sommets | **Déjà levée** — deux tracés de même longueur et de densité différente suffisaient |
| H9 résolution propre à la mesure | **Déjà levée** — le champ existe au contrat |

### 5.3 Ce qui a été fait dans le dépôt, indépendamment de vos plans

Les trois points que la version précédente de ce document listait comme « à
faire » sont traités. Voici comment, et ce qui reste.

**1. ε n'est plus supposé — il est MESURÉ.** Le point demandait un test
unitaire sur `resolutionDeLaLoupe`, et notait deux prérequis absents :
exporter le symbole, et installer un lanceur de tests unitaires (`apps/web`
n'avait ni vitest ni jest, et n'en a toujours pas). **La fonction a disparu**,
remplacée par `resolutionAffichee`, qui ne calcule plus rien depuis une
constante : elle **lit** la boîte de l'image et la zone déclarée par le
serveur (§ 2.7). Il n'y a donc plus de formule à éprouver en isolation — il y a
une lecture, et c'est le résultat qui est vérifié de bout en bout.

**2. Les deux formules de ε sont reliées, et par le serveur.** Le point
demandait un test comparant `resolution_du_pointage` déclaré au rendu réel.
C'est mieux que cela : **le serveur déclare la zone qu'il a rendue**, en
en-tête HTTP et dans le PNG lui-même, et l'écran calcule ε depuis cette
déclaration. Les deux formules n'ont plus à coïncider par chance, puisqu'il n'y
en a plus qu'une. H4, H5 et H6 tombent ensemble.

**3. Le contrat de `MesureCreate` est tranché.** `resolution_du_pointage` y
entre, optionnel, avec un défaut **pessimiste** — un point de papier par pixel
— pour qu'un client qui ne déclare rien obtienne le doute et non la précision
de la calibration. H9 est levée.

**Et une quatrième chose, qui n'était pas dans la liste.** La vérification
chiffrée — la mesure affichée comparée à une longueur connue, par le navigateur
— ne vivait que dans `apps/web/captures/`, qu'aucune configuration de CI ne
ramasse. Elle est maintenant dans `apps/web/e2e-premier-devis/suite-mesure-juste-pdf.spec.ts`
et **tombe à chaque livraison** ; les captures en restent une sortie
complémentaire, produite par le même scénario.

**Ce qui reste à faire dans le dépôt :**

1. **Un lanceur de tests unitaires pour `apps/web`.** Toujours absent. Ce n'est
   plus un prérequis de H5, mais c'est ce qui manque pour éprouver une fonction
   d'écran sans démarrer un navigateur.
2. **H10, le mécanisme de zone.** Il existe et son refus hors zone est testé ;
   **rien n'oblige à poser une zone**. Tant que ce n'est pas le cas, une page à
   deux échelles donne une mesure fausse sans réserve.
3. **H11, la corrélation.** Le jour où des mesures seront additionnées dans un
   métré, le terme de facteur devra être traité comme commun à toute la page :
   un total de 20 murs a une incertitude en 20, pas en √20. Aucune agrégation
   n'est livrée aujourd'hui, et la première reprise d'une mesure dans un
   bordereau (`services/reprise_de_mesure.py`) **n'additionne rien**.

## 6. Trois dérives de rédaction, relevées en lisant

Aucune n'affecte un calcul ; toutes affectent la lecture.

1. **« A0 » dans les commentaires.** **Onze emplacements du code et des tests,
   répartis sur neuf fichiers**, décrivent les plans du propriétaire comme des
   A0 :
   - `apps/web/src/components/LecturePdf.tsx` — commentaire de
     `TAILLE_DE_LA_LOUPE` ;
   - `apps/web/src/lib/api.ts` — commentaire de `calibrerLePlan` ;
   - `apps/api/src/metreo_api/schemas.py` — commentaire du champ
     `resolution_du_pointage` de `CalibrationCreate` ;
   - `apps/api/src/metreo_api/services/mesures_pdf.py` — commentaire de
     `RESOLUTION_PAR_DEFAUT_EN_POINTS` ;
   - `apps/api/src/metreo_api/services/rendu_pdf.py` — commentaire de
     `MARGE_DE_TUILE` (« deux clics aux cinquièmes de la loupe donnaient
     9 points d'écart là où ils en désignent 101 sur un A0 ») ;
   - `apps/api/src/metreo_api/services/rendu_pdf.py` — dans `rendre_une_zone`
     (« un A0 atteint 16 pixels au facteur 1,4 ») ;
   - `apps/api/src/metreo_api/services/lecture_de_plan.py` — commentaire de
     `PLAFOND_APERCUS` (« une page A0 rendue à 2 000 pixels de grand côté prend
     environ 0,3 s ») ;
   - `apps/api/tests/test_mesures_pdf.py` — docstring de
     `test_an_undeclared_pointing_resolution_is_treated_pessimistically` ;
   - `apps/api/tests/test_calibration_de_plan_api.py` — docstring de
     `test_a_coarse_pointing_keeps_the_measurement_to_be_verified` ;
   - `apps/web/e2e-premier-devis/suite-plan-pdf-mesures.spec.ts` — en-tête du
     fichier ;
   - `apps/web/e2e-premier-devis/suite-plan-pdf-mesures.spec.ts` — commentaire
     de l'étape de calibration.

   Un A0 mesure 841 × 1 189 mm ; les quatre plans mesurent 1 480 × 850,
   1 690 × 850 et deux fois 1 189 × 914 mm. L'ADR 0008 emploie déjà la
   formulation juste (`docs/adr/0008-tuiles-de-detail-des-plans.md`, § « Le
   problème se voit à l'écran, et il est chiffrable ») ; les commentaires ne
   l'ont pas suivie.

   Deux autres occurrences de « A0 » font exception à bon droit, parce qu'elles
   ne parlent pas des plans du propriétaire : `apps/api/tests/test_lecture_pdf.py`,
   dans `test_a_window_around_a_text_shows_the_drawing_and_not_only_the_text`,
   où la **fixture** est réellement dimensionnée en A0 (3 370 × 2 384 points),
   et `scripts/fabriquer_pdf_de_test.py`, où le commentaire de
   `LARGEUR_REFERENCE` invoque un A0 **hypothétique** pour expliquer pourquoi
   la page de référence est petite.
2. **Une docstring périmée — corrigée le 7 octobre 2026.**
   `apps/api/tests/test_calibration_de_plan_api.py`, docstring de l'assistant
   `_calibrer`, annonçait **5 000 mm** alors que
   `DISTANCE_DE_CALIBRATION = "7500"`. Les assertions, elles, étaient justes :
   seule la phrase mentait, ce qui est le pire des deux cas — on relit la phrase
   avant le code.
3. **Deux docstrings portent des chiffres qu'elles ne vérifient pas.**
   `test_a_finer_pointing_gives_a_proportionally_smaller_uncertainty`
   (`test_mesures_pdf.py`) cite dans sa docstring « 12 à 42 mm » et
   « 0,6 à 6 mm » ; l'assertion ne compare qu'un **rapport de 2** entre deux
   valeurs de ε. Ces chiffres sont une motivation, pas une vérification. Même
   remarque pour `test_a_coarse_pointing_keeps_the_measurement_to_be_verified`
   (`test_calibration_de_plan_api.py`).

---

## 7. En une phrase

Metreo calcule juste, sait dire quand il est mal déterminé, et **retombe
désormais sur une géométrie connue par le navigateur** : 6 001,2 mm pour 6 000
attendus, 24,012 m² pour 24,000, à chaque livraison. Le « ± » qu'il affiche est
une incertitude **type**, à k = 1, propagée depuis la seule finesse du pixel
sous la souris — il ne couvre **aucune** des sept hypothèses qui restent
ouvertes, et une mesure peut être fausse d'un facteur 2 avec un ± de 0,04 % si
l'échelle saisie est fausse. Il ne sait donc toujours pas s'il **mesure** juste
sur un plan d'exécution. Ce qui manque tient dans un fichier, et la procédure
est écrite : `docs/VALIDATION_SUR_PLANS_REELS.md` — un PDF d'exécution, trois
cotes relevées à la main (une courte, une longue, une oblique), et cinq
répétitions de la courte.