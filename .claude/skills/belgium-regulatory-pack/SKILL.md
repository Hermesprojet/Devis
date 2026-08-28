---
name: belgium-regulatory-pack
description: À utiliser dès qu'une variation par PAYS ou par RÉGION entre en jeu dans Metreo — RegionProfile et la table region_profiles, les packs BE-WAL / BE-VLG / BE-BRU / FR, Wallonie, Flandre, Bruxelles, une locale fr-BE / nl-BE / de-BE / fr-FR, la terminologie locale (métré, meetstaat, bestek, cahier spécial des charges, CCTP, DPGF, DQE, BPU), un taux de TVA et sa date d'effet, le format d'un numéro d'entreprise BCE/KBO, d'un SIREN ou d'un SIRET, la traçabilité des terres excavées, une classification de déchets, une mention légale de devis, l'ajout d'un pays ou d'une région — ou dès qu'un code, un export ou une réponse risque d'énoncer une obligation réglementaire sans version, sans date d'effet, sans source officielle datée ni réserve de validation par un spécialiste. Concerne le CONTENU versionné de la règle, pas les connecteurs qui iraient la chercher (supplier-rfq) ni le calcul qui l'applique (price-engine).
---

# Metreo — packs pays/région versionnés (phase 1 partielle, phases 5-6 prévues)

**État réel : partiellement implémenté.** La table existe, quatre packs sont
semés en `draft` ou `planned` avec `disclaimer`, **aucune règle n'est validée
juridiquement**. Ne jamais présenter un pack comme une règle applicable.

Chemins abrégés : `models.py`, `seed.py`, `schemas.py`, `config.py`, `routers/`,
`services/` vivent sous `apps/api/src/metreo_api/`.

## 1. Ce qui existe, ce qui n'existe pas

| Existe | Ancrage |
| --- | --- |
| Modèle `RegionProfile` (table `region_profiles`, unicité `uq_region_code_version` sur `code` + `version`) | `models.py` |
| Quatre packs semés : `BE-WAL`, `BE-VLG`, `BE-BRU` (`draft`), `FR` (`planned`), tous en `version` `2026.1`, `effective_from` 2026-01-01 | `seed.py` → `REGION_PACKS`, `_get_or_create_region_packs()` |
| Lecture publique `GET /api/v1/region-profiles?country_code=` | `routers/meta.py` → `list_region_profiles()` |
| Sérialisation `RegionProfileOut` | `schemas.py` |
| Test de garde-fou `test_region_packs_declare_their_status_and_disclaimer` | `apps/api/tests/test_platform.py` |
| Création de la table | `apps/api/alembic/versions/20260820_1726_initial_schema.py` |
| Locale par défaut `fr-BE`, devise `EUR` | `config.py` (`default_locale`, `default_currency`) |
| Couche i18n front (`Locale`, `SUPPORTED_LOCALES`, `DEFAULT_LOCALE`, `t()`) | `apps/web/src/lib/i18n.ts` |

N'existent **pas** (ne jamais les citer comme disponibles) : `CountryProfile`,
`RuleSetVersion`, `Connector`, tout résolveur de pack, toute liaison
projet ↔ plusieurs profils, toute traduction NL ou DE réelle, toute source
officielle renseignée (`sources` vaut `[]` ou un libellé « à compléter »).

## 2. Règle d'or

Aucune règle réglementaire codée en dur. Une règle n'existe que dans un pack
versionné portant les **quatre** garanties suivantes, sans exception :

