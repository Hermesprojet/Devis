# Hypothèses retenues

Le cahier des charges prévoit dix questions bloquantes. Elles ont été posées
sans réponse à ce stade, donc les valeurs par défaut du cahier des charges ont
été appliquées. **Chaque hypothèse est réversible** ; la colonne « coût du
changement » indique ce que coûterait une décision différente.

| # | Question | Hypothèse appliquée | Coût du changement |
| --- | --- | --- | --- |
| 1 | SaaS hébergé, installation dédiée, ou les deux ? | SaaS multi-tenant hébergé dans l'UE. Le mono-tenant reste possible : l'isolation passe par `organization_id` et non par une base par client. | Faible. Une installation dédiée n'est qu'un déploiement avec une seule organisation. |
| 2 | Volumétrie du pilote ? | 5 à 20 entreprises, ~10 utilisateurs simultanés, dossiers jusqu'à 500 Mo, bordereaux jusqu'à ~2 000 lignes. | Faible tant qu'on reste sous PostgreSQL avec pagination serveur. |
| 3 | Formats actuels des bibliothèques et bordereaux ? | CSV et XLSX, séparateur `;`, décimale française, encodages UTF-8/CP1252. Seul le CSV est implémenté ; XLSX est prévu en phase 2. | Faible. Le parseur est isolé dans `services/price_import.py`. |
| 4 | Comment sont calculés frais de chantier, frais généraux, aléas et marge ? | Chaîne configurable par entreprise : déboursé sec → frais de chantier → frais généraux → prix de revient → aléas → marge → prix de vente HT. Base de chaque taux configurable, marge sur coût ou sur prix de vente. | Faible pour un autre ordre de la même chaîne, moyen pour une logique différente (répartition au prorata des heures, par exemple). |
| 5 | Langues obligatoires au pilote ? | Français seul, structure i18n en place (`apps/web/src/lib/i18n.ts`, locales `fr`/`nl`/`en` déclarées). | Faible : c'est un travail de traduction, pas de refonte. |
| 6 | Premier type de chantier couvert de bout en bout ? | Terrassement / égouttage / voirie. Les fixtures décrivent une réfection de voirie wallonne fictive. | Nul. Rien n'est spécifique à ce type de travaux dans le code. |
| 7 | Fournisseurs OCR/IA/cloud autorisés, zone des données ? | Aucun. `METREO_AI_ENABLED=false`, aucun appel sortant. Les interfaces sont prévues, pas les implémentations. | Faible : un adaptateur à écrire derrière une interface déjà décrite. |
| 8 | Système d'e-mail pour les demandes de prix ? | Aucun. La phase 4 n'est pas implémentée et rien ne prétend l'être. | Sans objet à ce stade. |
| 9 | Intégrations disposant d'un contrat et d'une API ? | Aucune. Aucune intégration externe n'est appelée. | Sans objet à ce stade. |
| 10 | Qui valide juridiquement les packs Belgique puis France ? | Personne. Les quatre packs semés sont en statut `draft` ou `planned` et portent un avertissement explicite. | Bloquant pour une mise en production, pas pour le développement. |

## Hypothèses techniques additionnelles

| Sujet | Hypothèse | Motif |
| --- | --- | --- |
| Nom du produit | **Metreo**, provisoire | Le cahier des charges laisse `[NOM_APPLICATION]` ouvert. Le nom n'apparaît que dans des libellés et des noms de paquets Python, renommables. |
| Base de données | PostgreSQL 16 en cible, SQLite accepté en développement et en test | Permet de lancer le projet et la suite de tests sans service. Refusé en `staging`/`production` par `Settings.validate_startup()`. |
| Montants en base | `NUMERIC(28,10)` sur PostgreSQL, chaîne exacte sur SQLite (type `Amount` dans `apps/api/src/metreo_api/db.py`) | SQLite n'a pas de type décimal ; passer par un flottant binaire corromprait les montants. Toute l'arithmétique se fait en Python de toute façon. |
| Identifiants | UUID v4 en `String(36)` | Portables entre PostgreSQL et SQLite, stables entre environnements, non devinables. |
| Authentification | JWT HS256, mode `dev` sans mot de passe pour le développement | Aucun fournisseur d'identité disponible. OIDC/OAuth2 est un chantier de phase 5 explicitement identifié. |
| Isolation multi-tenant | Filtrage systématique par `organization_id` dans la couche service (`services/tenant.py`), testé par `apps/api/tests/test_tenant_isolation.py` | Le RLS PostgreSQL est plus fort mais rendrait la suite de tests SQLite impossible. Voir `adr/0002-multi-tenancy.md`. |
| Application des marges | Par ligne, puis somme | Pratique courante pour un bordereau de prix unitaires : le client voit un prix unitaire cohérent poste par poste. Une application globale reste possible via `markup_override`. |
| Arrondi | Stockage non arrondi, arrondi à l'affichage et à l'export via `RoundingPolicy` | Exigence explicite du cahier des charges. Les deux valeurs sont exposées par l'API (`total_selling_price_ht` brut et `..._display`). |
| Rotation de camion | Arrondi supérieur par défaut | Un demi-camion ne quitte pas le chantier. Désactivable par composant (`round_up=False`). |
| Aperçu de devis | HTML imprimable, pas de PDF binaire | Imprimable en PDF depuis n'importe quel navigateur, sans chaîne de compilation, et n'usurpe pas le rendu typographique définitif prévu plus tard. |

## Comment changer une hypothèse

1. Ouvrir une ADR dans `docs/adr/` qui remplace celle concernée.
2. Mettre à jour la ligne correspondante ici, avec la date et la décision.
3. Adapter le code, les migrations et les tests dans la même tranche.

Une hypothèse qui change sans ADR est une dette invisible.
