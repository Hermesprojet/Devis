# Essai utilisateur court — lire et mesurer un plan PDF

Sept gestes, dans cet ordre : **connexion · dépôt du plan · ouverture du PDF ·
calibration dans la loupe · mesure d'une longueur · mesure d'une surface ·
décision sur une valeur.**

Les libellés entre guillemets sont ceux qui s'affichent : ils viennent de
`apps/web/src/lib/i18n.ts` ou sont écrits en clair dans les composants. Les
messages de refus cités sont ceux que le serveur rédige ; l'écran les affiche
tels quels.

**Deux encadrés, deux couleurs, et la distinction compte au moment de relever
un incident.** Un refus d'API s'affiche en rouge, en gras, avec sous lui la
permission manquante quand il y en a une (`Feedback.tsx:13-16`, classe
`.notice.error`, `globals.css:104`). Un avertissement s'affiche en ambre
(`.notice.warning`, `globals.css:103`) : c'est le cas du refus de connexion sur
la page d'accueil (`app/page.tsx:174`), de l'avertissement de durée avant
l'analyse (`LecturePlan.tsx:282`) et de l'absence d'échelle. **Un encadré ambre
n'est pas forcément une panne.**

---

## Avant de commencer

### Ce qu'il faut avoir sous la main

| À prévoir | Pourquoi, et où c'est écrit |
| --- | --- |
| **Un plan au format PDF**, pas un `.dwg` | Le DWG est refusé à la réception, sur la signature de ses octets : « Le DWG n'est pas lu par Metreo. Exportez le plan en DXF ou en PDF depuis votre logiciel de dessin, puis déposez ce fichier. » (`document_storage.py:267-272`) |
| **Un PDF de moins de 12 Mio** | Au-delà, le dépôt réussit — le plafond de dépôt est 25 Mio (`config.py:72`) — mais l'analyse immédiate refuse, code `plan_trop_lourd` (`config.py:86`, `routers/documents.py:486-498`) |
| **Une cote connue, longue, lisible sur le plan** | C'est elle qui servira de base de calibration. Voir « La base de calibration, concrètement » : une base courte n'est pas refusée, et elle empoisonne toutes les mesures de la page |
| **La valeur réelle de cette cote et son unité** (mm, cm ou m — écrite, pas supposée) | Ce sont les trois seules unités du formulaire (`LecturePdf.tsx:43`) |
| **Un deuxième ouvrage à mesurer** (un mur) et **une surface** (une dalle, une pièce) | Étapes 5 et 6 |
| **Un navigateur à base Chromium** (Chrome, Edge) | C'est le seul navigateur que le parcours automatisé exerce (`apps/web/playwright.premier-devis.config.ts`, projet `chromium`, ligne 48). Firefox et Safari ne sont pas cassés — ils ne sont pas éprouvés |
| **Un chronomètre ou une horloge** | Les durées sont la seule information de diagnostic qu'une personne non développeur peut relever de façon fiable |
| **Un compte qui porte `document:write`** | Il faut ce droit pour déposer un document, pour lancer l'analyse (`LecturePlan.tsx:191`), pour calibrer et pour mesurer. Les deux routes posent la contrainte en dépendance, `Depends(require(Permission.DOCUMENT_WRITE))` : `apps/api/src/metreo_api/routers/documents.py`, fonctions `post_calibration` et `post_mesure_de_pdf` |
| **Un compte qui porte `document:validate`** | Il faut ce droit pour confirmer ou corriger une mesure (`routers/documents.py:324`). Voir la liste exacte des rôles ci-dessous |

### Les rôles qui portent `document:validate`

Ils sont **quatre**, et non deux. Lu dans `apps/api/src/metreo_api/security/roles.py` :

| Rôle | Libellé affiché | D'où vient le droit |
| --- | --- | --- |
| `org_admin` | Administrateur de l'entreprise | `set(_ALL)` — toutes les permissions (ligne 68) |
| `estimating_manager` | Responsable étude de prix | `_ALL - {USER_MANAGE}` (ligne 69) |
| `estimator` | Métreur / deviseur | `DOCUMENT_VALIDATE` listé explicitement (ligne 75) |
| `project_manager` | Chef de projet / conducteur | `DOCUMENT_VALIDATE` listé explicitement (ligne 90) |

`buyer` (Acheteur) et `viewer` (Lecteur / auditeur) ne le portent pas : ils
n'ont que `DOCUMENT_READ`, et `viewer` n'a même pas `DOCUMENT_WRITE` — il ne
peut donc ni déposer, ni analyser, ni mesurer.

### Trois choses à savoir avant de cliquer

**1. Un PDF ne porte aucune unité.** Ses coordonnées sont des points PostScript
(1/72 de pouce), qui décrivent la feuille et ne disent rien de l'ouvrage
(`mesures_pdf.py`, en-tête du module, lignes 7-12). Tant qu'une échelle n'a pas
été déclarée à la main sur **cette page**, aucune mesure n'est possible, et
l'écran l'annonce : « aucune : rien ne peut être mesuré ».

**2. On ne pointe jamais sur l'aperçu pleine page.** L'aperçu est borné à
2 000 pixels de grand côté (`lecture_pdf.py:80`). Sur les quatre plans du
propriétaire, un pixel d'aperçu vaut **0,594 à 0,845 mm de papier**
(`rendu_pdf.py:99-103`), soit **12 à 42 mm d'ouvrage** aux échelles 1:20 à 1:50
(`routers/documents.py:1048-1052`). L'aperçu sert à **trouver** la zone ; la
loupe sert à **pointer**.

**3. Une fois une mesure enregistrée, il n'y a plus de retour arrière par le
schéma.** Éprouvé sur une base PostgreSQL :

- sans aucune citation ancrée par page et boîte, `alembic downgrade c7d8e9fa0102`
  rend le code 0 et ramène la base ;
- avec **une seule** citation ancrée page + boîte — c'est ce qu'écrit la
  première mesure — la commande rend le code 1 sur
  `RuntimeError: 1 citation(s) sont ancrées par page et boîte…` et **la base
  reste inchangée**.

La garde est dans la migration elle-même
(`apps/api/alembic/versions/20261002_0008_calibration_d_un_plan_pdf.py:193-199`)
et elle est voulue : « Les retirer ferait disparaître des mesures de plan, et
les décisions humaines qui s'y rattachent. »

**Conséquence pratique : dès la première mesure enregistrée, la seule sortie est
la restauration de la sauvegarde prise avant le déploiement.** Prenez-la avant
de commencer l'essai.

Et le retour arrière par image ne remet pas l'API en service : éprouvé, une base
migrée en `f3a4b5c60708` puis `alembic upgrade head` depuis l'arbre de `main`
sort en **255** sur `Can't locate revision identified by 'f3a4b5c60708'` ; or
`infra/docker-compose.staging.yml` fait dépendre `api` de `migrate` par
`condition: service_completed_successfully`, donc l'API ne démarre pas.
`docs/EXPLOITATION.md` affirmait le contraire — « le retour arrière suffit,
rien d'autre à faire » — et a été corrigé le 2026-10-06 au vu de cette mesure.
Son § « Pourquoi l'image seule ne suffit pas » porte la démonstration et les
trois sorties réelles.

### Avertissement sur la fiche de cotes de référence

`docs/COTES_DE_REFERENCE.md` est **absent de la branche de travail
`claude/mesures-pdf`** (tête `8a17378` au moment de la rédaction). Il vit sur la
branche `claude/cotes-de-reference`, portée par la **PR #88**, et sur le
candidat.

