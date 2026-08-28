# Brief produit

## Le produit en dix lignes

Metreo aide un métreur ou un deviseur d'entreprise de travaux à passer d'un
dossier d'appel d'offres à un devis défendable. Il centralise le dossier,
propose l'extraction des exigences et des quantités avec leur source, laisse
l'humain valider, applique les prix et les règles de calcul de l'entreprise,
signale ce qui manque, permet de consulter fournisseurs et sous-traitants, puis
produit un devis gelé, traçable et exportable. Les calculs sont déterministes et
entièrement décomposables ; l'IA lit et propose, elle ne chiffre pas. Le premier
marché est la Belgique, avec des profils distincts pour la Wallonie, la Flandre
et Bruxelles, puis la France et le reste de l'Europe par packs versionnés.
L'outil est une aide à la décision : il ne rend jamais un avis juridique ni une
conformité automatique.

## Le problème

Une entreprise qui répond à un marché de voirie ou d'égouttage travaille
aujourd'hui avec un cahier spécial des charges en PDF, un métré en tableur, des
plans en DWG, un rapport de sol, et une bibliothèque de prix qui vit dans
plusieurs classeurs Excel. Le temps de l'étude part dans la re-saisie et la
recherche d'informations, pas dans le chiffrage. Trois conséquences :

1. **Les erreurs sont silencieuses.** Une quantité recopiée à l'envers, une
   densité supposée, une unité mal convertie ne se voient qu'après attribution.
2. **Rien n'est traçable.** Six mois plus tard, personne ne sait d'où venait le
   prix retenu ni quelle hypothèse a été faite sur l'évacuation des terres.
3. **Le prix de revient n'est pas comparable au réel.** Les coûts d'exécution
   ne remontent pas dans la bibliothèque.

## Les utilisateurs

| Rôle | Ce qu'il vient faire | Ce qu'il ne doit pas voir |
| --- | --- | --- |
| Administrateur de l'entreprise | Utilisateurs, paramètres, bibliothèques, règles de calcul, intégrations | — |
| Responsable étude de prix | Marges, validations, gel, approbation finale | — |
| Métreur / deviseur | Documents, métrés, postes, sous-détails, brouillons de devis | Marges et coefficients commerciaux |
| Chef de projet / conducteur | Hypothèses, variantes, retours d'exécution | Marges, salaires chargés |
| Acheteur | Fournisseurs, demandes de prix, comparatifs | Marges |
| Lecteur / auditeur | Lecture seule, journal d'audit | Coûts internes |
| Fournisseur / sous-traitant (à terme) | Ses propres demandes et réponses | Tout le reste |

## Parcours cible

1. Créer l'entreprise cliente et son profil régional.
2. Importer prix, ressources, rendements, fournisseurs, coefficients.
3. Créer un projet et son profil réglementaire.
4. Déposer le dossier (cahier des charges, bordereau, plans, rapports, photos).
5. Classer les documents, détecter doublons et révisions.
6. Extraire clauses, postes, unités, quantités candidates, risques, questions —
   **avec preuve**.
7. Faire valider par un humain.
8. Construire ou importer le métré.
9. Affecter des prix internes ou demander des prix externes.
10. Comparer les réponses à périmètre égal.
11. Calculer des variantes et des scénarios.
12. Contrôler puis geler une version.
13. Exporter devis, détail interne, hypothèses, dossier de consultation.
14. Après exécution, enregistrer les coûts réels sans écraser l'historique.

Les étapes **1, 2, 3, 8, 9 (prix internes), 11 (partiellement), 12 et 13** sont
implémentées. Les autres sont décrites dans `ROADMAP.md`.

## Ce que le produit refuse de faire

- Présenter une quantité, un prix ou une conformité générés par IA comme
  certains, sans preuve et sans validation humaine.
- Compléter un prix ou une quantité manquante « au mieux ». Un poste sans prix
  reste un poste sans prix, et bloque l'approbation si l'entreprise le décide.
- Convertir des mètres cubes en tonnes sans une masse volumique dont la source
  est enregistrée.
- Modifier une quantité approuvée sans dérogation explicite et motivée.
- Modifier rétroactivement un devis gelé parce qu'un prix de référence a bougé.
- Envoyer quoi que ce soit à l'extérieur sans confirmation humaine explicite.
- Rendre un avis juridique ou déclarer une conformité réglementaire.
- Aspirer des données de LinkedIn, Google, Walterre ou Embuild.

## Ce qui distingue le produit

Le pari n'est pas « l'IA fait le métré ». Le pari est que **la traçabilité et le
déterminisme du calcul** valent plus que l'automatisation : un chiffre que l'on
peut refaire à la main, dont on connaît la source et l'hypothèse, se défend en
réunion d'attribution. Un chiffre produit par un modèle, non.

Concrètement, dans la version livrée :

- chaque montant expose sa formule (`120 m3 ÷ 28 m3/h = 4,2857 h × 92,00 €/h`) ;
- chaque conversion volume/masse cite le rapport de sol qui donne la densité ;
- chaque version gelée conserve la bibliothèque de prix employée et une
  empreinte SHA-256 de son propre contenu ;
- chaque action importante est chaînée dans un journal d'audit vérifiable.

## Mesure du succès

| Indicateur | Cible pilote |
| --- | --- |
| Temps de préparation d'un métré de voirie moyen | −30 % contre le processus tableur |
| Postes chiffrés sans hypothèse documentée | 0 |
| Écart entre prix de revient estimé et coût réel après chantier | mesuré, puis réduit |
| Devis remis avec une version gelée et exportée | 100 % |
| Incidents d'isolation entre entreprises clientes | 0 |
