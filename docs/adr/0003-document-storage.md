# ADR 0003 — Stockage documentaire

- **Statut** : accepté (décision de conception ; l'implémentation arrive en phase 2)
- **Date** : 2026-08-20

## Contexte

Le produit recevra des cahiers des charges, des plans, des rapports de sol et
des photos de chantier — souvent confidentiels, parfois volumineux, toujours
susceptibles d'être révisés en cours d'appel d'offres. La phase 1 n'en manipule
aucun, mais le modèle de données et les frontières doivent être décidés
maintenant, sous peine de reprise coûteuse.

## Décision

**Le fichier original est immuable et conservé tel quel ; la base ne stocke que
des métadonnées, une empreinte et des références.**

1. **Original intact.** Le binaire reçu est stocké sans transformation, avec son
   SHA-256. Toute dérivée (texte extrait, image rendue, conversion CAO) est un
   artefact reproductible, jamais la référence.
2. **Stockage objet compatible S3**, derrière une interface `ObjectStore` avec
   une implémentation locale sur disque pour le développement. Aucun code métier
   ne connaît le fournisseur.
3. **Révisions, pas écrasement.** `Document` porte l'identité logique (« CSC lot
   2 »), `DocumentRevision` porte le fichier. Deux dépôts du même contenu
   (même SHA-256) se dédupliquent ; un contenu différent crée une révision.
4. **Accès uniquement par URL signée de courte durée**, émise après contrôle de
   permission. Jamais d'URL publique, jamais d'identifiant devinable.
5. **La citation est un objet de première classe.** `SourceCitation` porte
   révision, page, plage de caractères, boîte englobante normalisée et, pour un
   plan, feuille/calque/objet. Pas de texte libre « vu page 12 ».
6. **Séparation proposition / donnée validée.** L'extraction écrit dans
   `ExtractionProposal`. Le passage vers une table approuvée est une
   `ValidationDecision` humaine. Aucun code d'IA n'écrit dans une table
   approuvée.

## Conséquences

- Le pipeline doit être **relançable** : une nouvelle version d'extraction se
  rejoue sur les originaux conservés, sans redemander les fichiers.
- Le cache d'extraction est indexé par `(SHA-256 du document, version du
  pipeline)`, ce qui rend le retraitement bon marché et reproductible.
- La suppression RGPD porte sur l'original **et** sur toutes ses dérivées ; elles
  doivent donc être découvrables depuis la révision.
- Une phase 1 sans documents reste cohérente : rien dans le modèle actuel ne
  contredit ce schéma.

## Notes

Le stockage objet est démarrable localement (MinIO ou système de fichiers). Le
fournisseur cloud est une décision de déploiement, contrainte par l'exigence
d'hébergement européen.
