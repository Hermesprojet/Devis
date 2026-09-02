# Scénarios de chiffrage — bas, probable, haut

Une simulation temporaire posée sur une version d'estimation. Elle n'écrit
rien : aucune table, aucune migration, aucun instantané. On envoie des
hypothèses, le serveur renvoie trois chiffrages, et la version reste ce
qu'elle était.

## La règle qui gouverne tout le reste

**Un facteur agit sur les ENTRÉES du moteur, jamais sur le total.**

Multiplier le total par 1,10 donnerait un nombre qui ressemble à un résultat
sans en être un : la TVA ne suit pas la même proportion que le déboursé, une
rotation arrondie ne se met pas à l'échelle, et un forfait ne bouge pas du
tout. Chaque hypothèse modifie donc les valeurs d'entrée qu'elle concerne,
puis **le moteur ordinaire recalcule tout** — rotations et arrondis, frais de
chantier, frais généraux, aléas, marge, taxes. Aucune de ces règles n'est
réécrite dans le module des scénarios.

Corollaire : le **scénario neutre reproduit la référence par construction**, et
non parce qu'on aurait pris soin de le faire coïncider. Sans hypothèse, aucune
entrée n'est modifiée et `appliquer()` rend la liste reçue — pas une copie.

## Les trois axes, et le sens de chacun

| Axe | Ce qu'il modifie | Sens |
| --- | --- | --- |
| `prix` | `unit_price` d'une consommation, `hourly_rate` d'un rendement, `cost_per_rotation` et `rate_per_km` d'un transport, prix de bibliothèque d'une ligne | `+0,10` renchérit de 10 % → le coût **monte** |
| `productivite` | `output_rate` des composants qui en ont un | `+0,10` = « on produit 10 % de plus par heure » → moins d'heures → le coût **baisse** |
| `distance` | `distance_km` d'un transport | `+0,10` allonge le trajet de 10 % → le coût de **chaque rotation** monte, mais leur **nombre** ne change pas |

`productivite` est le seul axe dont le signe s'inverse entre l'hypothèse et son
effet. C'est écrit dans le module, dans le contrat HTTP et à côté du champ de
saisie — jamais supposé connu.

`distance` est appliquée **avant** le calcul des rotations. L'effet n'est donc
pas proportionnel : 100 m³ à 8 m³ par camion font 13 rotations, et 13 rotations
restent 13 rotations quand le trajet s'allonge.

## Ce que les hypothèses ne touchent pas, et pourquoi

- **Les forfaits** (`lump_sum`). Un forfait n'a pas de prix unitaire : c'est un
  montant convenu. Lui appliquer une variation de prix reviendrait à
  renégocier un accord sous couvert de simulation.
- **Les taux commerciaux** — frais de chantier, frais généraux, aléas, marge —
  et les **taux de taxe**. Ils sont recalculés par le moteur à partir des
  réglages de l'organisation, jamais modifiés ici. Faire varier une marge est
  une décision commerciale, pas une hypothèse de chiffrage.
- **Les quantités du bordereau.** Le métré est ce qu'il est.
- **La charge utile, la masse volumique, l'effectif d'équipe, le coefficient de
  perte.** Ce sont des propriétés du camion, du matériau ou de l'équipe, pas
  des hypothèses de conjoncture.

## Les catégories de ressource

Une variation de prix peut être limitée à certaines natures de ressource. La
liste et ses libellés sont **dérivés de `ResourceKind`** (`metreo_domain.pricing`)
et rendus par le serveur dans `categories` : ni le service, ni l'interface n'en
tiennent une seconde copie, qui divergerait à la première nature ajoutée.

Un **prix de bibliothèque** posé sur une ligne ne porte pas de nature de
ressource — celle-ci vit sur la fiche du prix, pas sur l'entrée du moteur. Une
variation ciblée par catégorie ne peut donc pas l'atteindre, et ne prétend pas
le faire ; une variation générale, si.

## Les bornes

| Valeur | Borne (`metreo_domain.bounds`) |
| --- | --- |
| L'écart lui-même | `SCENARIO_VARIATION` : `> -1` (strict) et `≤ 10` |
| Prix unitaire, taux horaire, coût par rotation, coût kilométrique, prix de bibliothèque, **après mise à l'échelle** | `UNIT_PRICE` |
| Rendement, **après mise à l'échelle** | `OUTPUT_RATE` |
| Distance, **après mise à l'échelle** | `DISTANCE_KM` |

La borne basse de `SCENARIO_VARIATION` est **strictement** exclue : `-1` vaut
« -100 % » et met un rendement à zéro, c'est-à-dire un diviseur nul.

Les valeurs mises à l'échelle sont vérifiées **au moment où elles naissent**.
Les valeurs saisies passent par les bornes ; celles qu'un scénario fabrique
n'étaient contrôlées par personne, et le moteur ne vérifie aucune borne au
moment de calculer. Sans ce contrôle, un transport de 19 000 km — légal, il
entre en base — majoré de 10 % devenait un trajet de 20 900 km, au-delà de la
demi-circonférence terrestre que `DISTANCE_KM` déclare, et le total sortait
avec l'apparence d'un résultat.

