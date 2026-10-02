# Feuille de route

Une phase n'est ouverte que lorsque les critères de la précédente sont
démontrés, sauf pour préparer une interface technique. Chaque critère est
formulé de façon vérifiable : soit un test l'atteste, soit il n'est pas atteint.

---

## Phase 0 — Cadrage et fondations · **Fonctionnellement complète**

| Livrable | État |
| --- | --- |
| `README.md` orienté développeur | ✅ |
| `docs/PRODUCT_BRIEF.md`, `ASSUMPTIONS.md`, `ARCHITECTURE.md`, `DATA_MODEL.md`, `SECURITY_THREAT_MODEL.md`, `ROADMAP.md` | ✅ |
| ADR (pile, multi-tenant, stockage documentaire, calcul des prix) | ✅ `docs/adr/` |
| Monorepo, Docker Compose, `.env.example`, CI | ✅ |
| Conventions de code et stratégie de tests | ✅ `docs/CONVENTIONS.md`, `docs/TESTING.md` |

---

## Phase 1 — Première tranche verticale utilisable · **Fonctionnellement close, candidate de préproduction**

> **Phase 1 fonctionnellement close.**
> **Le blocage de sécurité est levé.** `apps/web/package.json` fixe
> `next@15.5.24` — exactement la version attendue —, qui couvre les deux RCE
> critiques du 25 août 2026. Preuves et décompte : `docs/PHASE1_VERIFICATION.md`.
>
> **Candidat de préproduction validé, PAS ENCORE DÉPLOYÉ.** Aucune installation
> n'est en ligne. Le domaine `metreobtp.com` est acquis et ses DNS sont tenus
> chez Cloudflare ; ce qui manque est un **serveur d'exécution** et un
> **fournisseur d'identité réel**. Un domaine n'est pas un hébergement : tant
> qu'aucune machine ne répond, l'enregistrement DNS n'a pas de cible.
> **Production réelle bloquée par des décisions externes, pas par du code** :
> sauvegardes distantes, supervision et décisions juridiques — la liste vit dans
> `docs/EXPLOITATION.md`, « Ce qui manque encore ».
>
> Quatre choses distinctes, qui ne se remplacent pas : *fonctionnellement
> complet* décrit ce qu'un utilisateur peut faire et ce que les tests prouvent ;
> *déployable* suppose en plus qu'aucun correctif de sécurité connu ne manque —
> c'est désormais le cas ; *déployé* suppose qu'une installation existe et
> réponde à une adresse — ce n'est pas le cas ; *prêt pour la production*
> suppose l'exploitation.


| # | Exigence | État | Preuve |
| --- | --- | --- | --- |
| 1 | Organisation et utilisateur de développement | ✅ | `seed.py`, `/auth/dev-login` |
| 2 | Profil Belgique/Wallonie par défaut, configurable | ✅ | `region_profiles`, 4 packs semés |
| 3 | Création d'un projet | ✅ | `routers/projects.py` |
| 4 | Import CSV avec prévisualisation et erreurs | ✅ | `test_price_import.py` |
| 5 | Création manuelle d'un bordereau | ✅ | `test_boq.py` |
| 6 | Calcul déterministe d'un sous-détail | ✅ | `test_pricing.py` |
| 7 | Version d'estimation gelable | ✅ | `test_estimating.py` |
| 8 | Export CSV et aperçu de devis imprimable | ✅ | `test_estimating.py` |
| 9 | Audit des actions principales | ✅ | `test_audit.py` |
| 10 | Tests d'isolation entre deux organisations | ✅ | `test_tenant_isolation.py` |
| 11 | Répertoire de clients réutilisable | ✅ | `test_repertoire_clients.py` |
| 12 | Devis remis : numéroté, figé, PDF téléchargeable et empreinte | ✅ | `test_devis_emis.py`, `test_motif_de_numerotation.py`, `suite-devis-client-pdf.spec.ts` |
| 13 | Cycle commercial : transmission, consultation, acceptation ou refus | ✅ | `test_reponse_client.py`, `suite-devis-reponse-client.spec.ts` |
| 14 | Lien client sécurisé, révocable, sans compte ni domaine | ✅ | `test_partage_de_devis.py` |
| 15 | Conservation et effacement encadrés | ✅ | `test_conservation_des_devis.py`, `test_purge_encadree.py` |
| 16 | Sous-détails construits, corrigés et employés dans un devis depuis l'interface | ✅ | `test_sous_details.py`, `test_sous_details_dans_un_devis.py`, `suite-sous-detail-au-devis.spec.ts` |
| 17 | Import XLSX, converti par le même pipeline que le CSV | ✅ | `test_price_import.py`, `test_import_xlsx.py`, `suite-import-xlsx-au-devis.spec.ts` |
| 18 | Scénarios bas / probable / haut, utilisables depuis l'interface | ✅ | `docs/SCENARIOS_DE_CHIFFRAGE.md`, `test_scenarios.py`, `suite-scenarios-de-chiffrage.spec.ts` |