Deux fichiers livrés le citent déjà :
`apps/api/src/metreo_api/services/mesures_pdf.py:82` et
`apps/web/e2e-premier-devis/suite-plan-pdf-mesures.spec.ts:32`. Ce sont les deux
seules références du dépôt, vérifié par recherche.

Pour le lire sans attendre la fusion :
`git show origin/claude/cotes-de-reference:docs/COTES_DE_REFERENCE.md`.

La PR #88 ne contient **que** ce document : la référence se résout dès qu'elle
est fusionnée. D'ici là, les deux renvois pointent dans le vide — et aucune
tolérance métier n'est écrite nulle part.

---

## Étape 1 — Connexion

### Le geste

Ouvrir l'adresse de Metreo. **Ce que vous voyez dépend de ce que `/health`
répond**, et la page ne propose rien avant d'avoir sa réponse.

La page d'accueil est bâtie de trois blocs **indépendants** — ce n'est pas un
choix entre deux modes (`apps/web/src/app/page.tsx:160-196`) :

| État | Ce qui s'affiche |
| --- | --- |
| `/health` n'a pas encore répondu | Sous le titre « **Connexion** », la seule mention « **Chargement…** ». Aucun bouton (ligne 180) |
| La liste contient `oidc` | Un encadré bleu « La connexion passe par le fournisseur d'identité de votre entreprise. Aucun mot de passe n'est conservé par Metreo. » puis un bouton « **Se connecter avec le compte de l'entreprise** » (lignes 182-189) |
| La liste est **vide** | Un encadré ambre « Ce déploiement n'offre aucune connexion depuis un navigateur. Il accepte des jetons émis ailleurs. » (lignes 191-193) |
| La liste contient `dev` | **Sous la première carte**, une deuxième carte avec l'encadré « Mode développement : la connexion se fait par adresse e-mail, sans mot de passe. Ce mode est refusé en environnement de production. », un champ « **Adresse e-mail** », un bouton « **Se connecter** » ; puis une troisième carte « **Comptes de démonstration** » (lignes 196-257) |

La page n'écrit nulle part que ces blocs s'excluent : elle teste
`methodes.includes('oidc')` et `methodes.includes('dev')` séparément
(lignes 161-162), et rien ne l'empêcherait d'afficher les deux.

Ce qui les rend exclusifs aujourd'hui est **le serveur**, pas l'écran :
`/health` construit la liste avec un `if/elif` sur un `auth_mode` qui n'a qu'une
valeur, donc la liste contient au plus un élément
(`apps/api/src/metreo_api/routers/meta.py:40-44`). `oidc` n'y entre que si le
mode est `oidc` **et** que la configuration OIDC est complète ; `dev` n'y entre
que si le mode est `dev` **et** que l'environnement n'est pas la production.

> **Si vous voyez la carte « Mode développement » sur un déploiement destiné à
> la production, arrêtez l'essai et signalez-le.** Elle signifie que
> `METREO_AUTH_MODE` vaut `dev` et que `METREO_ENVIRONMENT` ne vaut pas
> production.

### Le résultat attendu

Le bouton passe à « Connexion en cours… », puis l'adresse du navigateur se
termine par **`/projets`** et la liste des chantiers s'affiche.

### Ce qui peut mal tourner

`apps/web/src/lib/i18n.ts` porte **dix** clés `login.error.*` (lignes 45 à 63).
Les voici toutes.

| Message affiché | Clé | Ce que cela veut dire |
| --- | --- | --- |
| « Le fournisseur d'identité a refusé la connexion. » | `provider_refused` | Refus côté fournisseur : ni Metreo ni vos droits ne sont en cause |
| « La demande de connexion était incomplète. » | `invalid_request` | Le retour du fournisseur ne portait pas tout ce qu'il fallait. **Ce refus manquait au tableau précédent** |
| « Ce compte n'appartient à aucune organisation active. Demandez à un administrateur de vous ajouter. » | `no_membership` | Le compte existe, l'appartenance manque |
| « Ce compte n'est pas connu de Metreo. Un administrateur doit le créer avant la première connexion. » | `unknown_user` | Le compte doit être créé côté Metreo d'abord |
| « Le fournisseur d'identité n'a pas confirmé cette adresse e-mail. » | `unverified_email` | Adresse non vérifiée chez le fournisseur |
| « La demande de connexion a expiré. Recommencez : le bouton ci-dessus repart de zéro. » | `expired_state` | Page laissée trop longtemps. **Recommencez une fois avant de signaler** |
| « Cette demande de connexion a déjà servi. Recommencez depuis le bouton ci-dessus. » | `invalid_state` | Page rechargée ou lien rejoué. **Recommencez une fois** |
| « Le fournisseur d'identité a rendu une réponse déjà périmée. Recommencez. » | `token_expired` | Jeton d'identité déjà expiré à l'arrivée. **Ce refus manquait au tableau précédent** |
| « L'horloge du fournisseur d'identité et celle du serveur divergent trop. Recommencez ; si le refus persiste, prévenez votre administrateur. » | `token_not_yet_valid` | Décalage d'horloge. La tolérance est de 60 secondes (`config.py:68`) |
| « La connexion a échoué. » | `generic` | Refus non nommé |

> **« La connexion a échoué. » est aussi la phrase de repli.** Un code de refus
> que l'écran ne connaît pas retombe sur ce message
> (`app/page.tsx:18-22`). C'est donc celui qui demande le plus de relevé : le
> code brut n'est jamais affiché, mais il est **dans l'adresse**.

« Ce déploiement n'offre aucune connexion depuis un navigateur. Il accepte des
jetons émis ailleurs. » n'est **pas** un refus de connexion : c'est
`login.noMethod` (`i18n.ts:43-44`), affiché quand la liste des moyens est vide.
Il ne décrit pas un échec, il décrit une configuration.

### Ce qu'il faut relever

- L'**adresse complète** de la barre du navigateur au moment du refus, recopiée
  en entier : elle porte le code du refus dans son paramètre `login_error`.
- La **date et l'heure à la minute**.
- L'**adresse e-mail** utilisée.
- Le **message exact**, et une capture **de l'écran entier**, pas recadrée.
- **Laquelle des quatre dispositions** décrites plus haut était à l'écran.
- S'il s'agit de « expiré » ou « déjà servi » : **avez-vous recommencé ?** et le
  second essai a-t-il donné le même refus ?

---

## Étape 2 — Dépôt du plan

### Le geste

1. Dans la liste des chantiers, cliquer sur la **référence** du chantier.
2. Descendre jusqu'à la section « **Documents** ». *(Le formulaire de dépôt
   n'apparaît qu'avec le droit d'écrire, `DocumentsDuProjet.tsx:226`.)*
3. Dans « **Catégorie** », choisir « **Plan** ». **Le choix par défaut est
   « CCTP »** (`DocumentsDuProjet.tsx:37, 137`) : il faut le changer à la main.
4. « **Libellé (facultatif)** » : par exemple le nom de la façade. 200
   caractères au plus.
5. « **Fichier à joindre** » : choisir le PDF.

> **L'envoi part dès que le fichier est choisi.** Il n'y a pas de bouton
> « Déposer » (`DocumentsDuProjet.tsx:260-270`). C'est voulu, et surprenant la
> première fois.

> **Le sélecteur de fichiers filtre sur l'extension** :
> `.pdf,.png,.jpg,.jpeg,.dxf,.csv,.xlsx,.docx` (`DocumentsDuProjet.tsx:60`). Un
> `.dwg` n'apparaîtra donc pas tant que vous n'aurez pas choisi « tous les
> fichiers » dans la boîte de dialogue de votre système.

### Le résultat attendu

