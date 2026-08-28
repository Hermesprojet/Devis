# Références vers les utilisateurs — note de décision

**Statut : aucune décision prise, aucun schéma modifié.** Cette note compare
quatre modèles et en recommande un. Elle ne l'implémente pas : le choix engage
une règle métier — que devient une trace quand son auteur quitte l'entreprise —
et cette règle appartient au métier, pas à une migration.

## Les sept relations concernées

| Enfant → `users` | Obligatoire | `ON DELETE` |
| --- | --- | --- |
| `memberships.user_id` | oui | **CASCADE** |
| `projects.created_by` | non | NO ACTION |
| `price_book_versions.created_by` | non | NO ACTION |
| `import_batches.created_by` | non | NO ACTION |
| `estimates.created_by` | non | NO ACTION |
| `estimate_versions.created_by` | non | NO ACTION |
| `estimate_versions.frozen_by` | non | NO ACTION |

Lu dans `pg_catalog` sur une base migrée pour l'expérience et détruite après.

## Pourquoi ces relations ne sont pas les neuf autres

Les neuf relations déjà protégées lient deux ressources **possédées par une
organisation**. Une clé composite `(parent_id, organization_id)` y a un sens
immédiat : l'enfant et le parent appartiennent à la même entreprise.

`users` n'a pas d'`organization_id`, et n'en aura pas. Un utilisateur peut
appartenir à plusieurs organisations — c'est le rôle de `memberships`, qui porte
déjà `UNIQUE (user_id, organization_id)`. Exiger
`(created_by, organization_id) → users` serait faux par construction.

La question correcte est autre : **l'auteur d'une ligne devait-il être membre de
l'organisation de cette ligne au moment où il a écrit ?** C'est une question
d'appartenance datée, pas d'identité.

## Ce que fait le système aujourd'hui — mesuré

Deux comportements, tous deux vérifiés sur PostgreSQL 16 :

1. Un utilisateur qui a **créé quelque chose** ne peut pas être supprimé
   physiquement : les six `NO ACTION` refusent. La trace est donc déjà protégée,
   sans que personne l'ait décidé explicitement.
2. Un utilisateur qui **n'a rien créé** peut l'être — et le `CASCADE` de
   `memberships` **détruit alors toutes ses appartenances**, sans trace de quelles
   organisations il a rejointes ni quand.

Le seul chemin de suppression encore ouvert est donc exactement celui qui efface
l'historique d'appartenance. C'est ce que la règle métier proposée n° 2 interdit.

## Quatre modèles

### Modèle A — clé étrangère globale vers `users`

L'état actuel. `created_by → users.id`, sans notion d'organisation.

* **Audit** : l'identité de l'auteur est préservée, y compris après son départ.
* **RGPD** : l'identité reste liée à la trace ; un effacement demandé oblige à
  toucher `users` et se heurte aux `NO ACTION`.
* **Départ** : rien à faire ; la référence tient.
* **Réactivation** : sans objet.
* **Changement de rôle** : invisible — la trace ne dit pas avec quel rôle
  l'action a été faite.
* **Suppression logique** : à ajouter sur `users`, aujourd'hui absente.
* **Historique** : conservé, mais **ne prouve pas** que l'auteur appartenait à
  l'organisation de la ligne.
* **Migrations** : aucune.
* **Complexité** : nulle.

### Modèle B — clé composite vers `memberships`

`(created_by, organization_id) → memberships (user_id, organization_id)`.

* **Audit** : prouve l'appartenance au moment de l'écriture — mais seulement
  tant que l'appartenance existe.
* **RGPD** : identique à A.
* **Départ** : le point dur. Supprimer l'appartenance casserait toutes les
  traces ; la désactiver ne suffit pas, la clé ne regarde pas `is_active`.
  Il faudrait interdire toute suppression d'appartenance — une règle nouvelle,
  non écrite aujourd'hui.
* **Réactivation** : une appartenance recréée porterait un nouvel `id` ; les
  anciennes traces pointeraient une ligne disparue.
* **Changement de rôle** : non enregistré — `memberships.role` est l'état
  courant, pas celui du moment de l'action.
