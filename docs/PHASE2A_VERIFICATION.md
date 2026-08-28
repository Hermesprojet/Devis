# Phase 2A — dossier de vérification

Ce dossier décrit uniquement les fondations documentaires. Il ne prétend pas
qu'un fichier peut déjà être téléversé ou analysé.

## Référence contrôlée

- dernière tête de code : `6618e30e3d05bf4928850e401410bb3d1ffc3d21` ;
- workflow CI : [32673926588](https://github.com/Hermesprojet/Devis/actions/runs/32673926588) ;
- résultat : 10 jobs sur 10 réussis ;
- PR : #6, empilée sur la PR #1 et maintenue en brouillon pendant la revue.

## Résultats reproductibles

| Contrôle | Résultat sur la tête de code |
| --- | ---: |
| Domaine pur | 127 réussis |
| Contrats documentaires purs | 22 réussis |
| API SQLite | 562 réussis, 27 ignorés car PostgreSQL requis |
| API PostgreSQL réel | 589 réussis |
| Inventaire PostgreSQL-only | 73 réussis |
| Migrations PostgreSQL | `base -> head -> base -> head` réussi |
| Lint, format et typage | réussis |
| Web, Playwright, Docker, installation et audits | réussis |

Commandes locales principales :

```bash
make install
make lint
make types
make skills
make test-domain
make test-contracts
make test-api
make test-api-postgres METREO_TEST_DATABASE_URL=postgresql+psycopg://…
```

La CI exécute la suite PostgreSQL et vérifie séparément que les tests marqués
PostgreSQL-only n'ont pas été ignorés.

## Matrice de fermeture

| Invariant | Preuve |
| --- | --- |
| Aucun contrat dépend d'un fournisseur | sept `Protocol` dans `packages/contracts`, sans FastAPI, ORM ni SDK |
| Isolation relationnelle | six tables portent `organization_id` et toutes les relations tenant-owned utilisent des clés étrangères composites |
| Citation résolvable | révision, page, plage, boîte normalisée et confiance `Decimal` sont obligatoires et bornées en SQL |
| Aucune approbation automatique | aucun statut documentaire `approved`; une proposition à confiance minimale reste `proposed` et ne crée aucun poste, prix ou gel |
| Révision publiée immuable | `UPDATE` et `DELETE` refusés par trigger sur SQLite et PostgreSQL |
| Décision humaine append-only | acteur du tenant obligatoire ; `UPDATE` et `DELETE` toujours refusés par trigger |
| Autorisations séparées | `DOCUMENT_READ`, `DOCUMENT_WRITE` et `DOCUMENT_VALIDATE` ont une matrice explicite ; `DOCUMENT_VALIDATE` n'accorde pas `BOQ_APPROVE` |
| 404 inter-tenant | documents, révisions, propositions et décisions sont lus via un filtre d'organisation |
| Numéros de révision concurrents | quatre transactions PostgreSQL obtiennent exactement les numéros 1, 2, 3 et 4 |
| Exécution idempotente | quatre revendications simultanées d'une même clé partagent une ligne et une seule la crée |
| Versions de traitement | la clé inclut révision, étape, pipeline, prompt et modèle ; changer une version produit une nouvelle exécution |
| Relance explicite | seule une étape `failed` peut être relancée ; l'identité est conservée, la tentative augmente et l'erreur précédente est effacée |
| Erreur non sensible | le service accepte uniquement des codes machine bornés, jamais une exception fournisseur ou un texte documentaire libre |

## Falsifiabilité

Les invariants SQL sont testés par écritures directes qui contournent Pydantic
et les services. Les verrous sont prouvés de deux manières complémentaires :

1. le contrôle AST échoue si `claim_step_run` ne verrouille plus la révision,
   ou si une transition ne verrouille plus la ligne d'étape ;
2. le test PostgreSQL ouvre quatre connexions réelles et vérifie l'unicité de
   l'identifiant, du créateur et de la ligne persistée.

La contrainte d'unicité reste le dernier rempart pour un écrivain qui
contournerait le service. Les tests de contraintes, de triggers et de clés
étrangères tournent sur les deux moteurs.

## Ce qui n'existe pas encore

Pas d'upload, de stockage binaire concret, d'antivirus, d'OCR, de LLM,
d'embedding, de recherche, de worker, de traitement en arrière-plan ni
d'interface documentaire. Aucun fournisseur externe n'est appelé et aucune
clé fournisseur n'est nécessaire à cette tranche.

Les scénarios d'acceptation complets 11 à 14 restent donc ouverts pour les
tranches suivantes. Le socle rend leur implémentation sûre ; il ne les simule
pas.

## Ordre d'intégration

La PR #6 a pour base la branche de la PR #1. Elle ne doit pas être fusionnée
avant l'intégration de cette base. Une autorisation humaine de fusion ne
remplace ni cet ordre Git, ni une CI verte sur la tête exacte à intégrer.
