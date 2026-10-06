# Faire atterrir la migration de fusion `f3a4b5c60708`

- **Écrit le** : 2026-10-06
- **Pourquoi ce document existe** : la migration de fusion n'existe aujourd'hui
  que sur `claude/candidat-complet`, la branche de la **#90**, qui est un
  brouillon destiné à ne jamais être fusionné. Si elle y reste, elle disparaît
  avec le brouillon, et `main` se retrouve à deux têtes sans que personne
  n'ait rien fait de mal.
- **Ce qu'il n'est pas** : une procédure de déploiement. Voir
  `docs/EXPLOITATION.md` pour cela.

---

## 1. L'état, relevé et non supposé

`main` porte **17 migrations**, et sa tête est `c7d8e9fa0102`.

Quatre migrations n'y sont pas, et deux branches partent du même point :

```
                                  c7d8e9fa0102  (tête de main)
                                   │
                 ┌─────────────────┴─────────────────┐
                 │                                   │
        d1e8…  d1e2f3a40506                  d8e9fa010203
        réauthentification                   citation de plan
        #81  claude/codes-de-connexion       #78  claude/parcours-plan-essayable
                 │                                   │
                 │                            e2f3a4b50607
                 │                            calibration d'un PDF
                 │                            #89  claude/mesures-pdf
                 │                                   │
                 └─────────────────┬─────────────────┘
                                   │
                            f3a4b5c60708
                            migration de FUSION
                            #90  claude/candidat-complet  ← brouillon
```

| Révision | Ce qu'elle fait | Seules branches qui la portent |
| --- | --- | --- |
| `d1e2f3a40506` | ajoute `login_transactions.reauthentication_requested` | `codes-de-connexion`, `candidat-complet` |
| `d8e9fa010203` | citations de plan et étapes de plan | `parcours-plan-essayable` et tout ce qui est au-dessus |
| `e2f3a4b50607` | crée `plan_calibrations`, élargit l'ancrage d'une citation | `mesures-pdf`, `candidat-complet` |
| `f3a4b5c60708` | **ne fait rien** : elle réunit les deux têtes | **`candidat-complet` seulement** |

La fusion est vide à dessein : ses deux parents touchent des tables disjointes.
Le jour où deux tranches toucheraient la même colonne, une fusion vide serait un
mensonge, et c'est là qu'il faudrait écrire la réconciliation.

---

## 2. Ce qui est déjà protégé, et ce qui ne l'est pas

**Deux têtes ne passent pas inaperçues.** `conftest.alembic_head()` et
`test_referential_action_drift.py` lisent la chaîne et affirment
`len(heads) == 1`. Vérifié sur un arbre à deux têtes : **6 échecs et 1 erreur**
dans la seule suite ordinaire, sans PostgreSQL. `scripts/schema_drift_gate.py`
les refuse également, et la CI tourne sur `push` de **toute** branche.

Le risque n'est donc pas qu'une `main` à deux têtes passe inaperçue. Le risque
est qu'elle **bloque tout** : `main` rouge, et chaque demande de fusion ouverte
rouge avec elle, jusqu'à ce que la fusion arrive.

Ce qui n'est pas protégé : rien n'empêche aujourd'hui une migration de fusion de
rester dans un brouillon. Un contrôle le pourrait difficilement — il devrait
connaître l'intention de fusionner. C'est ce document qui tient ce rôle.

---

## 3. Les trois façons dont on pourrait croire s'en sortir, et pourquoi elles échouent

Chacune a été essayée, pas raisonnée.

| Idée | Ce qui se passe réellement |
| --- | --- |
| Une PR « migrations seules » vers `main`, portant les trois fichiers | `KeyError: 'd8e9fa010203'` — `e2f3a4b50607` descend d'une migration qui est elle-même sur une autre branche. Et même en l'ajoutant, la porte de dérive refuserait : la migration créerait `plan_calibrations` sans que `models.py` la déclare, donc Alembic proposerait un `DROP`. **Une migration ne voyage pas sans son modèle.** |
| Mettre la fusion dans la #89 telle quelle | `KeyError: 'd1e2f3a40506'` — l'arbre de la #89 ne contient pas la parente de la connexion. |
| Mettre la fusion dans la #81 telle quelle | `KeyError: 'e2f3a4b50607'` — symétriquement. |

