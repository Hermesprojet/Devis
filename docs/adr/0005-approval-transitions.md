# ADR 0005 — Transitions de statut d'une ligne de bordereau

- **Statut** : accepté
- **Date** : 2026-08-21
- **Contexte** : phase 1, à la suite d'une revue indépendante

## Problème

Le statut d'une ligne (`proposed`, `verified`, `approved`, `rejected`) était
modifiable par `PATCH /boq-items/{id}` comme n'importe quel champ. Un porteur
de `BOQ_WRITE`, refusé sur `/approve`, obtenait le même résultat en écrivant
`{"status": "approved"}` : la matrice route-permission restait verte alors que
le privilège s'obtenait par un champ.

Une fois ce chemin fermé, le raccourci `/approve` faisait encore passer une
ligne `proposed` par `verified` **en mémoire**, sans journaliser ce passage. Le
journal affirmait donc une vérification que personne n'avait effectuée.

## Décision

**Le statut ne change que par `POST /boq-items/{id}/transition`**, sous
`Permission.BOQ_APPROVE`, avec une machine d'états explicite :

| Depuis | Vers |
| --- | --- |
| `proposed` | `verified`, `approved`, `rejected` |
| `verified` | `approved`, `proposed`, `rejected` |
| `approved` | `verified`, `rejected` — motif obligatoire |
| `rejected` | `proposed` |

`proposed → approved` est **autorisé directement**. Approuver exige déjà
`BOQ_APPROVE` ; celui qui détient ce droit peut approuver sans étape
intermédiaire, et l'audit enregistre la transition réellement effectuée —
`from=proposed, to=approved`.

Nous avons écarté l'autre option — exiger deux actions distinctes, `proposed →
verified` puis `verified → approved` — parce qu'elle n'a de sens que si la
vérification est un contrôle humain **distinct de l'approbation**, confié à une
autre personne. Ce n'est pas le cas aujourd'hui : les deux relèvent de la même
permission. Imposer deux clics sans deux responsables ne produirait qu'une
formalité, et une formalité finit toujours par être expédiée.

Si la vérification devient un contrôle séparé — par exemple un métreur qui
vérifie et un responsable qui approuve — il faudra une permission
`BOQ_VERIFY` distincte, et cette ADR devra être révisée. La transition
`proposed → approved` serait alors retirée.

## Dérogation sur une quantité approuvée

Modifier la quantité ou l'unité d'une ligne `approved` exige `BOQ_APPROVE`,
**en plus** de `override_approved: true` et d'un motif. Une case à cocher n'est
pas une autorisation : sans ce contrôle, un porteur de `BOQ_WRITE` modifiait
une quantité approuvée en se déclarant lui-même autorisé à déroger, et la ligne
redescendait à `verified` — le verrou se contournait par son propre mécanisme
de dérogation.

L'événement porte son propre nom, `boq_item.approved_quantity_overridden`, avec
la quantité avant et après, l'unité, le motif et l'acteur. Le noyer dans
`boq_item.updated` l'aurait rendu introuvable, alors que c'est précisément
l'action qu'un auditeur cherche.

## Conséquences

- Une transition absente de la table est refusée (`409`), pas ignorée.
- Déclasser exige le même droit qu'approuver : sinon le verrou des quantités
  approuvées se rouvrirait en deux requêtes.
- `BoqItemUpdate` refuse tout champ inconnu. Sans cela, `{"status": …}`
  renvoyait `200` sans rien changer, et l'appelant croyait avoir réussi.