* **Suppression logique** : `is_active` existe déjà, mais la contrainte l'ignore.
* **Historique** : fragile, exactement là où on l'attend.
* **Migrations** : une unicité `(user_id, organization_id)` déjà présente, plus
  sept clés composites.
* **Complexité** : moyenne, et un piège opérationnel réel.

### Modèle C — identité globale plus instantané d'appartenance

`created_by → users` conservé, et deux colonnes ajoutées à la ligne :
l'organisation et le **rôle au moment de l'action**.

* **Audit** : le plus complet des quatre — qui, dans quelle organisation, avec
  quel rôle, sans dépendre d'un état qui bougera.
* **RGPD** : la donnée d'appartenance est dupliquée sur chaque ligne ; c'est le
  contraire de la minimisation, sauf à considérer le rôle comme une donnée de
  traçabilité nécessaire — ce qu'il est, pour un devis engageant.
* **Départ** : sans effet. L'instantané ne dépend d'aucune ligne vivante.
* **Réactivation** : sans effet.
* **Changement de rôle** : conservé, et c'est son intérêt principal.
* **Suppression logique** : indépendante.
* **Historique** : le mieux préservé.
* **Migrations** : deux colonnes sur six tables, remplissables sans décision —
  l'organisation est déjà sur la ligne ; le rôle passé, lui, est inconnu pour
  l'existant et resterait NULL.
* **Complexité** : élevée, et une duplication assumée.

### Modèle D — appartenance sans suppression physique

`memberships` devient un journal : aucune suppression, des dates de début et de
fin, et les clés pointent une appartenance historique.

* **Audit** : complet, et sans duplication — la période est portée une fois.
* **RGPD** : concentre l'appartenance en un endroit, ce qui **facilite** un
  effacement ciblé.
* **Départ** : une date de fin, rien de plus.
* **Réactivation** : une nouvelle période, l'ancienne intacte.
* **Changement de rôle** : une nouvelle période, si le rôle fait partie de la
  clé de période.
* **Suppression logique** : native.
* **Historique** : conservé par construction.
* **Migrations** : les plus lourdes — `CASCADE` de `memberships.user_id` à
  retirer, dates à ajouter, `UNIQUE (user_id, organization_id)` à remplacer par
  une contrainte de non-chevauchement, et les lignes existantes à dater — ce qui
  demande une décision sur leur date de début.
* **Complexité** : la plus élevée.

## Recommandation

**Modèle D pour `memberships`, modèle A conservé pour les six `created_by`.**

Le raisonnement tient en trois points.

Les six `created_by` n'ont pas de problème à résoudre : elles refusent déjà la
suppression de leur auteur, elles sont nullables, et rien ne les rend
exploitables. Leur ajouter une clé composite (modèle B) crée un piège au départ
d'un employé sans rien prouver de plus qu'un instantané. Les laisser telles
quelles est le choix le moins coûteux et le plus honnête.

Le vrai défaut est ailleurs, et il est mesuré : le `CASCADE` de
`memberships.user_id` est le seul chemin par lequel un historique d'appartenance
peut disparaître, et il le fait en silence. Le modèle D le ferme, et c'est
exactement ce que la règle métier proposée n° 2 demande.

Le modèle C reste ouvert si le métier veut tracer le **rôle** au moment de
l'action — un besoin réel pour un devis engageant, mais distinct de celui-ci.
Il pourra se poser sur D sans le contredire.

## Ce qui doit être décidé avant toute écriture

1. Le départ d'un utilisateur clôture-t-il son appartenance ou la supprime-t-il ?
   La règle proposée dit « clôture » ; elle n'est pas confirmée.
2. Pour les appartenances existantes, quelle date de début inscrire ? Le champ
   `created_at` de la ligne est disponible, mais rien ne dit qu'il correspond à
   l'entrée réelle dans l'entreprise.
3. Un effacement RGPD doit-il pouvoir supprimer une période d'appartenance,
   ou seulement anonymiser l'utilisateur ? Les deux se défendent, et ils ne
   mènent pas au même schéma.
4. Le rôle au moment de l'action doit-il être conservé (modèle C en plus) ?

Tant que le point 1 n'est pas tranché, aucune migration ne doit toucher
`memberships` : passer de `CASCADE` à autre chose change ce qui arrive aux
données d'une personne qui part, et ce n'est pas une décision technique.