Un dépassement **refuse le seul scénario concerné**. Ses voisins restent
calculés : perdre une comparaison entière pour une hypothèse mal saisie sur un
tiers de l'écran n'apprendrait rien.

## Les libellés ne garantissent rien

« Bas », « probable » et « haut » sont des noms de colonne, pas une promesse
mathématique. Rien n'oblige « bas » à coûter moins cher : il suffit d'y mettre
une hausse de prix. Quand les totaux ne suivent pas l'ordre des libellés, la
réponse porte `ordre_incoherent: true` et l'écran le **signale**. Il ne
réordonne rien : masquer ce désordre supprimerait l'information la plus utile —
que les hypothèses saisies ne disent pas ce que leur nom laisse croire.

Aucun pourcentage n'est proposé par défaut. Les trois colonnes démarrent à
`0 %` : souffler « -10 / 0 / +10 » inventerait une dispersion qui dépend du
chantier, du marché et du moment, et que personne ici n'est en position de
connaître.

## Le contrat HTTP

`POST /api/v1/estimates/{estimate_id}/versions/{version_id}/scenarios`

Classée **en lecture** dans le registre transactionnel (`transactions.py`) :
c'est un POST parce qu'un corps est nécessaire, et il n'écrit rien.

Corps — les trois clés sont facultatives et valent « neutre » :

```json
{
  "bas":      {"prix": "-0.10"},
  "probable": {},
  "haut":     {"prix": "0.10", "prix_categories": ["material"], "distance": "0.05"}
}
```

Réponse — chaque scénario est une **union discriminée sur `status`** : il porte
ses totaux ou son refus, jamais les deux et jamais aucun des deux.

```json
{
  "from_snapshot": false,
  "includes_internal_costs": true,
  "includes_margin_steps": true,
  "currency": "EUR",
  "ordre_incoherent": false,
  "categories": {"material": "Matériaux", "labor": "Main-d'œuvre", "...": "..."},
  "scenarios": [
    {"status": "success",  "nom": "bas", "hypotheses": {...}, "totaux": {...},
     "lignes_sans_prix": [], "bloquant": false,
     "ecart": {"absolu": "-298.45", "absolu_display": "-298.45", "pourcentage": "-8.69"}},
    {"status": "refused",  "nom": "haut", "hypotheses": {...},
     "refus": {"code": "out_of_bounds", "message": "…", "context": {...}, "scenario": "haut"}}
  ]
}
```

`ecart.absolu` est le décimal **exact** — un écart de productivité est
périodique, 750,00 ÷ 1,1 ne tombe pas juste. `ecart.absolu_display` est le même
écart arrondi par la politique de l'organisation : c'est celui que l'écran
affiche, parce qu'un arrondi décidé dans le navigateur diverge du devis au
premier centime. `pourcentage` vaut `null` quand la référence est nulle.

Une version **gelée** se simule depuis son **instantané** : `from_snapshot` le
dit, et les taux employés sont ceux du jour du gel. Ce choix n'est pas refait
ici — il vient de `compute_version`, seul endroit où il est pris.

## Les permissions

| Permission | Ce qu'elle ouvre |
| --- | --- |
| `estimate:read` | l'accès à la route |
| `cost:read` | **exigée** : sans elle, la route refuse et l'interface n'affiche pas le panneau. Un scénario compare des déboursés. |
| `margin:read` | les étapes de markup et leurs taux. **Séparée de `cost:read`** |

Le rôle `estimator` porte `cost:read` **sans** `margin:read`. Les confondre a
déjà fait apparaître le taux de marge de l'entreprise dans la réponse rendue à
un métreur — sur la route de calcul comme sur celle des scénarios. Le filtre
partagé `totals_for_display` prend donc **deux décisions explicites**,
`include_costs` et `include_margin`, et retire les étapes plutôt que de les
renommer : `{"key": "margin", "rate": "0.08"}` reste un taux de marge quelle que
soit sa clé.

## Ce qui n'existe pas, et n'est pas prévu ici

Aucune persistance de scénario, aucune table, aucune migration. Le bloc livre
une simulation temporaire. Aucun calcul financier n'est dupliqué en TypeScript :
le navigateur envoie les hypothèses et affiche la réponse. Les seules
transformations faites à l'écran portent sur la **saisie** — « 10 » lu comme
« 10 % » devient `0.10`, par décalage de virgule sur la chaîne, jamais par une
division en virgule flottante.

## Preuves

- `apps/api/tests/test_scenarios.py` — le contrat, les cinq chiffres posés à la
  main, la fuite de taux commercial reproduite puis fermée, la non-mutation, et
  un refus déterministe qui n'emporte pas ses voisins.
- `apps/web/e2e-premier-devis/suite-scenarios-de-chiffrage.spec.ts` — le
  parcours navigateur, d'un sous-détail à quatre composants jusqu'aux trois
  colonnes, sur un devis brouillon puis gelé, avec trois rôles.
