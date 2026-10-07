# Revue finale du candidat `daeb7f0`

**Version relue** : `daeb7f0d7c2a12d47e1b941c0d79932a1e20d464`, branche
`claude/candidat-complet`, quatorze PR assemblées (#72, #73, #76, #77, #78,
#80, #81, #82, #83, #85, #86, #87, #88, #89 — appartenance vérifiée par
`git merge-base --is-ancestor` et non par lecture du journal).

**Ce que relire veut dire ici.** Les douze ateliers de CI sont verts sur ce
SHA, et la suite de l'API rejouée sur cette copie donne **1 605 réussites,
59 sauts, code de sortie 0**, extras de lecture exigés
(`METREO_REQUIRE_PLAN_EXTRAS=1`). Cette revue ne refait donc pas ce que les
tests font : elle cherche ce qu'aucun test ne regarde — les contradictions
entre deux écrans, les nombres affichés, les hypothèses qu'une formule porte
sans le dire, et les chemins que le parcours de livraison ne traverse pas.

**La distinction à tenir dans tout ce qui suit.** Le prototype réalise une
**mesure assistée** : une personne déclare une échelle, pointe des points, et
le programme rend un nombre avec son incertitude propagée. Sa **justesse sur
vos plans reste à établir** — c'est l'objet de `docs/COTES_DE_REFERENCE.md`,
et aucun des constats ci-dessous ne la démontre ni ne l'infirme.

---

## A. Défauts bloquants

Bloquant veut dire : **à corriger avant que vous ne posiez votre premier plan
réel**, parce que l'écran dit quelque chose de faux, ou parce qu'il présente
un nombre d'une façon qui vous induirait en erreur. Aucun des trois n'est une
faille de sécurité, aucun ne perd de donnée, et les trois se corrigent dans le
seul code de l'interface.

### A1. L'écran d'un PDF commence par annoncer qu'aucune mesure n'est possible

**Ce qui s'affiche.** Sur chaque plan PDF, au-dessus de l'écran de mesure, un
bandeau d'avertissement :

> Aucune mesure n'est exploitable : le plan ne déclare pas son unité de
> dessin, et une longueur sans unité ne veut rien dire. Le plan reste
> consultable, et les cotes écrites par le dessinateur restent lisibles sur le
> rendu.

Et juste en dessous, l'écran PDF, ses trois outils, et un tableau de mesures
marquées « mesurable ». **La page se contredit elle-même, et le bandeau est ce
qu'on lit en premier.**

**Pourquoi.** `LecturePlan.tsx` rend `ConstatDuPlan` **sans condition**, avant
de choisir entre l'écran DXF et l'écran PDF. Or `lecture_de_plan` pose
`"mesurable": False` **sans condition pour un PDF**, et le serveur a raison de
le faire : un PDF ne porte aucune unité de dessin. Ce qui est faux n'est pas le
champ, c'est sa traduction à l'écran — la phrase a été écrite pour un DXF sans
`$INSUNITS`, où elle est juste, et elle est resservie telle quelle à un format
où elle ne veut rien dire.

**Le même bandeau entraîne quatre champs vides.** « $INSUNITS », « Version
DXF », « Feuilles », « Entités par type » affichent « – » sur un PDF, et
« Anomalies du fichier » annonce qu'il n'y en a aucune. Ce n'est que du bruit ;
le bandeau, lui, est une affirmation fausse.

**Preuve** : captures `02-apercu.png`, `03-calibration.png` et
`05-longueur.png` du parcours joint — le bandeau y est, au-dessus d'une mesure
marquée « mesurable ».

**Ce que la correction demande.** Une condition sur `plan.format` dans
`LecturePlan.tsx`, et rien d'autre : ni migration, ni changement d'API, ni
nouvelle fonctionnalité. Le constat DXF ne concerne pas un PDF.

**Ce qu'il en coûte de ne pas corriger** : rien de technique. Mais la première
phrase que vous lirez sur chacun de vos plans affirmera le contraire de ce que
l'écran fait, et `docs/ESSAI_UTILISATEUR_PDF.md` ne vous prévient pas — c'est
une lacune de ce guide, pas seulement de l'écran.

### A2. Les valeurs sont affichées à dix décimales, à côté d'une incertitude de l'ordre du centimètre

**Ce qui s'affiche**, relevé sur le parcours joint :

| Ce que l'écran montre | Ce que ça devrait dire |
| --- | --- |
| `4180.6822810844 mm` | `4180,7 mm` |
| `± 26.3682553403 mm` | `± 26,4 mm` |
| `16.2046806767 m2` | `16,20 m²` |
| `± 0.1094736856 m2` | `± 0,11 m²` |
| `555.8912386702 mm par point` | `555,9 mm par point` |

