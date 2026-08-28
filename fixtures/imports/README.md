# Fixtures d'import

Jeux de données **entièrement fictifs**. Aucun prix ne représente un prix de marché.

| Fichier | Contenu | Sert à |
| --- | --- | --- |
| `modele_import_prix.csv` | En-têtes seuls | Modèle téléchargeable proposé à l'utilisateur |
| `prix_valides_5_lignes.csv` | 5 lignes valides | Import nominal |
| `prix_5_valides_2_erreurs.csv` | 5 lignes valides + 2 lignes fautives | Scénario d'acceptation n° 2 |

Les deux erreurs volontaires de `prix_5_valides_2_erreurs.csv` :

1. ligne 4 — unité `bordure` inconnue ;
2. ligne 7 — code et prix unitaire manquants.

Séparateur `;`, décimale française `,`, encodage UTF-8 : le parseur détecte les
trois automatiquement et signale ce qu'il a détecté dans le rapport de
prévisualisation.
