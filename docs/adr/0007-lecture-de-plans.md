# ADR 0007 — Lire un plan : bibliothèques, licences et isolation

- **Statut** : accepté pour le périmètre PDF et DXF · **refusé en l'état pour le DWG**
- **Date** : 2026-10-02
- **Remplace** : rien. Étend l'ADR 0003 (stockage documentaire) au cas des plans.
- **Numéro** : 0007. La fiche `cad-bim-takeoff` annonçait 0005 comme prochain libre ;
  0005 et 0006 ont été écrits depuis. La fiche est périmée sur ce point, et sur d'autres
  relevés au §1.

## 1. Ce qui existe déjà, mesuré et non supposé

La fiche `cad-bim-takeoff` affirme « aucune ligne de ce sous-système n'existe » et
« stockage de fichiers : configuration seule ». **Les deux sont faux depuis la livraison
du socle documentaire.** Inventaire relevé dans le code, le 2026-10-02 :

| Brique | État réel | Où |
| --- | --- | --- |
| Original immuable, empreinte SHA-256, taille, type réel lu dans les octets | **implémenté**, avec déclencheurs de base qui interdisent la réécriture | `models.py` : `DocumentRevision` |
| Dépôt, révisions, téléchargement de l'original, décision humaine | **implémenté** | `routers/documents.py`, 8 routes |
| Écriture défensive : lecture par morceaux, plafond à l'octet, chemin dérivé du serveur, refus nommé des exécutables et archives, OOXML reconnu par son sommaire sans décompresser | **implémenté** | `services/document_storage.py` |
| Garde ASGI sur la taille du corps, par route | **implémenté** | `garde_de_corps.py`, `corps_bornes.py` |
| Citation structurée : page, plage de caractères, boîte englobante, **feuille, calque, identifiant d'objet**, extracteur, confiance | **implémenté** | `models.py` : `SourceCitation` |
| Proposition d'extraction versionnée, statut `proposed`/`accepted`/`corrected`/`rejected` | **implémenté** | `models.py` : `ExtractionProposal` |
| État idempotent par étape de pipeline, 11 étapes déclarées | **implémenté** | `models.py` : `DocumentStepRun` |
| Sept ports de traitement, en `Protocol` sans dépendance fournisseur | **implémenté** | `packages/contracts/` |
| Unités, conversions, dimension, refus d'unité inconnue | **implémenté** | `packages/domain/src/metreo_domain/units.py` |
| Verrou sur quantité approuvée, avec motif de dérogation | **implémenté** | `routers/boq.py` |
| PostGIS | image disponible, **aucune colonne géométrique** | `infra/docker-compose.yml` |

Et ce qui n'existe pas, vérifié par recherche sur tout le dépôt — zéro fichier pour
chacun :

- aucune bibliothèque de lecture PDF, aucune de lecture CAO, aucun OCR ;
- aucune table de mesure, aucune colonne géométrique, aucun calcul de longueur ou d'aire ;
- `apps/worker/` ne contient qu'un `README.md` : **aucune exécution asynchrone** ;
- aucune des quatre étapes nécessaires à un plan — rendu d'une page, géométrie
  vectorielle, lecture CAO, mesure — ne figure parmi les 11 étapes déclarées ;
- aucun des quatre scénarios d'acceptation de la phase 2 n'est automatisé.

**Conséquence : le socle du niveau 1 est posé, la lecture ne l'est pas.** La phrase à
tenir reste : *Metreo range et rend un plan à l'identique ; il ne le lit pas encore.*

Un défaut a été trouvé en inventoriant, et corrigé : un DXF ASCII n'a pas de signature,
il est du texte lisible sans octet nul. La détection terminait donc sur
`_ressemble_a_du_texte` et rendait `text/csv`. Un plan déposé était rangé en `.csv` et
offert au pipeline d'import de prix, sans une erreur.

## 2. Décision — PDF

