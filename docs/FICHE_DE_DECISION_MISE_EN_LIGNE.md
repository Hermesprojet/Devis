# Fiche de décision — mettre en ligne la lecture de plans

Une page, pour décider. Le détail, les preuves et les cas particuliers sont
dans `docs/MISE_EN_LIGNE_LECTURE_DE_PLANS.md` (procédure complète),
`docs/FUSION_DES_MIGRATIONS.md` (ordre des fusions) et
`docs/REVUE_FINALE_DU_CANDIDAT.md` (ce qui a été corrigé, et ce qui reste).

---

## 1. Version concernée

| | |
| --- | --- |
| **Version de référence**, pour comparer | `daeb7f0d7c2a12d47e1b941c0d79932a1e20d464` — branche `claude/candidat-complet` (#90, **brouillon**). Inchangée |
| **Candidat corrigé**, à déployer | branche `claude/candidat-corrige`. Son SHA se lit par `git rev-parse origin/claude/candidat-corrige` — il n'est pas recopié ici, parce qu'un SHA écrit dans le commit qu'il désigne est impossible, et qu'un SHA recopié à la main se périme en silence |
| **Les corrections seules** | branche `claude/corrections-du-candidat` |
| **Ce qui sera réellement déployé** | `main`, **après** les onze fusions — pas une branche de candidat |

Les trois défauts bloquants de la revue sont corrigés sur le candidat corrigé :
le bandeau qui annonçait qu'aucune mesure n'était possible sur un PDF, les
valeurs à dix décimales, et la purge d'organisation — qui, vérification faite,
**échouait entièrement** dès qu'un document avait été déposé, et pas seulement
sur les fichiers. Deux écarts d'incertitude sont corrigés avec eux : la page en
portrait et les tracés à plusieurs sommets.

**Une seconde passe s'y ajoute**, après relecture des captures
(`docs/REVUE_FINALE_DU_CANDIDAT.md`, section F) :

| | Ce que cela change pour la mise en ligne |
| --- | --- |
| **Le pointage est fermé** tant que l'image affichée n'est pas celle de la zone demandée | un point ne peut plus s'enregistrer ailleurs que là où il a été vu |
| **L'écart de mesure est divisé par cinq** : 0,020 % en longueur, 0,050 % en surface, contre 0,105 % et 0,250 % | l'écart est désormais **contenu** dans le ± affiché, ce qui n'était pas le cas |
| **La vérification chiffrée tombe à chaque livraison** | une régression de pointage ne peut plus passer les douze ateliers verts |
| **Le diagnostic « probablement scanné » est remplacé par une description** | l'écran ne qualifie plus de scan un plan vectoriel |
| **Une mesure tranchée alimente un bordereau**, puis le devis, puis le PDF — **et le parcours existe à l'écran** | **une migration de plus** : voir l'encadré ci-dessous |

```bash
git fetch origin main
git diff --stat origin/main claude/candidat-corrige
```

Sortie attendue après les fusions : **vide**. Toute ligne est une divergence
entre ce qui a été éprouvé et ce qui serait mis en ligne, et doit être expliquée
avant d'aller plus loin.

> **La tête d'Alembic a changé, deux fois.** La tranche des plans porte
> maintenant **deux** révisions de plus : `a4b5c6d70809` (la purge d'une
> révision publiée) puis `b5c6d7e8090a` (la provenance d'une ligne de
> bordereau). La migration de fusion `f3a4b5c60708` doit donc citer
> `("d1e2f3a40506", "b5c6d7e8090a")` — et non plus `e2f3a4b50607`, ni
> `a4b5c6d70809`. Toute autre citation laisse la base à deux têtes. Le contrôle
> de l'étape 1 ci-dessous le voit.
>
> `b5c6d7e8090a` est **purement additive** : deux colonnes nullables sur
> `boq_items`, une contrainte d'unicité et deux clés étrangères. Elle ne touche
> aucune ligne existante, et son `downgrade` ne détruit aucune ligne de
> bordereau — seulement la trace de leur origine.
>
> **La montée depuis la préproduction est éprouvée, et non supposée.**
> `scripts/epreuve_montee_depuis_preproduction.py` monte une base au schéma de
> `main` avec les fichiers de `main`, puis y applique les migrations du
> candidat. Mesuré sur les deux moteurs depuis `39ad8d0` :
> `c7d8e9fa0102 → f3a4b5c60708`, six révisions, une seule tête. L'atelier
> « API (PostgreSQL + PostGIS) » le rejoue à chaque commit.

## 2. Sauvegarde — avant toute chose

```bash
cd <racine-du-clone-sur-la-machine>
BACKUP_COMPOSE_PROJECT=metreo-staging \
BACKUP_COMPOSE_FILES="-f $PWD/infra/docker-compose.staging.yml -f $PWD/infra/docker-compose.derriere-proxy.yml" \
ops/sauvegarder.sh /var/backups/metreo
```

Les deux variables ne sont pas facultatives : sans elles le script ne résout
pas la pile réelle. `POSTGRES_USER` et `POSTGRES_DB` n'ont pas à être
exportés — le script lit `infra/staging.env`.

**Couvert** : la base (`pg_dump --format=custom`) et **tout** le volume de
stockage, originaux compris.
**Non couvert, et à sauvegarder à la main** : `infra/staging.env` (secrets
OIDC, `METREO_JWT_SECRET`, mot de passe PostgreSQL), le certificat TLS du proxy
de devant, et tout plan déposé hors de l'application.

---

## 3. Vérification de restauration — la seule qui prouve quelque chose

Une sauvegarde non restaurée n'est pas une sauvegarde. L'exercice se fait dans
une pile **jetable**, jamais sur celle en service :

```bash
docker compose -p metreo-restauration \
  -f infra/docker-compose.staging.yml \
  -f infra/docker-compose.jetable.yml up -d db
# restaurer le dump dans CETTE pile, puis :
python ops/verifier_restauration.py
```

`infra/docker-compose.jetable.yml` pose `ports: !override []` et
`labels: !override {}` : sans lui, la pile de restauration prendrait les ports
80/443 ou **les mêmes étiquettes Traefik que le site en service**, et le trafic
réel pourrait y être routé. Ce fichier arrive avec la #72 ; il est sur le
candidat.

`ops/verifier_restauration.py` ne se contente pas d'une absence d'erreur : il
compte, vérifie la chaîne d'audit organisation par organisation, et **refuse de
conclure** si aucun compte n'a d'appartenance active.

---

## 4. Déploiement

| Étape | Commande / geste | Contrôle avant de passer à la suivante |
| --- | --- | --- |
| 1 | Les onze fusions, dans l'ordre (`docs/FUSION_DES_MIGRATIONS.md` §4), **transfert de `f3a4b5c60708` compris à l'étape 6** | `cd apps/api && PYTHONPATH=src python -m alembic heads` → **une seule** ligne, `f3a4b5c60708`. Deux lignes = le second parent de la fusion n'a pas été remis à jour |
| 2 | Construire et publier les deux images — **avec votre accord explicite** | les ateliers « Images Docker » verts sur le SHA publié |
| 3 | **Noter le SHA sortant**, celui qui tourne aujourd'hui | écrit quelque part hors de la machine |
| 4 | Sauvegarder (section 2) | le dump n'est pas vide, et le script n'a pas rendu 1 |
| 5 | Remplacer les deux images, monter la pile | `migrate` sort en **0** ; sans quoi `api` ne démarre pas du tout |
| 6 | `ops/verifier_deploiement.sh` | chaque maillon vert |
| 7 | Déposer un plan, le lire, le mesurer | l'écran répond — c'est la seule vérification qui vaut |

Les commandes exactes, avec les `-f` et les surcouches, sont dans
`docs/MISE_EN_LIGNE_LECTURE_DE_PLANS.md` §4.

---

## 5. Retour arrière — et ce qu'il coûte aux données de l'essai

> **Remettre l'ancienne image ne remet pas le service en marche.** Mesuré :
> l'ancienne image lance `alembic upgrade head`, ne trouve pas
> `f3a4b5c60708` dans son propre arbre, et **sort en 255**. `api` en dépend par
> `condition: service_completed_successfully` : il ne démarre jamais. C'est
> vrai même si la migration est purement additive.

| Voie | Ce qu'elle fait | Données de l'essai |
| --- | --- | --- |
| **A — démarrer `api` sans `migrate`** | l'ancien code tourne sur le schéma neuf | **tout est conservé — mesuré, et non plus supposé.** `scripts/epreuve_retour_arriere.py` fait tourner l'API de `origin/main` sur le schéma neuf : **16 parcours sur 19 fonctionnent**, la chaîne d'audit écrite par la version neuve reste valide, et les mesures de l'essai sont toujours en base. Les 3 autres sont les deux routes qui n'existent pas dans l'ancienne version (404) et le refus de migrer (**255**), qui est la raison d'être de cette voie |
| **B — redescendre le schéma, puis revenir à l'ancienne image** | `alembic downgrade c7d8e9fa0102`, lancé par la **nouvelle** image (seule à contenir les fichiers de migration) | **refusé dès la PREMIÈRE mesure prise.** Un `RuntimeError` arrête la descente s'il reste une citation ancrée page + boîte, et le DDL étant transactionnel, la base reste intacte. Pour passer outre il faut détruire ces mesures — définitif |
| **C — restaurer la sauvegarde de l'étape 4** | la base revient à l'instant d'avant le déploiement | **tout ce qui a été créé depuis est perdu** : calibrations, mesures, décisions, mais aussi tout devis, client, poste ou prix saisi pendant la même fenêtre. Prendre une seconde sauvegarde juste avant est la seule façon de pouvoir encore changer d'avis |

**Ce que la voie B détruit si on la force** : `plan_calibrations` en entier —
les deux points pointés, la distance réelle déclarée, l'unité, la résolution du
pointage, le motif et l'auteur. Le facteur d'échelle n'est pas stocké, il se
recalcule depuis ces champs : détruire la table détruit donc **la seule trace de
l'échelle déclarée par un humain**. Recalibrer ensuite ne la rend pas — c'est
une autre déclaration, par une autre personne, à un autre moment.

**Ce qu'aucune voie ne défait** : les fichiers posés sur le volume. Un plan
déposé pendant l'essai reste sur le disque après un retour arrière, et après une
purge d'organisation (`docs/REVUE_FINALE_DU_CANDIDAT.md`, A3).

---

## 6. Ce que cette mise en ligne ne promet pas

Le prototype réalise une **mesure assistée** : vous déclarez une échelle sur une
cote que vous connaissez, vous pointez, il rend un nombre avec son incertitude
propagée et la provenance de l'échelle utilisée. **Sa justesse sur vos plans
reste à établir.** Ce qui est éprouvé est l'arithmétique, sur des cas dont la
réponse est connue d'avance ; ce qui ne l'est pas est l'écart entre ce que
Metreo rend et ce que vos plans portent réellement.

**Le « ± » affiché est une incertitude type, à k = 1**, propagée depuis la seule
finesse du pixel sous la souris. Il ne couvre **aucune** des sept hypothèses qui
restent ouvertes : une mesure peut être fausse d'un facteur 2 avec un ± de
0,04 % si l'échelle saisie est fausse. Le détail est au § 2.0 de
`docs/PRECISION_DES_MESURES.md`.

La procédure d'essai sur vos plans est `docs/VALIDATION_SUR_PLANS_REELS.md` :
les fichiers nécessaires, les trois cotes à relever, le tableau attendu /
mesuré / écart, et la ligne « votre tolérance » laissée vide. Cette fiche ne la
remplace pas.
