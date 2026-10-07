# Faire atterrir la migration de fusion `f3a4b5c60708`

> **Mise à jour du 7 octobre 2026 — le second parent a changé.** La tranche des
> plans a reçu une révision de plus, `a4b5c6d70809` (une purge autorisée peut
> détruire une révision publiée), qui descend de `e2f3a4b50607`. Celle-ci a donc
> cessé d'être une tête, et la migration de fusion cite désormais
> `("d1e2f3a40506", "a4b5c6d70809")`. Continuer à citer `e2f3a4b50607` aurait
> laissé `a4b5c6d70809` à part, et la base serait repartie à **deux têtes** —
> exactement ce que cette migration existe pour empêcher. **On cite la tête de
> la tranche, pas un de ses maillons**, et c'est la règle à retenir si la
> tranche en reçoit une autre.
>
> Le fichier corrigé vit sur `claude/candidat-corrige`. Le reste du document
> vaut tel quel : seul le nom du second parent change.

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
| `a4b5c6d70809` | **la tête de la tranche** : une purge autorisée peut détruire une révision publiée | `corrections-du-candidat`, `candidat-corrige` |
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
| 4 | fusionner **#89** `claude/mesures-pdf` | `a4b5c6d70809` |
| 5 | fusionner `main` dans `codex/login-account-choice`, puis dans `claude/codes-de-connexion` | — |
| 6 | **déplacer `20261006_0009_fusion_des_deux_tetes.py`** depuis `claude/candidat-complet` vers `claude/codes-de-connexion`, et l'y committer | — |
| 7 | fusionner **#73** `codex/login-account-choice` | inchangée |
| 8 | fusionner **#81** `claude/codes-de-connexion` | `f3a4b5c60708` |

**Une seule tête à chaque étape.** `main` n'est jamais rouge.

À l'étape 6, la #81 contient alors `d1e2f3a40506` (la sienne), `a4b5c6d70809`
(venue de `main`) et la fusion : sa propre CI est donc verte, et elle devient
exactement ce qu'il faut — **une demande de fusion destinée à être fusionnée,
portant la migration de fusion et ses deux parentes**.

### Les commandes de l'étape 6 — le transfert effectif

Relevé le 7 octobre 2026 : `20261006_0009_fusion_des_deux_tetes.py` existe
**sur `claude/candidat-complet` et nulle part ailleurs** — ni sur `main`, ni
sur `pr/73`, ni sur `pr/81`, ni sur `pr/89` (vérifié par `git cat-file -e` sur
les cinq références). La #90 étant un brouillon qui ne sera pas fusionné, ce
fichier n'atteindra jamais `main` tant que ce transfert n'a pas eu lieu.

À jouer **après l'étape 4** — c'est-à-dire une fois la #89 fusionnée et
`main` à `a4b5c6d70809` — et **avant l'étape 7** :

```bash
git fetch origin main codex/login-account-choice \
    claude/codes-de-connexion claude/candidat-complet

# 5a — remonter main dans la base de la #81
git checkout -B codex/login-account-choice origin/codex/login-account-choice
git merge --no-ff origin/main -m "Fusion de 'main' dans la base du choix de compte"
git push -u origin codex/login-account-choice

# 5b — puis dans la #81 elle-même
git checkout -B claude/codes-de-connexion origin/claude/codes-de-connexion
git merge --no-ff codex/login-account-choice \
    -m "Fusion de la base dans les codes de connexion"

# 6 — LE TRANSFERT : le fichier quitte le brouillon pour une PR fusionnable
git checkout origin/claude/candidat-complet -- \
    apps/api/alembic/versions/20261006_0009_fusion_des_deux_tetes.py
git add apps/api/alembic/versions/20261006_0009_fusion_des_deux_tetes.py
git commit -m "Les deux têtes se rejoignent sur la branche qui sera fusionnée"
```

**Le contrôle qui décide si le transfert a réussi**, à faire AVANT de pousser :

```bash
cd apps/api && PYTHONPATH=src python -m alembic heads
```

- **une seule ligne, `f3a4b5c60708 (head)`** → le transfert est bon, poussez ;
- **deux lignes** → `main` n'a pas encore été remontée dans la branche :
  reprenez à 5b ;
- **`KeyError: 'a4b5c6d70809'`** → l'étape 4 n'a pas eu lieu, ou la remontée de
  `main` a été sautée. Ne poussez pas : la CI de la #81 tomberait, et le
  diagnostic serait plus coûteux là-bas qu'ici.

```bash
git push -u origin claude/codes-de-connexion
```

La #81 porte alors `d1e2f3a40506` (la sienne), `e2f3a4b50607` (venue de `main`)
et la fusion. Ce qu'il faut vérifier sur sa page avant de la fusionner : sa
description doit annoncer la migration de fusion, et ses ateliers doivent être
verts **après** ce commit, pas avant.

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
