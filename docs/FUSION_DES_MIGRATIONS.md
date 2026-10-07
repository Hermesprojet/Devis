# Le graphe des migrations tient sur une seule tête

- **Écrit le** : 2026-10-06, pour organiser l'atterrissage de la migration de
  fusion `f3a4b5c60708` sur `main`. **Refait le** : 2026-10-07, quand cet
  atterrissage a cessé d'être à faire.
- **Ce que ce document dit** : où est la migration de fusion aujourd'hui,
  pourquoi elle existe, ce qui détecte deux têtes dans ce dépôt, et ce qu'il ne
  faut pas faire au graphe.
- **Ce qu'il n'est pas** : une voie de livraison. Il n'y en a qu'une, et elle
  est dans `docs/FICHE_DE_DECISION_MISE_EN_LIGNE.md`. Le détail d'exploitation
  est dans `docs/EXPLOITATION.md`.

> **La séquence des onze fusions de demandes empilées est retirée de ce
> document, et pas parce qu'elle avait vieilli.** Elle était inexécutable : la
> migration de fusion cite `b5c6d7e8090a`, et aucune des onze étapes n'amenait
> cette révision sur `main`. Jouée telle qu'elle était écrite, elle rendait
> `KeyError: 'b5c6d7e8090a'`. Les étapes, leurs commandes de transfert de
> fichier et leur « preuve » ont disparu d'ici : il n'y a rien à y récupérer, et
> corriger le tableau aurait été lui redonner l'air d'une procédure.

---

## 1. Où est la migration de fusion, et pourquoi elle n'a plus à atterrir

Ce document existait parce que
`apps/api/alembic/versions/20261006_0009_fusion_des_deux_tetes.py` ne vivait que
sur `claude/candidat-complet`, la branche du brouillon #90 : il disparaissait
avec le brouillon, et `main` repartait à deux têtes sans que personne n'ait rien
fait de mal.

Relevé sur `2d18299ee08b2a66bb54573d665f233a72aaf6ee`, par `git cat-file -e` sur
chaque référence :

| Référence | Le fichier de fusion y est-il ? |
| --- | --- |
| `claude/candidat-integre` — la branche de la demande de livraison | **oui** |
| `claude/candidat-complet` — le brouillon #90 | oui |
| `main` | non, et c'est normal : la livraison ne l'a pas encore apportée |

Et `claude/candidat-complet` (`daeb7f0`) est **ancêtre** du candidat — vérifié
par `git merge-base --is-ancestor`, comme `claude/candidat-corrige` (`483176a`)
et `claude/corrections-du-candidat` (`0618d27`). Le fichier est donc dans la
branche que la demande de livraison fusionne. **Aucun transfert de fichier
d'une branche à une autre n'est à faire.**

> « Ne pas fusionner la #90 » porte sur la demande de fusion, jamais sur son
> contenu. Le contenu est dans le candidat.