| Rôle | Bibliothèque | Version vérifiée | Licence |
| --- | --- | --- | --- |
| Géométrie vectorielle et texte positionné | **pdfplumber** sur **pdfminer.six** | 0.11.10 · 20260107 | **MIT** · **MIT** |
| Rendu d'une page en image | **pypdfium2** (PDFium) | 5.13.0 · PDFium 153.0.7999.0 | **Apache-2.0 OU BSD-3-Clause** · PDFium **BSD-3-Clause** |
| Réparation, déchiffrement, inspection amont | **pikepdf** (QPDF) | 10.16.0 · QPDF 12.4.2 | **MPL-2.0** · QPDF **Apache-2.0** |
| OCR | **Tesseract** + données `tessdata` via **pytesseract** | 5.5.3 · 0.3.13 | **Apache-2.0** pour le moteur **et pour les poids** |
| Orchestration de l'OCR sur un PDF | **OCRmyPDF** ≥ 17 | 17.13.0 | **MPL-2.0** |

**Pourquoi pdfplumber et non pypdfium2 pour la géométrie.** Les deux exposent les
chemins. La différence est l'espace de coordonnées, et elle est décisive pour un métré :
pdfplumber rend des coordonnées **déjà en espace page**, les blocs répétés — des
`Form XObject`, ce qu'un export CAO produit en masse — étant résolus. pypdfium2 rend les
points en espace **local du bloc** ; il faut composer soi-même les matrices de chaque
ancêtre. Vérifié par exécution sur un PDF fabriqué portant un bloc de 10×10 placé par
`2 0 0 2 100 100 cm` : pdfplumber rend `(100, 100, 120, 120)`, pypdfium2 rend
`M(0,0) L(10,0)` et une boîte `[-1,-1,11,11]`. Une machine d'état à écrire sur ce chemin
serait une source d'erreur d'échelle — exactement la faute qui fausse une quantité.

### Rejets, et leur motif

| Écarté | Licence | Motif |
| --- | --- | --- |
| **PyMuPDF / MuPDF** | **AGPL-3.0**, ou licence commerciale Artifex | Techniquement le meilleur du lot : `get_drawings()` rend des primitives typées, résout les blocs, expose les groupes de contenu optionnels. Mais l'AGPL §13 impose de fournir le code source correspondant **aux utilisateurs du service en réseau**. Metreo est propriétaire et exposé en réseau : l'obligation porterait sur Metreo lui-même. Auto-héberger n'y change rien — c'est précisément la situation que l'AGPL vise, contrairement à la GPL. **Réintégrable uniquement sous contrat Artifex**, si la finesse de lecture devient un différenciateur. |
| **Ghostscript** | **AGPL-3.0-or-later** | Même raisonnement. Le piège est qu'OCRmyPDF l'appelle par défaut : `--output-type auto` peut retomber sur lui. D'où les deux options imposées ci-dessous, et un contrôle de CI. |
| **Poppler, python-poppler, pdftotext** | Poppler **GPL-2 ou GPL-3** | Pas de clause réseau, donc pas de divulgation immédiate en service pur. Mais un `import` crée une œuvre combinée sous GPL, ce qui interdirait toute image livrée à un client ou toute installation chez lui — demande très plausible pour un éditeur BTP. Et sans intérêt ici : aucun des deux liens Python n'expose les chemins vectoriels. |
| **PaddleOCR, EasyOCR, docTR** | Code Apache-2.0 ; **poids non vérifiés** | Le code ne pose pas de problème. Les **poids** n'ont pas pu être vérifiés : Hugging Face est bloqué depuis l'environnement de recherche. PaddleOCR-VL est bâti sur ERNIE, dont les conditions ont historiquement été propres. Tesseract est le seul moteur dont les poids portent une licence permissive **explicitement lue** (`tessdata` et `tessdata_best` : Apache-2.0). |

### Conditions d'emploi, non négociables

