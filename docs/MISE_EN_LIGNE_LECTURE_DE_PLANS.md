# Revue, fusion, déploiement : le chemin de cette tranche

- **Écrit le** : 2026-10-06, depuis la branche `claude/mesures-pdf`
  (`8a173783a3352a9067ddd7052f2d140b80b49b52`).
- **Dernières observations de la machine** : **16 septembre 2026**. Tout ce que
  ce document dit de l'état du serveur date de ce jour-là. Trois semaines ont
  passé ; la préproduction a pu bouger sans que rien ici ne le sache.
- **Rien de ce document n'a été exécuté, et rien ne doit l'être sans votre
  accord.** Aucune écriture dans le dépôt, aucun `git` ni `gh` en écriture,
  aucune image construite ni publiée, aucun contact avec le VPS. Les commandes
  sont écrites pour être relues par vous avant d'être tapées.
- **Ce qu'il n'est pas** : une procédure d'exploitation courante. Voir
  `docs/EXPLOITATION.md` — dont ce document dit, section 1, ce qui est devenu
  faux.

Tout chiffre ci-dessous porte son fichier, et son **symbole** — constante,
fonction, bloc — quand j'ai rouvert le fichier pour le vérifier. Les numéros de
ligne ont été remplacés par des noms partout où ils désignaient du code : un
numéro se périme au commit suivant, un nom non. Ce qui n'a pas été mesuré est
écrit « non mesuré » ; ce qui n'a pas été exécuté sur la machine, « non vérifié
sur cette pile ».

---

## 1. Le point qui domine tout le reste, et qu'il faut lire avant la première commande

> Le retour arrière par image, tel que `docs/EXPLOITATION.md` le décrit
> aujourd'hui, **ne remet pas l'API en service** après cette tranche. Et dès la
> première mesure enregistrée, le schéma ne peut plus redescendre. La seule
> sortie est alors **la restauration de la sauvegarde prise avant le
> déploiement** — ce qui fait de cette sauvegarde non pas une précaution, mais
> la condition du déploiement.

### Pourquoi, mécaniquement

`infra/docker-compose.staging.yml` fait dépendre `api` de `migrate` par une
condition d'achèvement :

```
    depends_on:
      db:
        condition: service_healthy
      migrate:
        condition: service_completed_successfully
```

(`infra/docker-compose.staging.yml`, bloc `depends_on` du service `api` ; et
`migrate` porte `restart: "no"`.)

`migrate` lance `alembic -c apps/api/alembic.ini upgrade head` **avec l'image
configurée** (`${API_IMAGE:?…}`, dans la définition du service `migrate`). Si
l'on remet l'ancienne image, c'est l'ancien arbre de migrations qui s'exécute
contre une base déjà montée plus haut.

Éprouvé, sur une base PostgreSQL migrée en `f3a4b5c60708` puis `alembic upgrade
head` depuis l'arbre de `main` :

```
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
ERROR [alembic.util.messaging] Can't locate revision identified by 'f3a4b5c60708'
FAILED: Can't locate revision identified by 'f3a4b5c60708'
```

Code de sortie **255**. Donc `migrate` ne s'achève pas en succès, donc la
condition d'`api` n'est jamais satisfaite, donc **l'API ne démarre jamais**.

Ce n'est pas une hypothèse sur l'état de `main` : la tête de `main` est
aujourd'hui `39ad8d0721e18d539064883c70881f40c8a32c00`, c'est le commit que la
préproduction sert, et son arbre porte **17 migrations** s'arrêtant à
`c7d8e9fa0102` (`git ls-tree -r 39ad8d0 -- apps/api/alembic/versions/`). Aucune
des quatre révisions de l'ensemble de la fusion n'y figure.