`claude/integration-cinq-pr` (le brouillon #84) n'est **pas** ancêtre : celui-là
est réellement hors du candidat.

### Le graphe, lu dans les fichiers

Vingt-trois fichiers de migration, **une seule tête**. Le point de branchement
est `c7d8e9fa0102` — le schéma de `39ad8d0`, ce qui tourne en préproduction.
Six révisions sont au-dessus :

```
                    c7d8e9fa0102   ← le schéma de 39ad8d0, ce qui tourne
                         │
        ┌────────────────┴────────────────┐
        │                                 │
  d1e2f3a40506                      d8e9fa010203
  tranche de la connexion                 │
  (seule de sa branche,              e2f3a4b50607
   donc elle est sa tête)                 │
        │                            a4b5c6d70809
        │                                 │
        │                            b5c6d7e8090a   ← tête de la tranche
        └────────────────┬────────────────┘             des plans
                         │
                   f3a4b5c60708   ← tête unique du graphe
                   migration de FUSION, vide
```

| Révision | Ce qu'elle fait |
| --- | --- |
| `d1e2f3a40506` | ajoute `login_transactions.reauthentication_requested` |
| `d8e9fa010203` | citations de plan et étapes de plan |
| `e2f3a4b50607` | crée `plan_calibrations`, élargit l'ancrage d'une citation au cas page + boîte |
| `a4b5c6d70809` | une purge autorisée peut détruire une révision publiée |
| `b5c6d7e8090a` | **la tête de la tranche des plans** : une ligne de bordereau dit de quelle mesure de plan elle vient (deux colonnes nullables, purement additive) |
| `f3a4b5c60708` | **ne fait rien** : elle réunit les deux têtes. `down_revision = ("d1e2f3a40506", "b5c6d7e8090a")` |

### Pourquoi elle existe, et pourquoi elle est vide

Deux tranches ont avancé en parallèle et aucune ne doit rien à l'autre. Elles
arrivent donc avec deux têtes, ce qui laisse l'ordre d'application indéterminé :
deux déploiements pourraient appliquer le même schéma dans deux ordres
différents. La réponse prévue par Alembic pour ce cas est une migration de
fusion — elle déclare les deux parents, ne touche à rien, et rend l'ordre
déterminé.

Elle n'a ni `upgrade` ni `downgrade`, et c'est une propriété, pas un oubli : les
deux tranches modifient des tables disjointes — `login_transactions` d'un côté,
`plan_calibrations` et `source_citations` de l'autre. Leur rencontre ne demande
aucune réconciliation.

> Le jour où deux tranches toucheraient la même colonne, une fusion vide serait
> un mensonge, et c'est dans ce fichier qu'il faudrait écrire la
> réconciliation.

---

## 2. Ce qui est déjà protégé, et ce qui ne l'est pas

Les quatre contrôles nommés ici ont été relus dans le code le 7 octobre 2026 :
ils existent tous, et voici dans quel atelier ils tournent.

| Contrôle | Ce qu'il affirme | Atelier |
| --- | --- | --- |
| `apps/api/tests/conftest.py`, fonction `alembic_head()` | `assert len(heads) == 1`, message « la chaîne des migrations a N têtes » | « API (SQLite, sans service) » et « API (PostgreSQL + PostGIS) », par `pytest -q` |
| `apps/api/tests/test_referential_action_drift.py` | `assert len(tetes) == 1`, message « la chaîne a N têtes » | les deux mêmes |
| `scripts/schema_drift_gate.py` | refuse et **sort en 3** : « la chaîne des migrations a N têtes » | « API (PostgreSQL + PostGIS) », étape « Le schéma migré correspond aux modèles » |
| `scripts/epreuve_montee_depuis_preproduction.py` | « ÉCHEC — N tête(s) » après avoir monté une base portant le schéma en service | « API (PostgreSQL + PostGIS) », étape « Montée depuis le schéma en service » |

`alembic_head()` ne sert pas qu'aux tests qui la citent : `schema_fingerprint()`
l'appelle pour décider si le gabarit SQLite est encore valide. Deux têtes font
donc échouer la **préparation** de tout test qui a besoin d'une base, et non
seulement les assertions sur la chaîne.

La CI tourne sur `push` de **toute** branche : `.github/workflows/ci.yml` porte
`push: branches: ["**"]`, puis `pull_request:`.

### Mesuré, et non plus hérité

Même machine, même commande — `pytest -q` dans `apps/api` —, le 7 octobre 2026.
L'arbre à deux têtes a été obtenu en faisant citer à la migration de fusion
`a4b5c6d70809`, c'est-à-dire **le maillon qui précède la tête**, dans un arbre
de travail jetable :

| Arbre | Résultat |
| --- | --- |
| intact, `("d1e2f3a40506", "b5c6d7e8090a")` | **1714 passés, 60 ignorés** (6 min 33) |
| à deux têtes, `("d1e2f3a40506", "a4b5c6d70809")` | **20 échecs, 930 erreurs**, 767 passés, 57 ignorés (4 min 24) |

> **La version précédente de cette section annonçait « 6 échecs et 1 erreur ».
> Le chiffre était faux**, et on voit d'où il venait : le message
> « la chaîne des migrations a 2 têtes » apparaît exactement 6 fois dans la
> sortie. Ce sont des occurrences du message, pas des tests. Le compte réel est
> ci-dessus.

Les 930 erreurs sont le gabarit : elles se produisent au montage de la base de
test, avant que le test lui-même ne commence. Les 20 échecs sont les assertions
sur la chaîne, dans `apps/api/tests/test_platform.py` et
`apps/api/tests/test_referential_action_drift.py`.

Le risque n'est donc pas qu'une `main` à deux têtes passe inaperçue. Le risque
est qu'elle **bloque tout** : `main` rouge, et chaque demande de fusion ouverte
rouge avec elle, jusqu'à ce que la fusion arrive.

### Ce qui n'est pas protégé

**Le geste lui-même est silencieux.** Mesuré sur l'arbre à deux têtes :

```
$ alembic heads
f3a4b5c60708 (head)
b5c6d7e8090a (head)
code de sortie : 0
```

Deux lignes, et **sortie 0**. Rien ne refuse, rien n'avertit : c'est à la
personne de compter les lignes. Le refus dur n'arrive qu'à l'emploi —
`alembic upgrade head` sort en **255** sur « Multiple head revisions are present
for given argument 'head' ».

Reste hors de portée d'un contrôle : l'intention. Aucun atelier ne sait qu'une
migration de fusion *devait* accompagner une tranche, ni laquelle. C'est ce
document qui tient ce rôle.

---

## 3. La règle de la tête — une fusion cite la tête de chaque tranche, jamais un de ses maillons

Cette règle a été enfreinte **deux fois** dans ce dépôt, les deux fois par la
même mécanique : la tranche des plans a reçu une révision de plus, et le second
parent de la fusion a cessé d'être une tête sans que le fichier de fusion ne
change. `e2f3a4b50607` d'abord, `a4b5c6d70809` ensuite.

**Ce qui se passe quand on cite un maillon.** La fusion se raccroche au milieu
de la tranche. Ce qui est au-dessus du maillon reste à part, et le graphe
repart à **deux têtes** — précisément ce que cette migration existe pour
empêcher. Rien ne proteste au moment de l'écriture : le fichier est valide,
`alembic heads` sort en 0, et le défaut ne se voit qu'en lisant ses deux lignes.

**Ce qui le rattrape.** Les quatre contrôles de la section « Ce qui est déjà
protégé, et ce qui ne l'est pas », dès le `push`. Parmi eux,
`scripts/epreuve_montee_depuis_preproduction.py` est le seul à jouer le geste du
déploiement — appliquer les migrations du candidat à une base qui porte le
schéma en service — et donc le seul à tomber pour cette raison-là et non pour
une autre.

**Ce qu'il faut faire.** À chaque révision ajoutée à une tranche, relire
`down_revision` dans le fichier de fusion et le décaler sur la nouvelle tête.
Le contrôle est `alembic heads`, et il faut **lire sa sortie** : une seule
ligne, `f3a4b5c60708 (head)`.

---

## 4. L'alternative, et pourquoi elle n'est pas retenue

On pourrait **supprimer la fusion** et re-chaîner `d1e2f3a40506` derrière la
tête de la tranche des plans. Une seule tête, toujours, sans fichier
supplémentaire.

Le prix est précisément celui que le commentaire de
`apps/api/alembic/versions/20261002_0008_calibration_d_un_plan_pdf.py` refuse de
payer : la branche placée en second devient **immigrable à elle seule** — sa CI
tomberait sur une révision absente de son arbre — et l'ordre de fusion cesse
d'être libre alors qu'aucune contrainte de schéma ne le justifie. La calibration
d'un plan ne doit rien à `max_age`.

La fusion coûte un fichier vide ; le re-chaînage coûte une dépendance qui
n'existe pas. On garde la fusion.

> C'est la réponse à la prochaine personne tentée de supprimer
> `apps/api/alembic/versions/20261006_0009_fusion_des_deux_tetes.py` parce qu'il
> « ne fait rien ».

---

## 5. Si `main` se retrouve à deux têtes

Le cas couvert ici : deux têtes sont arrivées sur `main`, qui est rouge, et
chaque demande de fusion ouverte est rouge avec elle. Peu importe comment —
une tranche fusionnée sans sa fusion, un ordre inversé par accident, une fusion
citant un maillon.

Le retour à une tête unique est une demande de fusion ordinaire contenant le
seul fichier
`apps/api/alembic/versions/20261006_0009_fusion_des_deux_tetes.py`, **à
condition que les deux parentes soient déjà sur `main`** — ce qui est le cas
dans cette situation, puisque c'est elle qui l'a créée. Sa CI sera verte, et
elle referme la bifurcation.

Aucune base déjà migrée n'est à reprendre : une migration de fusion vide ne
change aucune table. `alembic upgrade head` la marque appliquée, et c'est tout.

---

## 6. Ce qu'il ne faut pas faire au graphe

| À ne pas faire | Pourquoi |
| --- | --- |
| Rejouer la séquence des onze fusions de demandes empilées | Elle est inexécutable, et elle n'est plus écrite nulle part. La voie est dans `docs/FICHE_DE_DECISION_MISE_EN_LIGNE.md` |
| Supprimer le fichier de fusion parce qu'il est vide | Voir « L'alternative, et pourquoi elle n'est pas retenue » |
| Faire citer à la fusion un maillon de tranche | Deux têtes, silencieusement. Voir « La règle de la tête — une fusion cite la tête de chaque tranche, jamais un de ses maillons » |
| Transférer le fichier de fusion d'une branche à une autre | Il est déjà dans la branche que la livraison fusionne. Voir « Où est la migration de fusion, et pourquoi elle n'a plus à atterrir » |
| Renvoyer à un autre document par un numéro de ligne | Ces renvois étaient déjà faux dans les deux sens. On nomme le **titre de section** |