1. **Aucun Ghostscript dans l'image.** OCRmyPDF se lance en
   `--rasterizer pypdfium --output-type pdf`. Un contrôle de CI échoue si le binaire `gs`
   est présent — c'est le genre de dépendance qu'un `apt install ocrmypdf` ramène en
   silence, la documentation d'OCRmyPDF le dit elle-même.
2. **Les fichiers `.traineddata` sont figés dans l'image, avec leur empreinte SHA-256.**
   Trois failles de Tesseract de 2026 se déclenchent par un modèle forgé, pas par l'image
   à lire : CVE-2026-88047 (débordement de pile, 8.6, ≤ 5.5.3), CVE-2026-73067,
   CVE-2026-88048. Un modèle ne se télécharge jamais à l'exécution et ne vient jamais d'un
   utilisateur. Packs retenus : `fra`, `nld`, `deu`.
3. **Ne jamais écrire de correctif dans pikepdf** sans le publier : la MPL-2.0 est un
   copyleft au fichier. L'employer sans le modifier n'impose rien.
4. **Épingler la roue de pypdfium2** et vérifier ses liens dynamiques : le projet avertise
   qu'un sous-ensemble de constructions se lie à `libgcc`. Les 16 licences embarquées dans
   la roue ont été relevées une à une, aucune n'est copyleft.

## 3. Décision — DXF

**`ezdxf` 1.4.4, licence MIT.** Seule dépendance CAO, et elle couvre tout le besoin
énoncé : géométrie, unités du document, calques, blocs et insertions, textes, cotations,
hachures et leurs contours, aire d'un contour fermé, et un mode de lecture tolérant.
MIT : aucune contrainte sur un service propriétaire, aucune redevance, aucune clause
réseau. Il suffit de conserver la notice.

`dxfgrabber` est écarté : son propre mainteneur le marque périmé, aucune publication
depuis janvier 2020. `libdxfrw` est écarté : GPL-2.0-or-later, et sa lecture DWG est
déclarée expérimentale par son README.

### Quatre pièges d'`ezdxf`, et la règle qui en découle

Ils viennent de la documentation et du code source, pas d'une supposition. Chacun
fausserait une quantité en silence.

1. **`$INSUNITS` se lit sur le document, pas sur le modelspace.** `modelspace().units`
   vaut **toujours** 0, « sans unité », et ne décrit rien. L'unité se lit dans
   `doc.units`. De plus, chaque définition de bloc porte ses propres unités, et **ezdxf
   n'applique aucune conversion implicite** : le facteur vit dans les attributs
   `xscale`/`yscale`/`zscale` de l'insertion. Une longueur reprise sans ce facteur est
   fausse du rapport des deux unités.
2. **La valeur d'une cotation a deux sources qui peuvent se contredire.**
   `Dimension.dxf.actual_measurement` est optionnel et « souvent absent ».
   `Dimension.get_measurement()` la recalcule depuis les points de définition, sans
   appliquer d'échelle. Et `Dimension.dxf.text` est le texte **saisi par un humain** :
   toute valeur autre que vide ou `<>` **écrase visuellement la mesure réelle**. Metreo
   conserve les **deux** et signale la divergence : un dessinateur qui a tapé « 3,50 »
   sur une cote qui mesure 3,47 est une information, pas un détail.
3. **L'aire d'une polyligne à arcs est fausse sans aplatissement.**
   `ezdxf.math.area()` prend une liste de sommets et applique la formule des lacets ; les
   *bulges* d'une `LWPOLYLINE` — ses segments en arc — n'y entrent pas. Toute entité est
   donc aplatie par `ezdxf.path` et `flattening()` avant calcul. L'aire est de plus
   **projetée sur le plan XY** : une polyligne inclinée rend sa projection.
4. **Le mode tolérant avale des erreurs de décodage par défaut.**
   `ezdxf.recover` charge des fichiers que la lecture normale refuse, mais journalise les
   erreurs de décodage **sans lever d'exception**, et sa documentation prévient que
   réécrire un tel fichier « peut produire du DXF invalide, ou au moins perdre de
   l'information ». Metreo appelle donc `recover.readfile(chemin, errors='strict')` et
   traite l'`UnicodeDecodeError` comme un fichier irrécupérable.