Dix décimales affichées pour une valeur dont l'incertitude vaut **vingt-six
millimètres**. Le dixième de picomètre est écrit avec la même autorité que le
mètre. C'est exactement l'effet que ce produit dit vouloir éviter : une
précision apparente qui n'existe pas.

**Pourquoi.** `DECIMALES = Decimal("0.0000000001")` dans `mesures_pdf` est la
précision de CALCUL, choisie pour correspondre à `Amount` en base, et elle est
juste. Elle est simplement rendue telle quelle : `LigneDeMesurePdf` écrit
`{mesure.valeur} {mesure.unite}` sans arrondi d'affichage. Le facteur, lui, a
bien reçu un `_sans_zeros_inutiles` côté serveur — mais il ne retire que des
zéros **terminaux**, et `555.8912386702` n'en a aucun.

**Deux défauts d'affichage s'ajoutent au même endroit** :

- **le séparateur décimal est un point**, pas la virgule belge — et dans la
  même cellule, la valeur corrigée que vous avez tapée s'affiche avec votre
  virgule : « 4180.6822810844 mm » puis « corrigée en 3800,5 ». Deux
  conventions dans un carré de deux centimètres ;
- **« m2 » au lieu de « m² »**, et la **valeur corrigée ne porte aucune
  unité** : « corrigée en 3800,5 » ne dit ni mm ni m.

**Ce que la correction demande.** Un arrondi d'affichage dans `LecturePdf.tsx`,
indexé sur l'incertitude. Le nombre stocké ne change pas — et il ne doit pas
changer : c'est lui qui permet de refaire le calcul.

**Ce qu'il en coûte de ne pas corriger** : vous lirez `4180.6822810844 mm` sur
chaque relevé de l'essai, et il faudra vous souvenir à chaque ligne que seuls
les quatre premiers chiffres veulent dire quelque chose.

### A3. Une purge d'organisation ne détruit pas les plans déposés

**Ce qui se passe.** `conservation.documents_a_detruire` inscrit au registre de
purge les PDF de devis, les logos, et **tous les dérivés d'un plan** — constat,
image, textes, aperçus, tuiles. Elle **n'inscrit pas** les ORIGINAUX, c'est-à-
dire `document_revisions.storage_key`. `executer` supprime ensuite les lignes.
Résultat : **le fichier de votre plan reste sur le volume, et plus aucune ligne
ne dit à qui il appartenait.**

**Ce n'est pas un défaut de cette livraison** : il est antérieur à la lecture
de plans, il est déjà écrit dans la docstring de la fonction, et aucune PR
ouverte ne le corrige. Ce qui a changé, c'est **ce que les fichiers
contiennent**. Tant que Metreo ne stockait que des imports de prix, un original
orphelin était un déchet. Le jour où vous déposez un plan d'exécution client,
c'en est un autre.

**Pourquoi je le classe bloquant malgré son antériorité** : vous avez posé
comme règle permanente que les plans clients sont confidentiels. Une purge qui
laisse les plans en place sans que rien ne les désigne est le contraire de
cette règle, et c'est le genre de chose qu'on découvre le jour où on en a
besoin.

**Ce qui le rend vivable pour l'essai** : vous seul déposerez des plans, sur
une seule organisation, et vous ne purgerez rien pendant l'essai. Le volume est
sous votre main (`METREO_STORAGE_ROOT`), et un `rm -rf` du sous-dossier de
l'organisation reste possible — manuellement, donc sans écrit et sans audit,
ce qui est précisément ce que ce module dit refuser.

---

## B. Ce que j'ai cherché et n'ai pas trouvé

Dit ici parce qu'une revue qui ne liste que ses trouvailles ne dit pas ce
qu'elle a couvert.