Un bandeau « **Envoi en cours — N %** » (`DocumentsDuProjet.tsx:285`), puis une
ligne s'ajoute au tableau des documents, portant le **nom exact du fichier**,
son type et sa taille. La phrase sous le formulaire rappelle : « PDF, PNG,
JPEG, DXF, CSV, XLSX ou DOCX. Le contenu est vérifié à la réception :
l'extension seule ne suffit pas. […] Un plan AutoCAD .dwg est REFUSÉ —
exportez-le en DXF ou en PDF. » (`i18n.ts:211-216`)

### Ce qui peut mal tourner

| Message affiché | Cause |
| --- | --- |
| « Le DWG n'est pas lu par Metreo. Exportez le plan en DXF ou en PDF depuis votre logiciel de dessin, puis déposez ce fichier. » | Un `.dwg`, même renommé en `.pdf` : c'est la signature des octets qui décide (`document_storage.py:267-272`) |
| « Type de fichier non reconnu. Formats acceptés : PDF, PNG, JPEG, CSV, XLSX, DOCX, DXF. » | Format hors liste (`document_storage.py:299-302`) |
| « Le contenu est un ‹type réel›, alors que « ‹type annoncé› » est annoncé. » | Extension et contenu divergent (`document_storage.py:459-463`) |
| « Fichier trop volumineux (maximum 26214400 octets). » | Au-delà de 25 Mio (`document_storage.py:174-178`, `config.py:72`) |
| « Ce contenu est déjà la révision N de ce document, au bit près. Rien n'a été remplacé. » | Le même fichier était déjà déposé — **ce n'est pas une panne** (`services/documents.py:347-352`) |
| « Le fichier est vide. » | Fichier de zéro octet (`document_storage.py:452`) |
| « Un contenu HTML ou XML n'est pas accepté comme document de chantier. » | Le fichier est en réalité une page web (`document_storage.py:218-221`) |
| « Une archive qui n'est ni un .docx ni un .xlsx n'est pas acceptée. » | Un ZIP qui n'est pas un format Office (`document_storage.py:281-284`) |

### Ce qu'il faut relever

- Le **nom exact du fichier**, extension comprise.
- Sa **taille**, telle que votre système l'affiche.
- Le **message exact**.
- Le pourcentage atteint par « Envoi en cours — N % » avant l'arrêt, s'il y en a
  eu un.
- L'**adresse complète** de la page du chantier.

---

## Étape 3 — Ouverture du PDF et analyse

### Le geste