### Le cycle de blocs : un refus, pas un avertissement

`ezdxf` détecte les références circulaires de blocs et les signale par le code d'audit
**104**. Mais l'auditeur **ne casse pas le cycle**, et ni `Insert.virtual_entities()` ni
`Insert.explode()` ne portent de garde de profondeur ou de détection — vérifié : aucune
occurrence de `cycle`, `depth` ou `recurs` dans `explode.py` et `insert.py`. Exploser un
bloc cyclique est donc une récursion infinie.

**Règle : l'audit tourne avant tout parcours géométrique. Le code 104 présent, le fichier
est refusé.** Le rapport d'audit est le « preview » du patron `preview` → `commit` déjà
éprouvé sur l'import CSV.

## 4. Décision — DWG : refusé en l'état

**Metreo n'annonce pas le DWG, et demande un export DXF.** Ce n'est pas un report faute
de temps : aucune option n'est à la fois licite pour un service commercial, mûre pour
porter une quantité facturée, et sûre.

| Option | Verdict | Motif |
| --- | --- | --- |
| **ODA File Converter** | **Inutilisable** | Hors adhésion, l'usage serait réservé au **non commercial** ; et la redistribution du binaire est interdite, donc aucune image livrable. ⚠ Condition d'usage **non vérifiée à la source** : `opendesign.com` est inaccessible depuis l'environnement de recherche. |
| **ODA Drawings SDK** (ex-Teigha) | **Sous condition — la seule voie propre** | Propriétaire, par adhésion annuelle. Aucun copyleft, aucun envoi chez un tiers, fidélité native, support contractuel. ⚠ Ordres de grandeur **non vérifiés à la source** : palier *Commercial* ~3 000 $ puis ~2 250 $/an mais **SaaS exclu** ; palier *Sustaining* ~7 500 $ puis ~4 500 $/an, **web autorisé**. Facturation rapportée **par instance**, ce qui pèse sur un worker qui se multiplie. |
| **LibreDWG, liée** | **Sous condition, risque différé** | GPL-3.0-or-later. Pas de clause réseau : un service hébergé sans distribution n'oblige à rien. Mais le jour où l'on livre une installation chez un client, l'œuvre combinée doit être fournie en source, Metreo compris. Pour un éditeur BTP, l'installation chez le client est une demande probable. |
| **LibreDWG via `dwg2dxf`, en processus séparé** | **Sous condition, avis juridique requis** | L'analyse de la FSF est favorable : fichiers, tuyaux et arguments de ligne de commande sont les mécanismes de deux programmes distincts. Mais la FSF rappelle que c'est le **droit d'auteur** qui fixe la portée, pas la licence, et qu'un juge tranche. Le droit belge et européen ne raisonne pas comme la FSF. **À faire valider par un avocat en propriété intellectuelle avant tout usage en production.** |
| **Autodesk Platform Services** | **Inutilisable** | Service en nuage : le plan **quitte le serveur**. Les conditions relevées mentionnent des sous-traitants d'infrastructure « dans des lieux du monde entier » et déconseillent le stockage de données personnelles sensibles. Contraire à la règle de confidentialité des plans clients. ⚠ Conditions **non vérifiées à la source** : `autodesk.com` inaccessible. |
| **CloudConvert, Zamzar et semblables** | **Inutilisable** | Le motif n'est pas la négligence de ces services — CloudConvert est certifié ISO 27001 et propose un contrat de sous-traitance. Le motif est structurel : un plan de client est une pièce confidentielle d'un dossier d'étude de prix, souvent sous clause du cahier spécial des charges. L'envoyer ajoute un sous-traitant à déclarer **pour chaque organisation cliente**, une rétention hors contrôle de 24 h à 7 jours, et une fuite hors du périmètre multi-tenant. |
| **`dwg2dxf` de libdxfrw** | **Sous condition, mais fidélité insuffisante** | GPL-2.0-or-later, même analyse. Le blocage est technique : son README qualifie la lecture DWG d'**expérimentale**. Une lecture expérimentale d'un plan qui sert à chiffrer est le risque que la fiche `cad-bim-takeoff` interdit. |
| **Lecteur Rust ou Go permissif** | **Inutilisable aujourd'hui** | `dwg-rs` est Apache-2.0 mais auto-déclaré pré-alpha, « inadapté à la production », non publié sur crates.io, 4 étoiles, et ses trous documentés portent justement sur les hachures et les cotations. `acadrust` est MPL-2.0 — la seule licence compatible — mais la bibliothèque a huit mois, sans corpus de validation publié : à réévaluer dans 12 à 18 mois. En Go, aucune lecture DWG n'existe. |

