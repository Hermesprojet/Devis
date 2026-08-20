# Conventions

## Code

| Sujet | Règle |
| --- | --- |
| Formatage Python | `ruff format`, 100 colonnes |
| Lint Python | `ruff check` (E, F, W, I, UP, B, C4, SIM, RUF) |
| Typage Python | `mypy` ; `disallow_untyped_defs` sur `metreo_domain` |
| Formatage TS | Conventions Next.js par défaut, `tsc --noEmit` en mode `strict` avec `noUncheckedIndexedAccess` |
| Langue du code | Identifiants et commentaires en anglais ; **messages destinés à l'utilisateur en français**, via `t()` côté web et en clair côté API |
| Commentaires | Expliquent *pourquoi*, jamais *quoi*. Un commentaire qui paraphrase la ligne suivante est supprimé en revue |
| Docstrings | Sur tout module et toute fonction dont le comportement n'est pas évident à la lecture. Elles portent la règle métier, pas la signature |

## Erreurs

- Le domaine lève des `DomainError` typées, porteuses d'un `code` stable et d'un
  contexte. L'API les traduit en 422 avec ce code.
- Un refus métier est un 4xx, pas un 500. Une conversion ambiguë ou un rendement
  nul, c'est le moteur qui fait son travail.
- Les messages utilisateur disent **quoi faire**, pas seulement ce qui a échoué :
  « Unité « bordure » inconnue. Unités acceptées : m, m2, m3, t, kg, h, pce, fft… »

## Base de données

- Aucune modification de schéma sans migration Alembic.
- Toute migration a un `downgrade` fonctionnel, rejoué en CI sur PostgreSQL.
- Le test `test_migrations_reproduce_the_models_exactly` échoue si un modèle
  diverge de la migration.
- Jamais de modification manuelle d'un schéma de production.

## Sécurité

- Aucun secret dans le dépôt. `.env.example` ne contient aucune valeur.
- Toute action vérifie une permission côté serveur via `require(Permission.X)`.
- Toute lecture métier passe par `services/tenant.get_owned`.
- Toute nouvelle ressource appartenant à un tenant reçoit un test « l'autre
  organisation reçoit 404 ».
- Les logs ne contiennent ni contenu de document, ni jeton, ni donnée
  personnelle au-delà de l'identification de l'objet.

## Git

- Branches : `claude/<sujet>` ou `feat/<sujet>`, `fix/<sujet>`.
- Messages de commit à l'impératif, en français ou en anglais, cohérents dans un
  même commit. Le corps explique le *pourquoi*.
- Un commit = une tranche verticale cohérente : code, migration, tests et
  documentation ensemble.
- Aucun identifiant de modèle d'IA dans un message de commit, un titre de PR ou
  un commentaire de code.

## Revue

Une modification est refusée si :

- une règle de calcul apparaît ailleurs que dans `packages/domain` ;
- une requête métier ne filtre pas sur `organization_id` ;
- un montant transite par un `float` ;
- un écran présente comme disponible une fonction non implémentée ;
- un test manque sur un comportement métier nouveau ;
- une chaîne destinée à l'utilisateur est écrite en dur côté web hors de `t()`.
