# ADR 0006 — Conservation des devis émis et effacement d'une organisation

- **Statut** : accepté pour la MÉCANIQUE ; la DURÉE reste ouverte et le reste
  tant qu'une source officielle datée et une validation de spécialiste ne
  l'auront pas fixée
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

Elle porte le motif écrit, la durée appliquée, le nombre de devis, et pour
chacun sa clé de stockage et l'empreinte de son PDF.

**Ce qu'elle ne porte pas**, tout aussi délibérément : aucun nom
d'organisation, de client, de chantier ni de personne. Des identifiants
techniques, des empreintes, des chemins — qui sont eux-mêmes des identifiants.
Le registre prouve qu'une destruction a eu lieu et ce qu'elle portait ; il ne
réintroduit pas ce que la destruction visait à effacer. Un test le vérifie en
cherchant les noms réels dans la ligne sérialisée.

### 3. La ligne du registre EST l'autorisation

Le déclencheur de conservation ne demande plus « l'organisation existe-t-elle »
— condition qui laissait justement passer la cascade — mais « une purge
inscrite et active autorise-t-elle ceci ». La base elle-même refuse de détruire
un devis dont la destruction n'a pas été écrite d'abord. `completed` ne figure
pas parmi les statuts qui ouvrent : une purge refermée n'autorise plus rien.

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

### 5. La durée n'est pas décidée ici, et le code n'en invente pas

`OrganizationSettings.quote_retention_years` est nullable, **sans valeur par
défaut**. `None` signifie « la question n'a pas été tranchée », jamais « sans
limite » : la purge **refuse** alors de s'exécuter.

Les quatre packs régionaux déclarent la règle `quote_retention` avec
`enabled: true`, `requires_expert_validation: true`, une note — et
`years: null`. Conformément à **belgium-regulatory-pack**, une règle
réglementaire n'existe que dans un pack versionné portant une `version`, une
`effective_from`, des `sources` datées et un `disclaimer`. Aucun de ces packs
ne cite de source datée ; ils restent `draft` ou `planned` ; aucune durée n'en
sort.

Écrire « 7 » dans un défaut de colonne serait rendre un avis juridique par une
valeur par défaut. Un test (`test_aucun_pack_ne_fixe_une_duree_de_conservation`)
empêche une durée d'apparaître par inadvertance.

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
  emprunte la même porte, avec le seul assouplissement nommé qu'elle admet
  (`sans_retention=True`), réservé aux organisations que le module a semées et
  retrouvées par leur nom exact. Les deux autres refus — organisation
  inexistante, motif vide — continuent de s'appliquer, et la ligne de registre
  est écrite comme pour une purge réelle.
- Une organisation ne peut pas être détruite tant que sa durée de conservation
  n'a pas été réglée. C'est voulu : le refus conserve, et conserver est la
  position sûre quand la règle est inconnue.

**Ce qui reste ouvert, et pour qui.**

1. **La durée elle-même.** Elle demande une source officielle datée et une
   validation de spécialiste. Elle arrivera par une **nouvelle `version`** de
   pack, jamais par modification en place.
2. **L'exposition d'une route d'API.** Aucune n'est créée : une route HTTP qui
   détruit un locataire entier a un rayon d'action considérable, et personne ne
   l'a demandée. La purge reste une opération de service, appelée par le code
   d'exploitation. Ouvrir une route serait une décision séparée.
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
