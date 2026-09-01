# Fixtures d'import

Jeux de données **entièrement fictifs**. Aucun prix ne représente un prix de marché.

| Fichier | Contenu | Sert à |
| --- | --- | --- |
| `modele_import_prix.csv` | En-têtes seuls | Modèle téléchargeable proposé à l'utilisateur |
| `prix_valides_5_lignes.csv` | 5 lignes valides | Import nominal |
| `prix_5_valides_2_erreurs.csv` | 5 lignes valides + 2 lignes fautives | Scénario d'acceptation n° 2 |
| `*.xlsx` | Les mêmes tableaux, en classeur | Équivalence CSV/XLSX |

Les trois classeurs sont **fabriqués**, jamais commités :

```
python3 scripts/fabriquer_classeurs_de_test.py
```

Un `.xlsx` est une archive binaire ; commité, il devient un bloc que personne ne
relit. Fabriqué, on voit quelles cellules et quels TYPES il porte — et c'est ce
qui rend l'équivalence probante : le CSV porte « 41,50 » et « 01/01/2026 », du
texte à la française, là où le classeur porte un nombre et une date. Les deux
disent la même chose sous deux formes que rien n'oblige à converger, sinon le
pipeline de normalisation commun.

Les deux erreurs volontaires de `prix_5_valides_2_erreurs.csv` :

1. ligne 4 — unité `bordure` inconnue ;
2. ligne 7 — code et prix unitaire manquants.

Séparateur `;`, décimale française `,`, encodage UTF-8 : le parseur détecte les
trois automatiquement et signale ce qu'il a détecté dans le rapport de
prévisualisation.
