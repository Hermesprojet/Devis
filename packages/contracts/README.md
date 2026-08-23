# Contrats documentaires Metreo

Ce paquet Python pur décrit les frontières fournisseur-indépendantes du futur
pipeline documentaire. Il ne contient aucun adaptateur, accès réseau, stockage,
modèle ORM, route HTTP, OCR ou LLM concret.

Il expose :

- des valeurs immuables pour révisions, citations, confiance, OCR, tableaux,
  classifications, extractions structurées, embeddings et recherche ;
- sept `Protocol` : `ObjectStore`, `OcrPort`, `TableExtractionPort`,
  `ClassifierPort`, `StructuredLlmPort`, `EmbeddingPort` et `SearchPort` ;
- des erreurs à codes stables et une sérialisation qui conserve les `Decimal`
  sous forme de chaînes, sans conversion flottante.

Chaque appel de port exige explicitement `organization_id`. Les adaptateurs
seront ajoutés dans des tranches ultérieures et resteront hors de ce paquet.