À cela s'ajoute un fait de sécurité : les deux seuls moteurs libres crédibles accumulent
les corruptions mémoire dans le cœur du décodeur. Pour LibreDWG, quatre dépassements de
tas récents, dont CVE-2026-9500 qui touche les versions **jusqu'à 0.14** et
CVE-2025-61154 dans la décompression des sections R2004.

**Dégradation retenue** : à la réception d'un DWG, le dépôt le reconnaît **à son en-tête
de version** — `AC` suivi de quatre chiffres — et le refuse en nommant le format et la
sortie : exporter en DXF ou en PDF. Tout logiciel qui produit du DWG sait exporter du
DXF. Le refus est implémenté et testé ; le mot « DWG » n'apparaît dans aucune interface,
aucune offre, aucun contrat.

## 5. Isolation du traitement

Trois analyseurs recevront des fichiers de clients. Deux sont en C ou C++ avec un
historique d'exécution de code — PDFium cumule plusieurs usages après libération
exploitables par PDF forgé par trimestre, Tesseract trois failles de 2026. Le troisième,
`ezdxf`, est en Python pur : il ne risque « que » le déni de service, et c'est l'argument
de sécurité le plus fort en sa faveur face à un analyseur en C.

**Le worker d'analyse est traité comme du code compromis par construction.**

- Conteneur dédié, **sans accès réseau**, ni sortant ni vers la base. Il reçoit un
  chemin, rend un rapport.
- Système de fichiers **en lecture seule**, sauf un `tmpfs` de travail. Utilisateur non
  root, `no-new-privileges`, capacités vidées, profil `seccomp` restrictif.
- Limites `cgroup` de mémoire et de processeur, et **délai maximal par fichier**, avec un
  échec rendu en erreur métier plutôt qu'un tueur de mémoire qui emporte le worker.
- **Un processus par fichier**, jeté ensuite : aucun worker long réutilisé entre deux
  organisations.
- Empreinte SHA-256 relevée **avant** l'analyse — déjà le cas — et refus journalisé en
  audit avec son motif.

### Plafonds, et ce qui reste à décider

`max_upload_bytes` vaut **25 Mio**. C'est bas pour un DXF d'exécution, et un IFC le
dépasse couramment. Trois décisions sont à prendre, et aucune ne l'est par cet ADR :

1. un plafond **par format** plutôt qu'un plafond unique ;
2. un plafond d'**amplification**, qui n'a rien à voir avec la taille : un MINSERT porte
   un nombre de lignes et de colonnes sur 16 bits, et un seul peut engendrer des
   milliards d'entités virtuelles. Imbriqué, l'effet est multiplicatif, et un fichier de
   deux kilo-octets suffit. Le comptage doit être **paresseux**, et abandonner au
   dépassement sans jamais matérialiser la liste ;
3. une enveloppe de **coordonnées plausibles**. `1e308` est un flottant valide : il passe
   l'audit, puis rend `inf` dans un calcul d'aire, puis `nan` dans un total. Le contrôle
   d'ordre de grandeur prévu par `cad-bim-takeoff` intervient **après** le calcul ; il
   faut un garde-fou avant.

