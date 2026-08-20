---
name: supplier-rfq
description: À utiliser dès qu'il est question de fournisseurs, de sous-traitants ou d'achats dans Metreo — construire l'annuaire interne (identité légale, numéro d'entreprise, catégories, zones, contacts, qualifications, assurances, documents de conformité, évaluations, délais, statut RGPD), monter un lot de consultation et une demande de prix, rédiger un brouillon d'e-mail multilingue FR/NL/EN, choisir et masquer les pièces jointes, envoyer ou relancer une consultation, brancher ou évaluer un connecteur de recherche et d'enrichissement (Google Places, Embuild, Walterre, LinkedIn, registres publics), trancher une question de scraping ou de conditions d'utilisation, importer une offre reçue et la comparer à périmètre égal (unités, quantités, délais, validité, exclusions, variantes), ou diagnostiquer un message parti sans confirmation humaine, un comparatif qui additionne des unités différentes, un gagnant désigné automatiquement, une pièce jointe confidentielle envoyée par erreur ou une offre sans document original.
---

## 1. État réel : phase 4, aucune ligne de code

Rien ici n'est implémenté : **ni table fournisseur, ni demande de prix, ni offre, ni connecteur,
ni envoi d'e-mail**. `apps/api/src/metreo_api/models.py` ne déclare aucun `__tablename__` de ce
domaine, `main.py` n'inclut que `meta`, `auth`, `organizations`, `projects`, `pricebooks`, `boq`,
`estimates`, `audit_log`, et `.env.example` n'a aucune variable de messagerie ni de connecteur.

- Seul point de contact existant : `PriceItem.supplier_name`, **texte libre** alimenté par la colonne
  `supplier_name` / `fournisseur` du CSV (`services/price_import.py`) ; la phase 4 le remplace par une
  clé étrangère.