**Conclusion :** la migration de fusion ne peut vivre dans aucune demande de
fusion tant que l'une des deux chaînes n'est pas sur `main`.

---

## 4. L'ordre qui marche, et il n'y en a qu'un de propre

> **La pile des plans d'abord, la connexion ensuite, et c'est la #81 qui apporte
> la fusion.**

Le point qu'il faut avoir en tête : **la bifurcation naît dès que `d8e9fa010203`
et `d1e2f3a40506` sont toutes deux sur `main`** — c'est-à-dire dès la #78 et la
#81, et non à la #89 comme on pourrait le croire.

### Étape par étape

| # | Action | Tête de `main` ensuite |
| --- | --- | --- |
| 1 | fusionner **#77** `claude/lecture-de-plans` | `c7d8e9fa0102` (inchangée) |
| 2 | fusionner **#78** `claude/parcours-plan-essayable` | `d8e9fa010203` |
| 3 | fusionner **#80**, **#85**, **#87** | inchangée |
| 4 | fusionner **#89** `claude/mesures-pdf` | `e2f3a4b50607` |
| 5 | fusionner `main` dans `codex/login-account-choice`, puis dans `claude/codes-de-connexion` | — |
| 6 | **déplacer `20261006_0009_fusion_des_deux_tetes.py`** depuis `claude/candidat-complet` vers `claude/codes-de-connexion`, et l'y committer | — |
| 7 | fusionner **#73** `codex/login-account-choice` | inchangée |
| 8 | fusionner **#81** `claude/codes-de-connexion` | `f3a4b5c60708` |

**Une seule tête à chaque étape.** `main` n'est jamais rouge.

À l'étape 6, la #81 contient alors `d1e2f3a40506` (la sienne), `e2f3a4b50607`
(venue de `main`) et la fusion : sa propre CI est donc verte, et elle devient
exactement ce qu'il faut — **une demande de fusion destinée à être fusionnée,
portant la migration de fusion et ses deux parentes**.

### L'ordre inverse coûte cher

Si la connexion passe en premier, `main` est à deux têtes **de l'étape #78
jusqu'à l'étape #89**, soit quatre fusions durant lesquelles `main` et toutes
les demandes ouvertes sont rouges. C'est jouable et c'est laid ; ce n'est pas à
faire sans raison.

### Preuve

L'enchaînement a été simulé sur un arbre réel — `main`, puis `mesures-pdf`, puis
`login-account-choice` et `codes-de-connexion`, puis la fusion :

```
après la pile des plans          : e2f3a4b50607 (head)
après la connexion, sans fusion  : d1e2f3a40506 (head)
                                   e2f3a4b50607 (head)      ← deux têtes
après ajout de la fusion         : f3a4b5c60708 (head)
```

et, sur un PostgreSQL 16 + PostGIS réel :

```
porte franchie : une tête, montée propre, aucune opération proposée.
aller-retour des migrations valide — 36 tables.
```

Aucun conflit de fusion n'est apparu entre les deux chaînes.

---

## 5. L'alternative, et pourquoi elle n'est pas retenue

On pourrait **supprimer la fusion** et re-chaîner `d1e2f3a40506` derrière
`e2f3a4b50607`. Une seule tête, toujours, sans fichier supplémentaire.

Le prix est précisément celui que le commentaire de
`20261002_0008_calibration_d_un_plan_pdf.py` refuse de payer : la branche placée
en second devient **immigrable à elle seule** — sa CI tomberait sur une révision
absente de son arbre — et l'ordre de fusion cesse d'être libre alors qu'aucune
contrainte de schéma ne le justifie. La calibration d'un plan ne doit rien à
`max_age`.

La fusion coûte un fichier vide ; le re-chaînage coûte une dépendance qui n'existe
pas. On garde la fusion.

---

## 6. Si l'ordre a déjà été inversé par accident

`main` est à deux têtes, rouge. Le retour à une tête unique est une demande de
fusion ordinaire contenant le seul fichier
`20261006_0009_fusion_des_deux_tetes.py`, **à condition que les deux parentes
soient déjà sur `main`** — ce qui est le cas dans cette situation, puisque c'est
elle qui l'a créée. Sa CI sera verte, et elle referme la bifurcation.

Aucune base déjà migrée n'est à reprendre : une migration de fusion vide ne
change aucune table. `alembic upgrade head` la marque appliquée, et c'est tout.