1. Sur la ligne du document, cliquer sur « **Lire le plan** ». *(Ce lien
   n'apparaît que pour un PDF ou un DXF — `DocumentsDuProjet.tsx:74, 96`.)*
2. L'écran de lecture s'ouvre et affiche : « Ce plan n'a pas encore été lu.
   Lancer la lecture ne modifie pas le fichier déposé : elle produit un rendu
   consultable et une liste de mesures proposées, que vous confirmerez ou
   corrigerez ensuite. »
3. Lire l'avertissement de durée, **posé avant le bouton et non après**
   (`LecturePlan.tsx:277-284`) : « La lecture se fait d'un seul tenant et prend
   une dizaine de secondes — mesuré : 7 à 9 secondes sur des plans de 7 et
   11 Mo. Laissez cet onglet ouvert : l'analyse se termine dans la réponse à ce
   bouton. »
4. Cliquer sur « **Analyser le plan** ».

### Le résultat attendu

Un encadré « Lecture du plan en cours. Comptez une dizaine de secondes ; ne
rechargez pas la page. » Puis une carte titrée « **Plan PDF** », avec :

- un en-tête de trois informations : « **Pages** », « **Textes lus** » (un
  nombre, ou « aucun — plan probablement scanné ») et « **Échelle déclarée** »
  (`LecturePdf.tsx:441-467`) ;
- sous « Échelle déclarée », un badge **ambre** : « **aucune : rien ne peut être
  mesuré** » — **c'est normal à ce stade** ;
- l'**aperçu de la page** ;
- des rectangles de surlignage sur les textes lus ; au survol, le texte lu
  s'affiche en infobulle, parce que chaque rectangle porte un `<title>`
  (`LecturePdf.tsx:498-505`) ;
- en bas, « **N textes affichés sur M lus** » — au plus **500** sont servis à la
  fois (`routers/documents.py:680`) ;
- si le PDF a plusieurs pages : « ‹ **Page 1 sur N** › ».

### Ce qui peut mal tourner

| Message affiché | Cause |
| --- | --- |
| « Ce plan dépasse 12 Mio, la limite de l'analyse immédiate. Il reste lisible hors ligne, par le traitement déporté. » | Fichier entre 12 et 25 Mio (`routers/documents.py:486-498`) |
| « Seuls un plan DXF et un PDF sont lus aujourd'hui. Le fichier reste déposé et téléchargeable. » | Le fichier n'est ni DXF ni PDF (`lecture_de_plan.py:163-168`) |
| « Lancer la lecture d'un plan demande le droit de déposer un document. » | Le bouton est **masqué** et remplacé par cette phrase (`LecturePlan.tsx:295-297`, texte à `i18n.ts:244-245`) |
| « Ce plan avait déjà été lu. Le constat ci-dessous est celui de cette lecture ; rien n'a été relu. » | **Ce n'est pas une erreur** (`LecturePlan.tsx:268-272`) |
| « Cette page n'a pas d'aperçu. Le fichier reste déposé et téléchargeable. » | Seules les **10 premières pages** reçoivent un aperçu (`lecture_de_plan.py:99`). **Sans aperçu, cette page ne peut pas être mesurée** : il n'y a rien où placer la loupe |
| « Les N premières pages sur M ont un aperçu. Les suivantes sont déposées et téléchargeables, mais elles ne s'affichent pas dans Metreo. » | Même cause, signalée dans les anomalies (`lecture_de_plan.py:635-647`) |
| « Textes lus : aucun — plan probablement scanné » | **Ce n'est pas un échec** : la mesure géométrique reste possible, seuls les textes manquent |

### Ce qu'il faut relever

- Le **temps écoulé** entre le clic sur « Analyser le plan » et l'échec, au
  chronomètre, en secondes. C'est l'information la plus utile : 8 secondes et
  60 secondes ne désignent pas la même panne.
- L'**adresse complète** de la page de lecture : elle porte trois identifiants
  (`/projets/…/plans/…/…`) et permet de retrouver le document, sa révision et le
  constat.
- Le **nombre de pages** annoncé par votre lecteur PDF habituel, comparé au
  nombre affiché sous « Pages ».
- Le **message exact**, et une capture de l'écran entier.

---

## Étape 4 — Calibration, **dans la loupe**

C'est l'étape qui décide de tout le reste : le facteur posé ici multipliera
**toutes** les mesures de cette page.

### Le geste

1. Cliquer sur « **Déclarer l'échelle** ». Sous les boutons s'affiche l'aide :
   « Dans la loupe, cliquez les deux extrémités d'une cote connue. »
2. Cliquer **sur l'aperçu**, à l'endroit de la cote connue. Un cadre apparaît
   sur l'aperçu et un panneau « **Loupe** » s'ouvre en dessous.
3. Pendant le chargement : « Agrandissement en cours. La première ouverture
   d'une page prend quelques secondes ; les suivantes sont immédiates. »
4. **Dans l'image agrandie**, cliquer les **deux extrémités** de la cote. Un
   compteur affiche « 1 point(s) posé(s) », puis « 2 point(s) posé(s) ».
5. Le formulaire « **Confirmer l'échelle de cette page** » apparaît, précédé de :
   « Vous venez de désigner deux points. Saisissez la distance RÉELLE qui les
   sépare sur l'ouvrage — pas le rapport d'échelle du cartouche, qui ne vaut
   plus si le PDF a été exporté « ajusté à la page ». »
6. Remplir :
   - « **Distance réelle entre les deux points** » — **obligatoire** ;
   - « **Unité** » — mm, cm ou m ;
   - « **Sur quoi avez-vous calibré ?** » (exemple proposé : « ex. cote 5000 de
     la façade sud ») — **obligatoire**. Cette phrase sera affichée à côté de
     chaque mesure : c'est la provenance de l'échelle.
7. Cliquer « **Confirmer l'échelle** » (le bouton passe à « Enregistrement… »).

### Le résultat attendu

- Dans l'en-tête, sous « **Échelle déclarée** », le badge ambre disparaît et
  laisse place à une ligne en gras du type « **50 mm par point** », avec **votre
  motif** en dessous (`calibration_de_plan.py:613-621`).
- L'outil **revient tout seul sur « Naviguer »** et les deux points sont effacés
  (`LecturePdf.tsx:359-360`). Ce n'est pas un bug.

### Ce qui peut mal tourner

| Message affiché | Cause |
| --- | --- |
| « Les deux points de calibration sont distants de **X,X** points, en deçà des **10** nécessaires. Un si petit écart ne détermine pas d'échelle : il multiplierait l'erreur de pointage sur toutes les mesures de la page. Désignez deux points plus éloignés — une cote longue est le meilleur choix. » | Base trop courte (`mesures_pdf.py:226-235`). Le test est `ecart < 10.0` : exactement 10 points passe |
| « La distance réelle saisie doit être strictement positive. » | Zéro ou valeur négative (`mesures_pdf.py:236-240`) |
| « « ‹votre saisie› » n'est pas une unité de longueur : une calibration déclare une DISTANCE entre deux points. » | Unité connue mais non linéaire (`mesures_pdf.py:243-248`) |
| « L'unité « X » n'est pas connue de Metreo. » | Unité inconnue de la table d'unités (`routers/documents.py:882-887`) |
| « Ce plan n'a pas encore été analysé : ses pages ne sont pas connues. » | L'étape 3 n'a pas abouti (`calibration_de_plan.py:131-135`) |
| « La calibration ne s'applique qu'à un PDF. Un DXF porte son unité de dessin dans le fichier : ses cotations se lisent sans échelle. » | Vous êtes sur un DXF (`calibration_de_plan.py:136-141`) |
| « Le document porte N page(s) : la page X n'existe pas. » | Page hors du document (`calibration_de_plan.py:144-148`) |
| « Le rendu de cette zone a dépassé 60 secondes et a été arrêté. » | La loupe n'a pas pu être rendue (`tuiles.py:75, 177-181`) |
| « La zone demandée est plate ou inversée. » | Zone de loupe dégénérée (`routers/documents.py:1072-1079`) |

### Les pièges du geste, qui ne produisent aucun message

**Un troisième clic efface les deux premiers.** La loupe accepte au plus deux
points en calibration ; au troisième, le tableau est remplacé par ce seul
nouveau point (`LecturePdf.tsx:238-239`). C'est le geste d'annulation, et il
n'est écrit nulle part à l'écran.

**Changer de page ou d'outil efface le tracé en cours**, sans avertissement
(`LecturePdf.tsx:202-204`). Changer de page ferme en plus la loupe
(`LecturePdf.tsx:255-258, 271-274`). Fermer la loupe par son bouton
« Fermer » efface aussi les points (`LecturePdf.tsx:331-334`).

**Une calibration ne remplace pas la précédente.** Elles s'empilent : à chaque
mesure, le serveur retient la plus récente qui couvre tous les points désignés,
une calibration de zone l'emportant sur une calibration de page
(`calibration_de_plan.py:284-305`). Vous pouvez donc recalibrer autant de fois
qu'il faut — la dernière compte, et c'est son motif qui s'affichera.

**La calibration vaut pour une page, et une seule.** Sur un PDF de plusieurs
pages, chaque page demande la sienne.

### La base de calibration, concrètement

Le seuil est `CALIBRATION_MINIMALE_EN_POINTS = 10.0` (`mesures_pdf.py:76`), et
le refus tombe si l'écart est **strictement inférieur** (`mesures_pdf.py:226`).
Dix points PostScript valent **3,5 mm de papier** — calculé : 10 × 25,4/72 =
3,528 mm — et ce chiffre ne dépend pas du format de la page.

La loupe, elle, en dépend. Elle couvre 5 % de la largeur de la page
(`TAILLE_DE_LA_LOUPE = 0.05`, `LecturePdf.tsx:52`) et ce morceau est rendu sur
une image de 512 pixels de côté (`COTE_TUILE = 512`, `rendu_pdf.py:88`).
**Calculé** à partir de ces deux constantes :

| Page | Largeur | Loupe | 10 points y font |
| --- | --- | --- | --- |
| Plan du propriétaire, 1 189 mm de large | 3 370 pts | 168,5 pts (59,4 mm de papier) | **5,9 %** de la largeur de la loupe, soit 30 pixels sur 512 |
| Plan du propriétaire, 1 480 mm | 4 195 pts | 209,8 pts (74,0 mm) | 4,8 % |
| Plan du propriétaire, 1 690 mm | 4 791 pts | 239,5 pts (84,5 mm) | 4,2 % |
| A3 paysage, 420 mm | 1 191 pts | 59,5 pts (21,0 mm) | **16,8 %** |
| Fixture du dépôt, 300 points de large | 300 pts | 15,0 pts (5,3 mm) | **66,7 %** |

Sur un plan réel, le refus ne tombera donc que si vous cliquez deux points
quasiment au même endroit — moins d'un dix-septième de la largeur de la loupe.
**En pratique, vous ne le verrez presque jamais.** C'est le piège : le seuil est
un plancher, pas un conseil.

### L'incertitude du facteur ne dépend pas de la taille de la page

L'incertitude relative du facteur vaut `√2·ε / écart`
(`mesures_pdf.py:255-264`), où `ε` est la résolution du pointage que l'écran
déclare. Et l'écran la calcule comme **la largeur de la loupe divisée par 512**
(`LecturePdf.tsx:65-68`).

Les deux dépendances se compensent exactement. L'incertitude du facteur ne
dépend donc **que de la fraction de la loupe couverte par votre base** :

> incertitude relative du facteur = (√2 / 512) ÷ (fraction de la largeur de la
> loupe)

**Calculé.** Elle est identique sur un plan de 1 189 mm et sur un A3 :

| Base, en fraction de la largeur de la loupe | Incertitude du facteur |
| --- | --- |
| 100 % (impossible en pratique : les bords exacts) | 0,276 % |
| **90 %** — clics à 5 % et à 95 %, ce que fait le parcours automatisé (`suite-plan-pdf-mesures.spec.ts:162-163`) | **0,307 %** |
| 50 % — à mi-largeur | **0,552 %** |
| 29,7 % | 0,93 % |
| **13,8 %** | **2,00 % — le seuil** |
| 5,9 % (le minimum accepté sur une page de 1 189 mm) | 4,65 % |

**La consigne pratique.** Cherchez une cote qui remplit la largeur de la loupe
et posez les deux points **aux deux bords opposés**, pas au milieu. Une base de
moins de **14 % de la largeur de la loupe** place le facteur seul au-dessus du
seuil de 2 %, **avant même l'erreur de pointage de la mesure elle-même** — et
rien ne vous le dira : la calibration sera acceptée, et toutes les mesures de la
page partiront « à vérifier » sans que la cause soit visible.

Sur une page de 1 189 mm de large, **13,8 %** de la loupe — le seuil exact du
tableau ci-dessus — font **23 points**, soit **8,2 mm de papier**. Les 14 % de la
consigne, arrondis vers le haut, en font 24, soit 8,3 mm.

### Ce qu'il faut relever

- Le **nombre affiché par « N point(s) posé(s) »** au moment du refus.
- Le **nombre de points de distance** annoncé par le message (« distants de
  X,X points ») — c'est la seule mesure chiffrée du geste.
- Ce que vous avez tapé dans « **Distance réelle entre les deux points** » et
  dans « **Unité** », exactement.
- **Où, dans la loupe, vous avez posé les deux points** : aux bords, aux quarts,
  au milieu. C'est ce qui détermine l'incertitude, et c'est invisible après coup.
- Le **numéro de page** affiché par « Page X sur Y ».
- Le **temps d'ouverture de la loupe**, en secondes.
- Les **dimensions du plan** (en mm, lues au cartouche ou dans les propriétés du
  PDF) et l'**échelle écrite au cartouche**.

---

## Étape 5 — Mesurer une longueur

### Le geste

1. Cliquer sur « **Mesurer une longueur** ». L'aide affiche : « Dans la loupe,
   cliquez les points du tracé. »
2. Cliquer **sur l'aperçu** à l'endroit de l'ouvrage : la loupe s'y déplace.
3. **Dans la loupe**, cliquer les **deux extrémités** du mur. Le compteur suit.
4. Le formulaire « **Nommer cette longueur** » apparaît dès le deuxième point
   (`LecturePdf.tsx:371-372`). Remplir « **Ce que vous mesurez** » (exemple
   proposé : « ex. mur nord, dalle du séjour ») — **obligatoire**.
5. Cliquer « **Mesurer** ».

> **Au-delà de deux points, c'est une ligne brisée** dont les segments
> s'additionnent.
>
> **Le maximum est 200 points** (`LecturePdf.tsx:238`). Et au 201ᵉ clic, le
> tracé n'est pas refusé : il est **effacé et remplacé par ce seul point**
> (`LecturePdf.tsx:239`), exactement comme le troisième clic en calibration.
> Aucun message ne le dit ; seul le compteur « N point(s) posé(s) » retombe à 1.
>
> **Pour un ouvrage plus long que la loupe**, recliquer sur l'aperçu déplace la
> loupe **sans effacer les points déjà posés** : on peut mesurer en plusieurs
> fenêtres. Mais **fermer la loupe**, **changer d'outil** ou **changer de page**
> efface tout (`LecturePdf.tsx:204, 331-334`).

### Le résultat attendu

Un tableau « **Mesures prises** » apparaît, à cinq colonnes :
« **Ouvrage** », « **Valeur** », « **Fiabilité** », « **Décision** »,
« **Actions** » (`LecturePdf.tsx:732-740`). Sur la ligne :

- **Ouvrage** : votre libellé, puis « p. 1 · longueur » ;
- **Valeur** : la valeur **dans l'unité de votre calibration** — calibration en
  mm, longueur en mm, aucune conversion (`mesures_pdf.py:296-304`) — et en
  dessous « ± ‹incertitude› ‹même unité› » ;
- **Fiabilité** : badge « **mesurable** » ou « **à vérifier** », suivi de
  « Échelle : ‹votre motif de calibration› » ;
- **Décision** : « **—** ».

> **Le badge « mesurable » n'est pas vert.** L'écran lui applique la classe
> `badge` nue, pas `badge success` (`LecturePdf.tsx:825`). En clair : fond
> `--bg` (#f6f7f9), bordure `--border-strong` (#c3ccd6), texte `--ink-muted`
> (#5b6472) — **un badge gris, discret** (`globals.css:108-112`). Le vert
> `--success` (#1d6b41) existe bien dans la feuille de style, sous
> `.badge.success` (`globals.css:115, 19`), et il est employé ailleurs dans
> l'application — mais pas ici.
>
> « à vérifier » reçoit `badge warning` : fond ambre pâle, texte brun-ambre
> `--warning` (#8a5a00) (`globals.css:113, 17`). **La différence entre les deux
> badges est donc gris contre ambre, pas vert contre orange.** Ne cherchez pas
> du vert.

Si la fiabilité est « à vérifier » pour cause d'incertitude, la ligne porte en
plus : « Le pointage était trop grossier pour cette distance : agrandissez
davantage et reprenez. » (`i18n.ts:336-337`)

### Ce qui peut mal tourner

| Message affiché | Cause |
| --- | --- |
| Encadré **ambre dans le formulaire** : « Aucune échelle n'a été confirmée pour cette page. Un PDF ne porte pas d'unité : déclarez d'abord une échelle sur une cote connue. » — et le bouton « Mesurer » reste **grisé** | L'étape 4 n'a pas été faite sur **cette page**. L'écran bloque avant l'envoi (`LecturePdf.tsx:690-694, 710`) |
| « Aucune échelle n'a été confirmée pour cette page, ou aucune ne couvre tous les points désignés. Un PDF ne porte pas d'unité : sans échelle déclarée, il n'y a rien à mesurer. Posez une calibration sur une cote connue — une cote longue donne le meilleur résultat. » | Même cause, constatée côté serveur (`calibration_de_plan.py:340-348`) |
| « Les points désignés sont confondus : il n'y a aucune longueur à mesurer. » | Deux clics au même pixel (`mesures_pdf.py:317-321`) |
| « Une longueur demande au moins deux points. » | (`mesures_pdf.py:306-310`) |
| « Une mesure demande au moins deux points. » | Même refus, posé plus tôt (`calibration_de_plan.py:330-331`) |

### Pourquoi une mesure bascule en « à vérifier »

L'incertitude totale d'une longueur additionne **en quadrature** celle du
facteur et celle du tracé lui-même (`mesures_pdf.py:327-329`), et la mesure
reste « à vérifier » dès que ce total **dépasse strictement** 2 %
(`mesures_pdf.py:283` : `if relative > SEUIL_D_INCERTITUDE`). Exactement 2 %
passe donc, et le seuil est `SEUIL_D_INCERTITUDE = Decimal("0.02")`
(`mesures_pdf.py:88`).

**Calculé** sur une page de 1 189 mm, avec une base de calibration à 90 % de la
loupe (0,307 %) :

| Longueur tracée, dans la loupe | En mm de papier | Incertitude totale | Verdict |
| --- | --- | --- | --- |
| 20 pts (12 % de la loupe) | 7,1 mm | 2,35 % | à vérifier |
| 23,3 pts (13,8 %) | 8,2 mm | 2,02 % | à vérifier |
| 50 pts (29,7 %) | 17,6 mm | 0,98 % | mesurable |
| 100 pts (59,3 %) | 35,3 mm | 0,56 % | mesurable |
| 150 pts (89,0 %) | 52,9 mm | 0,44 % | mesurable |

Autrement dit : **un ouvrage trop court dans la loupe bascule en « à
vérifier », même avec une calibration parfaite.** La réponse n'est pas de
recalibrer, c'est de placer la loupe plus près de l'ouvrage.

**Et toute réserve dégrade**, pas seulement celle d'incertitude : la fiabilité
vaut « mesurable » **si et seulement si** la liste des réserves est vide
(`mesures_pdf.py:285`).

### Ce qu'il faut relever

- La **valeur affichée** et son **unité**, recopiées exactement.
- L'**incertitude** affichée en dessous (« ± … »).
- Le **badge de fiabilité** : « mesurable » (gris) ou « à vérifier » (ambre).
- La ligne « **Échelle : …** » sous le badge : c'est le motif de la calibration,
  et il dit sur quoi la valeur repose.
- **La valeur réelle attendue**, et d'où vous la tenez (cote lue au plan, relevé
  sur place).
- Le **nombre de points posés** au moment du clic sur « Mesurer ».
- Si la valeur est fausse d'un **facteur rond** (deux fois trop, dix fois trop) :
  c'est presque toujours l'unité de calibration. Relevez ce que vous aviez
  choisi dans « Unité ».

---

## Étape 6 — Mesurer une surface

### Le geste

1. Cliquer sur « **Mesurer une surface** ». L'aide affiche : « Dans la loupe,
   cliquez au moins trois points. Le contour se ferme seul. »
2. Placer la loupe, puis cliquer **au moins trois points** dans la loupe, en
   faisant le tour de la pièce. Le tracé se ferme automatiquement à l'écran
   (`LecturePdf.tsx:80-84`).
3. Le formulaire « **Nommer cette surface** » apparaît au troisième point
   (`LecturePdf.tsx:371-372`). Remplir « **Ce que vous mesurez** ».
4. Cliquer « **Mesurer** ».

Le maximum est le même que pour une longueur : **200 points**, et le 201ᵉ efface
le contour (`LecturePdf.tsx:238-239`).

### Le résultat attendu

Une nouvelle ligne dans « Mesures prises », portant « p. 1 · surface », et une
valeur **en m²**.

> **Une surface est toujours rendue en mètres carrés**, quelle que soit l'unité
> de votre calibration (`UNITE_DE_SURFACE = "m2"`, `mesures_pdf.py:352`). Une
> calibration en millimètres donne une longueur en mm et une surface en m². Ce
> n'est pas une incohérence : la table d'unités du dépôt déclare `m2`, `cm2` et
> `ha`, et pas de millimètre carré (`mesures_pdf.py:342-351`).
>
> **L'incertitude d'une surface est le double, en relatif, de celle d'une
> longueur** (`mesures_pdf.py:399-406`) : une aire va comme le carré d'une
> longueur. Et l'incertitude du tracé y est calculée sur le **périmètre**, pas
> sur un côté.

**Calculé**, toujours avec une base de calibration à 90 % de la loupe :

| Périmètre tracé | En mm de papier | Incertitude de la surface | Verdict |
| --- | --- | --- | --- |
| 40 pts | 14,1 mm | 2,41 % | à vérifier |
| 49 pts (29 % de la loupe) | 17,3 mm | 2,00 % | limite |
| 60 pts | 21,2 mm | 1,67 % | mesurable |
| 100 pts | 35,3 mm | 1,12 % | mesurable |
| 200 pts | 70,6 mm | 0,77 % | mesurable |

Le plancher, même avec un contour immense : **0,61 %** — le double de
l'incertitude du facteur. Une calibration qui donne 1 % sur une longueur donne
2 % sur une surface, et bascule donc.

### Ce qui peut mal tourner

| Message affiché | Cause |
| --- | --- |
| « Une surface demande au moins trois points. » | (`mesures_pdf.py:370-374`) |
| « Les points désignés sont alignés ou confondus : ils n'enferment aucune surface. » | (`mesures_pdf.py:389-393`) |
| Sur la ligne, en fiabilité « à vérifier » : « Le contour se croise lui-même : la surface calculée ne correspond à rien de dessiné. » | Tracé en nœud papillon (`mesures_pdf.py:396-397`, texte à `i18n.ts:338-339`). La mesure est **conservée**, mais marquée, et le fichier dit pourquoi : décider ce que l'utilisateur voulait dessiner n'appartient pas au module (`mesures_pdf.py:364-368`) |

### Ce qu'il faut relever

- Le **nombre de points** posés et, si possible, une capture de la loupe **avec
  le tracé visible** : la forme du contour est l'information décisive.
- La **valeur en m²** affichée et la surface réelle attendue.
- L'**unité choisie à la calibration** — c'est la première chose à vérifier sur
  une surface aberrante.
- Si le badge est « à vérifier » : **laquelle des deux phrases de réserve**
  s'affiche sous lui.

---

## Étape 7 — Décider : confirmer ou corriger

### Le geste

Sur la ligne de la mesure, dans la colonne « **Actions** », cinq commandes se
suivent dans cet ordre (`LecturePdf.tsx:846-882`) :

1. Le bouton « **Montrer sur le plan** » — il ramène la page et la loupe sur
   l'endroit mesuré (`LecturePdf.tsx:406-418`).
2. Le champ « **Motif** ».
3. Le bouton « **Confirmer** » — grisé tant que le Motif est vide
   (`LecturePdf.tsx:861`).
4. Le champ « **Valeur retenue** ».
5. Le bouton « **Corriger** » — grisé tant que le Motif **ou** la Valeur retenue
   est vide (`LecturePdf.tsx:877`).

Pour l'essai : remplir « **Motif** », puis « **Valeur retenue** », puis cliquer
« **Corriger** ».

> **Une décision sans raison n'est pas enregistrable** : le motif est exigé des
> deux côtés, par l'écran (boutons grisés) et par le serveur, qui refuse un
> motif vide ou fait d'espaces, et le plafonne à 2 000 caractères
> (`schemas.py:1138`).

### Le résultat attendu

- la colonne « **Décision** » affiche « **corrigée** » (`i18n.ts:341`) ;
- la colonne « **Valeur** » affiche **toujours la valeur mesurée par Metreo**,
  et **en dessous** un badge « **corrigée en ‹votre valeur›** »
  (`LecturePdf.tsx:813-820`).

**Les deux valeurs restent visibles côte à côte.** La proposition de la machine
n'est jamais réécrite : c'est ce que le parcours automatisé vérifie en dernier
(`suite-plan-pdf-mesures.spec.ts:221-228`), et c'est ce qui rend le dossier
auditable.

### Ce qui peut mal tourner

| Message affiché | Cause |
| --- | --- |
| Encadré rouge avec, en dessous, en police fixe, « permission requise : document:validate » | Votre rôle ne porte pas le droit de valider (`routers/documents.py:324`, `Feedback.tsx:15-17`) |
| Un 422 listant le champ `reason` | Motif vide ou fait d'espaces (`schemas.py:1138`) |
| « Une correction doit conserver les valeurs avant et après. » | Correction envoyée sans valeur retenue (`schemas.py:1146-1148`) |

### Trois limites de cet écran, à ne pas prendre pour des pannes

> **Une quatrième limite a été relevée puis fermée pendant la rédaction de ce
> document.** L'écran n'offrait **aucun bouton « Rejeter »** : la colonne
> « Décision » savait afficher « rejetée », l'API acceptait la valeur et la
> fonction interne de l'écran la déclarait dans sa signature — mais aucun bouton
> ne l'appelait. Une mesure visiblement fausse ne laissait donc que deux issues :
> la confirmer, ou la « corriger » vers une valeur que la personne ne connaît
> pas. Le bouton **« Rejeter »** existe désormais, au bout de la ligne, à côté de
> « Confirmer » et « Corriger ». Comme eux, il exige un motif.
>
> **Ce qu'un rejet fait, et ne fait pas** : il enregistre la décision et laisse
> la proposition de la machine intacte, valeur comprise. Il n'efface rien — un
> rejet qui effacerait rendrait le dossier inauditable, puisqu'on ne saurait plus
> ce qui avait été proposé ni pourquoi il a été écarté. Éprouvé par
> `test_a_rejected_measurement_keeps_its_proposal_and_says_it_is_rejected`.

**1. Les boutons de décision ne sont pas masqués aux comptes qui n'ont pas le
droit de valider.** L'écran DXF reçoit le droit et masque ses commandes
(`LecturePlan.tsx:327` passe `peutValider`, employé à `LecturePlan.tsx:891`) ;
l'écran PDF, lui, ne le reçoit pas (`LecturePlan.tsx:315-321`). Un compte sans
`document:validate` verra donc les boutons, remplira le motif, cliquera — et
recevra l'encadré rouge. Le contrôle est bien fait, mais **côté serveur
seulement**.

**2. La valeur retenue n'est pas revérifiée.** Elle part telle que vous l'avez
tapée, simplement débarrassée de ses espaces de bord
(`LecturePdf.tsx:774-783`) : aucun contrôle d'unité, de format, de séparateur
décimal ni de cohérence avec la mesure. Une virgule, un point, une unité
oubliée : rien ne vous arrêtera.

**3. Une décision ne remplace pas la précédente, elle s'ajoute.** Seule la plus
récente s'affiche (`calibration_de_plan.py:553-563, 593`) ; toutes restent en
base et dans le journal d'audit. Vous pouvez donc corriger une correction.

### Ce qu'il faut relever

- Le **libellé de la mesure** concernée (colonne « Ouvrage »).
- Ce que vous avez tapé dans « **Motif** » et dans « **Valeur retenue** »,
  caractère pour caractère — **virgule ou point compris**.
- Le contenu des colonnes « **Valeur** » et « **Décision** » avant et après le
  clic.
- Le **message exact**, et notamment la ligne en police fixe sous l'encadré
  rouge, s'il y en a une.

---

## La fiche de relevé

À remplir **à chaque anomalie**, sans attendre la fin de l'essai. Un relevé fait
de mémoire le lendemain ne permet pas de diagnostiquer.

| À relever | Comment |
| --- | --- |
| **L'étape** | Son numéro dans ce document (1 à 7) |
| **La date et l'heure** | À la minute. Sans elle, les journaux du serveur ne sont pas exploitables |
| **L'adresse complète** | Copiée depuis la barre du navigateur, **entière**. Elle porte les identifiants du projet, du document et de la révision, et le code de refus de connexion |
| **Le nom exact du fichier** | Extension comprise |
| **Le message affiché** | Recopié mot pour mot, **ou** capture d'écran **de l'écran entier** — jamais recadrée sur le seul message : le reste de l'écran porte l'état |
| **La couleur de l'encadré** | Rouge (refus d'API) ou ambre (avertissement). Les deux ne se diagnostiquent pas de la même façon |
| **L'outil sélectionné** | Le bouton en surbrillance : « Naviguer », « Déclarer l'échelle », « Mesurer une longueur » ou « Mesurer une surface » |
| **Le compteur de points** | Le nombre affiché par « N point(s) posé(s) » |
| **La page courante** | Ce qu'affiche « Page X sur Y » |
| **L'échelle déclarée** | Ce qu'affiche l'en-tête : le badge « aucune : rien ne peut être mesuré », ou bien « … par point » et son motif |
| **La durée** | Au chronomètre, en secondes, entre le clic et le résultat ou l'échec |

### Ce que coûte la première loupe, et la suivante

C'est la durée la plus surprenante de l'essai, et elle est documentée dans la
route elle-même (`routers/documents.py:1054-1060`) :

- **première demande d'une page : 0,2 à 5,3 secondes** ;
- **demandes suivantes : 0,3 milliseconde**, servies depuis le volume.

L'écart vient de PDFium : le rendu se fait dans un processus séparé qui meurt
ensuite, parce que charger une page réserve **54 à 422 Mo** que PDFium ne rend
pas au système. Le délai maximal avant abandon est de **60 secondes**
(`tuiles.py:69-75`), soit dix fois la marge sur le pire cas mesuré.

Deux conséquences pour l'essai :

1. **La deuxième loupe sur la même page n'est pas toujours instantanée.** La clé
   de cache arrondit la zone au **millième de page** (`PRECISION_DE_LA_CLE = 3`,
   `tuiles.py:84`) : deux clics voisins partagent leur tuile, deux clics
   éloignés non. Déplacer la loupe ailleurs sur la même page, c'est repayer les
   secondes.
2. **Rien n'est mis en cache dans votre navigateur.** La réponse porte
   `Cache-Control: private, no-store` (`routers/documents.py:1108`) : le cache
   est côté serveur, sur le volume. Revenir sur une zone déjà vue redemande
   l'image au serveur, qui la sert depuis le disque.

### L'en-tête `X-Metreo-Tuile` — ce qu'il vaut, et ce qu'il coûte

Chaque image de loupe est servie avec un en-tête `X-Metreo-Tuile` qui vaut
**`cache`** ou **`rendue`**, et rien d'autre (`routers/documents.py:1111`). Il
dit **une seule chose** : si l'image venait du volume (donc instantanée) ou si
elle a dû être calculée.

Il n'apprend donc rien de plus que le chronomètre. Le code le dit lui-même, à la
ligne juste au-dessus : « une tuile servie en une milliseconde vient du volume,
une servie en quatre secondes vient d'être rendue. » Il n'indique ni la durée,
ni la taille, ni la cause d'un échec.

**Ce n'est pas un relevé raisonnable à demander à quelqu'un qui n'est pas
développeur** : il faut ouvrir les outils du navigateur, trouver la bonne
requête dans un flux et lire ses en-têtes. Si on vous le demande, dans Chrome ou
Edge : **F12** → onglet « **Réseau** » → recharger la page → refaire le geste →
cliquer sur la ligne dont le nom contient **`tuile`** → onglet « **En-têtes** »
→ chercher `x-metreo-tuile` dans les en-têtes de réponse. **Sinon, le
chronomètre suffit.**

---

## Ce qui est vérifié aujourd'hui, et par quoi

| Vérifié | Par quoi |
| --- | --- |
| La géométrie, l'incertitude et la provenance, sur des cas dont la réponse est connue d'avance | Les tests de l'API sur `mesures_pdf.py`, module pur — sans base, sans fichier, sans réseau (`mesures_pdf.py:3-5`) |
| Que l'écran compile et que ses types tiennent | `npm run typecheck` |
| Que le parcours entier passe **par le navigateur** : dépôt, aperçu, textes situés, navigation entre pages, loupe, calibration, mesure, correction | `apps/web/e2e-premier-devis/suite-plan-pdf-mesures.spec.ts` |
| Qu'un PDF de **deux pages** change vraiment de page et ne resert pas la même image | Le même parcours : la fixture porte neuf fragments sur la page 1 et cinq sur la page 2, dont « DETAIL B » qui n'existe que sur la seconde (`suite-plan-pdf-mesures.spec.ts:119-141`) |
| Que la correction humaine s'affiche **à côté** de la proposition, sans la réécrire | Le même parcours, points 11 et 12 (`suite-plan-pdf-mesures.spec.ts:207-228`) |
| Que le motif est exigé avant toute décision | Le même parcours, ligne 210 : le bouton « Corriger » est attendu désactivé |

| **Non vérifié** | Ce qu'il faudrait |
| --- | --- |
| La justesse d'une mesure sur un plan réel | Une paire DXF + PDF de la même révision, et trois cotes relevées à la main : une courte, une longue, une oblique (`docs/PLANS_REELS.md`, tableau « Quoi fournir », lignes 1 et 2) |
| Votre seuil de tolérance | Une réponse du propriétaire. Rien dans le dépôt ne la porte aujourd'hui |
| Le comportement sur un plan scanné (sans texte) | Un plan scanné de votre dossier. `docs/PLANS_REELS.md` note qu'aucun des quatre plans fournis ne l'est |
| Firefox, Safari | Un second projet dans la configuration Playwright |

---

## Note sur les formats de page

Les quatre plans du propriétaire **ne sont pas des A0**. Mesuré, une page
chacun : **1 480 × 850, 1 690 × 850 et deux fois 1 189 × 914 mm**
(`apps/api/src/metreo_api/services/lecture_pdf.py:36-37`). Un A0 fait
841 × 1 189 mm.

La formule juste est donc : **quatre plans réels de grand format, 1 189 à
1 690 mm de grand côté**.

**Neuf** commentaires du code livré écrivent encore « A0 » par raccourci, et non
cinq. Les voici tous :

| Fichier | Ce que le commentaire dit |
| --- | --- |
| `services/mesures_pdf.py:93` | « l'aperçu pleine page d'un A0 » |
| `services/lecture_de_plan.py:93` | « une page A0 rendue à 2 000 pixels de grand côté » |
| `services/rendu_pdf.py:297` | « 101 sur un A0 » |
| `services/rendu_pdf.py:413` | « un A0 atteint 16 pixels au facteur 1,4 » |
| `schemas.py:1062` | « sur l'aperçu pleine page d'un A0, un pixel vaut 12 à 42 mm » |
| `apps/web/src/lib/api.ts:470` | « l'aperçu pleine page d'un A0 vaut 12 à 42 mm d'ouvrage » |
| `LecturePdf.tsx:48` | « sur un A0 de 1 189 mm, cela fait 59 mm » |
| `suite-plan-pdf-mesures.spec.ts:43` | « mesuré jusqu'à 5,3 s sur un A0 » |
| `suite-plan-pdf-mesures.spec.ts:157` | « Sur un A0 les mêmes fractions en désignent 160 » |

**Deux de ces neuf décrivent le contrat de l'API** — `schemas.py:1062` et
`api.ts:470` — c'est-à-dire l'endroit où l'on irait vérifier ce que vaut un
pixel d'aperçu. S'y ajoutent **quatre** occurrences dans les tests de l'API
(`test_calibration_de_plan_api.py:385`, `test_lecture_pdf.py:582` et `:593`,
`test_mesures_pdf.py:501`), soit **treize dans `apps/`**, et **deux de plus dans
les ADR** (`docs/adr/0007-lecture-de-plans.md:64`,
`docs/adr/0008-tuiles-de-detail-des-plans.md:148`) : **quinze en tout**.

Pour les retrouver sans se fier à ces numéros de ligne, qui se périment au
commit suivant :

```
grep -rn "A0" --include=*.py --include=*.ts --include=*.tsx apps packages \
  | grep -v node_modules
```

Le `grep -v node_modules` est indispensable : sans lui, la commande rend aussi
une vingtaine de lignes de dépendances installées, qui n'appartiennent pas au
dépôt. Ainsi écrite, elle rend quinze lignes sur la tête `8a17378` — ce ne sont
pas les mêmes quinze, et la coïncidence du nombre est trompeuse : ce sont les
treize d'`apps/` ci-dessus, plus **deux à écarter**, `services/exports.py:97` et
`tests/test_export_hardening.py:143`, qui désignent la cellule de tableur `A0`
et pas un format de page. Les deux ADR ne sont pas sous `apps/` et se cherchent
séparément, avec `grep -rn "A0" docs/adr`.

Ce sont des raccourcis de rédaction, pas des mesures ; les dimensions mesurées
sont celles ci-dessus, et ce sont elles qui comptent.

Cela ne change rien aux fractions calculées plus haut : **l'incertitude relative
du facteur ne dépend pas du format de la page**, seulement de la fraction de la
loupe que votre base de calibration couvre.

---

## Ce que cet essai ne prouve pas

**1. Il ne prouve aucune justesse de mesure sur vos plans.**
C'est la limite principale, et elle est écrite dans le parcours automatisé
lui-même (`suite-plan-pdf-mesures.spec.ts:30-35`) : « La JUSTESSE du nombre
mesuré ne l'est pas : un clic de souris sur un rendu ne vaut pas une
référence. » Ce que l'essai montre, c'est qu'un nombre **existe**, qu'il
**porte son unité**, qu'il **porte son incertitude**, qu'il **dit sa
fiabilité**, et que **votre correction s'affiche à côté de la proposition sans
la réécrire**. Il ne dit pas que ce nombre est le bon.

**2. L'incertitude affichée n'est pas une erreur constatée.**
Le « ± » est **calculé** à partir de la résolution à laquelle vous avez pointé
(`mesures_pdf.py:255-264, 327-329`). C'est une propagation d'erreur de
pointage, pas une comparaison à une vérité. Un plan **déformé à l'export** —
« ajusté à la page » resté coché — produira une mesure fausse avec une petite
incertitude affichée. Rien dans cet essai ne détecte cela, et le formulaire de
calibration le dit en toutes lettres (`i18n.ts:301-302`).

**3. Le seuil de 2 % n'est pas votre tolérance métier.**
`SEUIL_D_INCERTITUDE = Decimal("0.02")` est décrit dans le dépôt comme « un
garde-fou de dernier recours », et **explicitement pas** une tolérance métier :
« le propriétaire n'a pas encore fixé la sienne, et un terrassement n'a pas la
même exigence qu'un châssis » (`mesures_pdf.py:78-88`). Le test est strict —
`if relative > SEUIL_D_INCERTITUDE` (`mesures_pdf.py:283`) — donc exactement 2 %
reste « mesurable ». Votre tolérance à vous n'est écrite nulle part dans le
dépôt aujourd'hui : c'est l'objet de `docs/COTES_DE_REFERENCE.md`, qui n'est pas
sur cette branche mais sur `claude/cotes-de-reference` (**PR #88**) — voir
« Avertissement sur la fiche de cotes de référence ».

**4. Il ne prouve rien sur un autre plan.**
Un plan, une révision, une échelle, un logiciel d'export. Un autre plan au même
format peut se comporter autrement.

**5. Il ne dit rien de ce que les nombres du plan signifient.**
Les textes surlignés ne sont pas des mesures : ce sont des textes situés, tels
que le dessinateur les a écrits. « Rien ici n'affirme que « 5000 » vaut
5 000 mm : le dire demande une échelle, et une échelle demande une confirmation
humaine. » (`routers/documents.py:711-714`) Sur le plus dense des quatre plans
du propriétaire, l'extraction rend **4 351 fragments** (`lecture_pdf.py:55-58`),
dont l'écran n'en affiche que 500 à la fois.

**6. Il n'éprouve qu'un seul navigateur.**
Seul Chromium est exercé par le parcours automatisé
(`playwright.premier-devis.config.ts:46-51`).

**7. Il ne dit rien de la tenue en charge.**
Un seul plan, un seul utilisateur, une seule session. L'analyse est
**synchrone** : elle occupe la requête HTTP pendant 7,3 et 8,8 secondes mesurées
sur deux plans réels, et **aucune file d'attente n'existe dans le dépôt**
(`routers/documents.py:339-345`). Ce que plusieurs analyses simultanées
produiraient n'est pas établi.

**8. Il n'éprouve pas le rejet d'une mesure dans un navigateur.**
Le bouton « Rejeter » existe désormais, et l'API est éprouvée par
`test_a_rejected_measurement_keeps_its_proposal_and_says_it_is_rejected`. Mais
le parcours automatisé, lui, ne couvre que la correction : personne n'a encore
cliqué « Rejeter » dans un navigateur. Si vous le faites pendant l'essai,
relevez-le — c'est un geste dont le chemin complet n'a pas de témoin.

**9. Il n'éprouve pas le retour arrière.**
Au contraire : il le ferme. Dès la première mesure enregistrée, la redescente du
schéma est refusée par construction, et la seule sortie est la restauration de
la sauvegarde (voir « Avant de commencer »).