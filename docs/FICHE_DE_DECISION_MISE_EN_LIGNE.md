# Fiche de décision — mettre en ligne la lecture de plans

Une page, pour décider. Le détail, les preuves et les cas particuliers sont
dans `docs/MISE_EN_LIGNE_LECTURE_DE_PLANS.md` (procédure complète),
`docs/FUSION_DES_MIGRATIONS.md` (ordre des fusions) et
`docs/REVUE_FINALE_DU_CANDIDAT.md` (ce qu'il reste à corriger).

---

## 1. Version concernée

| | |
| --- | --- |
| **Candidat éprouvé** | `daeb7f0d7c2a12d47e1b941c0d79932a1e20d464`, branche `claude/candidat-complet` (#90, **brouillon, jamais fusionné**) |
| **Ce qui sera déployé** | `main`, **après** les onze fusions — pas la branche du candidat |
| **Ce que le candidat prouve** | que ces quatorze PR, ensemble, passent les douze ateliers |
| **Ce qu'il ne prouve pas** | que `main` après fusion est identique. **À vérifier avant de construire les images** |

```bash
git fetch origin main
git diff --stat origin/main daeb7f0d7c2a12d47e1b941c0d79932a1e20d464
```

Sortie attendue : **vide**. Toute ligne est une divergence entre ce qui a été
éprouvé et ce qui serait mis en ligne, et doit être expliquée avant d'aller
plus loin.

> **Trois défauts d'interface sont ouverts** sur ce candidat
> (`docs/REVUE_FINALE_DU_CANDIDAT.md`, section A) : un bandeau qui annonce
> qu'aucune mesure n'est possible sur un PDF, des valeurs affichées à dix
> décimales à côté d'une incertitude en centimètres, et une purge qui laisse
> les originaux sur le volume. Aucun n'empêche le service de tourner ; les deux
> premiers rendent l'essai pénible à lire. **À trancher avant, pas après.**

---

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
| 1 | Les onze fusions, dans l'ordre (`docs/FUSION_DES_MIGRATIONS.md` §4), **transfert de `f3a4b5c60708` compris à l'étape 6** | `cd apps/api && PYTHONPATH=src python -m alembic heads` → **une seule** ligne |
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
| **A — démarrer `api` sans `migrate`** | l'ancien code tourne sur le schéma neuf | **tout est conservé.** Les tables neuves restent, l'ancien code les ignore. Le plus réversible, et le plus rapide |
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
Metreo rend et ce que vos plans portent réellement. C'est l'objet de
`docs/COTES_DE_REFERENCE.md`, et cette fiche ne le remplace pas.
