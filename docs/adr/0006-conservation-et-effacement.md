# ADR 0006 — Conservation des devis émis et effacement d'une organisation

- **Statut** : accepté. La mécanique est décidée ; la DURÉE se prend
  organisation par organisation, sous la forme structurée décrite en §5, et le
  dépôt n'en fournit aucune
- **Date** : 2026-08-31
- **Remplace** : la réserve explicite de l'ADR implicite portée par la révision
  `e3f4a5b6c7d8` (« la purge d'une organisation reste hors périmètre tant que la
  politique de conservation/RGPD n'est pas décidée »)

## Contexte

La révision `e3f4a5b6c7d8` a fermé quatre des cinq portes par lesquelles un
devis émis disparaissait : chantier, estimation et version gelée retiennent en
`RESTRICT`, et un déclencheur refuse la suppression directe. Elle en a laissé
une ouverte, en le disant : la suppression de l'organisation entière.

Ce cas a été reproduit sur base PostgreSQL jetable avant toute correction :

```
ligne issued_quotes   1 → 0     le devis disparaît sans un mot
journal d'audit       9 → 0     la trace de l'émission disparaît avec lui
fichier PDF           présent   et il le reste, octet pour octet
```

Le troisième fait condamne les deux autres. Une purge motivée par un
effacement **détruisait sa propre preuve tout en conservant le document du
client**. C'est l'inverse exact de ce qu'un effacement doit produire : ce qui
devait partir restait, ce qui devait rester partait.

Deux difficultés rendaient la question non triviale.

1. **Le journal d'audit meurt avec son organisation.** `audit_events` porte un
   `organization_id` en `CASCADE`. Aucune trace interne à l'organisation ne peut
   donc attester de sa propre destruction.
2. **La base et le volume n'ont pas de transaction commune.** Des lignes et des
   fichiers ne peuvent pas partir « en même temps ». Un ordre doit être choisi,
   et il détermine ce qui reste quand la machine s'arrête au milieu.

## Décision

**Rien ne se détruit sans un écrit préalable qui dit ce qui va être détruit, et
qui survit à la destruction.**

### 1. La dernière cascade silencieuse disparaît

`issued_quotes.organization_id` passe de `CASCADE` à `RESTRICT`.
`DELETE FROM organizations` échoue désormais bruyamment tant qu'un devis émis
subsiste. Il n'existe aucune route d'API qui supprime une organisation ; ce
chemin n'était atteignable qu'en SQL direct, ce qui le rendait d'autant plus
dangereux qu'il était invisible.

### 2. Un registre qui survit à ce qu'il enregistre

La table `organization_purges` est **la seule table du dépôt sans clé
étrangère**, et c'est délibéré : `organization_id` y est une colonne nue. Un
registre rattaché à ce qu'il enregistre disparaît avec lui et ne prouve rien.

Elle porte un code de motif, une référence facultative, la durée appliquée, le
nombre de devis, et pour chacun sa clé de stockage et l'empreinte de son PDF.

**Aucun texte libre.** C'est une correction apportée après coup : le motif
était d'abord une chaîne de 500 caractères. Un registre censé prouver une
destruction sans conserver ce qu'elle a effacé ne peut pas offrir une zone où
l'on écrit « à la demande de M. Dupont, Terrassements Untel » — la donnée
personnelle rentre alors par la porte prévue pour la faire sortir.

Le motif est donc un code pris dans une liste fermée, tenue par une contrainte
`CHECK` : `contract_ended`, `subject_request`, `retention_elapsed`,
`duplicate_organization`, `demo_reset`, `test_fixture`. La référence est
facultative et contrainte à une forme opaque — ni blanc ni ponctuation de
phrase — de sorte qu'elle désigne un dossier ailleurs sans le raconter ici. La
contrainte ne rend pas l'abus impossible ; elle le rend délibéré, ce qui est le
maximum qu'un format puisse offrir. Un test vérifie qu'aucun nom réel ne se
retrouve dans la ligne sérialisée.

### 3. Une autorisation d'exécution bornée, vérifiée par la base

Le déclencheur ne demande plus « l'organisation existe-t-elle » — la condition
qui laissait justement passer la cascade. Une première correction lui a fait
demander « une purge est-elle inscrite », ce qui était encore faux : **une
demande n'est pas une autorisation**, et une demande abandonnée laissait la
porte ouverte indéfiniment.

Demander et autoriser sont donc deux gestes. `demander()` inscrit et n'ouvre
rien. `autoriser()` ouvre une fenêtre de quinze minutes. Le déclencheur
interroge :

```sql
status = 'executing' AND authorized_until > <horloge de la base>
```

**L'horloge est celle du serveur, jamais celle de l'appelant** — une fenêtre
validée par qui la demande ne prouve rien, il suffirait de mentir sur l'heure.
`now() AT TIME ZONE 'UTC'` sous PostgreSQL, `datetime('now')` sous SQLite.

Quatre états ne permettent donc rien, et chacun a son test : une demande
abandonnée (`requested`), une fenêtre expirée, une purge terminée
(`completed`), une purge en échec (`failed`) — cette dernière même si sa borne
est encore dans le futur, parce que c'est le statut qui décide en premier.

### 4. L'ordre : lignes d'abord, fichiers ensuite

Deux ordres étaient possibles, et un seul est réparable.

| Ordre | Ce qui reste si la machine s'arrête au milieu |
| --- | --- |
| fichiers puis lignes | des lignes désignant des fichiers absents — un devis qui existe et ne se télécharge plus |
| **lignes puis fichiers** | **des fichiers que plus rien ne désigne — mais que le registre NOMME** |

Le second est retenu parce que son état intermédiaire est réparable : le
registre nomme chaque fichier restant, `reprendre()` achève la purge sans rien
redécouvrir, et l'opération est idempotente. Le premier produit un état qu'aucun
écrit ne peut rattraper.

### 5. Une décision structurée, jamais un nombre nu

Une première version stockait la durée dans un `Integer` nullable sur les
réglages de l'organisation. C'était insuffisant, et pas seulement par prudence :
**un nombre d'années seul n'est pas une décision, c'est une opinion sans
auteur.** Rien n'obligeait quiconque à dire d'où sortait le chiffre, ni sous
quel droit, ni depuis quand, ni qui l'avait validé.

La table `quote_retention_decisions` exige les cinq éléments ensemble :

| Élément | Pourquoi il ne peut pas manquer |
| --- | --- |
| `years` | la durée elle-même |
| `jurisdiction` | deux entreprises sous deux droits n'ont pas la même durée |
| `source_label` / `source_url` | « la loi dit » sans texte cité n'est pas vérifiable |
| `source_checked_on` | les textes changent ; une source non datée périme en silence |
| `effective_from` | une décision s'applique à partir d'une date, pas rétroactivement |
| `validated_by` / `validated_at` | un identifiant interne, jamais un nom saisi |

**Versionnée, jamais modifiée en place**, comme les packs régionaux et pour la
même raison : une purge exécutée hier doit rester jugeable sur la règle qui
l'autorisait. Corriger crée une ligne de plus ; l'ancienne reste lisible. La
durée est de surcroît **recopiée** dans le registre de purge, parce que la
décision vit dans l'organisation et meurt avec elle.

**Le dépôt n'en sème aucune.** Ni migration, ni `seed`, ni valeur par défaut :
elles viendraient d'un droit qu'il ne détient pas. Les quatre packs régionaux
déclarent la règle `quote_retention` avec `enabled: true`,
`requires_expert_validation: true` et **`years: null`** ; aucun ne cite de
source datée, tous restent `draft` ou `planned`, et un test
(`test_aucun_pack_ne_fixe_une_duree_de_conservation`) empêche une durée d'y
apparaître par inadvertance.

Sans décision en vigueur, la destruction est refusée. Le refus conserve, et
conserver est la position sûre quand la règle est inconnue.

## Conséquences

**Ce qui devient vrai.**

- Aucun devis émis ne disparaît plus par aucune cascade, organisation comprise.
- Aucun PDF ne survit à la ligne qui le désigne sans qu'un écrit le nomme.
- Une destruction sans motif écrit est refusée, jusque dans le jeu de
  démonstration.
- Une purge interrompue se termine ; `orphelins()` la rend vérifiable de
  l'extérieur.

**Ce qui coûte.**

- `seed --reset` ne peut plus supprimer ses organisations directement. Il
  emprunte la même porte, motif `demo_reset`, avec le seul assouplissement
  nommé qu'elle admet (`sans_retention=True`) — réservé aux organisations que
  ce module a semées et retrouvées par leur nom exact. Il ouvre et referme une
  vraie fenêtre d'exécution : s'il pouvait détruire sans autorisation, il ne
  prouverait rien du mécanisme.
- Une organisation ne peut pas être détruite tant qu'une décision de
  conservation complète n'a pas été prise pour elle. C'est voulu.
- Prendre cette décision et exécuter une purge demandent l'accès au serveur :
  ni l'une ni l'autre n'a de route HTTP.

**Ce qui reste ouvert, et pour qui.**

1. **La durée elle-même, organisation par organisation.** Le dépôt fournit la
   forme, pas le contenu : il faut un texte, sa date de consultation et un
   validateur. Aucune valeur ne sera devinée à la place de personne.
2. **L'exposition d'une route d'API.** Aucune n'est créée, et un test tient la
   frontière : une route HTTP qui détruit un locataire entier a un rayon
   d'action considérable, et une route qui fixe une durée de conservation
   engagerait l'entreprise sur un droit depuis un écran. Les deux se font par
   `scripts/purger_organisation.py`. Ouvrir l'une ou l'autre serait une
   décision séparée.
3. **L'anonymisation comme alternative à la destruction.** Écartée pour le PDF :
   le document est immuable et son empreinte est la garantie tenue au client
   depuis le cycle commercial. Le modifier casserait le SHA-256, donc la
   garantie. Elle resterait envisageable pour les instantanés, et n'a pas été
   traitée ici.

## Alternatives écartées

- **Refuser toute destruction (`RESTRICT` sans porte).** Simple et sûr, mais
  livre une impasse : aucun effacement ne serait jamais possible, et la question
  reviendrait entière au premier client qui le demande.
- **Garder `CASCADE` et balayer les fichiers orphelins après coup.** Un
  balayage ne sait pas distinguer un fichier abandonné par une destruction
  légitime d'un fichier abandonné par un incident. Sans écrit préalable, il
  devine.
- **Écrire le registre dans le journal d'audit.** Impossible : `audit_events`
  est en `CASCADE` sur l'organisation. La trace mourrait avec son objet.
