# packages/contracts — réservé

**Vide volontairement.** Le contrat entre l'API et le web est aujourd'hui le
document OpenAPI produit par FastAPI (`GET /openapi.json`), et les types
TypeScript correspondants sont écrits à la main dans
`apps/web/src/lib/api.ts`, où ils restent lisibles et peu nombreux.

Ce répertoire accueillera les types générés depuis OpenAPI le jour où la surface
de l'API rendra la maintenance manuelle coûteuse. Générer maintenant ajouterait
une étape de build pour un fichier de 400 lignes.
