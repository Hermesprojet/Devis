# Skills Metreo

Huit skills encadrent le travail sur ce dépôt. Chacun vit dans
`.claude/skills/<nom>/SKILL.md`, rédigé en français, avec un frontmatter YAML
`name` + `description`, un titre `#` rappelant la phase, des sections numérotées
`## N.` et une section finale `## Signaux d'alerte`. **Une règle n'est écrite
qu'une fois** : les autres skills y renvoient par son nom en gras.

## Les huit skills

| Skill | Se déclenche quand… | Statut |
| --- | --- | --- |
| **btp-product-rules** | aucun skill spécialisé ne couvre la question, deux d'entre eux semblent se contredire, ou une décision structurante d'architecture, de format, de dépendance ou de découpage se pose | transverse — constitution, arbitre les phases 1 à 6 |
| **price-engine** | un calcul est en jeu : `Decimal`, unités, conversions, densité sourcée, composants de sous-détail, frais, aléas, marges, taxes, arrondis, gel et recalcul depuis l'instantané | implémenté (phase 1) |
| **multitenant-security** | il faut décider qui accède à quoi : `organization_id`, jetons, permissions, 404 vs 403, masquage des coûts, secrets, journal d'audit | implémenté (phase 1) |
| **definition-of-done** | une tranche approche de sa fin : série de vérifications, CI, compte rendu d'itération, ce qui peut être dit « terminé » | implémenté (phase 1) |
| **belgium-regulatory-pack** | une variation par pays ou région entre en jeu : `RegionProfile`, terminologie locale, locales, TVA datée, identifiants légaux, mentions de devis | partiellement implémenté — table et packs `draft` / `planned` semés, aucune règle validée juridiquement (phases 5-6) |
| **document-analysis** | le **texte** d'un document de marché est en cause : upload, OCR, extraction structurée, citations, seuils de confiance, validation humaine | phase 2A : socle relationnel/contrats/API ; pipeline absent |
| **cad-bim-takeoff** | la **géométrie** d'un plan ou d'un fichier CAO/BIM est en cause : échelle, feuilles, calques, IFC/DXF/DWG, mesure traçable, rapprochement plan ↔ bordereau | cahier des charges — phase 3, aucun code |
| **supplier-rfq** | un tiers externe entre en jeu : annuaire fournisseurs, demande de prix, envoi confirmé par un humain, connecteurs, comparatif d'offres | cahier des charges — phase 4, aucun code |

Frontières à ne pas confondre : un plan est **classé** par `document-analysis`
mais **mesuré** par `cad-bim-takeoff` ; une obligation régionale est **décrite**
par `belgium-regulatory-pack` mais **récupérée** par `supplier-rfq` et
**appliquée** par `price-engine`.

## Ajouter un skill au dépôt

1. Créer `.claude/skills/<nom>/SKILL.md`. Le champ `name` du frontmatter doit
   être **exactement** le nom du dossier ; les deux seules clés autorisées sont
   `name` et `description`.
2. Rédiger une `description` à la 3ᵉ personne qui dit **quand** utiliser le
   skill, avec des mots-déclencheurs concrets (symboles, chemins, termes métier).
   Elle doit être **discriminante** face aux huit descriptions existantes : un
   lecteur choisit sans hésiter. Piège YAML : ne jamais y écrire une espace après
   un deux-points (`… audit : ajouter`) — la description est un scalaire non
   quoté, cela casse le parsing. Employer un point ou un tiret cadratin.
3. Corps en français, identifiants de code en anglais. Style impératif et
   vérifiable : tableaux, listes, extraits courts. Pas de prose de présentation.
4. **120 à 220 lignes.** Densité avant exhaustivité.
5. Ne citer que des chemins et des symboles vérifiés au préalable avec `ls` et
   `grep`. Distinguer explicitement ce qui est implémenté de ce qui est prévu
   (phase N) — un skill qui laisse croire qu'une phase ≥ 2 existe est un défaut.
6. Ne pas dupliquer le contenu d'un autre skill : renvoyer vers lui par son nom
   en gras. Si la règle nouvelle recouvre une règle existante, corriger l'autre
   fichier plutôt que recopier.
7. Terminer par `## Signaux d'alerte` : la liste des erreurs concrètes que le
   skill doit faire éviter, sans phrase d'introduction.
8. Ajouter la ligne correspondante au tableau ci-dessus, et relire les
   descriptions voisines pour vérifier qu'aucune ne se recouvre.