| Axe | Constat |
| --- | --- |
| Isolation par organisation | Les cinq routes de plan passent toutes par `documents.get_revision`, qui filtre par `organization_id` et rend **404** — jamais 422 ni 403 — pour une révision d'un autre tenant. La route de tuile donne même ses paramètres de zone des valeurs par défaut pour que le contrôle d'appartenance passe AVANT le 422 de validation. |
| Registre transactionnel | Les trois routes d'écriture (`analyse`, `calibration`, `mesures`) sont classées `ECRITURE_ET_FICHIER`, avec leurs compensations. Le démarrage refuse une route d'écriture non classée. |
| Permissions | `DOCUMENT_WRITE` pour calibrer et mesurer, `DOCUMENT_VALIDATE` pour trancher — la séparation tient côté serveur. Calibrer n'exige pas `VALIDATE`, et c'est documenté comme un choix. |
| Têtes de migration | **Une seule tête**, `f3a4b5c60708`, vérifiée par `alembic heads` sur cette copie. Les dix colonnes numériques de `20261002_0008` déclarent bien `metreo_api.db.Amount` et non `sa.String(64)`. |
| Cache de tuiles | Clé déterministe, organisation et révision dans le CHEMIN et non dans l'empreinte ; deux plafonds (500 tuiles, 64 Mio) dont le premier atteint arrête l'écriture ; rendu dans un processus séparé avec plafond d'adressage et délai de 60 s. Les tuiles sont inscrites à la purge via `lecture_de_plan.cles_derivees`. |
| Rendu SVG | Politique de refus total sur les en-têtes, contenu vérifié avant d'être posé sur le volume, servi par `<img>`. `Cache-Control: private, no-store` sur l'aperçu et sur la tuile. |
| Secrets | L'atelier « Aucun secret commité » est vert ; `infra/staging.env` n'est pas versionné ; aucun plan réel dans l'arbre. |
| Formatage et lint | `ruff check` et `ruff format --check` passent sur les 220 fichiers. |

---

## C. Améliorations futures

Réelles, mesurées, et **non bloquantes** : aucune ne fausse ce que l'écran
affiche pendant l'essai sur vos quatre plans.

### C1. Sur une page en format PORTRAIT, l'incertitude annoncée est 1,41 fois trop petite

L'écran déclare au serveur `resolution_du_pointage = largeur_de_la_page × 0,05 / 512`.
Le rendu, lui, ajuste le facteur sur le **plus grand côté** de la zone
(`rendu_pdf`, `facteur = min(cote / max(largeur_zone, hauteur_zone), …)`). Les
deux coïncident exactement tant que la page est en PAYSAGE, et divergent dès
qu'elle est en portrait.

| Page | Déclarée | Rendue | Écart |
| --- | --- | --- | --- |
| vos quatre plans (1480×850, 1690×850, 1189×914 mm) | — | — | **exact** |
| A0 paysage | 0,329 | 0,329 | exact |
| A0 **portrait** | 0,233 | 0,329 | **1,41× sous-estimée** |
| A3 **portrait** | 0,082 | 0,116 | 1,41× sous-estimée |
| A4 **portrait** | 0,058 | 0,082 | 1,42× sous-estimée |

**Vos quatre plans sont tous en paysage** : le défaut ne les touche pas. Une
page de détail en portrait dans un jeu de plans, si. Le sens de l'écart est le
mauvais : il annonce une mesure **plus sûre** qu'elle ne l'est.

Reproduction : `/tmp` n'est pas un endroit durable, le calcul tient en dix
lignes et est reproduit dans la section D ci-dessous.

### C2. L'incertitude d'un tracé à plusieurs sommets est calculée comme s'il n'en avait que deux

`mesures_pdf.longueur` pose `du_trace = √2·ε / L`, ce qui est **exact pour un
segment droit à deux points** et optimiste pour une polyligne : chaque sommet
intermédiaire porte sa propre erreur de pointage.

| Cas (ε = 0,329 pt, loupe sur un plan de 1189 mm) | Annoncée | Majorant | Rapport |
| --- | --- | --- | --- |
| segment droit, calibration 170 pt | 0,289 % | 0,331 % | 1,14× |
| polyligne 5 segments, calibration 170 pt | 0,289 % | 0,423 % | 1,46× |
| polyligne 20 segments, calibration 170 pt | 0,289 % | 0,662 % | 2,29× |
| polyligne 20 segments, calibration longue 1 000 pt | 0,162 % | 1,006 % | 6,21× |
| polyligne 50 segments, calibration longue 1 000 pt | 0,162 % | 1,567 % | 9,68× |

**Dans aucun de ces cas le seuil de 2 % n'est franchi** : la *fiabilité*
annoncée reste juste, seul le **±** affiché est trop petit. Et pour un tracé
LISSE — une polyligne qui suit une courbe — les erreurs des sommets
intermédiaires se compensent largement, et la formule actuelle est proche du
vrai. C'est un tracé ANGULEUX qui l'écarte.

Ce qui manque n'est donc pas forcément une autre formule : c'est que la
docstring dise sous quelle hypothèse elle vaut.

### C3. L'écran PDF ne masque pas les commandes de décision à qui n'a pas le droit de trancher

`LecturePlan.tsx` calcule `peutValider = can(permissions, documentValidate)` et
le passe à l'écran DXF. **L'écran PDF ne le reçoit pas** : un compte `buyer`
ou `viewer` y voit « Confirmer », « Corriger » et « Rejeter », que le serveur
refusera par un 403. Le commentaire de `permissions.ts` affirme pourtant déjà
que « l'écran leur montre donc les mesures, sans les trois commandes » — c'est
vrai pour le DXF, faux pour le PDF.

