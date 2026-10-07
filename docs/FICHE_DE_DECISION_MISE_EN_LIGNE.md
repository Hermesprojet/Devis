# Fiche de décision — mettre en ligne la lecture de plans

Une page, pour décider. **C'est le seul document qui décrit la voie de
livraison.** Le détail opérationnel est dans `docs/EXPLOITATION.md`, les cas
particuliers du retour arrière dans `docs/MISE_EN_LIGNE_LECTURE_DE_PLANS.md`,
et ce qui a été corrigé dans `docs/REVUE_FINALE_DU_CANDIDAT.md`.

> **Il n'y a plus qu'une voie.** Jusqu'ici, trois documents en décrivaient
> trois, et elles se contredisaient. L'une d'elles — une séquence de onze
> fusions de demandes empilées — n'était pas seulement périmée : elle était
> **inexécutable**, parce que la migration de fusion `f3a4b5c60708` cite
> `b5c6d7e8090a`, une révision qu'aucune des onze étapes n'amenait sur `main`.
> Elle est retirée. Ce qui suit la remplace entièrement.

---

## 1. Version concernée

| | |
| --- | --- |
| **Le candidat, à livrer** | branche `claude/candidat-integre`. Son SHA se lit par `git rev-parse origin/claude/candidat-integre` — il n'est pas recopié ici, parce qu'un SHA écrit dans le commit qu'il désigne est impossible, et qu'un SHA recopié à la main se périme en silence |
| **Ce qui tourne aujourd'hui** | `39ad8d0`, en préproduction depuis le 16 septembre 2026. **C'est la seule cible de retour arrière qui existe** : le travail « Publier les images » n'a tourné qu'une fois, sur ce SHA |
| **Ce qui sera réellement déployé** | `main`, **après la fusion de la demande de livraison** — pas une branche de candidat |