- Ne jamais présenter ces fonctions comme disponibles (`apps/web/src/app/parametres/page.tsx`
  annonce qu'elles ne le sont pas), ni livrer un connecteur factice : interface remplaçable +
  implémentation locale étiquetée, comme `Settings.ai_provider="null"` (`config.py`).
- Tables cibles (cahier des charges §10) : `Supplier`, `SupplierContact`, `SupplierQualification`,
  `SupplierCategory`, `ServiceArea`, `RFQ`, `RFQPackage`, `RFQRecipient`, `RFQMessage`,
  `SupplierOffer`, `SupplierOfferLine`, `Connector`, `ConnectorCredentialReference`, `ConnectorRun`.

## 2. L'annuaire interne d'abord, le connecteur ensuite

Aucun connecteur ne se conçoit avant que l'annuaire ne soit complet et utilisable seul :

| Bloc | Contenu | Contrainte |
| --- | --- | --- |
| Identité légale | `legal_name`, `company_number` (BCE/KBO), forme juridique, TVA, siège | `company_number` unique **par organisation**, jamais globalement |
| Catégories | familles de travaux et `resource_kind` (`material`, `labor`, `equipment`, `transport`, `disposal`, `subcontract`) | mêmes valeurs que `packages/domain/src/metreo_domain/pricing.py` |
| Zones | provinces, communes, rayon km, zone de reprise de terres | une zone vide n'est pas « partout », c'est inconnu |
| Contacts | nom, fonction, e-mail, téléphone, langue de correspondance | statut RGPD par contact (§3) |
| Qualifications | agréations, classes, certificats, enregistrements | date de validité + justificatif obligatoires |
| Assurances | RC, décennale, accidents du travail : assureur, police, montants, échéance | une échéance dépassée dégrade le statut, ne supprime pas la ligne |
| Documents de conformité | attestations ONSS/TVA, capacité, sécurité, terres | stockage immuable + SHA-256 |
| Historique | consultations, réponses, taux de réponse, délais constatés | dérivé des `RFQ`, jamais saisi à la main |
| Évaluations | qualité, délais, sécurité, litiges | auteur, date et projet obligatoires |
| Délais | délai standard, délai en pointe, quantité minimale | unité explicite via `get_unit()` ; `d` vaut 8 h de travail, pas un jour calendrier |

Un fournisseur appartient à **une** organisation : `organization_id` non nul, accès via `owned_query`
/ `get_owned` (`apps/api/src/metreo_api/services/tenant.py`). Jamais d'annuaire partagé entre tenants.

## 3. Statut RGPD, contact par contact

Un `SupplierContact` est une personne physique et porte : `legal_basis` (intérêt légitime,
contrat, consentement) et sa finalité ; `source` (saisie, import, connecteur) et `collected_at` ;
`retention_until` issu d'une durée configurable, jamais codée en dur ; `opted_out_at`.

- Un contact opposé est **exclu de toute sélection de destinataires**, relances comprises, et l'UI
  dit pourquoi il est grisé.
- Export et suppression sur demande sans casser l'audit : on efface le contact, on conserve
  l'`AuditEvent` qui référence son identifiant.
- Le suivi d'ouverture (étape 7) n'existe que s'il est légal dans le pays du destinataire **et**
  disponible chez le fournisseur e-mail ; sinon le champ reste `null`, jamais « non ouvert ».

## 4. Interface de connecteur : la fiche de déclaration est bloquante

Un connecteur implémente une interface unique (rechercher des candidats / enrichir une fiche) et
**DOIT** déclarer, en données et non en commentaire, les dix éléments ci-dessous avant d'être
activable. Un champ vide ⇒ connecteur désactivé, sans dérogation.

| Déclaration | Contenu exigé |
| --- | --- |
| `authentication` | mécanisme exact (clé API, OAuth2, mTLS) et **où vit le secret** : variable `METREO_*` ou coffre, jamais en base en clair ni dans le dépôt |
| `permissions` | la `Permission` Metreo exigée **et** le périmètre accordé côté fournisseur. `ROLE_PERMISSIONS` (`security/roles.py`) est une matrice plate, sans hiérarchie de rôles : créer une permission dédiée, `Role.BUYER` n'ayant aujourd'hui ni `MARGIN_READ` ni droit d'envoi |
| `purposes` | finalités limitatives ; tout autre usage est un défaut |
| `data_obtained` | liste nominative des champs récupérés, pas « profil » ni « données de contact » |
| `retention` | durée de conservation des données du connecteur, distincte de celle de l'annuaire |
| `quotas` | limites par minute/jour et comportement au dépassement (file, refus — jamais une boucle de retry) |
| `costs` | coût par appel ou par mois, et plafond au-delà duquel le connecteur se coupe |
| `limits` | ce que la source ne donne pas, sa fraîcheur, sa couverture géographique |
| `terms_url` | conditions d'utilisation **avec la date de leur consultation** |
| `disable_strategy` | comment on coupe : sort des données déjà importées, dégradation acceptée, purge |

Chaque exécution est journalisée (`ConnectorRun` : connecteur, acteur, requête, volume, coût, résultat)
et chaque champ enrichi garde `source` + `retrieved_at`. Une donnée de connecteur est **proposée**,
jamais écrite d'office : acceptation, correction ou rejet, comme dans **document-analysis**.

## 5. Sources externes : verdict source par source

| Source | Autorisé | Interdit |
| --- | --- | --- |
| Données internes, import/export utilisateur | oui, sans condition | — |
| Registres publics officiels | oui **si** la réutilisation est explicitement permise | rejouer une base entière hors licence |
| Google Places (Text Search) | oui via l'API officielle, règles d'affichage et de cache respectées | requêter les pages web de Google |
| Embuild | oui **si** un accès, une API ou un accord existe réellement | déduire une API de l'existence du site |
| Walterre et sources officielles « terres » | oui **si** l'accès l'autorise | aspirer les fiches publiques |
| LinkedIn | uniquement via une fonctionnalité/API officiellement autorisée ; à défaut **un simple lien de recherche** que l'utilisateur ouvre | toute récupération automatisée, y compris « une seule page » |

**Aucun scraping, jamais** : ni navigateur headless, ni parsing HTML d'un tiers, ni contournement de
`robots.txt` ou d'un captcha. Les cinq modes d'accès admis et la règle « un site web ne prouve pas
qu'une API existe » sont posés par **btp-product-rules** ; le tableau ci-dessus les applique aux achats.

## 6. Workflow de demande de prix : les 10 étapes

| # | Étape | Condition de passage |
| --- | --- | --- |
| 1 | Sélectionner les postes, définir le périmètre | périmètre écrit : quantités, unités, limites de prestation, exclusions |
| 2 | Sélectionner les entreprises, vérifier les contacts | e-mail vérifié, contact non opposé (§3), fournisseur du bon tenant |
| 3 | Générer un brouillon multilingue | langue du contact (`Locale` = `fr` \| `nl` \| `en`, `apps/web/src/lib/i18n.ts`), pas celle de l'utilisateur |
| 4 | Sélectionner les pièces jointes, masquer les documents non autorisés | §8 |
| 5 | **Faire confirmer l'envoi par un humain** | barrière bloquante, §7 |
| 6 | Envoyer via le fournisseur e-mail configuré | derrière une interface remplaçable ; aucun fournisseur n'est câblé aujourd'hui |
| 7 | Suivre les états | `sent`, `delivered`, `opened` (si légal et disponible), `answered`, `reminded`, `declined`, `expired` |
| 8 | Importer ou saisir l'offre reçue | §9 |
| 9 | Comparer à périmètre égal | §10 |
| 10 | Recommander | **jamais** sélectionner le gagnant automatiquement |

Relances contrôlées (maximum, délai minimal, arrêt sur `declined` ou `opted_out`). Une relance est un envoi : elle repasse par l'étape 5.

## 7. Étape 5 : la confirmation humaine est bloquante

Aucun message ne part sans confirmation explicite, par un humain, de **trois choses distinctes**
affichées côte à côte : les **destinataires** (liste nominative, e-mail complet visible, aucun
destinataire caché ni ajout implicite), les **pièces jointes** (nom, taille, motif d'autorisation),
le **contenu** exact qui partira, dans la langue qui partira. Mise en œuvre calquée sur le patron
de l'import CSV (`create_preview` puis `commit_batch` dans `services/price_import.py`, statut
`previewed` → `committed`, rien n'est écrit avant le commit) :

- la préparation fige un `RFQMessage` et un hash de la charge utile (destinataires + pièces jointes
  + corps) ; la confirmation référence **ce** hash, et toute modification ultérieure l'invalide ;
- une confirmation vaut pour un envoi, expire, et n'est ni réutilisable ni transposable ;
- l'envoi est journalisé via `audit.record(...)` (`services/audit.py`) : `rfq.send_confirmed` puis
  `rfq.message_sent`, avec acteur, date, objet et identifiants des destinataires — jamais le corps
  du message ni les documents dans `payload`, comme l'impose la docstring de `record`.

Interdits : envoi déclenché par un job, un webhook, une auto-relance ou un appel API sans jeton
de confirmation ; route unique « créer + confirmer + envoyer » ; case cochée par défaut.

## 8. Pièces jointes : refus par défaut, masquage explicite

- Liste blanche par consultation **et** par destinataire : par défaut, un document du projet n'est pas joignable.
- Ne sortent jamais : coûts internes, taux horaires chargés, marges, aléas, frais généraux,
  sous-détails, autres offres reçues, notes internes, coordonnées d'un concurrent. Le cloisonnement
  existe déjà (`Permission.COST_READ`, `Permission.MARGIN_READ`, `security/roles.py`) : le réutiliser.
- Un document partiellement confidentiel est masqué **sur une copie** ; l'original n'est jamais
  modifié et la copie envoyée est conservée telle qu'envoyée, avec son SHA-256.
- Liens signés, courts, non devinables ; taille plafonnée comme `Settings.max_upload_bytes`.
- Le récapitulatif affiche par document : nom, version, taille, « masqué : oui/non ». Une pièce
  jointe sans motif d'autorisation bloque l'envoi au lieu de partir.

## 9. Réception d'une offre : conserver l'original, toujours

Une `SupplierOffer` conserve obligatoirement, quel que soit le mode de saisie :

- le **document original** reçu (PDF, XLSX, e-mail), immuable, avec son SHA-256 ;
- la **date** de l'offre et la date de réception ; la **validité** (`valid_until`) : une offre
  expirée est signalée, jamais prolongée ;
- la **devise**, jamais convertie en silence : comparer deux devises lève `CurrencyMismatchError`
  (`packages/domain/src/metreo_domain/errors.py`) tant qu'aucun taux daté et sourcé n'est saisi ;
- les **hypothèses d'alignement** : unité convertie, quantité ramenée, poste rattaché, exclusion
  réintégrée — tout ce qui a été supposé pour rendre l'offre comparable.

Une saisie sans document original marque l'offre `declared` — valeur par défaut de `PriceItem.confidence` (`models.py`), seul niveau de confiance que le dépôt définit aujourd'hui — état visible dans le comparatif et dans tout export.

## 10. Comparaison à périmètre égal

- Comparer prix, **unités**, quantités, délais, validité, exclusions et variantes ; une colonne
  manquante rend la ligne incomparable, elle ne l'approxime pas.
- Une offre exprimée dans une autre unité n'entre au comparatif **qu'après conversion explicite et
  traçable** : `convert()` de `packages/domain/src/metreo_domain/units.py`, avec
  `ConversionResult.explanation` affiché à côté du montant converti. Volume ↔ masse exige une `Density`
  à `source` non vide (sinon `AmbiguousConversionError`), dimensions non pontables ⇒ `IncompatibleUnitsError`.
  Détail : **price-engine** ; ne pas réécrire de conversion locale.
- Le montant d'origine reste affiché à côté du montant normalisé ; l'un ne remplace jamais l'autre.
- Les **exclusions et réserves** sont listées en clair, même sans équivalent chez les concurrents :
  une offre moins chère parce qu'elle exclut l'évacuation n'est pas moins chère.
- L'outil **recommande** (classement, écarts, points d'attention) et **ne sélectionne jamais** le
  gagnant ; le choix est humain, tracé dans l'audit avec son motif. Le comparatif est reproductible.

## 11. Renvois

- Conversions, `Money`, arrondis, sous-détails : **price-engine**. Extraction d'une offre PDF,
  citations, validation humaine : **document-analysis**. Quantités de plans définissant le périmètre
  consulté : **cad-bim-takeoff**.
- Exigences produit générales et interdiction du scraping : **btp-product-rules**. BCE/KBO, TVA,
  agréations, obligations belges, hébergement UE : **belgium-regulatory-pack**. Isolation,
  permissions, secrets : **multitenant-security**. Tests et livraison : **definition-of-done**.

## 12. Critères de fin — scénarios d'acceptation 15 et 16

Une tranche « achats » n'est pas terminée tant que ces deux tests n'existent pas et ne passent pas, dans `apps/api/tests/` avec les fixtures de `conftest.py` :

- **15. Aucun message ne part sans confirmation explicite de l'utilisateur.** En appelant l'API
  directement : envoi sans jeton ⇒ refus ; destinataire modifié après confirmation ⇒ confirmation
  invalidée, refus ; envoi confirmé ⇒ un `RFQMessage` par destinataire et les événements d'audit
  correspondants dans la chaîne validée par `verify_chain`.
- **16. Une offre exprimée dans une autre unité n'est comparée qu'après conversion explicite et
  traçable.** Offre en `t` face à un poste en `m3` : sans `Density` sourcée la ligne est incomparable
  et refuse d'être classée ; avec la densité, le montant normalisé s'affiche avec son explication de
  conversion et la source de la densité.

Ajouter le test d'isolation correspondant (modèle : `apps/api/tests/test_tenant_isolation.py`) :
l'organisation B ne voit ni les fournisseurs, ni les consultations, ni les offres de A.

## Signaux d'alerte

- Un envoi possible sans jeton de confirmation, ou une confirmation encore valable après modification
  des destinataires, des pièces jointes ou du corps du message.
- Une case « confirmer l'envoi » cochée par défaut, une confirmation unique couvrant une campagne
  entière, ou un job, un webhook, une auto-relance capables de déclencher un envoi.
- Un destinataire ajouté implicitement (copie interne, liste dynamique, adresse de test), ou un
  contact `opted_out` réintégré par une relance.
- Une pièce jointe jointe par défaut, un masquage appliqué à l'original au lieu d'une copie, ou un
  sous-détail, une marge ou une offre concurrente partis chez un fournisseur.
- Un comparatif qui additionne `m3` et `t`, une conversion réécrite dans le module achats au lieu de
  `convert()`, une densité « standard » sans `source`, ou un montant converti qui remplace l'original.
- Une devise alignée avec un taux ni daté ni sourcé, ou une offre expirée prolongée d'office.
- Un gagnant présélectionné, une offre « recommandée » cochée d'avance, un classement qui ignore
  exclusions et réserves, ou un comparatif non reproductible.
- Une offre saisie sans document original, date, devise ou validité, présentée avec la même confiance
  qu'une offre documentée.
- Un connecteur activable avec un champ de déclaration vide, un secret de connecteur en base ou dans
  le dépôt, un `ConnectorRun` non journalisé, ou une donnée de connecteur écrite d'office sur une
  fiche validée sans proposition acceptable, corrigeable ou rejetable.
- Du scraping sous n'importe quel nom : navigateur headless, parsing HTML d'un tiers, « juste une
  page », contournement de `robots.txt` ou d'un captcha.
- Une API supposée exister parce que le site existe, ou des conditions citées sans date de consultation.
- Un fournisseur ou une consultation sans `organization_id`, ou une requête sans `owned_query` / `get_owned`.
- Un écran, une route ou une note de version laissant croire que le module achats existe déjà.
