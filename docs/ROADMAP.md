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

## Phase 1 — Première tranche verticale utilisable · **Fonctionnellement complète, candidate de validation**

> **Phase 1 fonctionnellement complète — candidate de validation.**
> Déploiement et clôture de sécurité **bloqués** jusqu'à Next.js 15.5.24.
> **Production non prête** : authentification réelle, sauvegardes, supervision
> et packs juridiques validés restent absents.
>
> Ces trois choses sont distinctes et ne se remplacent pas : *fonctionnellement
> complet* décrit ce qu'un utilisateur peut faire et ce que les tests prouvent ;
> *déployable* suppose en plus qu'aucun correctif de sécurité connu ne manque ;
> *prêt pour la production* suppose l'exploitation.


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

Fonctionne localement avec des fournisseurs factices et **aucune clé payante**.

**Reste ouvert en phase 1 :** import XLSX (seul le CSV est fait), scénarios bas /
probable / haut exposés par l'API (la fonction `sensitivity` existe dans le
domaine et est testée, aucun point d'entrée HTTP ne l'expose encore).

**Ouvert autour du cycle commercial :** l'envoi d'e-mail — le lien client se
copie à la main, ce qui est suffisant sans domaine mais ne l'est pas
durablement. Et la **durée** de conservation, qui se décide organisation par
organisation : le dépôt fournit la forme (durée, juridiction, source datée,
date d'effet, validateur) et n'en remplit aucune, faute de source qu'il
puisse tenir.

---

## Phase 2 — Intelligence documentaire · **Non commencée**

Périmètre : dépôt sécurisé PDF/image/XLSX, extraction texte native, OCR par
adaptateur, classification, extraction structurée de quelques champs et clauses,
citations page/zone, écran de validation côté à côté, recherche plein texte,
comparaison de deux révisions, jeux d'évaluation anonymisés.

Critères de fin :

- Un PDF scanné est traité en arrière-plan et son état est visible (scénario 11).
- Une clause extraite renvoie à la bonne page/zone et peut être acceptée,
  corrigée ou rejetée (scénario 12).
- Sous le seuil de confiance, **aucune donnée approuvée n'est créée** (scénario 13).
- Une instruction malveillante dans un PDF ne change pas le comportement du
  système (scénario 14).
- L'extraction reste désactivable : l'édition d'un devis fonctionne sans elle.

Dépendances à trancher avant de commencer : fournisseur OCR autorisé, zone
d'hébergement des données, budget par page.

---

## Phase 3 — Métrés assistés, plans et CAO/BIM · **Non commencée**

Périmètre : visionneuse et annotation, mesures manuelles traçables, extraction
IFC/DXF, interface de conversion DWG, extraction assistée progressive,
rapprochement avec le bordereau, contrôles unités/échelles.

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
| `apps/worker` vide | Les opérations longues n'existent pas encore ; le répertoire est réservé | Phase 2 |
| Import XLSX absent | Seul le CSV est pris en charge | Phase 2 |
| Sous-détails non éditables depuis l'interface | Création par API uniquement | Phase 1 bis |
| Analyse de sensibilité non exposée par l'API | Implémentée et testée dans le domaine | Phase 1 bis |