**Le candidat réunit tout.** `claude/candidat-complet` (le brouillon #90,
`daeb7f0`), `claude/candidat-corrige` (`483176a`) et
`claude/corrections-du-candidat` sont tous les trois **ancêtres** de la tête de
`claude/candidat-integre` — vérifiable par `git merge-base --is-ancestor`. Dire
« #90 n'est pas fusionnée » ne veut donc pas dire que son contenu est absent :
il est là, et la demande de livraison l'apporte. Ce qui reste dehors est
`claude/integration-cinq-pr` (le brouillon #84), qui n'est **pas** ancêtre.

Les trois défauts bloquants de la revue sont corrigés : le bandeau qui annonçait
qu'aucune mesure n'était possible sur un PDF, les valeurs à dix décimales, et la
purge d'organisation — qui, vérification faite, **échouait entièrement** dès
qu'un document avait été déposé. Deux écarts d'incertitude sont corrigés avec
eux : la page en portrait et les tracés à plusieurs sommets. S'y ajoutent une
seconde passe (`docs/REVUE_FINALE_DU_CANDIDAT.md`, section F) et la reprise
d'une mesure au bordereau, suivie jusqu'au PDF du devis.

> **La tête d'Alembic.** Le graphe du candidat a **une seule tête**,
> `f3a4b5c60708`, et la migration de fusion cite bien
> `("d1e2f3a40506", "b5c6d7e8090a")` — la tête de chaque tranche, et non un de
> leurs maillons. Toute autre citation laisserait la base à deux têtes.
> `docs/FUSION_DES_MIGRATIONS.md` dit ce qui le détecte.
>
> **La montée depuis la préproduction est éprouvée, et avec des données.**
> `scripts/epreuve_montee_depuis_preproduction.py --avec-donnees` monte une base
> au schéma de `main` avec les fichiers de `main`, y écrit **avec le code de
> `main`** un chantier complet — organisation, administrateur, client, projet,
> bordereau, bibliothèque de prix et devis **gelé** — puis applique les
> migrations du candidat et vérifie ce que ce chantier est devenu. Mesuré sur
> les deux moteurs depuis `39ad8d0` : `c7d8e9fa0102 → f3a4b5c60708`, six
> révisions, une seule tête, **dix-sept lignes métier conservées sans une
> différence**, le devis gelé rendant le même total et la même empreinte sous le
> moteur du candidat, et la chaîne d'audit relue **et continuée**. L'atelier
> « API (PostgreSQL + PostGIS) » le rejoue à chaque commit.

---

## 2. La voie unique, de bout en bout

Treize gestes. Chacun a son contrôle ; aucun ne se saute.

| | Geste | Contrôle avant de passer au suivant |
| --- | --- | --- |
| 1 | **Ouvrir une demande de fusion** de `claude/candidat-integre` vers `main` | elle existe, et elle ne vise pas une autre base que `main` |
| 2 | **Les douze contrôles verts sur le SHA de tête de la demande** : les onze ateliers de `.github/workflows/ci.yml`, plus « Répétition de préproduction » déclenchée à la main (`workflow_dispatch`) | douze `success`, tous sur **le même SHA**. Un atelier vert sur un SHA antérieur ne prouve rien sur celui qu'on fusionne |
| 3 | **Fusionner** — votre décision, et elle seule | voir « comment fusionner » ci-dessous |
| 4 | **Comparer le contenu fusionné au candidat éprouvé** | sortie **vide** (section 3) |
| 5 | **Attester le contenu livré** : `ops/manifeste_release.py` | le manifeste est écrit, et porte le SHA de `main` |
| 6 | **Noter le SHA sortant** — celui qui tourne aujourd'hui, `39ad8d0` | écrit quelque part **hors de la machine** |
| 7 | **Sauvegarder** (section 4) | le dump n'est pas vide, et le script n'a pas rendu 1 |
| 8 | **Vérifier la restauration** dans une pile jetable (section 5) | `ops/verifier_restauration.py` conclut, et ne refuse pas de conclure |
| 9 | **Construire et publier les deux images** — **avec votre accord explicite** | l'atelier « Images Docker » vert sur le SHA publié |
| 10 | **Remplacer les deux images, monter la pile** | `migrate` sort en **0** ; sans quoi `api` ne démarre pas du tout |
| 11 | `ops/verifier_deploiement.sh` | chaque maillon vert (sortie 0 ; 2 = Docker injoignable) |
| 12 | **Déposer un plan, le lire, le mesurer** | l'écran répond — c'est la seule vérification qui vaut |
| 13 | **Relancer la répétition sur `main`**, à la main | elle ne part **pas** toute seule : `repetition-staging.yml` ne filtre le `push` sur `main` que pour `infra/**`, `ops/**` et le workflow lui-même |

**Comment fusionner.** Un **merge commit**, ni `squash` ni `rebase`. Les deux
autres réécrivent les SHA : l'historique du candidat disparaît, et la
comparaison de l'étape 4 ne peut plus s'appuyer que sur le contenu. Avec un
merge commit, le SHA éprouvé reste dans l'historique de `main` et la
comparaison porte sur les deux à la fois.

Les commandes exactes de déploiement, avec leurs `-f` et leurs surcouches, sont
dans `docs/EXPLOITATION.md`, section « Déployer ».

---

## 3. Comparer ce qui est fusionné à ce qui a été éprouvé

C'est l'étape 4, et c'est elle qui ferme l'écart entre « les contrôles sont
verts » et « ce qui est en ligne est ce qui a été contrôlé ».

```bash
git fetch origin main claude/candidat-integre
git diff --stat origin/main origin/claude/candidat-integre
```

**Sortie attendue : vide.** Toute ligne est une divergence entre ce qui a été
éprouvé et ce qui serait mis en ligne, et doit être expliquée avant d'aller plus
loin. Les deux références portent `origin/` à dessein : sans lui, `git` prendrait
une branche locale homonyme, qui peut être en retard de plusieurs commits.

Et l'attestation du contenu, qui ne dépend d'aucune branche :

```bash
python ops/manifeste_release.py \
  --sortie var/livraison/manifeste-release.json \
  --sha "$(git rev-parse origin/main)" \
  --horodatage "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

Le manifeste mesure ce qu'il atteste : empreintes des images, tête d'Alembic lue
dans le graphe, sommes des fichiers de déploiement. Il ne porte **aucune valeur**
de variable d'environnement, seulement des noms — il se range donc à côté de la
sauvegarde sans précaution particulière.

---

## 4. Sauvegarde — avant toute chose

```bash
cd <racine-du-clone-sur-la-machine>
BACKUP_COMPOSE_PROJECT=metreo-staging \
BACKUP_COMPOSE_FILES="-f $PWD/infra/docker-compose.staging.yml -f $PWD/infra/docker-compose.derriere-proxy.yml" \
ops/sauvegarder.sh /var/backups/metreo
```

Les deux variables ne sont pas facultatives : sans elles le script ne résout pas
la pile réelle. `POSTGRES_USER` et `POSTGRES_DB` n'ont pas à être exportés — le
script lit `infra/staging.env`.

**Couvert** : la base (`pg_dump --format=custom --no-owner --no-privileges`) et
**tout** le volume de stockage, originaux compris.
**Non couvert, et à sauvegarder à la main** : `infra/staging.env` (secrets OIDC,
`METREO_JWT_SECRET`, mot de passe PostgreSQL), le certificat TLS du proxy de
devant, et tout plan déposé hors de l'application.

Deux refus durs du script, à connaître : un dump vide est effacé et le script
sort en 1 ; un dépôt distant sans `BACKUP_AGE_RECIPIENT` est refusé.

---

## 5. Vérification de restauration — la seule qui prouve quelque chose

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
réel pourrait y être routé. Ce fichier **est sur le candidat** — l'exercice
n'attend plus rien.

`ops/restaurer.sh` porte deux gardes de jetabilité : sur le nom de la base et
sur le nom du projet Compose. Elles refusent de restaurer dans la pile en
service, et elles sont sur le candidat elles aussi.

`ops/verifier_restauration.py` ne se contente pas d'une absence d'erreur : il
compte, vérifie la chaîne d'audit organisation par organisation, et **refuse de
conclure** si aucun compte n'a d'appartenance active. Il lit
`METREO_DATABASE_URL`, qui doit pointer la base **restaurée**.

---

## 6. Retour arrière — et ce qu'il coûte aux données de l'essai

> **Remettre l'ancienne image ne remet pas le service en marche.** Mesuré :
> l'ancienne image lance `alembic upgrade head`, ne trouve pas `f3a4b5c60708`
> dans son propre arbre, et **sort en 255**. `api` en dépend par
> `condition: service_completed_successfully` : il ne démarre jamais. C'est vrai
> même si la migration est purement additive.

| Voie | Ce qu'elle fait | Données de l'essai |
| --- | --- | --- |
| **A — démarrer `api` sans `migrate`** | l'ancien code tourne sur le schéma neuf | **tout est conservé — mesuré, et non plus supposé.** `scripts/epreuve_retour_arriere.py` fait tourner l'API de `origin/main` sur le schéma neuf : **16 parcours sur 19 fonctionnent**, la chaîne d'audit écrite par la version neuve reste valide, et les mesures de l'essai sont toujours en base. Les 3 autres sont les deux routes qui n'existent pas dans l'ancienne version (404) et le refus de migrer (**255**), qui est la raison d'être de cette voie |
| **B — redescendre le schéma, puis revenir à l'ancienne image** | `alembic downgrade c7d8e9fa0102`, lancé par la **nouvelle** image (seule à contenir les fichiers de migration) | **refusé dès la PREMIÈRE mesure prise.** Un `RuntimeError` arrête la descente s'il reste une citation ancrée page + boîte, et le DDL étant transactionnel, la base reste intacte. Pour passer outre il faut détruire ces mesures — définitif |
| **C — restaurer la sauvegarde de l'étape 7** | la base revient à l'instant d'avant le déploiement | **tout ce qui a été créé depuis est perdu** : calibrations, mesures, décisions, mais aussi tout devis, client, poste ou prix saisi pendant la même fenêtre. Prendre une seconde sauvegarde juste avant est la seule façon de pouvoir encore changer d'avis |

**La cible de retour arrière est `39ad8d0`, et il n'y en a pas d'autre.** Les
images n'ont été publiées qu'une fois, le 16 septembre 2026, sur ce SHA.

**Ce que la voie B détruit si on la force** : `plan_calibrations` en entier —
les deux points pointés, la distance réelle déclarée, l'unité, la résolution du
pointage, le motif et l'auteur. Le facteur d'échelle n'est pas stocké, il se
recalcule depuis ces champs : détruire la table détruit donc **la seule trace de
l'échelle déclarée par un humain**. Recalibrer ensuite ne la rend pas — c'est
une autre déclaration, par une autre personne, à un autre moment.

**Ce qu'aucune voie ne défait** : les fichiers posés sur le volume. Un plan
déposé pendant l'essai reste sur le disque après un retour arrière, et après une
purge d'organisation (`docs/REVUE_FINALE_DU_CANDIDAT.md`, A3).

Le détail cas par cas — « ça ne démarre pas », « ça démarre mais les plans ne se
lisent pas », « il faut redescendre le schéma » — est dans
`docs/MISE_EN_LIGNE_LECTURE_DE_PLANS.md`, section « Le retour arrière, cas par
cas ».

---

## 7. Ce que cette mise en ligne ne promet pas

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

Trois documents portent l'essai sur vos plans, et ils ne se remplacent pas :

- **`docs/ESSAI_GUIDE_PLANS_REELS.md`** — l'essai court, les sept gestes du PDF
  au devis, les fichiers et les références à fournir, et la fiche d'une page à
  rendre. **C'est par là qu'on commence.**
- `docs/VALIDATION_SUR_PLANS_REELS.md` — la méthode chiffrée : le tableau
  attendu / mesuré / écart, la répétition qui lève le biais de pointage, et la
  ligne « votre tolérance » laissée vide.
- `docs/ESSAI_UTILISATEUR_PDF.md` — le détail écran par écran, mille lignes,
  pour quand quelque chose ne se passe pas comme prévu.

Cette fiche ne les remplace pas.
