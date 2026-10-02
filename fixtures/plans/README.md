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
