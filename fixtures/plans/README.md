# Fixtures de plans

Fichiers **entièrement fictifs**, fabriqués pour les tests. Aucun n'est un plan
réel, aucune cote ne décrit un ouvrage existant.

| Fichier | Contenu | Sert à |
| --- | --- | --- |
| `mur_simple.dxf` | DXF ASCII valide : `$INSUNITS` = 4 (millimètres), deux `LINE` sur le calque `MURS` | Lecture nominale |
| `sans_unites.dxf` | Le même, **sans** `$INSUNITS` | Unité absente : la mesure doit être refusée, pas supposée |
| `tronque.dxf` | Un DXF coupé au milieu de son en-tête | Fichier invalide : le refus doit nommer la cause |
| `piege_section.csv` | Un CSV de prix dont les codes valent `AC1032` et dont les libellés contiennent « SECTION » | Non-régression : ni pris pour un DWG, ni pour un DXF |

Deux cas ne sont **pas** commités, parce qu'ils sont binaires et qu'un binaire
commité devient un bloc que personne ne relit :

```
python3 scripts/fabriquer_plans_de_test.py
```

Il fabrique `binaire.dxf` — la sentinelle du DXF binaire — et `faux.dwg` — un
en-tête DWG suivi d'octets quelconques. Ce dernier n'est pas un vrai DWG et
n'a pas à l'être : il sert à vérifier que son **en-tête** suffit à le faire
refuser, avant toute tentative de lecture.

## `mur_cote.dxf` — fabriqué, pas commité

Un mur de 5 m **avec sa cotation**, en millimètres. C'est la seule fixture de
ce dossier qui propose quelque chose à mesurer : `mur_simple.dxf` porte deux
lignes et aucune cotation, donc son plan se lit sans rien proposer.

Elle n'est pas commitée bien qu'elle soit du texte : une cotation a besoin de
son bloc géométrique — sans lui, l'audit la retire au rechargement et l'espace
modèle revient vide — et ce bloc fait trois mille lignes qu'aucun relecteur ne
lira. `scripts/fabriquer_plans_de_test.py` l'écrit en dix lignes lisibles, et
le banc Playwright l'appelle avant de servir quoi que ce soit.

## `plan_cote.pdf` — fabriqué, pas commité

Un plan PDF de **deux pages** de 300 × 220 points, neuf textes sur la première
et cinq sur la seconde, chacun à une position connue en points PostScript.

**Deux pages, et aux textes distincts**, parce que la navigation entre pages
est une des choses qu'un écran peut faire semblant de faire : changer le numéro
affiché et resservir la même image. « DETAIL B » n'existe que sur la seconde,
et c'est ce mot que le parcours cherche après avoir cliqué sur « page
suivante ».

Les octets sont écrits à la main par `scripts/fabriquer_pdf_de_test.py`, table
d'offsets comprise — la **même** fabrique que les tests du dépôt et que
l'épreuve qui tourne dans l'image, pour qu'il n'y ait pas deux vérités sur ce
que contient la fixture.

    python3 scripts/fabriquer_plans_de_test.py

## `plan_batiment.pdf` — une géométrie dont on connaît les dimensions

**Pourquoi un second plan PDF, alors que `plan_cote.pdf` existe.** Celui-ci
porte des TEXTES isolés. Il éprouve les interactions — le texte est situé, la
loupe agrandit, le clic retombe au bon endroit — et c'est tout ce qu'il peut
éprouver : il n'y a rien à mesurer sur un mot. Une démonstration faite dessus
agrandit « 00 » et mesure un fragment de « Coupe A-A ». Les gestes sont les
bons, le résultat ne veut rien dire.

`plan_batiment.pdf` porte, sur 420 × 320 points :

- **une ligne de cote horizontale** de 200 points, avec ses deux traits de
  rappel et le texte « 5000 ». C'est sur elle qu'on calibre, et ses extrémités
  sont POINTABLES — sans traits de rappel, les deux bouts d'une ligne sont deux
  pixels indiscernables du reste, et « cliquez les deux extrémités » cesse
  d'être une consigne exécutable ;
- **une pièce rectangulaire fermée** de 240 × 160 points, tracée par un seul
  contour fermé et non par quatre segments — quatre segments laisseraient
  quatre micro-ouvertures aux angles ;
- un cartouche minimal, pour que l'écran ait du texte à situer.

**L'échelle du dessin est déclarée dans la fabrique** : un point de papier vaut
25 mm d'ouvrage. La cote fait donc 5 000 mm, la pièce 6 000 × 4 000 mm, et sa
surface **24,00 m²** — trois nombres calculés depuis la géométrie posée, jamais
écrits sur le dessin et jamais lus par le lecteur.

`plan_batiment.json`, écrit à côté du PDF par la même fabrique, porte ces
nombres et les coordonnées des points à cliquer. C'est la **seule** passerelle
vers le TypeScript : un parcours de navigateur ne peut pas importer du Python,
et recopier les chiffres ferait deux sources dont la seconde finirait par
mentir.

Il existe aussi en **portrait** — `plan_de_batiment_en_portrait()`, non écrit
sur le disque — parce que les quatre plans du propriétaire sont tous en
paysage, et qu'un défaut de résolution de pointage ne se voit que sur une page
plus haute que large.

    python3 scripts/fabriquer_plans_de_test.py
