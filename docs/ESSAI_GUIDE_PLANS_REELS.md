# L'essai guidé : vos plans, en une heure, du PDF au devis

> **Ce document est court à dessein.** Il enchaîne les sept gestes de l'essai et
> dit, à chaque étape, ce qu'il faut relever. Le détail écran par écran — les
> messages d'erreur, les pièges du pointage, ce que coûte la première loupe —
> est dans `docs/ESSAI_UTILISATEUR_PDF.md`, qui fait mille lignes et n'est pas
> fait pour être lu en marchant. La méthode de validation chiffrée, avec son
> tableau attendu / mesuré / écart, est dans `docs/VALIDATION_SUR_PLANS_REELS.md`.
>
> **Un premier essai a été joué sur un de vos plans**, un plan d'étage au 1/50
> livré en PDF et en DXF du même dessin, sur une pile locale isolée. Son compte
> rendu et ses captures montrent des extraits du plan : ils sont restés hors du
> dépôt, et vous ont été remis directement. Le § 5 dit comment le rejouer sur un
> autre plan. Ce premier essai ne remplace pas le vôtre : il a été pointé par un
> automate, au pixel près, et la main d'une personne ajoute une erreur qu'il ne
> mesure pas.

---

## 1. Ce qu'il faut fournir

### Les fichiers

| | Ce qu'il faut | Pourquoi |
| --- | --- | --- |
| **Un PDF** | un plan d'exécution, une page au moins, portant une cote écrite dont vous connaissez la valeur | c'est le format que l'essai mesure |
| **Le DXF du même dessin**, à la même révision | exporté depuis le même fichier source, le même jour | il porte les cotes du dessinateur : ce sont vos références, gratuites |
| *(facultatif)* un second PDF, format différent | A3 et A0, ou portrait et paysage | le format de page change l'incertitude annoncée, pas la mesure |

**Un `.dwg` est refusé, et l'écran le dit.** Exportez-le en DXF ou en PDF
depuis AutoCAD. Ce n'est pas une limitation de confort : lire un DWG demande
une bibliothèque sous licence propriétaire, et la décision n'a pas été prise.

**Les fichiers ne sont jamais commités ni transmis à un tiers.** Les trois
voies de dépôt possibles sont dans `docs/PLANS_REELS.md` ; cet essai n'en ajoute
aucune.

### Les références

**Au moins trois cotes par plan**, et pour chacune : la page, une description
de l'endroit (« cote 5000 de la façade sud, entre les deux traits de rappel »),
la valeur et son unité.

Une seule cote ne distingue pas une erreur d'échelle d'une erreur de pointage.
Idéalement : **une cote courte et une cote longue sur le même plan** — c'est ce
qui montre si l'erreur suit la longueur ou non — et **une cote oblique**, la
seule qui éprouve l'isotropie des deux axes. Le format exact attendu est au
§ 4 de `docs/COTES_DE_REFERENCE.md`.

**Et une surface** : une pièce fermée dont vous connaissez l'aire, ou dont vous
pouvez la calculer depuis deux cotes du plan.

### L'état du chantier

- un **projet** créé, avec sa référence ;
- une **fiche client rattachée** — l'émission d'un devis l'exige, et le refus
  arrive tard si on l'oublie ;
- un **bordereau** créé, même vide ;
- une **bibliothèque de prix** publiée, avec au moins un prix à l'unité de la
  mesure que vous reprendrez ;
### Le rôle qu'il faut, et pourquoi « métreur » ne suffit pas

Le parcours complet demande six droits : `document:write` pour déposer,
`document:validate` pour trancher une mesure, `boq:write` pour reprendre au
bordereau, `estimate:write` pour l'étude, **`estimate:freeze` pour geler** et
l'émission pour le devis.

**`estimator` — « Métreur / deviseur » — ne porte pas `estimate:freeze`.** Un
métreur mène donc l'essai jusqu'à la ligne de bordereau chiffrée, et s'arrête
là : il ne peut ni geler ni émettre. Pour aller jusqu'au PDF, conduisez l'essai
avec un compte **`org_admin`** ou **`estimating_manager`** — ou faites geler
par quelqu'un qui le peut, ce qui est d'ailleurs le découpage voulu.

