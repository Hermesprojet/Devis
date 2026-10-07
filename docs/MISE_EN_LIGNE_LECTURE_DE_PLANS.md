# Déployer la lecture de plans, et revenir en arrière

- **Écrit le** : 2026-10-07, depuis la branche `claude/candidat-integre`
  (`2d18299ee08b2a66bb54573d665f233a72aaf6ee`).
- **Les contrôles invoqués ici** : **douze contrôles distincts sont verts sur ce
  SHA**, et tous sur lui — les onze ateliers de `.github/workflows/ci.yml`
  (<https://github.com/Hermesprojet/Devis/actions/runs/37629113133>) et
  « Répétition de préproduction », déclenchée à la main
  (<https://github.com/Hermesprojet/Devis/actions/runs/37629959343>).
- **Dernières observations de la machine** : **16 septembre 2026**. Tout ce que
  ce document dit de l'état du serveur date de ce jour-là. Trois semaines ont
  passé ; la préproduction a pu bouger sans que rien ici ne le sache.
- **Rien de ce document n'a été exécuté, et rien ne doit l'être sans votre
  accord.** Aucune image construite ni publiée, aucun contact avec le VPS. Les
  commandes sont écrites pour être relues par vous avant d'être tapées.

> **La voie de livraison est ailleurs, et il n'y en a qu'une.**
> `docs/FICHE_DE_DECISION_MISE_EN_LIGNE.md` la décrit : une demande de fusion de
> `claude/candidat-integre` vers `main`, treize gestes, chacun avec son
> contrôle. **Ce document-ci en est le détail — les commandes, les cas de panne,
> le retour arrière — et non une alternative.** Il ne décrit aucun ordre de
> fusion : il n'y en a plus qu'un, et c'est celui de la fiche. Si les deux se
> contredisent, c'est la fiche qui a raison et ce document qui est à corriger.

**Ce qu'il n'est pas** : une procédure d'exploitation courante. Celle-là est
`docs/EXPLOITATION.md`, qui a été réécrit depuis et dit désormais la même chose
que ce document sur le retour arrière.

Tout chiffre ci-dessous porte son fichier, et son **symbole** — constante,
fonction, bloc — quand j'ai rouvert le fichier pour le vérifier. Les renvois
vers un autre document nomment sa **section**, jamais un numéro de ligne : un
numéro se périme au commit suivant, et les renvois par ligne de ce document
étaient déjà faux dans les deux sens. Ce qui n'a pas été mesuré est écrit « non
mesuré » ; ce qui n'a pas été exécuté sur la machine, « non vérifié sur cette
pile ».

---

## 1. Le point qui domine tout le reste, et qu'il faut lire avant la première commande

> Le retour arrière **par image seule ne remet pas l'API en service** après
> cette tranche. Et dès la première mesure enregistrée, le schéma ne peut plus
> redescendre. La seule sortie est alors **la restauration de la sauvegarde
> prise avant le déploiement** — ce qui fait de cette sauvegarde non pas une
> précaution, mais la condition du déploiement.

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
préproduction sert, et son arbre porte **17 fichiers de migration** s'arrêtant à
`c7d8e9fa0102` (`git ls-tree -r 39ad8d0 -- apps/api/alembic/versions/`). Aucune
des six révisions que le candidat ajoute n'y figure.

**Ce que le candidat ajoute, et où il n'y a plus qu'une tête.** Le graphe du
candidat porte **23 fichiers** de migration, soit six de plus que `39ad8d0`, et
une **seule** tête — `f3a4b5c60708`. Le point de branchement est
`c7d8e9fa0102`, le schéma de ce qui tourne. Deux tranches en partent :

- la **tranche des plans** — `d8e9fa010203` → `e2f3a4b50607` →
  `a4b5c6d70809` → `b5c6d7e8090a` ;
- la **tranche de la connexion** — `d1e2f3a40506`, seule de sa branche.

La migration de fusion `f3a4b5c60708` réunit les deux **têtes** :
`down_revision = ("d1e2f3a40506", "b5c6d7e8090a")`. C'est cette citation, et
non celle d'un maillon intermédiaire, qui laisse une seule tête ;
`docs/FUSION_DES_MIGRATIONS.md` dit ce qui le détecte. La montée depuis
`39ad8d0` est éprouvée sur SQLite **et** sur PostgreSQL —
`c7d8e9fa0102 → f3a4b5c60708`, six révisions, une tête — par
`scripts/epreuve_montee_depuis_preproduction.py`, que l'atelier
« API (PostgreSQL + PostGIS) » rejoue à chaque commit.

**Le code applicatif ancien, lui, tournerait très bien sur le schéma avancé** —
mais il faut dire exactement ce que les migrations de la tranche des plans font,
parce qu'une version précédente de ce paragraphe parlait de colonnes ajoutées
qui n'existent pas. Inventaire relu dans les fichiers :

- **deux tables ajoutées** : `plan_calibrations` (le seul `op.create_table` de
  `…0008…`, dans son `upgrade()`) ; et aucune dans `…0007…`.
- **aucune colonne ajoutée par `…0007…`.** Ce qui bouge sur les colonnes y est
  l'inverse d'un ajout : **sept colonnes existantes deviennent nullables**
  (tuple `COLONNES_DE_TEXTE` — `page`, `char_start`, `char_end`, `x0`, `y0`,
  `x1`, `y1` — et la boucle `lot.alter_column(…, nullable=True)` de son
  `upgrade()`).
- **deux colonnes ajoutées par `…0011…`** (`b5c6d7e8090a`) :
  `boq_items.source_proposal_id` et `boq_items.source_mesure`, toutes deux
  `nullable=True` (les deux `op.add_column` de son `upgrade()`). L'ancien code
  ne les écrit pas, et rien ne l'y oblige.
- **trois contraintes `CHECK` NOUVELLES** sur `source_citations`, posées par
  `…0007…` (tuple `CONTRAINTES_CITATION`) : `ck_source_citation_ancrage`,
  `ck_source_citation_bbox_complete`, `ck_source_citation_reperes_cao_nonempty`.
  **Ce sont les seules qui pourraient refuser une écriture de l'ancien code**,
  donc exactement celles qu'il fallait nommer pour établir la conclusion de ce
  paragraphe.
- **deux relâchements sémantiques** : `ck_document_step_run_step` passe de onze
  à quinze étapes (`…0007…`, tuple `ETAPES`), et `ck_source_citation_ancrage`
  passe de `ANCRAGE_AVANT` à `ANCRAGE_APRES` (`…0008…`).
- **trois réécritures par `CAST`**, qui ne changent pas ce qui est permis mais
  le rendent vrai sur les deux moteurs : `ck_source_citation_bbox`,
  `ck_source_citation_confidence`, `ck_extraction_proposal_confidence`
  (`…0007…`, `CONDITION_BBOX_APRES` et `CONDITION_CONFIANCE_APRES`).
- **un déclencheur relâché** par `…0010…` (`a4b5c6d70809`) : une révision de
  document publiée devient **supprimable**, à la seule condition d'une purge en
  `executing` encore autorisée selon l'horloge de la base. La **modification**
  reste refusée sans exception.

La conclusion se vérifie sur les trois contraintes neuves, et c'est là qu'il
fallait la vérifier : l'ancien code écrivait `page`, `char_start`, `char_end` et
`x0`..`y1` tous non nuls, ce qui les satisfait toutes les trois. Rien n'est
retiré, rien n'est renommé, aucun `NOT NULL` n'est posé à la montée. Le blocage
n'est donc pas un blocage de compatibilité : c'est **le service `migrate` seul**
qui empêche le retour arrière.

### Les trois voies de sortie

#### Voie A — démarrer `api` sans le service `migrate`

**La définition de `mc` d'abord, ici et non trois sections plus loin** : c'est la
première ligne qu'on tape pendant la panne, et les deux `-f` ne s'improvisent
pas. En oublier un recrée le conteneur `proxy` **sans ses étiquettes** — voir
l'étape 3 de la section 2, qui explique pourquoi.

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
| **Coûte** | un état que la commande documentée ne reproduit plus : **tout `mc up -d` ultérieur sans `--no-deps` rejoue `migrate`**, qui échouera de nouveau. C'est un sursis, pas un état stable. |
| **Perd** | rien. Ni la base, ni le volume, ni une ligne. |
| **Irréversible** | non. |

**Ce geste exact est déjà joué par le dépôt.** `ops/repetition_staging.sh`,
fonction `etape_restauration`, monte sa pile de restauration par
`docker compose --project-name "$PROJET_RESTAURE" "${COMPOSITIONS[@]}"
--env-file "$ENV_FICHIER" up -d --no-deps api`, et sa raison est écrite dans le
commentaire juste au-dessus : « `--no-deps db api` : ni proxy ni front, on ne
restaure pas pour servir ». C'est la même définition de service `api`, et cela
tourne à **chaque « Répétition complète »** — l'un des douze contrôles verts
cités en tête de ce document.

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
| **Coûte** | la table `plan_calibrations` et ses deux index — la fin du `downgrade()` de `…0008…` : deux `op.drop_index` puis `op.drop_table("plan_calibrations")`. Et, en amont, les deux colonnes de provenance de `boq_items` — `op.drop_column` de `source_mesure` puis de `source_proposal_id`, dans le `downgrade()` de `…0011…`, **sans aucune garde**. Après la descente, `migrate` de l'ancienne image réussit : la base est déjà à `c7d8e9fa0102`, il n'a rien à faire. |
| **Perd** | les calibrations déclarées, s'il y en a, et la provenance de toute quantité reprise d'un plan dans un bordereau. |
| **Irréversible** | oui. Ni `plan_calibrations` ni les deux colonnes de provenance ne se reconstituent. |

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

Éprouvé sur une même base, en deux essais — **et sur l'arbre d'alors, qui ne
portait pas encore `a4b5c6d70809` ni `b5c6d7e8090a`** :

- **sans aucune citation page + boîte** : `alembic downgrade c7d8e9fa0102` →
  code **0**, base ramenée à `c7d8e9fa0102`. Le journal montre qu'une seule
  commande traite **les deux branches** : `f3a4b5c60708 → d1e2f3a40506`,
  `d1e2f3a40506 → c7d8e9fa0102`, `e2f3a4b50607 → d8e9fa010203`,
  `d8e9fa010203 → c7d8e9fa0102`.
- **avec UNE citation page + boîte** : code **1**,
  `RuntimeError: 1 citation(s) sont ancrées par page et boîte…`, et la base
  **inchangée** à `f3a4b5c60708`. Le retour à l'état initial vient du DDL
  transactionnel de PostgreSQL, qu'Alembic annonce lui-même en tête de journal
  (« Will assume transactional DDL ») : les descentes déjà jouées sont annulées
  avec l'erreur.

**Non remesuré sur le candidat.** Le même `alembic downgrade c7d8e9fa0102` y
traverse maintenant **six** révisions et non quatre : `a4b5c6d70809` et
`b5c6d7e8090a` s'insèrent entre `f3a4b5c60708` et `e2f3a4b50607`. Le mécanisme
est le même et les trois gardes sont intactes, mais le journal ci-dessus n'est
pas celui qu'on lira.

**Un trou qu'il faut nommer** : aucune garde ne compte les lignes de
`plan_calibrations` elles-mêmes, ni les lignes de bordereau qui portent une
provenance. Une calibration déclarée sans qu'aucune mesure n'en soit tirée ne
satisfait aucune des trois conditions ci-dessus, et `drop_table` l'emporterait ;
une quantité reprise dans un bordereau perd sa provenance sans qu'un refus
s'y oppose. Ce qui les protège aujourd'hui, c'est le refus de la migration **en
aval** et le DDL transactionnel — pas une garde écrite pour elles. Sur un moteur
sans DDL transactionnel, elles seraient parties.

#### Voie C — restaurer la sauvegarde prise avant le déploiement

| | |
| --- | --- |
| **Exige** | une sauvegarde prise **avant**, et **vérifiée** (section 3). Et un geste que le dépôt ne fournit pas : voir ci-dessous. |
| **Coûte** | une interruption de service le temps de la restauration, et une procédure à écrire. |
| **Perd** | **tout ce qui a été écrit en base depuis la sauvegarde** : mesures, calibrations, devis, clients, et les événements de la chaîne d'audit. Sur le volume, rien n'est perdu mais rien n'est remplacé non plus : le `tar` se détare **par-dessus** (`ops/restaurer.sh`, l'étape qui détare dans le conteneur `api`), donc les fichiers ajoutés depuis restent. L'état obtenu est un mélange : base d'avant, volume d'avant **plus** les fichiers d'après. |
| **Irréversible** | oui, dès que la base en service est écrasée. **Prendre une seconde sauvegarde juste avant de restaurer la première** est la seule façon de pouvoir encore changer d'avis. |

**Ce que le dépôt ne fournit pas, et c'est le manque le plus grave de cette
procédure :** `ops/restaurer.sh` **refuse** de restaurer dans la pile en
service. Il exige un marqueur de jetabilité dans le nom de la base (la garde
`case "$CIBLE"` en tête du script, qui exige `restore`, `scratch`, `jetable` ou
`tmp` dans le nom) **et le même marqueur sur le nom du projet Compose** (la
garde `case "$RESTORE_COMPOSE_PROJECT"`). C'est un bon refus — mais il signifie
qu'**aucun script du dépôt ne sait remettre une sauvegarde en service**. Le
script est un script d'exercice. Le jour où la voie C est la seule, le geste est
à improviser sous pression, à la main, dans le conteneur `db`. C'est à écrire
avant, pas pendant.

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

**Ce que `docs/EXPLOITATION.md` en dit désormais.** Une version précédente de ce
document reprochait à `docs/EXPLOITATION.md` d'affirmer qu'un retour arrière par
image « suffit, rien d'autre à faire ». Vérifié sur le candidat : ce document a
été réécrit, sa section « Retour arrière » porte un encadré qui nomme ce faux et
le corrige, et ses sections « Pourquoi l'image seule ne suffit pas » et « Les
trois sorties, et ce que chacune coûte » disent la même chose que celle-ci,
avec la même mesure du code 255. **Le document qu'on ouvrira pendant l'incident
n'envoie plus dans le mur.** Ce qui reste à y corriger est hors du retour
arrière, et la section 5 le dit.

---

## 2. Le déploiement : les étapes, les commandes, et le contrôle entre chacune

Ces étapes sont les gestes 9 à 13 de `docs/FICHE_DE_DECISION_MISE_EN_LIGNE.md`,
section « La voie unique, de bout en bout ». Elles ne commencent qu'une fois la
fusion faite, le contenu comparé et la sauvegarde vérifiée.

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

**La montée du schéma, elle, est éprouvée avant de toucher la machine, et avec
des données.** `scripts/epreuve_montee_depuis_preproduction.py` monte une base au
schéma de `main` — avec les fichiers de `main` — puis y applique les migrations
du candidat. Avec `--avec-donnees`, il y écrit d'abord, **avec le code de
`main`**, un chantier complet — organisation, administrateur, client, projet,
bordereau, bibliothèque de prix et devis **gelé** (le jeu d'essai est dans
`scripts/jeu_d_essai_de_montee.py`) — puis vérifie après la montée que **dix-sept
lignes métier sont conservées sans une différence**, que le devis gelé rend le
même total et la même empreinte sous le moteur du candidat, et que la chaîne
d'audit se relit **et se continue**. C'est le geste exact de l'étape 4,
joué hors machine. L'atelier « API (PostgreSQL + PostGIS) » le rejoue à chaque
commit.

Ce que les deux contrôles voisins ne disent pas : `scripts/migration_roundtrip.py`
part d'une base **vide**, et `scripts/schema_drift_gate.py` compare l'**arrivée**
aux modèles. Ni l'un ni l'autre ne tombe si un `down_revision` cite un maillon
au lieu d'une tête — ce qui laisse la base à deux têtes — ni si une révision du
candidat suppose une table que la version en service n'a pas.

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

Vérifié sur le candidat : `docs/EXPLOITATION.md` (section « Les noms ») et
`infra/staging.env.example` donnent bien ces deux noms. Une version précédente
de ce document signalait qu'ils montraient encore `metreo-api`, un nom
d'image qui n'existe pas : ce n'est plus le cas.

*Contrôle avant de continuer* : que le résumé de l'atelier affiche bien les deux
lignes, et que l'image `web` porte `HOSTNAME` :

```
docker image inspect -f '{{range .Config.Env}}{{println .}}{{end}}' \
  ghcr.io/hermesprojet/devis-web:<sha> | grep -x 'HOSTNAME=0.0.0.0'
```

Sans cette variable, Next se lie à l'adresse du conteneur, la sonde échoue, le
conteneur ne devient jamais `healthy` — et le proxy de devant, lui, ne voit rien
d'anormal. Vérifié sur le candidat : `HOSTNAME=0.0.0.0` **est** dans
`infra/web.Dockerfile`, dans son bloc `ENV`, avec le commentaire qui dit
pourquoi il n'est pas décoratif. Il est absent de `39ad8d0` : c'est donc
l'image publiée depuis `main` après fusion qui le portera, et pas celle qui sert
aujourd'hui.

### Étape 2 — noter le SHA sortant, puis sauvegarder

Dans cet ordre, et avant de toucher à `infra/staging.env`. Voir section 3. C'est
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
`infra/staging.env.example` ne la donne qu'en commentaire. Le
`infra/staging.env` de la machine doit donc déjà la porter — la pile tourne,
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

| Ce qu'on vérifie | Commande | Codes de sortie |
| --- | --- | --- |
| Les six maillons derrière le proxy | `ops/verifier_deploiement.sh` | `0`, `1`, ou `2` — et `2` signifie, dans les mots exacts de son en-tête : « 2 Docker injoignable — rien n'a été contrôlé. » |
| La disponibilité vue de l'extérieur | `ops/verifier_disponibilite.sh https://<domaine>` | `0` disponible, `1` dégradé, `2` indisponible (le `case "$etat"` final du script) |

`ops/verifier_deploiement.sh` **est sur le candidat** — une version précédente
de ce document le disait absent. Il contrôle six maillons, dans l'ordre de ses
propres titres : « 1. Les conteneurs, aux yeux de Docker » ; « 2. Caddy, joint
en local avec le bon Host » ; « 3. Le chemin que le proxy de devant emprunte :
l'IP du conteneur » ; « 4. Le certificat réellement présenté sur le 443 de cette
machine » ; « 5. Vu de l'extérieur, en HTTPS, par le nom public » ; « 6. Ce que
le proxy de devant dit de l'émission du certificat ».

**Ne lancez pas `ops/verifier_disponibilite.sh` nu contre `127.0.0.1:8081`.** Le
Caddyfile n'ouvre qu'un site, celui de `PUBLIC_HOST` ; une requête portant un
autre `Host` n'est pas servie par ce bloc, **et le script conclurait
« INDISPONIBLE » sur une pile saine**. C'est aussi ce que dit
`docs/EXPLOITATION.md`, section « Derrière un proxy déjà en place », qui donne
en plus la sortie de secours : `METREO_HOST_HEADER=<domaine>`.

Pour le diagnostic local, gardez le bon `Host`, **mais ne regardez pas que le
code de réponse** : à un `Host` qu'il ne sert pas, Caddy répond un `200`
**vide**, pas une erreur. Un `curl` qui n'inspecte ni les en-têtes ni le corps
rend donc le **même** `200` dans les deux cas qu'il est censé séparer. La forme
qui tranche :

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
mesure. C'est le seul geste qui éprouve la tranche. Le parcours écran par écran
est `docs/ESSAI_UTILISATEUR_PDF.md` ; ce qu'il faut relever sur vos propres
plans est `docs/VALIDATION_SUR_PLANS_REELS.md`.

> **Et ce geste ferme la voie B de la section 1.** Dès que ce plan est affiché,
> `page_render` est enregistré, et le schéma ne redescend plus.

---

## 3. La sauvegarde AVANT : base, volume, et comment savoir qu'elle vaut quelque chose

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
| **Le certificat TLS**, quand il vit chez le proxy de devant (`acme.json` de Traefik) | `docs/EXPLOITATION.md`, section « Derrière un proxy déjà en place » : « qu'aucune sauvegarde Metreo ne couvre » |
| **`infra/staging.env` lui-même** — secrets OIDC, `METREO_JWT_SECRET`, mot de passe PostgreSQL. Non versionné, et le script ne l'archive pas | absence dans `ops/sauvegarder.sh` |
| **Les plans réels déposés hors application** (par exemple `/opt/metreo-plans/`) | `docs/PLANS_REELS.md` : « la sauvegarde chiffrée le couvre **si on l'y ajoute explicitement** » |

### Comment vérifier qu'elle est exploitable

Une sauvegarde non vérifiée n'en est pas une. La vérification est une
**restauration dans une pile jetable**, suivie de
`ops/verifier_restauration.py`, qui ne se contente pas d'une absence d'erreur :
il compte, il vérifie la **chaîne d'audit organisation par organisation** et il
refuse de conclure si aucun compte n'a d'appartenance active.

> **Cet exercice est faisable aujourd'hui, et rien ne le retient plus.** Une
> version précédente de ce document l'interdisait jusqu'à la fusion d'une
> demande. Vérifié sur le candidat, les deux manques qu'elle invoquait sont
> comblés : `infra/docker-compose.jetable.yml` **existe**, et `ops/restaurer.sh`
> porte **deux** gardes de jetabilité — sur le nom de la base
> (`case "$CIBLE"`) et sur le nom du projet Compose
> (`case "$RESTORE_COMPOSE_PROJECT"`) — ainsi qu'une fonction `valeur_env`.
> Interdire l'exercice bloquait la seule vérification qui prouve qu'une
> sauvegarde vaut quelque chose.

Pourquoi `infra/docker-compose.jetable.yml` n'est pas une commodité : il pose
`ports: !override []` et `labels: !override {}`. Sans lui, une seconde pile
montée avec la seule composition de base tenterait de prendre 80 et 443 —
collision avec le proxy en place ; et avec la surcouche `derriere-proxy`, elle
porterait **les mêmes étiquettes Traefik que la pile en service**, donc la règle
`Host()` du domaine réel (`infra/docker-compose.derriere-proxy.yml`). Traefik
pourrait router le trafic du site vers la pile de restauration.

Pourquoi la garde sur le nom de projet compte autant que celle sur le nom de la
base : le stockage des fichiers est détaré dans le conteneur `api` **du projet
nommé**. Taper `RESTORE_COMPOSE_PROJECT=metreo-staging` fusionnerait l'archive
par-dessus les pièces jointes vivantes, **sans un message**. La base, elle,
serait épargnée : elle est recréée à part. Le refus porte donc sur les deux noms.

Le bloc est celui que `infra/docker-compose.jetable.yml` documente lui-même dans
son en-tête d'usage :

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

`RESTORE_ENV_FILE` est lue par le script, et non seulement passée à Compose :
la fonction `valeur_env` l'extrait par `sed`, et ne la *source* pas,
délibérément — « ce n'est pas un fichier shell, et une valeur non protégée y
exécuterait une commande ». C'est aussi la raison de ne jamais sourcer
`infra/staging.env` à la main : un mot de passe contenant `$(…)` s'exécuterait.

`ops/verifier_restauration.py` lit `METREO_DATABASE_URL`, qui doit pointer la
base **restaurée**.

### Une précision qui compte en mode `oidc`

`verifier_restauration.py` contrôle une **structure** — un compte actif avec une
appartenance active —, pas un accès. En `oidc`, entrer demande en plus une
identité liée. **Une base restaurée peut passer ce contrôle et rester fermée.**

---

## 4. Le retour arrière, cas par cas, et ce qui est irréversible

La section 1 porte le fait qui domine : **le retour arrière par image seule ne
remet pas l'API en service.** Les trois cas ci-dessous s'y rapportent.
`docs/FICHE_DE_DECISION_MISE_EN_LIGNE.md`, section « Retour arrière — et ce
qu'il coûte aux données de l'essai », en donne le résumé d'une page ; ce qui
suit est le détail.

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

`ops/verifier_deploiement.sh` donne le maillon exact ; à défaut, `mc ps -a` et
les sondes. Le tableau de `docs/EXPLOITATION.md`, section « Incident »,
sépare les quatre lectures : API tombée, base injoignable (**ne pas redémarrer
l'API**), configuration dégradée, front ou routage.

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

La dernière ligne nomme `a4b5c6d70809` parce que c'était la **tête** de l'arbre
le jour du relevé. Sur le candidat la tête est `f3a4b5c60708`, et c'est elle que
le message nommera — c'est d'ailleurs la mesure donnée en section 1. Le code de
sortie, lui, est le même : le refus ne dépend pas de laquelle des révisions
l'ancien arbre ignore.

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
2. **La mémoire.** Conteneur borné à **1 Go**
   (`infra/docker-compose.staging.yml`, `limits: { cpus: "2.0", memory: 1G }` du
   service `api`), plafond du processus fils à **1536 Mo**
   (`rendu_tuile.py`, constante `PLAFOND_MEMOIRE`) : le plafond du fils est
   **au-dessus** de celui du conteneur, donc c'est le cgroup qui mordra le
   premier. Symptôme `OOMKilled`, et `docs/EXPLOITATION.md`, section « Limites
   de ressources », rappelle que rien d'autre ne l'annonce. Le fils le plus
   lourd mesuré a culminé à **510 Mo** (`rendu_tuile.py`, en-tête du module),
   sur quatre plans réels hors dépôt — **des grands formats de 1 189 à
   1 690 mm de grand côté**. Je n'ai pas de mesure sur vos plans : c'est
   l'arbitrage qui reste ouvert, et la section 5 le redit.

**Le geste :** cas A.
- **Perd** : rien en base. Les mesures et calibrations déjà prises restent.
- **Ne se récupère pas** : rien. Les tuiles déjà écrites sur le volume sont
  re-dérivables ; ce n'est pas une perte.

### Cas C — « il faut redescendre le schéma »

C'est la voie B de la section 1. **À ne décider qu'explicitement, jamais dans
l'urgence, et jamais sans une sauvegarde fraîche et vérifiée.**

Les six révisions, dans l'ordre où la descente les rencontre :

| Révision | `downgrade()` | Ce qu'il détruit | Refuse-t-il ? |
| --- | --- | --- | --- |
| `f3a4b5c60708` fusion | ne fait rien | rien. « Redescendre rouvre les deux têtes, ce qui est correct : c'est exactement l'état d'avant la fusion. » | non |
| `d1e2f3a40506` réauth. | `drop_column` | la colonne `login_transactions.reauthentication_requested`. Aucune donnée métier : une transaction de connexion est éphémère | non |
| `b5c6d7e8090a` reprise au bordereau | retire deux contraintes, l'unicité, puis `drop_column` deux fois | `boq_items.source_proposal_id` et `boq_items.source_mesure` — **le lien ET l'empreinte** de toute quantité reprise d'un plan. La ligne de bordereau survit avec son montant ; sa provenance, non | **non** — et c'est le trou nommé en section 1, voie B |
| `a4b5c6d70809` purge des révisions publiées | recrée le déclencheur d'immuabilité sans exception | rien en données. Il **rend le défaut** : une organisation ayant déposé un document redevient indestructible. Le fichier le dit lui-même : « la descente rend le schéma d'avant, défaut compris » | non |
| `e2f3a4b50607` calibration | supprime la table et remet l'ancienne contrainte | **toute la table `plan_calibrations`** et ses deux index (fin du `downgrade()` de `…0008…` : deux `op.drop_index`, puis `op.drop_table("plan_calibrations")`) | **oui**, s'il reste une citation ancrée par page + boîte (le `raise RuntimeError` du même `downgrade()`) |
| `d8e9fa010203` citation de plan | ramène à onze étapes, repose les `NOT NULL` | l'état d'exécution des étapes de lecture de plan, et toute citation CAO | **oui, deux fois** (les deux `raise RuntimeError` du `downgrade()` de `…0007…`, sur `etapes_jouees` puis sur `citations_cao`) |

**Un détail d'ordre, et il est rassurant :** une seule commande traite les deux
branches — mesuré sur l'arbre d'alors, où `alembic downgrade c7d8e9fa0102`
descendait `f3a4b5c60708`, puis `d1e2f3a40506`, puis `e2f3a4b50607`, puis
`d8e9fa010203`. Et comme le DDL est transactionnel sur PostgreSQL, un refus en
fin de chaîne annule tout ce qui précède : mesuré, la base est restée
**inchangée à `f3a4b5c60708`** après le refus. Sur le candidat, la même commande
traverse les six révisions du tableau ci-dessus ; **non remesuré**.

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

**La provenance d'une quantité reprise ne se reconstitue pas non plus, et rien
ne la défend.** `source_mesure` est l'**empreinte** — valeur, unité,
incertitude, décision humaine et motif, figés à l'instant de la reprise — et
`source_proposal_id` est le **lien** qui remonte de la ligne de devis au plan, à
la page et à la boîte où la mesure a été pointée (en-tête du module `…0011…`).
Le `downgrade()` les retire tous les deux par `op.drop_column`, **sans aucune
garde**. Le montant reste au devis ; ce qui disparaît est la réponse à la
question « d'où vient ce chiffre ».

**Les refus ne sont pas des obstacles à contourner.** Ils disent : il reste en
base des mesures qu'un humain a peut-être validées, et il n'existe aucune valeur
honnête à écrire à leur place. Les supprimer pour débloquer la descente est une
décision humaine, et elle est définitive — hors restauration d'une sauvegarde
antérieure.

**Restaurer la sauvegarde est irréversible aussi**, dès que la base en service
est écrasée. Et le dépôt ne fournit aucun script pour le faire (section 1,
voie C). Prendre une seconde sauvegarde juste avant est la seule façon de
pouvoir encore changer d'avis.

**Les fichiers posés sur le volume, à l'inverse, ne sont défaits par aucune
voie.** Un plan déposé pendant l'essai reste sur le disque après un retour
arrière, et après une purge d'organisation
(`docs/REVUE_FINALE_DU_CANDIDAT.md`, A3).

---

## 5. Ce qui manque encore, et qu'aucune livraison ne comble

Les lignes ci-dessous ont été revérifiées une par une sur
`2d18299ee08b2a66bb54573d665f233a72aaf6ee`. Celles que des versions précédentes
de ce document rangeaient sous « absent de la branche de travail, présent
ailleurs » ont été **retirées** : les cinq fichiers qu'elles nommaient sont sur
le candidat.

| Ce qui manque | Conséquence | Source |
| --- | --- | --- |
| **Une procédure de restauration en service** | La seule sortie de la section 1 n'a pas de script. `ops/restaurer.sh` refuse par construction toute cible non jetable. | `ops/restaurer.sh`, gardes `case "$CIBLE"` et `case "$RESTORE_COMPOSE_PROJECT"` |
| **Toute sonde de la lecture de plans** | `/live` et `/health` restent verts avec une API incapable d'ouvrir un PDF. | `infra/api.Dockerfile`, directive `HEALTHCHECK` ; `ops/verifier_disponibilite.sh`, ses deux sondes HTTP — `/api/v1/live` et `/api/v1/health` |
| **La répétition relancée toute seule sur `main` après cette tranche** | Sur `push: main`, le workflow ne se déclenche que pour `infra/**`, `ops/**` et lui-même — **pas pour `apps/api/alembic/**`**. D'où le dernier geste de la fiche de décision : la relancer **à la main** sur `main`. | `.github/workflows/repetition-staging.yml`, filtre `paths` du déclencheur `push` |
| **Une valeur mesurée pour la limite mémoire de `api`** | Conteneur à 1 Go, plafond du fils à 1536 Mo, et 510 Mo mesurés sur quatre plans hors dépôt. Soit on relève la limite avant de servir de vrais plans, soit on accepte un `OOMKilled` que rien n'annonce. Je n'ai pas de mesure sur vos plans. | `infra/docker-compose.staging.yml`, `limits` du service `api` ; `rendu_tuile.py`, constante `PLAFOND_MEMOIRE` et en-tête du module |
| **Toute purge des artefacts dérivés sur le volume** | Aperçus et tuiles vivent sous `rendus-de-plan/` (`services/rendu_de_plan.py`, constante `DOSSIER_RENDUS`) dans `METREO_STORAGE_ROOT`. `ops/sauvegarder.sh` archive **tout** `/var/lib/metreo` : les sauvegardes grossissent de données re-dérivables. Les seuls plafonds sont par révision — 500 tuiles ou 64 Mio (`services/tuiles.py`, constantes `PLAFOND_DE_TUILES_PAR_REVISION` et `PLAFOND_D_OCTETS_PAR_REVISION`). Aucun plafond global, aucune purge. | — |
| **La vérification de ces artefacts par la répétition** | `ops/repetition_staging.sh`, fonction `empreintes_du_volume`, ne hache que `/var/lib/metreo/documents`. Rendus et tuiles ne passent par aucun aller-retour de sauvegarde. | — |
| **`PUBLIC_DOMAIN` comme ligne active d'un fichier d'exemple** | La surcouche l'exige (`infra/docker-compose.derriere-proxy.yml`, règle `Host()` des étiquettes Traefik), et `infra/staging.env.example` ne la donne qu'en commentaire. Un nouveau déploiement échoue sur un message de Compose. | `infra/staging.env.example` |
| **Des sauvegardes distantes** | Les archives restent sur la machine sauvegardée. Une machine perdue les perd toutes. | `docs/EXPLOITATION.md`, section « Ce qui manque encore » |
| **Une supervision réelle** | Les **onze** seuils du tableau décrivent ce qu'il faut surveiller ; aucun outil ne les applique. (Onze : 5xx ; 4xx hors 401/403/404 ; indisponibilité ; base injoignable ; service `degraded` ; latence de `/health` ; disque `db-data` ; disque `api-storage` ; sauvegarde ; restauration éprouvée ; certificat TLS.) | `docs/EXPLOITATION.md`, section « Seuils recommandés » |
| **Une connexion de bout en bout constatée** | La machine répond depuis le 16 septembre 2026, mais personne n'y est encore entré. Aucun test ne peut exercer le parcours contre un vrai fournisseur d'identité. | `docs/EXPLOITATION.md`, section « Ce qui manque encore » |
| **Les décisions juridiques** | Conservation, sous-traitance, localisation des données, information des personnes. | `docs/EXPLOITATION.md`, section « Ce qui manque encore » |
| **Tout atelier de déploiement** | Rien dans `.github/workflows/` ne touche le VPS. Les étapes 2 à 6 de la section 2 sont manuelles. | `ls .github/workflows/` |

**Deux énoncés encore faux dans `docs/EXPLOITATION.md`**, relus sur le candidat
et qui n'ont rien à voir avec le retour arrière :

- section « Limites de ressources », la justification du plafond de `api` —
  « calcul déterministe, pas de traitement d'image ». L'API rend désormais des
  images : aperçus de page et tuiles de détail. La justification est tombée avec
  la tranche, et c'est la ligne dont dépend l'arbitrage mémoire ci-dessus.
- section « Les pièces de chantier, sur le volume » — « aucun antivirus, aucune
  OCR, aucune extraction, aucun rendu à l'écran ». La tranche affiche et mesure
  des PDF à l'écran.

Aucun des deux n'empêche de déployer, et aucun des deux n'égare pendant un
incident. Ils sont écrits ici pour ne pas être perdus.