1. `version` (chaîne, ex. `2026.1`) ;
2. `effective_from` (date d'effet) ;
3. `sources` non vide : `label`, `url`, `checked_on` par entrée ;
4. `disclaimer` + `status` (`draft` tant qu'un spécialiste n'a pas validé).

```python
# INTERDIT — règle régionale en dur dans le code applicatif
if project.region_code == "BE-WAL":
    require_soil_traceability_document()
```

```python
# ATTENDU — la règle vient du pack ; le résolveur reste À ÉCRIRE
# (aucune fonction de résolution n'existe aujourd'hui dans le dépôt)
pack = resolve_region_pack(session, code=project.region_code, at=reference_date)
rule = pack.rules.get("soil_traceability")
if rule and rule.get("enabled"):
    # afficher pack.version, pack.effective_from, pack.sources, pack.disclaimer
    # signaler rule["requires_expert_validation"] — ne jamais conclure à la place
    ...
```

Un pack se corrige en créant une **nouvelle `version`**, jamais en modifiant en
place une version déjà publiée : `uq_region_code_version` autorise plusieurs
versions du même `code`, c'est le mécanisme prévu.

## 3. Ce qu'un pack peut définir

| Domaine | Support | État |
| --- | --- | --- |
| Langues et formats locaux | `default_locale`, `locales` | implémenté (valeurs semées) |
| Devise | `default_currency` | implémenté |
| Terminologie locale | `terminology` (JSON) | implémenté, 4 clés seulement (§ 5) |
| Règles de validation régionales | `rules` (JSON) | implémenté, 2 clés semées : `soil_traceability` (BE-WAL, BE-VLG, BE-BRU), `identifiers` (BE-WAL, BE-VLG, FR) |
| Sources officielles | `sources` (JSON) | structure implémentée, **contenu vide** |
| Unités et arrondis | `OrganizationSettings.rounding_scale`, `rounding_mode`, `unit_price_scale` | par organisation, **pas encore rattaché au pack** — calculs : **price-engine** |
| Taux de taxe et dates | `TaxRateRow` (`tax_rates`, `applies_from`, `applies_to`, `is_default`, `source`) résolu par `services/estimating.py` → `active_taxes()` | par organisation, **pas encore alimenté par le pack** |
| Mentions et modèles de documents | — | prévu phase 5/6 ; `services/exports.py` produit un CSV et un HTML sans modèle régional |
| Classifications de travaux et de déchets | — | prévu, rien dans le dépôt |
| Profils de conformité | — | prévu, rien dans le dépôt |
| Connecteurs autorisés | — | prévu, aucun connecteur (§ 8) |

Rattacher taxes, arrondis et mentions au pack reste à faire : par migration
Alembic + test, jamais par une valeur par défaut cachée dans le code.

## 4. Belgique

- **Trois profils distincts et non fusionnables** : `BE-WAL`, `BE-VLG`,
  `BE-BRU`. Jamais de pack `BE` unique, jamais d'héritage entre régions.
- **Langues** : français et néerlandais d'abord, allemand ensuite. Semé :
  `BE-WAL` = `["fr-BE", "de-BE"]`, `BE-VLG` = `["nl-BE", "fr-BE"]`,
  `BE-BRU` = `["fr-BE", "nl-BE"]`. Toute chaîne visible passe par `t()`
  (`apps/web/src/lib/i18n.ts`) : ajouter le néerlandais doit rester une
  traduction, pas une refonte.
- **Traçabilité des terres excavées** : le pack **déclare l'existence** de
  l'obligation (`rules.soil_traceability.enabled`,
  `requires_expert_validation`, `note`) et rien de plus. Ne pas coder de
  procédure, de seuil, de formulaire, de délai ni de nom d'organisme.
- **Numéro d'entreprise (BCE)** : champ `Organization.company_number`
  (`String(50)`, **nullable**) ; le format vit dans le pack
  (`rules.identifiers.company_number.pattern`, semé `^(BE)?0?\d{9}$`, label
  `Numéro d'entreprise (BCE)` en FR, `Ondernemingsnummer` en NL). Validé mais
  extensible : jamais de `NOT NULL`, jamais de regex figée dans `schemas.py`,
  jamais de contrôle de clé de contrôle réputé officiel.
- **Un projet, plusieurs profils** : aujourd'hui `Project.country_code` +
  `Project.region_code` désignent **un seul** profil (défaut `BE` / `BE-WAL`).
  Si un marché en exige plusieurs, ajouter une table de liaison par migration
  Alembic — jamais une liste sérialisée dans `region_code` (`String(10)`).

## 5. Terminologie FR / NL et équivalent France

| Clé `terminology` | Wallonie / Bruxelles (fr-BE) | Flandre (nl-BE) | France (fr-FR) |
| --- | --- | --- | --- |
| `boq` | métré | meetstaat | DPGF |
| `specification` | cahier spécial des charges (CSC) | bestek | CCTP |
| `unit_price_schedule` | bordereau de prix unitaires | eenheidsprijzenlijst | BPU |
| `detailed_quantities` | métré détaillé | *(à définir)* | DQE |

Valeurs FR semées dans `BE-WAL`, NL dans `BE-VLG`, France dans le pack `FR` ;
`BE-BRU` ne déclare aujourd'hui que `boq` et `specification`.

- Une clé absente se résout par le libellé technique neutre, **jamais** par la
  valeur d'une autre région.
- Ajouter une clé : l'ajouter dans **tous** les packs du même pays dans le même
  commit, et étendre `test_region_packs_declare_their_status_and_disclaimer`.
- Glossaire de calcul (déboursé sec, frais généraux, marge) : **btp-product-rules**.

## 6. France — préparé, non implémenté

- Pack `FR`, `status` `planned`, `disclaimer` explicite : seuls terminologie et
  identifiants sont préparés. Ne jamais le proposer comme utilisable.
- `rules.identifiers.company_number` semé avec `pattern` `^\d{9}$`, label
  `SIREN`. Le SIRET (14 chiffres) n'est pas modélisé : l'ajouter dans le pack,
  jamais dans une validation Python.
- Modèles de devis configurables, règles de déchets et de terres, adaptations
  fiscales et documentaires : phase 6, rien dans le dépôt.
- Vocabulaire de `status` utilisé aujourd'hui : `draft` et `planned`. Toute
  nouvelle valeur (`active`, `retired`…) s'introduit avec le code qui la lit et
  le test qui la vérifie, pas avant.