---

## 2. Les sept gestes

### 1 — Déposer le PDF

Projet → **Documents** → catégorie, libellé, fichier → **Déposer**.

Le type réel est vérifié à la signature, pas à l'extension : un `.pdf` qui n'en
est pas un est refusé en le disant.

> **À relever** : le document apparaît-il avec sa taille et son empreinte ?

### 2 — Lire le plan

Sur la ligne du document → **Lire le plan**.

L'écran annonce ce qu'il a extrait : *« 4 fragment(s), 27 caractère(s) · 4
tracé(s) vectoriel(s) »*. C'est un constat, pas un verdict : un plan sans texte
mais avec du dessin vectoriel n'est pas un scan, et l'écran le dit autrement.

> **À relever** : le nombre de pages, le nombre de caractères et de tracés, et
> si l'écran annonce « document probablement scanné ».

### 3 — Déclarer l'échelle

**Déclarer l'échelle** → cliquez les deux extrémités d'une cote que vous
connaissez, **dans la loupe** et non sur l'aperçu → tapez sa distance réelle et
son unité → confirmez avec un motif (« cote 5000 du plan RDC, pointée à ses deux
extrémités »).

> **Un pixel de l'aperçu vaut plusieurs centimètres d'ouvrage sur un grand
> format.** C'est pourquoi les points se posent dans la loupe. Tant que
> l'agrandissement charge, le pointage est fermé : un clic ne s'enregistre pas
> dans une zone que vous ne voyez plus.

> **À relever** : le facteur affiché, en millimètres par point, et le motif.
>
> **Le contrôle qui prend dix secondes et sauve l'essai.** Un point PDF vaut
> 1/72 de pouce, soit 0,3528 mm de papier. Le facteur attendu est donc
> l'échelle du cartouche multipliée par 0,3528 :
>
> | Cartouche | Facteur attendu |
> | --- | --- |
> | 1:20 | ≈ 7,06 mm par point |
> | 1:50 | ≈ 17,64 mm par point |
> | 1:100 | ≈ 35,28 mm par point |
>
> Si le facteur rendu s'en écarte de plus de quelques pour cent, arrêtez-vous :
> l'échelle est fausse, et tout le reste le sera. Un écart **exactement** d'un
> rapport rond — 2, 2,5, 10 — veut dire que la feuille n'est pas à l'échelle
> que vous croyez, ou qu'elle a été réimprimée réduite.

### 4 — Mesurer deux cotes indépendantes et une surface

**Mesurer une longueur** → deux points dans la loupe. Recommencez sur la
deuxième cote de référence, puis sur la troisième.
**Mesurer une surface** → au moins trois points ; le contour se ferme seul.

Chaque mesure s'affiche avec son **±**, et le détail de la calibration utilisée.

> **À relever**, pour chacune : la valeur attendue, la valeur rendue, l'écart en
> pour cent, et le ± affiché. Le tableau est dans
> `docs/VALIDATION_SUR_PLANS_REELS.md`.
>
> **Le ± ne couvre pas tout.** C'est une incertitude type à k = 1, propagée
> depuis la seule finesse du pixel sous la souris. Une mesure peut être fausse
> d'un facteur 2 avec un ± de 0,04 % si l'échelle saisie est fausse.

### 5 — Comparer au DXF du même dessin

Déposez le DXF comme un second document, puis **Lire le plan**.

**Ce que le DXF donne, et que le PDF ne donne pas** : les **cotations écrites
par le dessinateur**, lues dans le fichier avec leur calque, leur feuille et
leur bloc d'origine. Metreo ne les pointe pas, il les lit — et quand le fichier
ne porte pas de mesure exploitable, il la recalcule depuis la géométrie et le
dit.

C'est la meilleure référence disponible pour contrôler votre pointage sur le
PDF : les deux chiffres viennent du même dessin, par deux chemins qui n'ont rien
en commun.