Fonctionne localement avec des fournisseurs factices et **aucune clé payante**.

**Phase 1 fonctionnellement close.** Les deux manques qui restaient — l'import
XLSX et les scénarios bas / probable / haut — sont livrés, éprouvés côté API et
parcourus au navigateur. Aucune exigence de la liste ci-dessus n'attend plus de
code. Ce que la phase 1 ne prétend toujours pas être : *déployable* (voir
l'encadré) et *prête pour la production* (exploitation, sauvegardes,
supervision).

**Ouvert autour du cycle commercial :** l'envoi d'e-mail — le lien client se
copie à la main, ce qui est suffisant sans domaine mais ne l'est pas
durablement. Et la **durée** de conservation, qui se décide organisation par
organisation : le dépôt fournit la forme (durée, juridiction, source datée,
date d'effet, validateur) et n'en remplit aucune, faute de source qu'il
puisse tenir.

---

## Phase 2 — Intelligence documentaire · **Socle livré, premier parcours de plan livré**

Périmètre : dépôt sécurisé PDF/image/XLSX, extraction texte native, OCR par
adaptateur, classification, extraction structurée de quelques champs et clauses,
citations page/zone, écran de validation côté à côté, recherche plein texte,
comparaison de deux révisions, jeux d'évaluation anonymisés.

**Ce qui est livré, et c'est le socle, pas le traitement** : le dépôt d'un
fichier et son original immuable avec empreinte SHA-256 ; le type réel lu dans
les octets et non dans l'extension, avec refus nommé des exécutables, des
archives et du HTML ; les révisions et le téléchargement à l'identique ; la
citation structurée — page, plage de caractères, boîte englobante, feuille,
calque, objet ; la proposition d'extraction versionnée et son statut ; l'état
idempotent par étape de pipeline ; les sept ports de traitement en `Protocol`.

**Ce qui est livré ensuite, et c'est un parcours entier mais étroit** : un plan
**DXF** se dépose, s'affiche dans Metreo sans logiciel externe, et rend ses
mesures avec leur provenance exacte — feuille, calque, handle de l'objet — et
leur réserve ; chacune se confirme, se corrige ou se refuse, et la décision
humaine est consignée sans jamais réécrire la proposition machine. Deux des
quinze étapes de pipeline s'exécutent pour de bon : `cad_read` et
`page_render`. Mesuré de bout en bout sur un plan d'exécution réel de 7,4 Mo :
663 mesures proposées dont 8 à vérifier, 663 situées sur l'image, en 14,1 s.

**Ce qui n'est toujours pas commencé** : la lecture d'un **PDF** — ni son
affichage, ni sa géométrie vectorielle, ni son OCR ; `vector_geometry` et
`measurement` sont déclarées et vides. Aucune exécution asynchrone :
`apps/worker/` n'a pas d'exécuteur, parce qu'il n'y a pas de file — le
traitement hors requête est `scripts/lire_un_plan.py`, un processus par
fichier. Aucune classification, aucune recherche, aucune comparaison de
révisions. Les quatre scénarios d'acceptation ci-dessous ne sont pas
automatisés — le scénario 12 l'est pour un PLAN, pas pour une clause de texte.

Critères de fin :

- Un PDF scanné est traité en arrière-plan et son état est visible (scénario 11).
- Une clause extraite renvoie à la bonne page/zone et peut être acceptée,
  corrigée ou rejetée (scénario 12).
- Sous le seuil de confiance, **aucune donnée approuvée n'est créée** (scénario 13).
- Une instruction malveillante dans un PDF ne change pas le comportement du
  système (scénario 14).
- L'extraction reste désactivable : l'édition d'un devis fonctionne sans elle.

Dépendances tranchées depuis : les bibliothèques et leurs licences sont
arrêtées par `docs/adr/0007-lecture-de-plans.md` — Tesseract et ses données en
Apache-2.0 pour l'OCR, PyMuPDF et Ghostscript écartés pour cause d'AGPL,
Poppler écarté pour cause de GPL. Restent à trancher : la zone d'hébergement
des données et le budget par page.

---

## Phase 3 — Métrés assistés, plans et CAO/BIM · **Non commencée**

Périmètre : visionneuse et annotation, mesures manuelles traçables, extraction
IFC/DXF, extraction assistée progressive, rapprochement avec le bordereau,
contrôles unités/échelles.

**Le DWG sort du périmètre, et ce n'est pas un report.** Aucune option n'est à
la fois licite pour un service commercial, assez mûre pour porter une quantité
facturée, et sûre : le motif de refus de chacune est consigné dans
`docs/adr/0007-lecture-de-plans.md`. Un DWG déposé est reconnu à son en-tête de
version et refusé en nommant la sortie — exporter en DXF ou en PDF. Le mot
« DWG » n'apparaît dans aucune interface ni aucune offre.

**Première brique livrée** : le DXF est reconnu au dépôt, ASCII comme binaire,
et rangé sous son propre type. Il passait jusqu'ici pour un CSV, faute de
signature — un plan était donc stocké en `.csv` et offert au pipeline d'import
de prix. Reconnaître un fichier n'est pas le lire : aucune géométrie n'est
encore extraite.

Critères de fin :

- Toute quantité issue d'un plan conserve fichier, version, feuille,
  calque/objet, unité source, échelle, formule, auteur ou moteur, confiance et
  statut de validation.
- Un écart entre plan et bordereau est **présenté**, jamais corrigé
  automatiquement.
- Le niveau de prise en charge de DWG est annoncé honnêtement, licences du
  convertisseur respectées.

---

## Phase 4 — Achats et demandes de prix · **Non commencée**

Périmètre : annuaire, lots de consultation, brouillons multilingues,
confirmation humaine et envoi, réception/import des offres, comparatif
normalisé, relances contrôlées, premiers connecteurs autorisés.

Critères de fin :

- Aucun message ne part sans confirmation explicite de l'utilisateur
  (scénario 15).
- Une offre exprimée dans une autre unité n'est comparée qu'après conversion
  explicite et traçable (scénario 16).
- Chaque connecteur déclare authentification, finalités, données, rétention,
  quotas, coûts, conditions et stratégie de désactivation.
- Aucun scraping. API officielle, accord contractuel, import utilisateur ou lien
  de recherche guidé.

---

## Phase 5 — Industrialisation Belgique · **Non commencée**

Périmètre : packs Wallonie/Flandre/Bruxelles validés, français/néerlandais,
intégrations approuvées, conformité, sécurité, sauvegardes, supervision, pilote
avec données réelles sous accord, boucle coûts estimés/réels.

Critères de fin :

- Les packs régionaux passent de `draft` à `published` avec source officielle
  datée et validation métier nommée.
- L'interface est complète en néerlandais.
- RLS PostgreSQL, MFA/SSO, antivirus, URL signées, sauvegardes chiffrées avec
  restauration testée.
- Les coûts réels remontent dans la bibliothèque **sans écraser l'historique**.

---

## Phase 6 — France puis Europe · **Non commencée**

Périmètre : pack France (SIREN/SIRET, CCTP/DPGF/DQE/BPU), adaptations fiscales
et documentaires validées, connecteurs locaux, nouveaux pays par packs
versionnés.

Critère de fin : ajouter un pays ne demande **aucune modification du moteur de
calcul** — seulement un pack versionné et des traductions.

---

## Dette technique connue

| Sujet | Impact | Quand |
| --- | --- | --- |
| Pas de RLS PostgreSQL | L'isolation repose sur la couche service (testée) sans filet de sécurité base | Phase 5 |
| `apps/worker` sans exécuteur | Le premier traitement hors requête existe (`scripts/lire_un_plan.py`), mais il traite UNE révision nommée et s'arrête : il n'y a pas de file à dépiler | Phase 2 |
| Lecture de plan SYNCHRONE | L'analyse part dans la requête HTTP. Mesuré de bout en bout sur un plan réel de 7,4 Mo : **14,1 s**. D'où le plafond `METREO_PLAN_SYNC_MAX_BYTES` (12 Mio) et le script hors requête pour le reste | Phase 2 |
| Seul le DXF est lu | Un PDF se dépose et se télécharge, mais ne s'affiche ni ne se mesure. Rien dans l'écran ne prétend le contraire | Phase 2 |