**Deux ensembles, qu'il faut distinguer, sans quoi la suite de ce document se
lit comme une contradiction.** L'**ensemble de la fusion** compte **quatre**
révisions — `d8e9fa010203`, `e2f3a4b50607`, `a4b5c6d70809`, `d1e2f3a40506`,
`f3a4b5c60708` —,
réparties sur trois branches. La **branche de travail `claude/mesures-pdf`**,
elle, n'en porte que **deux** : `d8e9fa010203`
(`20261002_0007_citation_de_plan_et_etapes_de_plan.py`, désormais `…0007…`,
apportée par la #78) et `e2f3a4b50607`
(`20261002_0008_calibration_d_un_plan_pdf.py`, désormais `…0008…`, apportée par
la #89). `d1e2f3a40506` vit sur `claude/codes-de-connexion`
(`20260901_0007_reauthentification_demandee.py`) et `f3a4b5c60708` seulement sur
`claude/candidat-complet` (vérifié : `git ls-tree -r --name-only <branche> --
apps/api/alembic/versions/` donne 17 fichiers sur `main`, 18 sur
`claude/codes-de-connexion`, 19 sur `claude/mesures-pdf`, 21 sur
`claude/candidat-complet`). Partout ci-dessous, « les deux migrations de la
tranche » désigne les deux de cette branche ; « les quatre révisions »,
l'ensemble de la fusion.

**Le code applicatif ancien, lui, tournerait très bien sur le schéma avancé** —
mais il faut dire exactement ce que les deux migrations de cette branche font,
parce qu'une version précédente de ce paragraphe parlait de colonnes ajoutées
qui n'existent pas. Inventaire relu dans les deux fichiers (`grep -n
'add_column'` ne rend rien, ni dans l'un ni dans l'autre) :

- **une table ajoutée**, et une seule : `plan_calibrations` — le seul
  `op.create_table` des deux fichiers, dans l'`upgrade()` de `…0008…` ;
- **aucune colonne ajoutée.** Ce qui bouge sur les colonnes est l'inverse d'un
  ajout : **sept colonnes existantes deviennent nullables** (`…0007…`, tuple
  `COLONNES_DE_TEXTE` — `page`, `char_start`, `char_end`, `x0`, `y0`, `x1`,
  `y1` — et la boucle `lot.alter_column(…, nullable=True)` de son `upgrade()`) ;
- **trois contraintes `CHECK` NOUVELLES** sur `source_citations`, posées par
  `…0007…` (tuple `CONTRAINTES_CITATION`) : `ck_source_citation_ancrage`,
  `ck_source_citation_bbox_complete`, `ck_source_citation_reperes_cao_nonempty`.
  **Ce sont les seules qui pourraient refuser une écriture de l'ancien code**,
  donc exactement celles qu'il fallait nommer pour établir la conclusion de ce
  paragraphe ;
- **deux relâchements sémantiques** : `ck_document_step_run_step` passe de onze
  à quinze étapes (`…0007…`, tuple `ETAPES`), et `ck_source_citation_ancrage`
  passe de `ANCRAGE_AVANT` à `ANCRAGE_APRES` (`…0008…`) ;
- **trois réécritures par `CAST`**, qui ne changent pas ce qui est permis mais
  le rendent vrai sur les deux moteurs : `ck_source_citation_bbox`,
  `ck_source_citation_confidence`, `ck_extraction_proposal_confidence`
  (`…0007…`, `CONDITION_BBOX_APRES` et `CONDITION_CONFIANCE_APRES`).

La conclusion se vérifie sur les trois contraintes neuves, et c'est là qu'il
fallait la vérifier : l'ancien code écrivait `page`, `char_start`, `char_end` et
`x0`..`y1` tous non nuls, ce qui les satisfait toutes les trois. Rien n'est
retiré, rien n'est renommé, aucun `NOT NULL` n'est posé à la montée. Le blocage
n'est donc pas un blocage de compatibilité : c'est **le service `migrate` seul**
qui empêche le retour arrière.

### Ce qui, dans `docs/EXPLOITATION.md`, est désormais faux

Quatre passages, cités tels qu'ils sont sur cette branche.

**(a) `:437` — la phrase qui fonde toute la section « Retour arrière » :**

> « son SHA de commit. Il ne touche pas à la base. »

Vrai sur la base, faux sur le service : le retour arrière ne touche pas à la
base, et c'est précisément pour cela qu'il échoue — `migrate` retrouve une base
en avance sur son arbre.

**(b) `:456` puis `:459-461` — le cas qui dit « rien d'autre à faire » :**

> `:456` « **Les migrations.** Le service `migrate` a déjà tourné, et le schéma est en »
> `:459` « 1. **La migration n'ajoute que des colonnes ou des tables** — le cas de toutes »
> `:461` « suffit, rien d'autre à faire. »

C'est le passage le plus dangereux du document, parce qu'il décrit exactement
notre cas — des migrations additives — et conclut qu'il n'y a rien à faire.
La prémisse est juste, la conclusion est fausse : l'ancienne image *ignore* bien
les colonnes nouvelles, mais elle ne démarre pas, parce que `migrate` tourne
avant elle et échoue. **Aucune des 43 demandes de fusion ouvertes ne corrige ce
passage** : la #83 réécrit la section « Retour arrière » autour, y ajoute un
encadré sur l'absence de cible et une sous-section « Les versions à conserver »,
et laisse ces trois lignes intactes (diff de `docs/EXPLOITATION.md` dans la
#83).

**(c) `:493` — la justification du plafond mémoire :**

> « | `api` | 2.0 | 1 Go | calcul déterministe, pas de traitement d'image | »

L'API rend désormais des images : aperçus de page et tuiles de détail. La
justification est tombée avec la tranche. Aucune PR ouverte ne la corrige.

**(d) `:264` — ce que l'application ne fait pas :**

> « antivirus, aucune OCR, aucune extraction, aucun rendu à l'écran. Un document se »

La tranche affiche et mesure des PDF à l'écran. Aucune PR ouverte ne corrige ce
paragraphe.

Deux faux de moindre portée, et eux sont corrigés par la #83 : `:94`
(`API_IMAGE=ghcr.io/<compte>/metreo-api:<sha-git>`, un nom d'image qui n'existe
pas — voir section 4) et `:399`, `:416`, `:441` (commandes Compose sans
`--env-file`, qui échouent telles qu'écrites).

Un dernier, découvert en comparant deux fichiers — **et celui-là est corrigé,
par la #72** : `:171` affirme qu'une requête portant un mauvais `Host`
« reçoit `404` ». Le script de cette même PR dit l'inverse, avec sa raison : « À
un Host qu'il ne sert pas, Caddy répond un 200 VIDE (son gestionnaire par
défaut), pas un 404 : le code seul ne prouve rien » (`ops/verifier_deploiement.sh`,
commentaire de la fonction `servi_par_caddy` — fichier qui **n'existe pas sur la
branche de travail** et vient de la #72, branche `claude/preprod-fiabilisation` ;
pour le lire : `git show
origin/claude/preprod-fiabilisation:ops/verifier_deploiement.sh`).

**La contradiction est déjà tranchée dans le dépôt, du côté du 200 vide**, et il
n'y a donc pas à pencher pour l'un ou pour l'autre : le **premier** bloc du diff
de la #72 sur `docs/EXPLOITATION.md` supprime ces lignes et les remplace par « à
une requête portant un autre `Host`, Caddy répond un `200` **vide** — pas un
`404` —, sans en-tête de sécurité ni corps » (`git diff
origin/main...origin/claude/preprod-fiabilisation -- docs/EXPLOITATION.md`,
premier hunk). Une version précédente de ce document attribuait la mesure au
script de la #72 **et** concluait que la #72 ne corrigeait pas le document :
c'était se contredire. Vérifié aussi qu'aucune autre PR ouverte ne touche ce
fichier — seules la #72 et la #83 le modifient, les brouillons #84 et #90 ne le
portent que par héritage.

### Les trois voies de sortie

#### Voie A — démarrer `api` sans le service `migrate`

**La définition de `mc` d'abord, ici et non trois sections plus loin** : c'est la
première ligne qu'on tape pendant la panne, et les deux `-f` ne s'improvisent
pas. En oublier un recrée le conteneur `proxy` **sans ses étiquettes** — voir
l'étape 3 de la section 4, qui explique pourquoi.

```
cd <racine-du-clone-sur-la-machine>
mc() {
  docker compose \
    -f infra/docker-compose.staging.yml \
    -f infra/docker-compose.derriere-proxy.yml \
    --env-file infra/staging.env "$@"
}
mc up -d --no-deps api
```

| | |
| --- | --- |
| **Exige** | que `db` tourne déjà et soit sain — c'est le cas sur une pile en service. Rien d'autre. |
| **Coûte** | un état que la commande documentée ne reproduit plus : **tout `mc up -d` ultérieur sans `--no-deps` rejoue `migrate`**, qui échouera de nouveau. C'est un sursis, pas un état stable. Aucune ligne de `docs/EXPLOITATION.md` ne décrit ce geste — le dépôt, lui, le joue déjà ailleurs, voir juste en dessous. |
| **Perd** | rien. Ni la base, ni le volume, ni une ligne. |
| **Irréversible** | non. |

**Ce geste exact est déjà joué par le dépôt, sur cette branche.**
`ops/repetition_staging.sh`, fonction `etape_restauration`, monte sa pile de
restauration par `docker compose --project-name "$PROJET_RESTAURE"
"${COMPOSITIONS[@]}" --env-file "$ENV_FICHIER" up -d --no-deps api`, et sa
raison est écrite dans le commentaire juste au-dessus : « `--no-deps db api` :
ni proxy ni front, on ne restaure pas pour servir ». C'est la même définition de
service `api`, et cela tourne à **chaque « Répétition complète »** — l'un des
douze contrôles verts que ce document cite plus bas pour la #90. (Une version
précédente donnait à la place deux renvois plus faibles :
`ops/sauvegarder.sh`, qui fait un `run --rm --no-deps` et non un `up`, et le
bloc d'usage de `infra/docker-compose.jetable.yml`, qui vit sur la branche de la
#72 et non ici.)

**Non vérifié sur cette pile** : le comportement de `--no-deps` est celui que
Compose documente (ne pas démarrer les services liés, donc ne pas attendre leurs
conditions) et celui que la répétition exerce, mais je ne l'ai pas exécuté
contre la préproduction.

#### Voie B — redescendre le schéma, puis revenir à l'ancienne image

```
# ORDRE IMPÉRATIF : la NOUVELLE image est encore celle d'`infra/staging.env`,
# parce que c'est elle, et elle seule, qui contient les fichiers de migration.
mc run --rm --no-deps api \
  alembic -c apps/api/alembic.ini downgrade c7d8e9fa0102
# SEULEMENT ENSUITE : remettre l'ancien SHA dans infra/staging.env, puis
mc up -d
```

(`infra/api.Dockerfile` ne déclare aucun `ENTRYPOINT`, seulement un `CMD` :
`mc run api alembic …` remplace donc bien la commande. `METREO_DATABASE_URL`
est posée dans l'environnement du service `api`.)

| | |
| --- | --- |
| **Exige** | que **trois** gardes passent, et chacune refuse — voir ci-dessous. En pratique : que rien n'ait encore été fait d'un plan. |
| **Coûte** | la table `plan_calibrations` et ses deux index — la fin du `downgrade()` de `…0008…` : deux `op.drop_index` puis `op.drop_table("plan_calibrations")`. Après la descente, `migrate` de l'ancienne image réussit : la base est déjà à `c7d8e9fa0102`, il n'a rien à faire. |
| **Perd** | les calibrations déclarées, s'il y en a — et c'est là que le dépôt est mince, voir le paragraphe suivant. |
| **Irréversible** | oui. `plan_calibrations` ne se reconstitue pas. |

**Les trois gardes, dans l'ordre où Alembic les rencontre en descendant :**

1. `e2f3a4b50607` compte les citations ancrées par **page + boîte** et lève
   `RuntimeError` s'il en reste (`…0008…`, le `raise RuntimeError` de son
   `downgrade()`, celui dont le message commence par « citation(s) sont ancrées
   par page et boîte »).
2. `d8e9fa010203` refuse si une **étape de lecture de plan** a tourné
   (`…0007…`, premier `raise RuntimeError` du `downgrade()`, sur
   `etapes_jouees`). Les quatre étapes surveillées sont
   `("page_render", "vector_geometry", "cad_read", "measurement")` (`…0007…`,
   tuple `ETAPES_DE_PLAN`) : `page_render` tourne **à l'affichage** d'un plan.
   Cette garde se referme donc dès le premier plan *ouvert*, avant toute mesure.
3. `d8e9fa010203` refuse aussi s'il reste une **citation ancrée sur un objet de
   plan** (`…0007…`, second `raise RuntimeError` du `downgrade()`, sur
   `citations_cao`).

Éprouvé sur une même base, en deux essais :

- **sans aucune citation page + boîte** : `alembic downgrade c7d8e9fa0102` →
  code **0**, base ramenée à `c7d8e9fa0102`. Le journal montre qu'une seule
  commande traite **les deux branches** : `f3a4b5c60708 → d1e2f3a40506`,
  `d1e2f3a40506 → c7d8e9fa0102`, `e2f3a4b50607 → d8e9fa010203`,
  `d8e9fa010203 → c7d8e9fa0102`.
- **avec UNE citation page + boîte** : code **1**,
  `RuntimeError: 1 citation(s) sont ancrées par page et boîte…`, et la base
  **inchangée** à `f3a4b5c60708`. Le retour à l'état initial vient du DDL
  transactionnel de PostgreSQL, qu'Alembic annonce lui-même en tête de journal
  (« Will assume transactional DDL ») : les trois descentes déjà jouées sont
  annulées avec l'erreur.

**Un trou qu'il faut nommer** : aucune garde ne compte les lignes de
`plan_calibrations` elles-mêmes. Une calibration déclarée sans qu'aucune mesure
n'en soit tirée ne satisfait aucune des trois conditions ci-dessus, et
`drop_table` l'emporterait. Ce qui la protège aujourd'hui, c'est le refus de la
migration **en aval** et le DDL transactionnel — pas une garde écrite pour elle.
Sur un moteur sans DDL transactionnel, la table serait partie.

#### Voie C — restaurer la sauvegarde prise avant le déploiement

| | |
| --- | --- |
| **Exige** | une sauvegarde prise **avant**, et **vérifiée** (section 5). Et un geste que **le dépôt ne fournit pas** : voir ci-dessous. |
| **Coûte** | une interruption de service le temps de la restauration, et une procédure à écrire. |
| **Perd** | **tout ce qui a été écrit en base depuis la sauvegarde** : mesures, calibrations, devis, clients, et les événements de la chaîne d'audit. Sur le volume, rien n'est perdu mais rien n'est remplacé non plus : le `tar` se détare **par-dessus** (`ops/restaurer.sh`, l'étape qui détare dans le conteneur `api`), donc les fichiers ajoutés depuis restent. L'état obtenu est un mélange : base d'avant, volume d'avant **plus** les fichiers d'après. |
| **Irréversible** | oui, dès que la base en service est écrasée. **Prendre une seconde sauvegarde juste avant de restaurer la première** est la seule façon de pouvoir encore changer d'avis. |

**Ce que le dépôt ne fournit pas, et c'est le manque le plus grave de cette
procédure :** `ops/restaurer.sh` **refuse** de restaurer dans la pile en
service. Il exige un marqueur de jetabilité dans le nom de la base (la garde
`case "$CIBLE"` en tête du script, qui exige `restore`, `scratch`, `jetable` ou
`tmp` dans le nom), et la #72 — branche `claude/preprod-fiabilisation`,
absente d'ici — ajoute le même marqueur sur le nom du projet Compose. C'est un
bon refus — mais il signifie qu'**aucun script du dépôt ne sait remettre une
sauvegarde en service**. Le script est un script d'exercice. Le jour où la voie
C est la seule, le geste est à improviser sous pression, à la main, dans le
conteneur `db`. C'est à écrire avant, pas pendant.

#### Laquelle est recommandée

**La voie A d'abord, pour rendre le service.** C'est la seule qui ne perd rien,
ne décide rien et ne ferme aucune porte. Elle rend l'ancienne application
immédiatement, sur le schéma avancé — ce qui marche, les migrations étant
additives — et laisse le temps de réfléchir au lieu de le prendre sur
l'incident.

**Puis, à froid, l'une des deux autres.** La voie B seulement si le retour
arrière survient dans les minutes qui suivent, avant que quiconque ait ouvert un
plan : passé ce point, ses gardes refusent, et c'est leur raison d'être. La
voie C est la seule qui rende un état *connu*, et c'est la plus chère : elle
perd tout l'intervalle et elle n'existe pas encore sous forme de script.

**Ce qu'il faut corriger dans le dépôt**, et que je n'ai pas fait : la section
« Retour arrière » de `docs/EXPLOITATION.md` doit dire que le retour arrière se
fait en **deux temps** — l'image *et* `--no-deps` — et le cas 1 de « Ce que le
retour arrière ne défait pas » (`:459-461`) doit cesser de dire « rien d'autre à
faire ». Tant que ce document n'est pas corrigé, c'est lui qu'on ouvrira pendant
l'incident, et il enverra dans le mur.

---

## 2. La revue : dans quel ordre relire, et ce que chacune demande

**43 demandes de fusion sont ouvertes** (`gh api
"repos/hermesprojet/devis/pulls?state=open&per_page=50"`, comptées), dont **21
hors brouillon**. Elles se rangent en six groupes.

**Une PR empilée ne montre que son écart à sa base.** La relire hors de son
ordre, c'est relire un diff dont la moitié du contexte manque.

### Groupe A — la pile des plans, à relire de bas en haut

| Ordre | PR | Branche → base | Ce qui demande l'attention |
| --- | --- | --- | --- |
| 1 | **#77** | `claude/lecture-de-plans` → `main` | Le socle DXF, et la seule du groupe qui vise `main`. Le refus du DWG doit être un refus nommé, pas un plantage. |
| 2 | **#78** | `claude/parcours-plan-essayable` → `lecture-de-plans` | **Porte `d8e9fa010203`.** C'est son `downgrade()` qui décide de ce qu'un retour arrière détruit : deux refus, les deux `raise RuntimeError` du `downgrade()` de `…0007…`. À relire ligne à ligne — la section 1 montre pourquoi. |
| 3 | **#80** | `claude/image-qui-lit-les-plans` → `parcours-plan-essayable` | Les extras de l'image (`infra/api.Dockerfile`, `[postgres,plans,pdf]`). Le commentaire du fichier nomme le défaut qu'il ferme : sans eux l'image « démarre, répond à tous les contrôles de santé, affiche l'écran "Lire le plan" » et échoue à l'analyse. |
| 4 | **#85** | `claude/lecteur-pdf` → `image-qui-lit-les-plans` | Lecture et aperçu PDF, sans mesure. Vérifier que rien n'y mesure encore. |
| 5 | **#87** | `claude/cotes-dans-les-blocs` → `lecteur-pdf` | Blocs DXF imbriqués : le risque de relecture est le **double comptage**. |
| 6 | **#89** | `claude/mesures-pdf` → `cotes-dans-les-blocs` | **Porte `e2f3a4b50607`**, la mesure et la tuile. 42 fichiers. Trois points ci-dessous. Depuis les corrections du 7 octobre, la tranche porte en plus `a4b5c6d70809`, qui en devient la tête. |

**Les trois points de la #89 :**

1. Le `downgrade()` supprime toute la table `plan_calibrations` (fin du
   `downgrade()` de `…0008…` : deux `op.drop_index`, puis
   `op.drop_table("plan_calibrations")`), et le facteur d'échelle **n'y est pas
   stocké** : « il se recalcule à l'identique depuis ces trois champs »
   (en-tête du module `…0008…`). Ce qui est détruit n'est donc pas une valeur
   dérivée, c'est la seule trace de l'échelle **déclarée par un humain**.
2. `apps/api/src/metreo_api/rendu_tuile.py` pose
   `PLAFOND_MEMOIRE = 1536 * 1024 * 1024` au processus fils, alors que le
   conteneur `api` est borné à **1 Go** (`infra/docker-compose.staging.yml`,
   limite mémoire du service `api`). Le plafond du fils est **au-dessus** de
   celui du conteneur : c'est le cgroup qui mordra le premier, et le symptôme
   sera un `OOMKilled`. Le fils le plus lourd mesuré a culminé à **510 Mo**
   (`rendu_tuile.py`, en-tête du module), sur les quatre plans du propriétaire —
   **des grands formats de 1 189 à 1 690 mm de grand côté**, pas des A0,
   mesurés : 1480 × 850, 1690 × 850, 1189 × 914 et 1189 × 914 mm.
3. La base de calibration minimale vaut `CALIBRATION_MINIMALE_EN_POINTS = 10.0`
   (`services/mesures_pdf.py`), soit **3,5 mm de papier** — 10 points
   PostScript, donc 10/72 de pouce, **quelle que soit la taille de la page**.
   Rapportée à la loupe (5 % de la page, rendue sur 512 px) : 6 % de la largeur
   de la loupe sur une page de 1 189 mm, 17 % sur un A3, 67 % sur la fixture de
   300 points du dépôt. Et l'incertitude relative du facteur est **sans
   échelle** : ε vaut la largeur de la loupe divisée par 512, donc √2·ε/d ne
   dépend que de la *fraction* de la loupe couverte par la base — **0,307 %**
   si les deux points sont aux bords, **0,552 %** à mi-largeur, identique sur
   1 189 mm et sur A3. Le relecteur doit savoir que ce seuil ne protège pas
   proportionnellement à la page.

### Groupe B — la connexion

| Ordre | PR | Branche → base | Attention |
| --- | --- | --- | --- |
| 7 | **#73** | `codex/login-account-choice` → `main` | Base de la #81. |
| 8 | **#81** | `claude/codes-de-connexion` → `codex/login-account-choice` | **Porte `d1e2f3a40506`**, et c'est elle qui doit recevoir la migration de fusion (section 3). Son `downgrade()` ne retire qu'une colonne sans donnée métier : c'est la seule des trois qui redescende sans rien perdre. |

### Groupe C — l'exploitation, à relire **avant** tout déploiement

C'est ici que la version précédente de ce document se trompait, et il faut le
dire franchement : **elle attribuait à la #83 ce qui appartient à la #72.**
Vérifié fichier par fichier (`gh api repos/hermesprojet/devis/pulls/<n>/files`).

| Ordre | PR | Branche → base | Ce qu'elle apporte **réellement** |
| --- | --- | --- | --- |
| 9 | **#72** | `claude/preprod-fiabilisation` → `main` | **Toute la mécanique.** 10 fichiers : `ops/verifier_deploiement.sh` (**ajouté**, 213 lignes) ; `infra/docker-compose.jetable.yml` (**ajouté**, 34 lignes) ; la **garde de jetabilité sur le nom de projet** dans `ops/restaurer.sh` (+49/−3) ; `infra/web.Dockerfile` (+9/−1, dont `HOSTNAME=0.0.0.0`) ; `.github/workflows/ci.yml` (+70) ; `ops/repetition_staging.sh` (+37/−3) ; `ops/verifier_disponibilite.sh` (+10/−1) ; `docs/EXPLOITATION.md` (+176/−6) ; `docs/AUTHENTIFICATION.md` (+105) ; `infra/staging.env.example` (+7). |
| 10 | **#83** | `claude/documentation-de-deploiement` → `preprod-fiabilisation` | **De la documentation, et rien d'autre.** 4 fichiers : `README.md` (+18/−10), `docs/EXPLOITATION.md` (+59/−13), `docs/ROADMAP.md` (+11/−5), `infra/staging.env.example` (+2/−1). Aucun script, aucune composition. |

Conséquence pratique : **c'est la #72 qui conditionne le déploiement**, pas la
#83. La #83 reste utile — elle corrige les noms d'images et les commandes sans
`--env-file` — mais elle n'ajoute aucun outil.

`ops/verifier_deploiement.sh` **n'existe pas sur la branche de travail** : il
vient de la #72, branche `claude/preprod-fiabilisation` (`git show
origin/claude/preprod-fiabilisation:ops/verifier_deploiement.sh`). Il contrôle
six maillons, dans l'ordre de ses propres titres : « 1. Les conteneurs, aux yeux
de Docker » ; « 2. Caddy, joint en local avec le bon Host » ; « 3. Le chemin que
le proxy de devant emprunte : l'IP du conteneur » ; « 4. Le certificat
réellement présenté sur le 443 de cette machine » ; « 5. Vu de l'extérieur, en
HTTPS, par le nom public » ; « 6. Ce que le proxy de devant dit de l'émission du
certificat ». Il rend `0`, `1` ou `2`, et `2` signifie, dans les mots exacts de
son en-tête : « 2 Docker injoignable — rien n'a été contrôlé. »

**Risque de conflit à signaler** : la #72 et la #89 modifient toutes deux
`.github/workflows/ci.yml`, dans des blocs distants — job `containers` pour la
#72, jobs `api-sqlite`, `api-postgres` **et `e2e`** pour la #89. Ce troisième
manquait à la version précédente de cette phrase : la #89 touche aussi `e2e`,
où elle ajoute l'extra `pdf` à l'installation du banc (`pip install -c
constraints/api.txt ./packages/domain "./apps/api[plans,pdf]"`). La conclusion
« blocs distants » tient quand même : les hunks de la #72 sont tous dans
`containers`, ceux de la #89 dans trois jobs qui le précèdent. La #83 et la #89
modifient toutes deux `README.md`. Rien d'insoluble ; à ne pas découvrir au
moment de la fusion.

### Groupe D — autonomes vers `main`, dans n'importe quel ordre

**#76** (failles PyJWT), **#82** (audit JavaScript), **#86** (provenance du
candidat), **#88** (fiche des cotes de référence). Aucune ne touche aux
migrations. La #88 est traitée à part en section 3.

### Groupe E — dépendances

**#70, #79, #14, #13, #12, #11, #10.** **Cinq au moins franchissent une version
majeure** — et non deux, comme l'écrivait la version précédente de ce document,
qui rassurait ainsi sur quatre PR qui en franchissent pourtant une. Relevé sur
les titres rendus par `gh api
"repos/Hermesprojet/Devis/pulls?state=open&per_page=100"` :

- **#10** `actions/setup-python 5.6.0 → 7.0.0` ;
- **#11** `actions/upload-artifact 4.6.2 → 7.0.1` ;
- **#12** `actions/checkout 4.4.0 → 7.0.1` ;
- **#14** `typescript 5.7.2 → 7.0.2` ;
- **#79** `@types/node 22.10.2 → 26.6.4`.

**#13** est un groupe `next` de 5 mises à jour dont le titre ne donne pas les
versions : à ouvrir pour le savoir. Seule la **#70** (`@playwright/test
1.62.1 → 1.63.0`) reste dans sa majeure — et ce n'est ni une action GitHub ni
`@types/node` : c'est la seule des sept qui touche un outil de la chaîne de
contrôle, puisque le job `e2e` de `ci.yml` dépend de Playwright.

### Groupe F — brouillons, à ne pas fusionner

22 brouillons, dont **#90** (`claude/candidat-complet`, en-tête « ÉPREUVE
SEULEMENT — ne pas fusionner ») et **#84** (`claude/integration-cinq-pr`, même
mention), plus les anciens #37 à #16.

La #90 compte pour une raison et une seule : **douze contrôles distincts y sont
verts** sur `466804165ccdb65859c12f31de2b929403f62c1d` — les onze ateliers de
`ci.yml` et « Répétition complète » (23 exécutions de contrôle pour 12 noms
distincts, `gh api repos/hermesprojet/devis/commits/4668041/check-runs`). C'est
la seule preuve existante que les quatorze branches tiennent ensemble. Et c'est
elle qui porte la migration de fusion, ce qui fait tout le sujet de la
section 3.

---

## 3. La fusion : l'ordre, où va la #88, et ce qui casse si on inverse

L'ordre est établi, simulé sur un arbre réel et éprouvé sur un PostgreSQL 16 +
PostGIS, dans `docs/FUSION_DES_MIGRATIONS.md` — **déjà dans le dépôt**, sur
cette branche. Je le reprends sans le modifier et j'y insère les groupes qui
manquent.

| # | Action | Tête d'Alembic sur `main` ensuite |
| --- | --- | --- |
| 0 | fusionner **#88** | inchangée (aucune migration) |
| 1 | fusionner **#77** | `c7d8e9fa0102` (inchangée) |
| 2 | fusionner **#78** | `d8e9fa010203` |
| 3 | fusionner **#80**, **#85**, **#87** | inchangée |
| 4 | fusionner **#89** | `a4b5c6d70809` |
| 5 | fusionner `main` dans `codex/login-account-choice`, puis dans `claude/codes-de-connexion` | — |
| 6 | **déplacer** `apps/api/alembic/versions/20261006_0009_fusion_des_deux_tetes.py` de `claude/candidat-complet` vers `claude/codes-de-connexion`, et l'y committer | — |
| 7 | fusionner **#73** | inchangée |
| 8 | fusionner **#81** | `f3a4b5c60708` |
| 9 | fusionner **#72**, puis **#83** | inchangée (aucune migration) |
| 10 | fusionner **#76**, **#82**, **#86**, puis les Dependabot | inchangée |

« **Une seule tête à chaque étape.** `main` n'est jamais rouge. »
(`docs/FUSION_DES_MIGRATIONS.md:108`)

Les **commandes exactes de l'étape 6** — le transfert effectif du fichier vers
la branche de la #81, et le contrôle `alembic heads` qui dit s'il a réussi —
sont dans `docs/FUSION_DES_MIGRATIONS.md`, section « Les commandes de
l'étape 6 — le transfert effectif ».

### Où placer la #88, et pourquoi en tête

La #88 **n'apporte qu'un seul fichier** : `docs/COTES_DE_REFERENCE.md`, ajouté,
220 lignes. Aucun code, aucune migration (`gh api
repos/hermesprojet/devis/pulls/88/files`). Elle vise `main` directement.

Or ce fichier est **absent de la branche de travail `claude/mesures-pdf`** — il
vient de la #88, branche `claude/cotes-de-reference`, et se lit par `git show
origin/claude/cotes-de-reference:docs/COTES_DE_REFERENCE.md` — et **deux
fichiers livrés par cette tranche le citent** :

- `apps/api/src/metreo_api/services/mesures_pdf.py` — « pas la même exigence
  qu'un châssis (voir `docs/COTES_DE_REFERENCE.md`) » ;
- `apps/web/e2e-premier-devis/suite-plan-pdf-mesures.spec.ts` — « c'est
  précisément ce que `docs/COTES_DE_REFERENCE.md` sert à établir ».

Ce sont donc deux renvois qui, sur la branche de travail, ne résolvent vers
rien. **La référence se résout dès que la #88 est fusionnée, et la #88 ne
contient QUE ce document** : il n'y a aucun ordre à respecter, aucun risque de
tête, aucune dépendance. La fusionner en premier coûte une minute et ferme deux
renvois cassés avant même que le code qui les porte n'arrive. C'est pourquoi je
la mets à l'étape 0 plutôt que de la ranger avec le groupe D.

### L'étape 6, et pourquoi elle n'est pas optionnelle

La migration de fusion `f3a4b5c60708` n'existe **que** sur
`claude/candidat-complet` — vérifié : `git ls-tree -r --name-only <branche> |
grep 0009_fusion` ne rend un résultat que pour cette branche, et rend zéro pour
`main`, `claude/codes-de-connexion`, `claude/mesures-pdf` et
`claude/cotes-de-reference`.

Comme la #90 est un brouillon destiné à ne pas être fusionné, l'y laisser revient
à la perdre, et `main` se retrouverait à deux têtes à l'étape 8 **sans que
personne n'ait rien fait de mal**.

### Ce qui casse si on inverse

| Inversion | Ce qui se passe |
| --- | --- |
| La connexion (#73/#81) avant la pile des plans | `main` est à deux têtes « de l'étape #78 jusqu'à l'étape #89 » (`docs/FUSION_DES_MIGRATIONS.md:117`), soit **quatre fusions** pendant lesquelles `main` et toutes les demandes ouvertes sont rouges. |
| Une PR « migrations seules » vers `main` | `KeyError: 'd8e9fa010203'`, et même corrigé, la porte de dérive refuse : la migration créerait `plan_calibrations` sans que `models.py` la déclare. |
| Mettre la fusion dans la #89 | `KeyError: 'd1e2f3a40506'`. |
| Mettre la fusion dans la #81 **avant** l'étape 5 | `KeyError: 'e2f3a4b50607'`. |

(Les quatre lignes ci-dessus sont celles du tableau de
`docs/FUSION_DES_MIGRATIONS.md`, section « Ce qui casse si on inverse ».)

**Deux têtes ne passent pas inaperçues**, et c'est une bonne nouvelle :
`conftest.alembic_head()` et `test_referential_action_drift.py` affirment
`len(heads) == 1`, et sur un arbre à deux têtes la seule suite ordinaire, sans
PostgreSQL, tombe à « **6 échecs et 1 erreur** »
(`docs/FUSION_DES_MIGRATIONS.md:57`). Le risque n'est pas que ça passe : c'est
que ça bloque tout, `main` rouge et chaque PR ouverte rouge avec elle.

---

## 4. Le déploiement : les étapes, les commandes, et le contrôle entre chacune

### Ce qui est automatisé, et ce qui ne l'est pas

| | Déclencheur | Source |
| --- | --- | --- |
| Les onze ateliers d'intégration | `push` sur toute branche, et `pull_request` | `.github/workflows/ci.yml`, bloc `on:` — `push: branches: ["**"]`, puis `pull_request:` |
| La répétition de préproduction complète | à la main ; sur `pull_request` touchant `infra/**`, `ops/**`, `apps/api/alembic/**`, `config.py`, `main.py`, `routers/meta.py` ; sur `push` vers `main` pour `infra/**`, `ops/**` et le workflow seul | `.github/workflows/repetition-staging.yml`, bloc `on:` (filtres `paths` de `push` et de `pull_request`) |
| La construction **et la publication** des deux images | **à la main uniquement** (`workflow_dispatch`) | `.github/workflows/publier-images.yml`, bloc `on:` |

**Tout ce qui touche la machine est manuel.** `.github/workflows/` ne contient
que `ci.yml`, `claude.yml`, `publier-images.yml` et `repetition-staging.yml`.
Aucun SSH, aucune livraison continue, aucun atelier de déploiement. Les
étapes 2 à 6 ci-dessous se tapent à la main, sur la machine.

### Étape 1 — publier les images (avec votre accord, et seulement avec lui)

Atelier « Publier les images », déclenché à la main sur le SHA complet de `main`
après fusion. Il résout le SHA lui-même et n'étiquette jamais `latest`.

Les noms produits sont **calculés depuis le dépôt**, pas depuis le produit :
l'atelier pose `depot=${GITHUB_REPOSITORY,,}` puis
`API_IMAGE: ghcr.io/${{ steps.cible.outputs.depot }}-api:…`
(`.github/workflows/publier-images.yml`, étape `cible` et le bloc qui s'en
sert). Donc :

```
ghcr.io/hermesprojet/devis-api:<sha>
ghcr.io/hermesprojet/devis-web:<sha>
```

`docs/EXPLOITATION.md:94` montre encore `ghcr.io/<compte>/metreo-api` et
`infra/staging.env.example` encore
`ghcr.io/hermesprojet/metreo-api:4604b7a8`. **Aucune image de ce nom n'existe.**
Une ligne recopiée à chaud pendant un incident tirerait une image introuvable.
La #83 corrige les deux.

*Contrôle avant de continuer* : que le résumé de l'atelier affiche bien les deux
lignes, et que l'image `web` porte le correctif de la #72 :

```
docker image inspect -f '{{range .Config.Env}}{{println .}}{{end}}' \
  ghcr.io/hermesprojet/devis-web:<sha> | grep -x 'HOSTNAME=0.0.0.0'
```

Sans cette variable, Next se lie à l'adresse du conteneur, la sonde échoue, le
conteneur ne devient jamais `healthy` — et le proxy de devant, lui, ne voit rien
d'anormal. Vérifié : `HOSTNAME` est **absent** de `infra/web.Dockerfile` sur
cette branche comme sur `39ad8d0`, et présent sur
`origin/claude/preprod-fiabilisation` (la branche de la #72), où il est posé dans le
bloc `ENV` de ce fichier, avec le commentaire qui dit pourquoi il n'est pas
décoratif.

### Étape 2 — noter le SHA sortant, puis sauvegarder

Dans cet ordre, et avant de toucher à `infra/staging.env`. Voir section 5. C'est
la seule étape qu'il ne faut jamais sauter : après la section 1, la sauvegarde
**est** la condition du déploiement.

### Étape 3 — poser les mêmes `-f` à chaque commande

La pile est derrière un proxy déjà en place. En oublier un recrée le conteneur
`proxy` **sans ses étiquettes** : Traefik retire le routeur et sert sur tout le
domaine une réponse qui n'a plus rien à voir avec Metreo, sans une erreur nulle
part.

```
cd <racine-du-clone-sur-la-machine>
mc() {
  docker compose \
    -f infra/docker-compose.staging.yml \
    -f infra/docker-compose.derriere-proxy.yml \
    --env-file infra/staging.env "$@"
}
mc images     # noter ce qui tourne, AVANT de rien changer
```

(C'est la même fonction que celle donnée en section 1, voie A : elle y est
répétée parce que c'est là qu'on la tape pendant une panne.)

**Le `--env-file` n'est pas décoratif.** Toutes les variables obligatoires
s'écrivent `${VAR:?message}` — `POSTGRES_USER`, `POSTGRES_PASSWORD`,
`POSTGRES_DB`, `API_IMAGE`, `METREO_JWT_SECRET` et les quatre `METREO_OIDC_*`,
`PUBLIC_ORIGIN`, `WEB_IMAGE`, `PUBLIC_HOST` (toutes dans
`infra/docker-compose.staging.yml`) — et, **avec la surcouche**,
`PUBLIC_DOMAIN` (`infra/docker-compose.derriere-proxy.yml`, la règle `Host()`
des étiquettes Traefik). Sans le fichier, Compose refuse de se résoudre, et
`mc images` échoue avant d'avoir rien montré.

**`PUBLIC_DOMAIN` n'apparaît comme ligne active dans aucun fichier du dépôt** :
`infra/staging.env.example` ne la donne qu'en commentaire, et la #72 n'y ajoute
que deux commentaires de plus (`HTTP_DIAGNOSTIC_PORT`, `TRAEFIK_CERT_RESOLVER`).
Le `infra/staging.env` de la machine doit donc déjà la porter — la pile tourne,
donc il la porte — mais **cela ne se vérifie pas depuis le dépôt**. À confirmer
sur la machine avant tout le reste :

```
grep -E '^(PUBLIC_HOST|PUBLIC_DOMAIN|PUBLIC_ORIGIN)=' infra/staging.env
```

### Étape 4 — remplacer les deux images, puis monter

Éditer `API_IMAGE` et `WEB_IMAGE` dans `infra/staging.env`, puis :

```
mc up -d
```

Les migrations tournent dans le service séparé `migrate`, **une seule fois**, et
l'API attend son achèvement (son `depends_on`). `restart: "no"` sur `migrate` :
le relancer en boucle rejouerait les migrations.

*Contrôle — et il lui faut le `-a`.* `docker compose ps` **ne liste pas** les
conteneurs arrêtés sans lui : « `-a, --all   Show all stopped containers
(including those created by the run command)` » (`docker compose ps --help`,
Compose v5.1.1 sur cette machine). Or `migrate` porte `restart: "no"` et sort
dès qu'il a fini : il est **déjà arrêté** quand on regarde. Un `mc ps` nu ne
montre donc rien et **ne tranche rien** — alors que c'est ici le seul contrôle
qui sépare le déploiement réussi du cas que la section 1 décrit. Une version
précédente de ce document écrivait « `mc ps` doit montrer `migrate` en
`exited (0)` » : tel quel, ce contrôle n'est pas exécutable.

Le dépôt fait autrement, et mieux : il lit le code de sortie au lieu de le
chercher dans une colonne de texte — `ops/repetition_staging.sh`, fonctions
`etape_demarrage` et `etape_migrations_une_seule_fois`, font
`docker inspect -f '{{.State.ExitCode}}' "$(compose ps -aq migrate)"`, avec le
`-a` ; elles gardent `compose ps -q api`, sans `-a`, pour les services qui
tournent (`etape_sondes_pendant_panne`). À taper :

```
mc ps -a                                                  # migrate en exited (0)
docker inspect -f '{{.State.ExitCode}}' "$(mc ps -aq migrate)"   # doit rendre 0
```

Si `migrate` sort non nul, **`api` ne démarrera pas**, et c'est le cas que la
section 1 décrit.

### Étape 5 — vérifier la chaîne

| Ce qu'on vérifie | Commande | Où elle existe |
| --- | --- | --- |
| Les six maillons derrière le proxy | `ops/verifier_deploiement.sh` | **uniquement sur la branche de la #72**, `claude/preprod-fiabilisation` |
| La disponibilité vue de l'extérieur — `0` disponible, `1` dégradé, `2` indisponible | `ops/verifier_disponibilite.sh https://<domaine>` | `ops/verifier_disponibilite.sh`, sur cette branche (verdict et codes de sortie dans le `case "$etat"` final) |

**Ne lancez pas `verifier_disponibilite.sh` nu contre `127.0.0.1:8081`.** Le
Caddyfile n'ouvre qu'un site, celui de `PUBLIC_HOST` ; une requête portant un
autre `Host` n'est pas servie par ce bloc, **et le script conclurait
« INDISPONIBLE » sur une pile saine**. C'est la phrase de
`docs/EXPLOITATION.md`, encadré « Ne lancez pas `ops/verifier_disponibilite.sh`
contre `127.0.0.1:8081` » (ligne 171 sur cette branche, réécrit par la #72) ;
une version précédente de ce document en avait laissé tomber le verdict, ce qui
inversait la mise en garde — on y lisait « le script conclurait que la pile est
saine », c'est-à-dire le contraire de la source.

Pour le diagnostic local, gardez le bon `Host`, **mais ne regardez pas que le
code de réponse** : la section 1 de ce document établit qu'à un `Host` qu'il ne
sert pas, Caddy répond un `200` **vide**, pas une erreur. Un `curl` qui
n'inspecte ni les en-têtes ni le corps rend donc le **même** `200` dans les deux
cas qu'il est censé séparer. La forme qui tranche est celle de la #72 :

```
curl -si -H 'Host: <domaine>' http://127.0.0.1:8081/api/v1/live \
  | grep -i '^HTTP/\|^strict-transport-security\|^x-request-id'
```

Un `200` accompagné de ces deux en-têtes dit que le site de Caddy a servi **et**
que l'API a répondu : `Strict-Transport-Security` est posé dans le bloc de site
de `infra/Caddyfile`, et `X-Request-Id` vient de l'API (le `infra/Caddyfile` ne
fait que le poser sur la requête quand le client n'en fournit pas, matcher
`@sans_identifiant`). Un `200` nu dit que Caddy a répondu sans reconnaître le
nom. C'est ce contrôle, et non le `curl` sec, qui sépare « Caddy est sain » de
« le proxy de devant ne l'atteint pas ».

(Le port vient de `HTTP_DIAGNOSTIC_PORT`, défaut `8081` —
`infra/docker-compose.derriere-proxy.yml`, publication de port du service
`proxy`.)

### Étape 6 — la vérification humaine, la seule qui prouve quelque chose

Un service qui répond `200` n'est pas un service qui lit des plans. Le
`HEALTHCHECK` de l'image interroge `/live` (`infra/api.Dockerfile`, directive
`HEALTHCHECK`), et **aucune sonde du dépôt ne touche à la lecture de plans**.
Deux contrôles, en lecture, sans effet de bord :

```
mc exec -T api python -c "import ezdxf, pypdfium2; print('ok')"
```

puis, au navigateur : déposer un plan, l'afficher, le calibrer, prendre une
mesure. C'est le seul geste qui éprouve la tranche. **Et il ferme la voie B de
la section 1** : dès que ce plan est affiché, `page_render` est enregistré, et
le schéma ne redescend plus.

---

## 5. La sauvegarde AVANT : base, volume, et comment savoir qu'elle vaut quelque chose

### Ce que `ops/sauvegarder.sh` couvre réellement

| Quoi | Comment | Où |
| --- | --- | --- |
| La base | `pg_dump --format=custom --no-owner --no-privileges`, par `exec` dans le conteneur `db` | `ops/sauvegarder.sh`, l'appel `exec -T db pg_dump` |
| Le volume de stockage, **en entier** | `tar -C /var/lib/metreo -cf - .` depuis un conteneur jetable, sans arrêter le service | `ops/sauvegarder.sh`, l'appel `run --rm --no-deps --entrypoint sh api` |

**Deux** refus utiles, et ce sont des refus, pas des avertissements :

1. **dump vide** → message, suppression du fichier et `exit 1`
   (`ops/sauvegarder.sh`, bloc `if [[ ! -s "$BASE.dump" ]]` : `echo`, puis
   `rm -f "$BASE.dump"`, puis `exit 1`) ;
2. **dépôt chez un tiers sans chiffrement** → `exit 1` (le bloc
   `if [[ -n "${BACKUP_DESTINATION:-}" ]]`, qui porte lui-même le commentaire
   « REFUS, et non un avertissement »).

Une version précédente de ce document en comptait trois et rangeait avec eux
« l'archive porte l'horodatage et n'écrase jamais » : ce n'en est pas un, c'est
une propriété de **nommage** (`HORODATAGE="$(date -u +%Y%m%dT%H%M%SZ)"`, puis
`BASE="$SORTIE/metreo-$HORODATAGE"`). Et le seul autre contrôle du script —
« aucun `BACKUP_AGE_RECIPIENT` : archive NON chiffrée » — est justement un
avertissement, c'est-à-dire ce que la phrase prétendait exclure.

### La commande, sur la pile en service derrière le proxy

```
cd <racine-du-clone-sur-la-machine>
BACKUP_COMPOSE_PROJECT=metreo-staging \
BACKUP_COMPOSE_FILES="-f $PWD/infra/docker-compose.staging.yml -f $PWD/infra/docker-compose.derriere-proxy.yml" \
ops/sauvegarder.sh /var/backups/metreo
```

**Exécutable telle qu'écrite**, et voici pourquoi, parce que ce n'est pas
évident :

- les deux variables ne sont pas facultatives : sans elles le script vise
  `-f <racine>/infra/docker-compose.staging.yml` **seul** (sa valeur par défaut
  de `BACKUP_COMPOSE_FILES`) et ne résout pas la pile réelle ;
- `POSTGRES_USER` et `POSTGRES_DB`, exigés par l'appel à `pg_dump`, **n'ont pas
  à être exportés** : le script source lui-même `BACKUP_ENV_FILE`, dont le
  défaut est `<racine>/infra/staging.env` ;
- les chemins des `-f` sont rendus absolus par `$PWD` parce que Compose les
  résout depuis le répertoire courant, et que le script, lui, n'en change pas.

Sans `BACKUP_AGE_RECIPIENT` **et** `BACKUP_DESTINATION`, l'archive reste sur la
machine qu'elle sauvegarde. Le script le dit : « Une sauvegarde qui vit sur la
machine sauvegardée ne protège de rien. »

### Ce que la sauvegarde ne couvre PAS

| Hors sauvegarde | Source |
| --- | --- |
| **Le certificat TLS**, quand il vit chez le proxy de devant (`acme.json` de Traefik) | `docs/EXPLOITATION.md:162-167` : « qu'aucune sauvegarde Metreo ne couvre » |
| **`infra/staging.env` lui-même** — secrets OIDC, `METREO_JWT_SECRET`, mot de passe PostgreSQL. Non versionné, et le script ne l'archive pas | absence dans `ops/sauvegarder.sh` |
| **Les plans réels déposés hors application** (par exemple `/opt/metreo-plans/`) | `docs/PLANS_REELS.md` : « la sauvegarde chiffrée le couvre **si on l'y ajoute explicitement** » |

### Comment vérifier qu'elle est exploitable

Une sauvegarde non vérifiée n'en est pas une. La vérification est une
**restauration dans une pile jetable**, suivie de
`ops/verifier_restauration.py`, qui ne se contente pas d'une absence d'erreur :
il compte, il vérifie la **chaîne d'audit organisation par organisation** et il
refuse de conclure si aucun compte n'a d'appartenance active.

> ### ⚠ Sur la branche de travail, cet exercice ne doit pas être fait
>
> Deux manques, tous deux comblés par la **#72** (branche
> `claude/preprod-fiabilisation`) et non par la #83.
>
> **1. `infra/docker-compose.jetable.yml` n'existe pas ici** — il vient de la
> #72 (`git show
> origin/claude/preprod-fiabilisation:infra/docker-compose.jetable.yml`). Sans
> lui, une seconde pile montée avec la seule composition de base tenterait de
> prendre 80 et 443 — collision avec le proxy en place ; et avec la surcouche
> `derriere-proxy`, elle porterait **les mêmes étiquettes Traefik que la pile en
> service**, donc la règle `Host()` du domaine réel
> (`infra/docker-compose.derriere-proxy.yml`). Traefik pourrait router le trafic
> du site vers la pile de restauration. Le fichier de la #72 existe exactement
> pour cela : `ports: !override []` et `labels: !override {}`.
>
> **2. `ops/restaurer.sh` ne vérifie la jetabilité que sur le nom de la base**
> (la garde `case "$CIBLE"` en tête du script). Le nom du **projet
> Compose** n'est pas vérifié. Or le stockage des fichiers est détaré dans le
> conteneur `api` **du projet nommé**. Taper
> `RESTORE_COMPOSE_PROJECT=metreo-staging` fusionnerait l'archive par-dessus les
> pièces jointes vivantes, **sans un message**. La base, elle, serait épargnée :
> elle est recréée à part. La #72 ferme ce trou en exigeant le même marqueur sur
> le nom de projet — cette garde-là, elle aussi, n'existe que sur sa branche.
>
> **Ne faites pas d'exercice de restauration avant que la #72 ne soit
> fusionnée.**

**Après la #72**, le bloc est celui que `infra/docker-compose.jetable.yml`
documente lui-même dans son en-tête d'usage :

```
J="--project-name metreo-restore \
   -f infra/docker-compose.staging.yml \
   -f infra/docker-compose.jetable.yml \
   --env-file infra/staging.env"
docker compose $J up -d --wait --wait-timeout 240 db
docker compose $J up -d --no-deps api

RESTORE_COMPOSE_PROJECT=metreo-restore \
RESTORE_COMPOSE_FILES="-f infra/docker-compose.staging.yml -f infra/docker-compose.jetable.yml" \
RESTORE_ENV_FILE=infra/staging.env \
ops/restaurer.sh /var/backups/metreo/<archive>.tar.gz metreo_restore_essai

docker compose $J down --volumes
```

**Cette commande n'est exécutable telle quelle qu'avec la #72**, et c'est un
point que la version précédente de ce document avait manqué. Sur la branche de
travail, `ops/restaurer.sh` exige `POSTGRES_USER` **et** `POSTGRES_DB` dans
l'environnement du shell, et utilise `${POSTGRES_PASSWORD:-}` — qui vaut la
chaîne vide si elle n'est pas posée, donc une URL de connexion sans mot de
passe. `RESTORE_ENV_FILE` n'est passée qu'à Compose ; le script ne la lit pas.
La #72 ajoute une fonction `valeur_env` qui l'extrait par `sed` — et qui ne la
*source* pas, délibérément : « ce n'est pas un fichier shell, et une valeur non
protégée y exécuterait une commande ». C'est une raison de plus de ne pas
contourner le manque en sourçant `infra/staging.env` à la main : un mot de passe
contenant `$(…)` s'exécuterait.

### Une précision qui compte en mode `oidc`

`verifier_restauration.py` contrôle une **structure** — un compte actif avec une
appartenance active —, pas un accès. En `oidc`, entrer demande en plus une
identité liée. **Une base restaurée peut passer ce contrôle et rester fermée.**
La #83 ajoute cette précision à `docs/EXPLOITATION.md`.

---

## 6. Le retour arrière, cas par cas, et ce qui est irréversible

La section 1 porte le fait qui domine : **le retour arrière par image seule ne
remet pas l'API en service.** Les trois cas ci-dessous s'y rapportent.

### Avant tout geste — récolter

Les journaux disparaissent au redémarrage d'un conteneur qui n'écrit pas
ailleurs :

```
mc logs --since 30m --no-color api > incident-api.log
mc logs --since 30m --no-color proxy db > incident-infra.log
```

**Ce qu'on ne récupère pas** : ces journaux, si on redémarre d'abord. C'est la
seule perte de tous les cas qui soit purement évitable.

### Cas A — « le déploiement ne démarre pas »

`ops/verifier_deploiement.sh` (après la #72 : il n'existe que sur
`claude/preprod-fiabilisation`) donne le maillon exact ; à défaut, `mc ps -a` et
les sondes. Le tableau de `docs/EXPLOITATION.md:405-410` sépare les quatre
lectures : API tombée, base injoignable (**ne pas redémarrer l'API**),
configuration dégradée, front ou routage.

**Le geste :** remettre le SHA précédent dans `infra/staging.env`, puis
`mc up -d` — **avec les mêmes `-f`** — **et constater que cela ne suffit pas** :
`migrate` échouera (section 1), et il faut enchaîner `mc up -d --no-deps api`.

- **Perd** : rien en base, rien sur le volume.
- **Irréversible** : rien, à ceci près que l'état obtenu n'est pas celui que la
  commande documentée reproduit.

#### Ce que l'ancienne API fait VRAIMENT sur le schéma neuf — mesuré

La voie A était classée « la plus réversible, et la plus rapide » sur un
raisonnement : rien ne disait que l'ancien code TOURNE sur un schéma qu'il ne
connaît pas, et une API qui démarre ne prouve rien de ce qui compte.

`scripts/epreuve_retour_arriere.py` le joue maintenant pour de bon : il monte le
schéma neuf, y dépose un plan, le calibre, le mesure, tranche — puis fait jouer
**l'API de `origin/main` (39ad8d0)** sur cette même base, **sans toucher aux
migrations**. Relevé le 7 octobre 2026 :

| Parcours | Résultat |
| --- | --- |
| Se connecter, lire son profil | **OK** |
| Lister chantiers, clients, bibliothèque | **OK** |
| Ouvrir le chantier créé par la version neuve | **OK** |
| Lister ses documents, relire une révision | **OK** |
| **Télécharger l'original du plan déposé pendant l'essai** | **OK** |
| Lire le journal d'audit, vérifier la chaîne par l'API | **OK** |
| Créer un client, créer un chantier, déposer un document | **OK** |
| Vérifier la chaîne d'audit en base | **OK** — `valid: True`, 12 maillons |
| **Les mesures de l'essai sont-elles encore là ?** | **OUI** — 1 proposition, 1 calibration, 1 décision |
| `GET …/plan` et `…/plan/mesures` | **404** — ces routes n'existent pas dans l'ancienne version. C'est le comportement correct, pas une panne |
| **Relancer les migrations (voie A manquée)** | **code 255** — `Can't locate revision identified by 'a4b5c6d70809'` |

**Seize parcours sur dix-neuf fonctionnent**, et les trois autres sont ceux
qu'on attend : deux routes absentes, et le refus de migrer qui est précisément
la raison d'être de cette voie.

**Ce que la mesure change à la procédure** : rien, elle la confirme — mais elle
la confirme. L'écran de lecture de plans disparaît, les mesures prises pendant
l'essai **restent en base, intactes et invisibles**, et tout le reste du
produit fonctionne. Le seul geste qui compte est de monter `api` **sans**
`migrate`, et la dernière ligne du tableau dit ce qu'il en coûte de l'oublier.

**Ce que cette épreuve ne couvre pas** : elle fait tourner du code Python
contre un schéma SQLite. Elle ne dit rien des déclencheurs PostgreSQL — qui ne
sont pas les mêmes — ni du comportement de `docker compose`, mesuré ailleurs
dans ce document.

### Cas B — « il démarre, mais les plans ne se lisent pas »

**Le cas le plus dangereux, parce que toutes les sondes restent vertes.** `/live`
répond, `/health` répond `ok`, `ops/verifier_disponibilite.sh` rend `0`, et le
`HEALTHCHECK` interroge `/live` (`infra/api.Dockerfile`, directive
`HEALTHCHECK`). Aucune sonde du dépôt ne touche à la lecture de plans.

Deux causes nommées par le dépôt :

1. **Les extras manquants.** `infra/api.Dockerfile` décrit le défaut mot pour
   mot, dans le commentaire qui précède l'installation des extras : l'image
   « démarre, répond à tous les contrôles de santé, affiche l'écran "Lire le
   plan" » et échoue à l'analyse. Contrôle :
   `mc exec -T api python -c "import ezdxf, pypdfium2; print('ok')"`.
2. **La mémoire.** Conteneur borné à 1 Go (`infra/docker-compose.staging.yml`,
   limite mémoire du service `api`), plafond du fils à 1536 Mo
   (`rendu_tuile.py`, constante `PLAFOND_MEMOIRE`) : le cgroup mord le premier.
   Symptôme `OOMKilled`, et `docs/EXPLOITATION.md:502` rappelle que rien d'autre
   ne l'annonce.

**Le geste :** cas A.
- **Perd** : rien en base. Les mesures et calibrations déjà prises restent.
- **Ne se récupère pas** : rien. Les tuiles déjà écrites sur le volume sont
  re-dérivables ; ce n'est pas une perte.

### Cas C — « il faut redescendre le schéma »

C'est la voie B de la section 1. **À ne décider qu'explicitement, jamais dans
l'urgence, et jamais sans une sauvegarde fraîche et vérifiée.**

| Révision | `downgrade()` | Ce qu'il détruit | Refuse-t-il ? |
| --- | --- | --- | --- |
| `f3a4b5c60708` fusion | ne fait rien | rien. « Redescendre rouvre les deux têtes, ce qui est correct : c'est exactement l'état d'avant la fusion. » | non |
| `d1e2f3a40506` réauth. | `drop_column` | la colonne `login_transactions.reauthentication_requested`. Aucune donnée métier : une transaction de connexion est éphémère | non |
| `e2f3a4b50607` calibration | supprime la table et remet l'ancienne contrainte | **toute la table `plan_calibrations`** et ses deux index (fin du `downgrade()` de `…0008…` : deux `op.drop_index`, puis `op.drop_table("plan_calibrations")`) | **oui**, s'il reste une citation ancrée par page + boîte (le `raise RuntimeError` du même `downgrade()`) |
| `d8e9fa010203` citation de plan | ramène à onze étapes, repose les `NOT NULL` | l'état d'exécution des étapes de lecture de plan, et toute citation CAO | **oui, deux fois** (les deux `raise RuntimeError` du `downgrade()` de `…0007…`, sur `etapes_jouees` puis sur `citations_cao`) |

**Un détail d'ordre, et il est rassurant :** une seule commande traite les deux
branches. Mesuré — `alembic downgrade c7d8e9fa0102` descend `f3a4b5c60708`, puis
`d1e2f3a40506`, puis `e2f3a4b50607`, puis `d8e9fa010203`. Et comme le DDL est
transactionnel sur PostgreSQL, un refus en fin de chaîne annule tout ce qui
précède : mesuré, la base est restée **inchangée à `f3a4b5c60708`** après le
refus.

#### Ce qui est IRRÉVERSIBLE, dit sans ménagement

**`plan_calibrations` ne se reconstitue pas.** Elle porte les deux points
pointés, la distance réelle déclarée, l'unité, la résolution du pointage, la
zone d'application, le motif et l'auteur — dix colonnes numériques et deux clés
étrangères (`…0008…`, l'appel `op.create_table("plan_calibrations")`). Le
**facteur d'échelle n'est pas stocké** : « il se recalcule à l'identique depuis
ces trois champs » (en-tête du module `…0008…`). La détruire ne détruit donc pas
une valeur dérivée : elle détruit **la seule trace de l'échelle déclarée par un
humain**. Toute mesure prise sur ce plan perd sa provenance, et recalibrer ne la
rend pas — la nouvelle calibration est une autre déclaration, par une autre
personne, à un autre moment.

**Les refus ne sont pas des obstacles à contourner.** Ils disent : il reste en
base des mesures qu'un humain a peut-être validées, et il n'existe aucune valeur
honnête à écrire à leur place. Les supprimer pour débloquer la descente est une
décision humaine, et elle est définitive — hors restauration d'une sauvegarde
antérieure.

**Restaurer la sauvegarde est irréversible aussi**, dès que la base en service
est écrasée. Et le dépôt ne fournit aucun script pour le faire (section 1,
voie C). Prendre une seconde sauvegarde juste avant est la seule façon de
pouvoir encore changer d'avis.

---

## 7. Ce qui n'est pas prêt : ce que cette procédure suppose et qui n'existe pas

### Absent de la branche de travail, présent ailleurs — bloquant pour déployer

Les quatre premières lignes viennent toutes de la **#72**, branche
`claude/preprod-fiabilisation` ; la dernière de la **#88**, branche
`claude/cotes-de-reference`. Pour lire n'importe lequel de ces fichiers sans
changer de branche : `git show origin/<branche>:<chemin>`.

| Ce qui manque | Où il existe | Conséquence |
| --- | --- | --- |
| `ops/verifier_deploiement.sh` (213 lignes, 6 maillons) | **#72**, `claude/preprod-fiabilisation` | Aucun contrôle de la chaîne derrière le proxy. Sans lui, deux pannes très différentes se ressemblent trait pour trait. |
| `infra/docker-compose.jetable.yml` | **#72**, `claude/preprod-fiabilisation` | L'exercice de restauration n'a pas de pile jetable, et une pile improvisée capterait le trafic du domaine. |
| La garde de jetabilité sur `RESTORE_COMPOSE_PROJECT`, et la lecture de `RESTORE_ENV_FILE` par le script | **#72**, `claude/preprod-fiabilisation` | Une restauration peut écrire dans le volume de la pile en service ; et la commande documentée n'est pas exécutable sans exporter trois variables à la main. |
| `HOSTNAME=0.0.0.0` dans `infra/web.Dockerfile` | **#72**, `claude/preprod-fiabilisation` | Le conteneur `web` ne devient jamais `healthy`. |
| `docs/COTES_DE_REFERENCE.md` | **#88**, `claude/cotes-de-reference` | Deux fichiers livrés le citent et pointent vers rien (section 3). |

### Faux dans `docs/EXPLOITATION.md`, et corrigé par aucune PR ouverte

- `:437`, `:456`, `:459-461` — le retour arrière par image « suffit, rien d'autre
  à faire ». **C'est le point de la section 1.**
- `:493` — « calcul déterministe, pas de traitement d'image ».
- `:264` — « aucune extraction, aucun rendu à l'écran ».

**Corrigé par la #72, et il faut le dire ici parce qu'une version précédente de
ce document le rangeait parmi les faux que personne ne corrige** : `:171`, le
`404` sur un mauvais `Host`. Le **premier** hunk du diff de la #72 sur ce fichier
supprime ces lignes et y met le `200` vide, en-têtes à l'appui (section 1).

Corrigé par la #83, pour mémoire : `:94-95` et `:444-445` (noms d'images),
`:399`, `:416-417`, `:441` (commandes sans `--env-file`), `:535-536` (« Un
serveur d'exécution » listé comme manquant alors que la machine sert depuis le
16 septembre 2026), et la précision `oidc` sur `verifier_restauration.py`.

### Absent partout

| Ce qui manque | Conséquence | Source |
| --- | --- | --- |
| **Une procédure de restauration en service** | La seule sortie de la section 1 n'a pas de script. `ops/restaurer.sh` refuse par construction toute cible non jetable. | `ops/restaurer.sh`, garde `case "$CIBLE"` ; et celle que la #72 ajoute sur le nom de projet |
| **Toute sonde de la lecture de plans** | `/live` et `/health` restent verts avec une API incapable d'ouvrir un PDF. | `infra/api.Dockerfile`, directive `HEALTHCHECK` ; `ops/verifier_disponibilite.sh`, ses deux sondes HTTP — `/api/v1/live` et `/api/v1/health` |
| **La répétition rejouée sur `main` après cette tranche** | Sur `push: main`, le workflow ne se déclenche que pour `infra/**`, `ops/**` et lui-même — **pas pour `apps/api/alembic/**`**. Les fusions de la pile des plans ne relanceront pas la répétition sur `main`. | `.github/workflows/repetition-staging.yml`, filtre `paths` du déclencheur `push` |
| **Toute purge des artefacts dérivés sur le volume** | Aperçus et tuiles vivent sous `rendus-de-plan/` (`services/rendu_de_plan.py`, constante `DOSSIER_RENDUS`) dans `METREO_STORAGE_ROOT`. `ops/sauvegarder.sh` archive **tout** `/var/lib/metreo` : les sauvegardes grossissent de données re-dérivables. Les seuls plafonds sont par révision — 500 tuiles ou 64 Mio (`services/tuiles.py`, constantes `PLAFOND_DE_TUILES_PAR_REVISION` et `PLAFOND_D_OCTETS_PAR_REVISION`). Aucun plafond global, aucune purge. | — |
| **La vérification de ces artefacts par la répétition** | `ops/repetition_staging.sh`, fonction `empreintes_du_volume`, ne hache que `/var/lib/metreo/documents`. Rendus et tuiles ne passent par aucun aller-retour de sauvegarde. | — |
| **`PUBLIC_DOMAIN` comme ligne active d'un fichier d'exemple** | La surcouche l'exige (`derriere-proxy.yml`, règle `Host()` des étiquettes Traefik), aucun fichier du dépôt ne la donne autrement qu'en commentaire. Un nouveau déploiement échoue sur un message de Compose. | `infra/staging.env.example` |
| **Des sauvegardes distantes** | Les archives restent sur la machine sauvegardée. Une machine perdue les perd toutes. | `docs/EXPLOITATION.md:529-530` |
| **Une supervision réelle** | Les **onze** seuils du tableau `:327-339` décrivent ce qu'il faut surveiller ; aucun outil ne les applique. (Onze, et non dix : 5xx ; 4xx hors 401/403/404 ; indisponibilité ; base injoignable ; service `degraded` ; latence de `/health` ; disque `db-data` ; disque `api-storage` ; sauvegarde ; restauration éprouvée ; certificat TLS.) | `docs/EXPLOITATION.md:531-532` |
| **Une connexion de bout en bout constatée** | La machine répond depuis le 16 septembre, mais personne n'y est encore entré. Aucun test ne peut exercer le parcours contre un vrai fournisseur d'identité. | corps de la #83 |
| **Les décisions juridiques** | Conservation, sous-traitance, localisation des données, information des personnes. | `docs/EXPLOITATION.md:533-534` |
| **Tout atelier de déploiement** | Rien dans `.github/workflows/` ne touche le VPS. Les étapes 2 à 6 sont manuelles. | `ls .github/workflows/` |

---

## 8. Ce que je vous demande de trancher avant quoi que ce soit

1. **Corriger `docs/EXPLOITATION.md` avant de déployer**, ou accepter que le
   document qu'on ouvrira pendant l'incident dise, aux lignes 459-461, qu'un
   retour arrière par image « suffit ». C'est le premier point, et de loin.
2. **Fusionner la #72 avant tout déploiement** — c'est elle, et non la #83, qui
   porte `ops/verifier_deploiement.sh`, `infra/docker-compose.jetable.yml`, la
   garde de jetabilité et `HOSTNAME=0.0.0.0`, et c'est elle aussi qui corrige le
   `404` sur un mauvais `Host`.
3. **Fusionner la #88 en premier** : un fichier, aucun risque, et deux renvois
   cassés fermés dans le code déjà écrit.
4. **Déplacer la migration de fusion vers la #81** (étape 6 de la section 3), ou
   décider autrement — mais pas la laisser dans le brouillon #90.
5. **Relever la limite mémoire du conteneur `api`** avant de servir de vrais
   plans, ou accepter un `OOMKilled` que rien n'annonce. Je n'ai pas de mesure
   de la bonne valeur : les 510 Mo mesurés l'ont été sur quatre plans hors
   dépôt, pas sur les vôtres.
6. **Écrire la procédure de restauration en service**, ou accepter de
   l'improviser le jour où elle sera la seule sortie.
7. **Regarder de près les cinq montées de dépendances qui franchissent une
   version majeure** (groupe E), et la #13 dont le titre ne dit pas ses
   versions.
8. **Votre accord explicite** pour chacune des trois actions irréversibles :
   publier les images, fusionner, toucher au VPS. Rien n'a été fait.