> **À relever** : pour chacune de vos trois cotes, la valeur lue dans le DXF à
> côté de celle mesurée sur le PDF. Un écart supérieur à votre tolérance pointe
> le pointage, pas le dessin.

> **Quatre réserves, dites franchement.**
>
> 1. **Metreo n'apparie pas les deux formats.** Une révision de document porte
>    un seul `media_type`, lu dans les octets ; votre PDF et votre DXF sont donc
>    deux documents (ou deux révisions) distincts, et le rapprochement se fait
>    par vous, à l'œil, sur deux onglets. Il n'existe en outre **aucun champ
>    pour l'indice de révision du cartouche** : « la même révision » est une
>    affirmation humaine, que le logiciel ne peut pas vérifier.
> 2. **Une cotation DXF ne se reprend pas encore dans un bordereau.** Seules les
>    mesures prises sur un plan PDF le peuvent aujourd'hui, et la route le
>    refuse en le disant. Le DXF sert donc de **référence**, pas de source de
>    quantités.
> 3. **Aucune surface n'est comparable.** L'écran DXF ne propose que des
>    cotations linéaires : le fichier ne porte pas d'aire. La surface mesurée
>    sur le PDF n'a donc pas de contrepartie, et se contrôle contre votre propre
>    relevé.
> 4. **Une cotation sans identifiant n'est pas proposée.** Metreo exige qu'une
>    cotation porte son `handle` DXF, pour qu'on puisse la retrouver et la
>    vérifier. Si votre DXF porte ses cotes « éclatées » — des traits et un
>    texte, et non des entités de cotation —, l'écran n'en proposera aucune.
>    C'est un constat sur le fichier, pas une panne.

### 6 — Trancher, puis reprendre au bordereau

Sur la mesure qui vous convient : **Confirmer**, **Corriger** (avec la valeur
retenue et son motif) ou **Rejeter** (avec son motif).

Puis, sous une mesure tranchée et retenue : **Reprendre dans un bordereau**.
Choisissez le bordereau, l'**unité du poste** — un métré se lit en mètres, la
mesure est en millimètres, la conversion est faite par le serveur —, le poste
et la désignation.

**Le nombre s'affiche avant d'être écrit** : « Quantité qui sera écrite » et sa
« Provenance » (la page, la décision, la valeur retenue). C'est exactement ce
que la ligne portera.

> **À relever** : la quantité annoncée, la quantité écrite au bordereau, et le
> badge « mesure de plan » sur la ligne.
>
> **Deux refus à éprouver** : une mesure **rejetée** n'offre aucune commande de
> reprise et affiche « n'alimentera aucun bordereau » ; une **seconde reprise**
> de la même mesure dans le même bordereau est refusée en le disant.

### 7 — Chiffrer, geler, émettre, télécharger

Bordereau → **Changer** le prix du poste → source « Bibliothèque de prix » →
choisissez le prix par son code → **Enregistrer**.
Puis **Créer une étude de prix** → **Ouvrir** → **Geler cette version** →
**Émettre le devis** (date de validité) → **Télécharger le PDF**.

> **À relever** : la quantité imprimée sur le PDF du devis. **Elle doit être
> exactement celle du bordereau**, à l'orthographe belge — « 6,02 m » à l'écran,
> « 6,02 » sur le document. Si les deux diffèrent, c'est un défaut, et le dire
> suffit : la règle d'écriture est dans `docs/ARRONDI_DES_DOCUMENTS.md`.

---

## 3. La fiche à rendre

Une page suffit. Pour chaque plan :

| | |
| --- | --- |
| Fichier (nom, empreinte SHA-256, format, taille) | |
| Échelle du cartouche, et facteur rendu par Metreo | |
| Cote 1 — attendue / mesurée / écart / ± affiché / valeur lue au DXF | |
| Cote 2 — idem | |
| Cote 3 (oblique) — idem | |
| Surface — attendue / mesurée / écart / ± affiché | |
| Quantité reprise au bordereau, et quantité imprimée sur le devis | |
| **Votre tolérance** — l'écart au-delà duquel vous refuseriez le chiffre | |

La dernière ligne est volontairement vide. Elle est la seule que je ne peux pas
remplir : une tolérance de métré est une décision d'entreprise, pas une
propriété du logiciel.