Autres défenses retenues : `ezdxf.readzip` n'est **jamais** appelé sur un flux reçu — il
lirait un DXF depuis une archive, donc une bombe de décompression. Les références
externes ne sont **pas** résolues, et `ezdxf` ne les suit pas de lui-même ; le jour où
elles le seraient, il faudra refuser le chemin absolu, le `..`, le chemin UNC qui ferait
sortir du trafic SMB, et l'URL qui ferait une requête côté serveur.

## 6. Modèle de données

Rien n'est créé par cet ADR. Ce qu'il fixe est la **forme** de ce qui sera créé, par
migration Alembic, dans une tranche ultérieure.

- **Les citations existent déjà** et portent `sheet`, `layer` et `object_id` : la
  provenance d'une donnée de plan n'a pas besoin d'une nouvelle table.
- **Une mesure est une nouvelle table**, portant `organization_id`, lue par `owned_query`,
  et les onze champs imposés par `cad-bim-takeoff` §4 : fichier, révision et empreinte,
  feuille, calque et objet, unité **du document** avant conversion, échelle avec son
  origine — déclarée, lue ou calibrée —, formule lisible, géométrie de mesure en
  coordonnées du document, auteur ou moteur, confiance, statut.
- **Les étapes de pipeline manquent.** Les onze déclarées décrivent un pipeline de texte.
  Un plan en demande quatre de plus : rendu d'une page, géométrie vectorielle, lecture
  CAO, mesure. La contrainte de vérification porte la liste en dur : une migration la
  réécrit, elle ne s'étend pas à la main.
- **La géométrie est stockée en GeoJSON dans une colonne JSON, pas en PostGIS.** PostGIS
  est disponible mais ne sert à rien ici : les coordonnées sont celles du document, sans
  système de référence spatial, et aucune requête spatiale n'est au programme. L'employer
  imposerait un type de colonne qui lie le schéma à PostgreSQL, alors que la suite de
  tests tourne aussi sur SQLite. À reconsidérer le jour où l'on cherchera « quelles
  mesures intersectent cette zone ».

## 7. Conséquences

**Acceptées.** La pile PDF retenue est en Python pur pour la géométrie, donc plus lente
qu'un moteur en C : mesuré comme acceptable au vu du volume attendu, et `pypdfium2`
reste disponible comme accélérateur ciblé. Le DWG n'est pas lu, ce qui demandera un
export au client. Aucune lecture n'est encore annoncée au niveau 2 ou au-dessus.

**Refusées.** Fournir le code source de Metreo à ses utilisateurs, ce qu'imposerait
l'AGPL de PyMuPDF ou de Ghostscript. Envoyer un plan de client chez un tiers sans accord
explicite, par organisation, désactivable et journalisé — `ai_enabled=False` et
`ai_provider="null"` couvrent déjà ce refus par défaut, et **ce défaut vaut aussi pour
les conversions CAO**. Écrire un lecteur DWG maison depuis des spécifications
rétro-conçues.

**À instruire avant toute décision contractuelle**, et aucune de ces trois lacunes n'a pu
être comblée depuis l'environnement de recherche :

1. un **devis écrit d'ODA**, avec trois questions explicites : quel palier couvre un
   service multi-locataire auto-hébergé ; comment le comptage par instance se comporte
   face à un worker qui se multiplie ; ce qu'il advient du droit d'exploitation en cas de
   non-renouvellement ;
2. la licence des **poids** de PaddleOCR et de docTR, si l'un d'eux était un jour
   envisagé ;
3. un **avis d'avocat** en propriété intellectuelle sur la frontière entre liaison et
   processus séparé, si la voie LibreDWG était explorée.

Les tarifs et conditions cités portent la date du 2026-10-02 et sont à revérifier au
moment de l'implémentation. **Ce document n'est pas un avis juridique.**