Le serveur reste l'autorité et refuse : ce n'est pas une faille. C'est une
commande proposée qui ne peut pas aboutir, et une affirmation fausse dans un
commentaire. Sans effet sur votre essai, où vous êtes administrateur.

### C4. La valeur corrigée est une chaîne libre, sans unité et sans contrôle

`DecisionCreate.after_value` est un `dict[str, Any]` ; `calibration_de_plan`
le relit en `str(apres.get("valeur"))`. Rien ne vérifie que c'est un nombre,
rien n'attache d'unité. « 3800,5 », « 3.8 m » et « environ quatre mètres »
seraient acceptés et affichés à l'identique.

Sans conséquence aujourd'hui, parce que **rien ne reprend encore ces valeurs
dans un bordereau** — l'écran le dit lui-même. Le jour où une mesure validée
alimentera un métré, ce champ devra être un nombre et porter son unité.

### C5. Une calibration de ZONE peut diverger de ce que l'en-tête affiche

Le serveur choisit la calibration applicable « zonée d'abord, puis la plus
récente » ; l'en-tête de l'écran affiche simplement la plus récente de la page.
Les deux peuvent désigner des lignes différentes.

**Inatteignable par l'écran** : le formulaire de calibration n'envoie jamais de
`zone`, donc seules des calibrations de page entière existent, et les deux
règles coïncident. Chaque LIGNE de mesure affiche d'ailleurs le motif de la
calibration réellement utilisée — la provenance par mesure est juste. Seul
l'en-tête pourrait mentir, et seulement via l'API directe.

### C6. Les limites déjà connues, inchangées

- Pas de pré-génération des tuiles, pas de purge LRU : le cache est borné,
  jamais rafraîchi.
- Le cache de tuiles est **local à une instance** : deux conteneurs d'API ne
  le partagent pas.
- Un seul navigateur éprouvé : Chromium.
- Le rejet n'était couvert par aucun parcours de navigateur jusqu'à cette
  revue ; les captures jointes le couvrent désormais, mais le parcours de
  livraison `suite-plan-pdf-mesures.spec.ts` ne le joue toujours pas.
- La route de tuile ne vérifie pas que la révision est un PDF : sur un DXF, le
  fils échoue et l'API rend un 422 `rendu_impossible`. Correct, mais le message
  ne dit pas la vraie cause.
- « Textes lus 14 » dans l'en-tête contre « 9 textes affichés sur 9 lus » en
  bas : le premier compte toutes les pages, le second la page courante, et rien
  ne le dit.

---

## D. Reproduire les deux mesures de la section C

```python
COTE_TUILE, TAILLE_DE_LA_LOUPE, FACTEUR_MAXIMAL = 512, 0.05, 40.0

def declaree(largeur_pt):            # resolutionDeLaLoupe, LecturePdf.tsx
    return largeur_pt * TAILLE_DE_LA_LOUPE / COTE_TUILE

def rendue(largeur_pt, hauteur_pt):  # rendu_pdf.tuile
    zone = TAILLE_DE_LA_LOUPE * max(largeur_pt, hauteur_pt)
    return 1.0 / min(COTE_TUILE / zone, FACTEUR_MAXIMAL)

print(declaree(2384.0), rendue(2384.0, 3370.0))   # A0 portrait : 0,233 vs 0,329
```

```python
import math
EPS = 0.329                                        # points par pixel, loupe
annoncee = lambda d, L: math.hypot(math.sqrt(2) * EPS / d, math.sqrt(2) * EPS / L)
majorant = lambda d, L, n: math.hypot(math.sqrt(2) * EPS / d, 2 * math.sqrt(n + 1) * EPS / L)
print(annoncee(1000.0, 300.0), majorant(1000.0, 300.0, 20))   # 0,162 % vs 1,006 %
```

---

## E. Ce que cette revue ne dit pas

Elle ne dit **pas** que les mesures sont justes sur vos plans. Elle dit que
l'arithmétique est éprouvée sur des cas dont on connaît la réponse, que la
provenance de chaque nombre est conservée, et que trois choses affichées à
l'écran doivent changer avant que vous ne posiez un plan réel.

La justesse, elle, se mesure : il faut des cotes de référence relevées sur vos
propres plans, et c'est la fiche `docs/COTES_DE_REFERENCE.md` qui dit
lesquelles et comment. Tant qu'elle est vide, « Metreo mesure juste » reste une
opinion — la mienne comprise.