### La répétition, si vous avez dix minutes de plus

**Mesurez cinq fois la même cote**, en refermant la loupe entre chaque. L'écart
entre vos cinq valeurs est l'erreur de pointage réelle — celle d'une main sur un
vrai dessin. C'est le seul essai qui lève l'hypothèse H7 : la quadrature du ±
suppose une erreur **aléatoire**, et un biais systématique n'y apparaît nulle
part. Mesuré sur fixture, ce biais vaut +0,017 mm sur 6 000 — mais la grille de
pixels est centrée, et une main ne l'est pas.

---

## 4. Ce que cet essai établit, et ce qu'il n'établit pas

**Il établit** : que Metreo mesure juste **sur vos plans**, dans votre tolérance,
et que la quantité tranchée arrive intacte jusqu'au PDF du devis.

**Il n'établit pas** :

- le comportement sur **une page à deux échelles** — un plan au 1:50 et un
  détail au 1:20 sur la même feuille donnent une mesure fausse de 2,5× **sans
  réserve affichée**. Si l'un de vos plans est dans ce cas, dites-le : c'est le
  seul fichier qui peut lever cette hypothèse ;
- l'**indépendance entre deux mesures d'une même page** : le terme du facteur
  d'échelle leur est commun et totalement corrélé. Un total de vingt murs n'a
  pas une incertitude en √20 mais en 20 ;
- quoi que ce soit sur le **serveur de préproduction** : cet essai se joue dans
  l'application, pas sur la machine.

> **Un essai n'est pas annulable côté schéma.** Dès la **première mesure
> enregistrée**, la descente du schéma est fermée : une migration refuse de
> redescendre s'il reste une citation ancrée par page et boîte. Ce n'est pas un
> défaut, c'est la garde qui empêche de détruire silencieusement les mesures
> d'un essai — mais il faut le savoir avant de commencer, et c'est pourquoi la
> sauvegarde se prend **avant**. Le détail des trois voies de retour arrière est
> dans `docs/MISE_EN_LIGNE_LECTURE_DE_PLANS.md`.

Le prototype réalise une **mesure assistée**. Sa justesse sur vos plans est
exactement ce que cet essai existe pour établir, et rien d'autre ne le fera.

---

## 5. Rejouer l'essai sur une pile locale isolée

Tout se passe sur votre machine, sur la boucle locale, et rien n'entre dans le
dépôt. Les plans restent dans un dossier privé en droits 700, hors du clone.

```
ops/essai_local.sh up            # migrations, amorçage, interface, API — sur 127.0.0.1
cp vos-plans/*.pdf vos-plans/*.dxf ~/.metreo-essai/plans/
```

Puis, dans l'ordre :

1. **Déposer et lire** chaque plan, et relever ce que Metreo en comprend :
   `scripts/essai_sur_plans_reels.py deposer`, puis `dxf` pour un DXF — il
   compare les cotations proposées à un recensement indépendant du fichier, et
   liste les types d'entités qui ne deviennent pas des mesures.
2. **Écrire le plan d'essai**, un fichier JSON privé : les deux extrémités de
   la cote de calibration, les cotes courte, longue et oblique, les coins d'une
   surface, les cinq répétitions, la mesure à reprendre. Les points s'écrivent
   en fractions de page, origine en haut à gauche, comme l'écran les compte.
   `textes` et `tuile` aident à les situer.
3. **Rejouer le parcours au navigateur**, qui photographie chaque étape :
   `apps/web/playwright.essai.config.ts`. Il refuse de démarrer si ses sorties
   ou le plan d'essai sont dans le dépôt, et aucune configuration de CI ne le
   ramasse.
4. **Rédiger le compte rendu** : `scripts/essai_sur_plans_reels.py rapport`
   compare chaque mesure à sa référence, à la cotation DXF de même handle, et
   suit la quantité reprise jusqu'au texte du PDF émis.

`ops/essai_local.sh effacer --confirmer` détruit la base, le stockage et les
résultats de l'essai, et ne touche jamais au dossier des plans.