## 7. Europe — ce qui doit rester extensible

Restent paramétrables et ne deviennent jamais un `if` sur le pays : devise
(`default_currency`, `Organization.currency`, `Estimate.currency`), langue,
taxes, unités, identifiants, format d'adresse (`Project.address` /
`postal_code` / `city` / `country_code` sont libres : ne pas imposer un format
belge) et politiques de données. Ajouter un pays = une entrée dans
`REGION_PACKS` + un test + les traductions : `terminology`, `rules` et
`sources` sont des colonnes JSON exactement pour cela, une migration par pays
est un défaut de conception.

## 8. Sources officielles et connecteurs

- Toute règle inscrite dans un pack cite une source officielle **datée**
  (`checked_on`) et conserve la référence dans le pack, pas dans un commentaire.
  Une source officielle n'est pas un connecteur : les modes d'accès admis et
  l'interdiction du scraping sont posés par **btp-product-rules**, leur mise en
  œuvre par **supplier-rfq**.
- Aucun connecteur n'est implémenté : aucun modèle `Connector`, aucun appel
  sortant. Ne pas décrire une intégration comme disponible.
- Aucune sortie de LLM, d'OCR ou de recherche web ne devient une règle de pack
  sans validation humaine tracée (**document-analysis**).

## 9. Jamais d'avis juridique automatique

- Toute règle affichée montre `version`, `effective_from`, `sources` et
  `disclaimer`. Sans ces quatre éléments : ne pas afficher la règle. Or
  `RegionProfileOut` (`schemas.py`) expose `version` mais **pas**
  `effective_from` : ajouter le champ au schéma avant d'afficher une règle.
- Formuler « le pack `BE-WAL` `2026.1` signale une obligation à confirmer »,
  jamais « vous devez » ni « c'est obligatoire ».
- Les données géologiques, environnementales ou de pollution ne remplacent
  jamais l'avis du bureau d'étude compétent : le rappeler à l'affichage.
- `services/exports.py` n'imprime aujourd'hui que
  `Profil réglementaire: {country_code} / {region_code}`. Avant toute mise en
  production, y ajouter version, date d'effet et avertissement.
- `EstimateVersion` gèle les prix (`snapshot`) ainsi que `markup`, `taxes` et
  `rounding`, mais **pas** la version du pack. Si une règle de pack entre dans un
  calcul ou dans une mention de devis, ajouter cette référence au gel par
  migration : sinon la traçabilité est fausse. Un devis gelé ne se recalcule
  jamais parce qu'un pack a changé (**price-engine**).

## 10. Procédure de modification d'un pack

1. Nouvelle `version` + `effective_from` ; l'ancienne version reste lisible.
2. `sources` renseignées et datées ; `status` reste `draft` sans validation.
3. Migration Alembic si le schéma bouge ; jamais de modification manuelle.
4. Test dans `apps/api/tests/test_platform.py`.
5. Décision structurante consignée dans `docs/adr/`.
6. Isolation et permissions inchangées : `region_profiles` est global et en
   lecture seule côté API — voir **multitenant-security**. Critères de sortie :
   **definition-of-done**.

## Signaux d'alerte

- Un `if` sur `region_code` ou `country_code` qui décide d'une règle
  réglementaire, d'un taux, d'une mention ou d'un document obligatoire.
- Une obligation, un seuil, un délai ou un formulaire régional écrit dans le
  code, un prompt, un test ou une chaîne d'interface plutôt que dans un pack.
- Une règle affichée sans `version`, sans `effective_from`, sans source datée
  ou sans `disclaimer` ; un pack `draft` présenté comme applicable.
- Une phrase impérative (« vous devez », « c'est obligatoire », « conforme »)
  produite automatiquement à partir d'un pack.
- Un pack publié modifié en place au lieu d'une nouvelle `version`.
- `sources` laissé vide, ou une source sans `url` ni `checked_on`.
- Un pack `BE` unique, une région belge héritant d'une autre, ou une valeur de
  terminologie empruntée à une autre région pour combler une clé manquante.
- Le pack `FR` (`planned`) utilisé comme s'il était implémenté.
- Un format de numéro d'entreprise, de SIREN ou de SIRET figé dans
  `schemas.py` ou dans le modèle, ou `company_number` rendu obligatoire.
- Plusieurs profils stockés dans `Project.region_code` au lieu d'une table de
  liaison créée par migration.
- Une clé de `terminology` ou de `rules` ajoutée dans un seul pack, sans test.
- Un taux de taxe codé en dur, sans `applies_from`, ou contournant
  `active_taxes()`.
- Un appel réseau vers Walterre, Embuild ou tout autre service, ou une API
  supposée exister à partir d'un site web.
- Une règle de pack issue d'une extraction automatique sans validation humaine.
- Un devis gelé recalculé, ou une règle de pack utilisée sans être tracée dans
  `EstimateVersion.snapshot`.
