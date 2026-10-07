/**
 * Minimal i18n layer.
 *
 * French is the only complete locale in Phase 1, but every user-facing string
 * goes through `t()` from day one so adding Dutch is a translation job and not
 * a refactor. Keys are dotted paths; a missing key returns the key itself,
 * which makes the gap visible instead of silently blank.
 */

export type Locale = 'fr' | 'nl' | 'en'

export const SUPPORTED_LOCALES: Locale[] = ['fr', 'nl', 'en']
export const DEFAULT_LOCALE: Locale = 'fr'

type Dictionary = Record<string, string>

const fr: Dictionary = {
  'app.name': 'Metreo',
  'app.tagline': "Étude de prix et devis BTP — outil d'aide à la décision",
  'app.demoBanner':
    'Environnement de démonstration. Toutes les données et tous les prix sont fictifs.',

  'nav.dashboard': 'Tableau de bord',
  'nav.projects': 'Projets',
  'nav.clients': 'Clients',
  'nav.quotes': 'Devis',
  'nav.priceBooks': 'Bibliothèque de prix',
  'nav.audit': "Journal d'audit",
  'nav.settings': 'Paramètres',
  'nav.signOut': 'Se déconnecter',

  'login.title': 'Connexion',
  'login.devNotice':
    "Mode développement : la connexion se fait par adresse e-mail, sans mot de passe. Ce mode est refusé en environnement de production.",
  'login.email': 'Adresse e-mail',
  'login.submit': 'Se connecter',
  'login.demoAccounts': 'Comptes de démonstration',
  'login.organization': 'Organisation',
  'login.oidcSubmit': "Se connecter avec le compte de l'entreprise",
  'login.oidcNotice':
    "La connexion passe par le fournisseur d'identité de votre entreprise. Aucun mot de passe n'est conservé par Metreo.",
  'login.oidcPending': 'Connexion en cours…',
  'login.noMethod':
    "Ce déploiement n'offre aucune connexion depuis un navigateur. Il accepte des jetons émis ailleurs.",
  'login.error.provider_refused':
    "Le fournisseur d'identité a refusé la connexion.",
  'login.error.invalid_request': 'La demande de connexion était incomplète.',
  'login.error.no_membership':
    "Ce compte n'appartient à aucune organisation active. Demandez à un administrateur de vous ajouter.",
  'login.error.unknown_user':
    "Ce compte n'est pas connu de Metreo. Un administrateur doit le créer avant la première connexion.",
  'login.error.unverified_email':
    "Le fournisseur d'identité n'a pas confirmé cette adresse e-mail.",
  'login.error.expired_state':
    'La demande de connexion a expiré. Recommencez : le bouton ci-dessus repart de zéro.',
  'login.error.invalid_state':
    'Cette demande de connexion a déjà servi. Recommencez depuis le bouton ci-dessus.',
  'login.error.token_expired':
    "Le fournisseur d'identité a rendu une réponse déjà périmée. Recommencez.",
  'login.error.token_not_yet_valid':
    "L'horloge du fournisseur d'identité et celle du serveur divergent trop. " +
    'Recommencez ; si le refus persiste, prévenez votre administrateur.',
  'login.error.generic': 'La connexion a échoué.',

  'common.loading': 'Chargement…',
  'common.error': 'Erreur',
  'common.retry': 'Réessayer',
  'common.cancel': 'Annuler',
  'common.confirm': 'Confirmer',
  'common.save': 'Enregistrer',
  'common.create': 'Créer',
  'common.delete': 'Supprimer',
  'common.edit': 'Modifier',
  'common.search': 'Rechercher',
  'common.none': '—',
  'common.total': 'Total',
  'common.unit': 'Unité',
  'common.quantity': 'Quantité',
  'common.reference': 'Référence',
  'common.status': 'Statut',
  'common.actions': 'Actions',
  'common.notImplemented': 'Non implémenté à ce stade',

  'projects.title': 'Projets',
  'projects.new': 'Nouveau projet',
  'projects.empty': 'Aucun projet pour le moment.',
  'projects.name': 'Nom',
  'projects.client': 'Client',
  'projects.deadline': 'Date limite',
  'projects.region': 'Profil réglementaire',
  'projects.created': 'Projet créé.',

  'boq.title': 'Bordereau',
  'boq.position': 'Poste',
  'boq.designation': 'Désignation',
  'boq.addItem': 'Ajouter une ligne',
  'boq.approved': 'Approuvé',
  'boq.empty': 'Ce bordereau est vide.',

  'priceBook.title': 'Bibliothèque de prix',
  'priceBook.code': 'Code',
  'priceBook.label': 'Libellé',
  'priceBook.family': 'Famille',
  'priceBook.unitPrice': 'Prix unitaire',
  'priceBook.supplier': 'Fournisseur',
  'priceBook.import': 'Importer un CSV',
  'priceBook.demoFlag': 'Donnée fictive',
  'priceBook.publish': 'Publier cette version',
  'priceBook.published': 'Version publiée',
  'priceBook.publishWarning':
    'Publier fige la version : ses prix et ses sous-détails passent en lecture seule, '
    + 'définitivement. Les études déjà chiffrées gardent leurs montants ; pour faire évoluer '
    + 'le catalogue, créez ensuite une nouvelle version.',
  'priceBook.publishConfirm': 'Confirmer la publication',
  'priceBook.newVersion': 'Nouvelle version',
  'priceBook.newVersionPrompt': 'Nom de la nouvelle version',

  'priceSource.label': 'Source du prix',
  'priceSource.none': 'Aucun prix — le gel sera refusé',
  'priceSource.library': 'Prix unitaire de bibliothèque',
  'priceSource.composite': 'Sous-détail',
  'priceSource.pick': 'Choisir…',
  'priceSource.change': 'Changer',
  'priceSource.notComputable': 'source non calculable',
  'priceSource.otherVersion': 'sous-détail d\u2019une autre version',
  'priceSource.mixedVersions':
    'Les études de ce chantier n\u2019utilisent pas toutes la même version de bibliothèque. '
    + 'Aucune source de prix n\u2019est proposée ici : en choisir une reviendrait à trancher '
    + 'en silence entre deux catalogues.',
  'composites.title': 'Sous-détails de prix',
  'composites.search': 'Rechercher un sous-détail',
  'composites.new': 'Nouveau sous-détail',
  'composites.newTitle': 'Nouveau sous-détail',
  'composites.editTitle': 'Modifier le sous-détail',
  'composites.duplicate': 'Dupliquer',
  'composites.duplicatePrompt': 'Code du duplicata',
  'composites.usedBy': 'Postes',
  'composites.publishedReadOnly':
    'Cette version de bibliothèque est publiée : ses sous-détails sont en lecture seule. '
    + 'Créez une nouvelle version pour les faire évoluer.',
  'composites.addComponent': 'Ajouter un composant',
  'composites.removeComponent': 'Retirer',
  'composites.duplicateComponent': 'Dupliquer',
  'composites.moveUp': 'Monter',
  'composites.moveDown': 'Descendre',
  'composites.unitCost': 'Coût unitaire (déboursé sec) :',
  'composites.notLinear':
    'Ce coût vaut pour UNE unité. Une rotation arrondie ne se met pas à l\u2019échelle : '
    + 'une unité demande un camion entier, cent unités n\u2019en demandent pas cent fois plus. '
    + 'Le montant du poste est calculé sur sa quantité réelle, il ne s\u2019obtient pas '
    + 'en multipliant celui-ci.',
  'composites.kind': 'Nature',
  'composites.amount': 'Montant',
  'composites.previewUnavailable':
    'Le coût s\u2019affichera dès que le sous-détail sera calculable.',
  'composites.density': 'Masse volumique (kg/m³)',
  'composites.densitySource': 'Source de la masse volumique',
  'composites.densityWhy':
    'Une masse volumique n\u2019est demandée que pour passer d\u2019un volume à une masse '
    + '(ou l\u2019inverse). Sa source est obligatoire : une densité supposée change le nombre '
    + 'de camions, donc le prix, sans que rien ne le signale. Aucune valeur n\u2019est '
    + 'proposée par défaut.',
  'composites.hint':
    'La décomposition d\u2019un prix : ressources, rendements, rotations et forfaits. '
    + 'Le coût unitaire est calculé par le serveur, jamais dans le navigateur : '
    + 'deux arithmétiques divergeraient au premier arrondi.',
  'composites.empty': 'Aucun sous-détail dans cette version.',
  'composites.noComponent': 'Ce sous-détail ne porte aucun composant.',
  'composites.code': 'Code',
  'composites.label': 'Désignation',
  'composites.unit': 'Unité',
  'composites.components': 'Composants',
  'composites.show': 'Voir la décomposition',
  'composites.hide': 'Masquer',
  'composites.componentType': 'Type',
  'composites.componentLabel': 'Ressource',
  'composites.componentKind': 'Nature',
  'composites.componentDetail': 'Détail',
  'composites.type.consumption': 'consommation',
  'composites.type.output_rate': 'rendement',
  'composites.type.rotation': 'rotation',
  'composites.type.lump_sum': 'forfait',
  'import.title': 'Import de prix',
  'import.step1': '1. Choisir le fichier',
  'import.formats':
    'CSV ou classeur Excel (.xlsx). Le format est reconnu au contenu du '
    + 'fichier, pas à son nom. Les classeurs à macros, à formules ou renvoyant '
    + 'à d\u2019autres fichiers sont refusés : collez les valeurs calculées.',
  'import.templateCsv': 'Modèle CSV',
  'import.templateXlsx': 'Modèle Excel',
  'import.sheet': 'Feuille à importer',
  'import.sheetHint':
    'Ce classeur porte plusieurs feuilles. La première a été lue ; choisissez '
    + 'celle qui porte le barème.',
  'import.step2': '2. Vérifier la prévisualisation',
  'import.step3': "3. Confirmer l'écriture",
  'import.nothingWritten':
    "Aucune ligne n'est écrite tant que vous n'avez pas confirmé.",
  'import.valid': 'lignes valides',
  'import.errors': 'lignes en erreur',
  'import.duplicates': 'doublons détectés',
  'import.strategy': 'Stratégie pour les codes existants',
  'import.strategy.create': 'Créer uniquement (signaler les conflits)',
  'import.strategy.replace': 'Remplacer',
  'import.strategy.ignore': 'Ignorer',
  'import.strategy.merge': 'Fusionner les champs fournis',
  'import.commit': "Confirmer l'import",
  'import.committed': 'Import confirmé.',
  'import.line': 'Ligne',

  'documents.formats':
    'PDF, PNG, JPEG, DXF, CSV, XLSX ou DOCX. Le contenu est vérifié à la réception : '
    + 'l’extension seule ne suffit pas. Metreo LIT les plans DXF et PDF, et pas de '
    + 'la même façon : un DXF porte ses cotes et son unité, un PDF demande que vous '
    + 'désigniez deux points et déclariez leur distance réelle. Un plan AutoCAD '
    + '.dwg est REFUSÉ — exportez-le en DXF ou en PDF.',
  'documents.readPlan': 'Lire le plan',

  // --- lecture d'un plan DXF ---------------------------------------------
  // Tout ce que l'écran de lecture d'un plan montre. Les RÉSERVES et les
  // ANOMALIES n'ont pas de clé : le serveur les rédige déjà en français, et
  // les recomposer ici reviendrait à tenir deux textes d'accord.
  'plan.title': 'Lecture du plan',
  'plan.backToProject': 'Retour au chantier',
  // Deux introductions, parce que les deux formats ne proposent pas le même
  // geste. La phrase unique d'avant décrivait un DXF — « aucune valeur n'est
  // convertie : elles sont affichées dans l'unité du document » — et était
  // resservie telle quelle à un PDF, qui ne porte AUCUNE unité. Elle laissait
  // donc croire que l'unité d'une mesure PDF venait du fichier, alors qu'elle
  // vient de la personne qui a calibré.
  'plan.intro.dxf':
    'Ce que Metreo a LU dans le fichier, et les mesures qu’il en propose. '
    + 'Rien n’est repris dans un bordereau tant qu’une personne n’a pas '
    + 'tranché, mesure par mesure. Aucune valeur n’est convertie : elles sont '
    + 'affichées dans l’unité du document.',
  'plan.intro.pdf':
    'Un PDF ne porte aucune unité de dessin : Metreo ne peut rien y mesurer '
    + 'seul. Vous déclarez l’échelle sur une cote que vous connaissez, puis '
    + 'vous POINTEZ ce qu’il faut mesurer — Metreo calcule, avec son '
    + 'incertitude. Rien n’est repris dans un bordereau tant que vous n’avez '
    + 'pas tranché, mesure par mesure.',
  'plan.notAnalysed':
    'Ce plan n’a pas encore été lu. Lancer la lecture ne modifie pas le fichier '
    + 'déposé : elle produit un rendu consultable et une liste de mesures proposées, '
    + 'que vous confirmerez ou corrigerez ensuite.',
  'plan.analyse': 'Analyser le plan',
  'plan.analyseWarning':
    'La lecture se fait d’un seul tenant et prend une dizaine de secondes — mesuré : '
    + '7 à 9 secondes sur des plans de 7 et 11 Mo. Laissez cet onglet ouvert : '
    + 'l’analyse se termine dans la réponse à ce bouton.',
  'plan.analysing':
    'Lecture du plan en cours. Comptez une dizaine de secondes ; ne rechargez pas la page.',
  'plan.alreadyAnalysed':
    'Ce plan avait déjà été lu. Le constat ci-dessous est celui de cette lecture ; '
    + 'rien n’a été relu.',
  'plan.analyseNotAllowed':
    'Lancer la lecture d’un plan demande le droit de déposer un document.',
  'plan.refused': 'Ce fichier a été refusé',
  'plan.notMeasurable':
    'Aucune mesure n’est exploitable : le plan ne déclare pas son unité de dessin, '
    + 'et une longueur sans unité ne veut rien dire. Le plan reste consultable, et les '
    + 'cotes écrites par le dessinateur restent lisibles sur le rendu.',
  'plan.findings': 'Constat de lecture',
  'plan.sourceUnit': 'Unité du document',
  'plan.insunits': '$INSUNITS',
  'plan.dxfVersion': 'Version DXF',
  'plan.sheets': 'Feuilles',
  'plan.entities': 'Entités par type',
  'plan.layers': 'Calques',
  'plan.layer': 'Calque',
  'plan.count': 'Entités',
  'plan.anomalies': 'Anomalies du fichier',
  'plan.noAnomaly': 'Aucune anomalie relevée sur le fichier.',

  'plan.view': 'Rendu du plan',
  'plan.imageAlt':
    'Rendu du plan déposé. Les mesures proposées sont détaillées en texte sous cette image.',
  'plan.withoutUnit': 'sans unité déclarée',
  'plan.imageUnavailable':
    'Aucun rendu n’est disponible pour ce plan. Les mesures ci-dessous restent '
    + 'lisibles, mais elles ne peuvent pas être situées à l’écran.',
  'plan.imageHint':
    'Rendu produit par le serveur à partir du fichier. Faites glisser pour déplacer ; '
    + 'les boutons de zoom fonctionnent au clavier.',
  'plan.zoomIn': 'Agrandir',
  'plan.zoomOut': 'Réduire',
  'plan.zoomFit': 'Ajuster',
  'plan.zoomLevel': 'Échelle d’affichage',

  // --- L'écran d'un plan PDF -------------------------------------------
  //
  // Un PDF n'a pas d'unité : tout ce vocabulaire tourne autour de ce fait.
  // « Échelle » y désigne une DÉCLARATION humaine, jamais une lecture.
  'plan.pdf.titre': 'Plan PDF',

  // ---- Le fil d'état -------------------------------------------------------
  //
  // Quatre étapes, affichées en permanence, celle en cours mise en avant. Elles
  // existent parce que l'écran ne disait nulle part OÙ l'on en était : il
  // affichait des outils, un badge « aucune échelle », et laissait deviner que
  // l'un conditionnait l'autre. Un parcours en quatre gestes se montre.
  'plan.pdf.fil': 'Où vous en êtes',
  'plan.pdf.etape.echelle': 'Déclarer l’échelle',
  'plan.pdf.etape.echelleAide':
    'Ouvrez la loupe sur une cote que vous connaissez, cliquez ses deux '
    + 'extrémités, et saisissez la distance réelle.',
  'plan.pdf.etape.echelleFaite': 'Échelle confirmée',
  'plan.pdf.etape.mesurer': 'Pointer et mesurer',
  'plan.pdf.etape.mesurerAide':
    'Choisissez « longueur » ou « surface », puis cliquez dans la loupe les '
    + 'points de ce que vous mesurez. Metreo calcule — il ne devine rien.',
  'plan.pdf.etape.mesurerFaite': '{nombre} mesure(s) calculée(s)',
  'plan.pdf.etape.decider': 'Décider',
  'plan.pdf.etape.deciderAide':
    'Confirmez, corrigez ou rejetez chaque mesure, avec un motif. Tant que '
    + 'vous n’avez pas tranché, rien ne peut alimenter un bordereau.',
  'plan.pdf.etape.deciderFaite': '{tranchees} tranchée(s) sur {total}',
  'plan.pdf.etape.enCours': 'en cours',
  'plan.pdf.etape.aFaire': 'à faire',
  'plan.pdf.etape.faite': 'fait',

  // ---- Ce que l'écran explique avant qu'on clique --------------------------
  'plan.pdf.commentPointer':
    'L’aperçu sert à TROUVER la zone ; la loupe sert à POINTER. Un pixel de '
    + 'l’aperçu vaut plusieurs centimètres d’ouvrage sur un plan de grand '
    + 'format — c’est pourquoi les points se posent dans la loupe.',
  'plan.pdf.loupeDeplacable':
    'La loupe se déplace sans perdre les points déjà posés : cliquez ailleurs '
    + 'sur l’aperçu pour atteindre l’autre extrémité d’une longue cote.',

  // ---- La mesure, sa décision, et ce qui les distingue ---------------------
  'plan.pdf.colMesuree': 'Mesure calculée',
  'plan.pdf.colRetenue': 'Valeur retenue',
  'plan.pdf.calculeePar': 'calculée par Metreo',
  'plan.pdf.pasEncoreTranchee': 'pas encore tranchée',
  'plan.pdf.rienARetenir': 'rien à retenir',
  'plan.pdf.reprenable': 'peut alimenter un bordereau',
  'plan.pdf.nonReprenable': 'n’alimentera aucun bordereau',
  'plan.pdf.motifRetenu': 'Motif : {motif}',
  'plan.pdf.voirLeTrace': 'Voir le tracé',
  'plan.pdf.traceAffiche': 'Tracé affiché sur le plan',

  'plan.pdf.pages': 'Pages',
  'plan.pdf.pagesLabel': 'Pages',
  'plan.pdf.pageSur': 'Page {page} sur {total}',
  'plan.pdf.pageCourte': 'p. {page}',
  'plan.pdf.outils': 'Outils',
  'plan.pdf.texteLabel': 'Textes lus',
  // Trois phrases, et aucune n'est un verdict d'un mot.
  //
  // L'ancienne — « aucun — plan probablement scanné » — s'affichait dès que
  // l'extraction rendait moins de cinquante caractères. Le plan de bâtiment du
  // dépôt en porte vingt-sept et quatre tracés vectoriels : il est tout sauf un
  // scan. Ce qui distingue un scan n'est pas la quantité de texte, c'est son
  // absence totale sur une page qui ne porte qu'une image.
  'plan.pdf.sansTexte': 'aucun',
  'plan.pdf.extraction':
    '{fragments} fragment(s), {caracteres} caractère(s) · {traces} tracé(s) vectoriel(s)',
  'plan.pdf.scanne':
    'aucun texte et aucun tracé : document probablement scanné. '
    + 'La mesure géométrique reste possible, la lecture des cotes non.',
  'plan.pdf.sansTexteMaisVectoriel':
    'aucun texte extrait, mais la page porte du dessin vectoriel : '
    + 'ce n’est pas un scan, c’est un plan exporté sans texte.',
  'plan.pdf.echelleLabel': 'Échelle déclarée',
  'plan.pdf.sansEchelle': 'aucune : rien ne peut être mesuré',
  'plan.pdf.sansApercu':
    "Cette page n'a pas d'aperçu. Le fichier reste déposé et téléchargeable.",
  'plan.pdf.apercuAlt': 'Aperçu de la page du plan',
  'plan.pdf.loupe': 'Loupe',
  'plan.pdf.loupeAlt': 'Agrandissement de la zone désignée',
  'plan.pdf.loupeEnCours':
    "Agrandissement en cours. La première ouverture d'une page prend quelques secondes ; les suivantes sont immédiates.",
  // Dit pendant que l'agrandissement charge, à la place du pointage.
  //
  // L'écran montrait auparavant « Agrandissement en cours » AVEC l'image
  // précédente encore affichée, et le clic restait accepté : un point pouvait
  // s'enregistrer dans une zone que le propriétaire ne voyait plus. Le
  // pointage est désormais fermé tant que l'image affichée n'est pas celle de
  // la page, de la révision et de la zone demandées.
  'plan.pdf.loupePasPrete':
    'Pointage suspendu : l’agrandissement de cette zone n’est pas encore affiché. '
    + 'Aucun clic n’est enregistré tant que l’image montrée n’est pas celle de la zone demandée.',
  // Le filet de sécurité du cas où le serveur ne dit PAS quelle zone son image
  // couvre : la géométrie serait alors supposée, et une mesure supposée ne
  // vaut rien. Mieux vaut le dire que de mesurer sur une hypothèse.
  'plan.pdf.zoneNonDeclaree':
    'Cet agrandissement n’a pas déclaré la zone qu’il couvre. '
    + 'La position des points ne peut pas être garantie : rechargez la page avant de mesurer.',
  'plan.pdf.pointsPoses': '{nombre} point(s) posé(s)',
  'plan.pdf.confirmerEchelle': "Confirmer l'échelle de cette page",
  'plan.pdf.aideEchelle':
    "Vous venez de désigner deux points. Saisissez la distance RÉELLE qui les sépare sur l'ouvrage — pas le rapport d'échelle du cartouche, qui ne vaut plus si le PDF a été exporté « ajusté à la page ».",
  'plan.pdf.distanceReelle': 'Distance réelle entre les deux points',
  'plan.pdf.unite': 'Unité',
  'plan.pdf.motif': 'Sur quoi avez-vous calibré ?',
  'plan.pdf.motifExemple': 'ex. cote 5000 de la façade sud',
  'plan.pdf.confirmer': "Confirmer l'échelle",
  'plan.pdf.nommerLongueur': 'Nommer cette longueur',
  'plan.pdf.nommerSurface': 'Nommer cette surface',
  'plan.pdf.libelle': 'Ce que vous mesurez',
  'plan.pdf.libelleExemple': 'ex. mur nord, dalle du séjour',
  'plan.pdf.mesurer': 'Mesurer',
  'plan.pdf.sansEchelleMesure':
    "Aucune échelle n'a été confirmée pour cette page. Un PDF ne porte pas d'unité : déclarez d'abord une échelle sur une cote connue.",
  'plan.pdf.aucuneMesure': 'Aucune mesure prise sur ce plan.',
  'plan.pdf.mesuresPrises': 'Mesures prises',
  'plan.pdf.colLibelle': 'Ouvrage',
  'plan.pdf.colValeur': 'Valeur',
  'plan.pdf.colFiabilite': 'Fiabilité',
  'plan.pdf.colDecision': 'Décision',
  'plan.pdf.colActions': 'Actions',
  'plan.pdf.longueur': 'longueur',
  'plan.pdf.surface': 'surface',
  'plan.pdf.mesurable': 'mesurable',
  'plan.pdf.aVerifier': 'à vérifier',
  'plan.pdf.depuis': 'Échelle : {motif}',
  'plan.pdf.montrer': 'Montrer sur le plan',
  'plan.pdf.confirmerMesure': 'Confirmer',
  'plan.pdf.corriger': 'Corriger',
  // « Rejeter » et non « Supprimer » : la proposition de la machine reste en
  // base avec sa citation. C'est la DÉCISION qui est enregistrée, pas un
  // effacement — sans quoi le dossier cesserait d'être auditable.
  'plan.pdf.rejeter': 'Rejeter',
  'plan.pdf.aideRejeter':
    'À utiliser quand la mesure ne veut rien dire — mauvais endroit, points mal '
    + 'posés, échelle douteuse. La proposition reste consultable ; elle ne sera '
    + 'simplement jamais reprise.',
  'plan.pdf.motifDecision': 'Motif',
  'plan.pdf.valeurCorrigee': 'Valeur retenue',
  'plan.pdf.corrigeeEn': 'corrigée en',
  'plan.pdf.textesLus': '{rendus} textes affichés sur {total} lus',
  // Les réserves, nommées. Une réserve sans phrase ne s'afficherait pas, et
  // l'utilisateur verrait « à vérifier » sans savoir pourquoi.
  'plan.pdf.reserve.incertitude_elevee':
    "Le pointage était trop grossier pour cette distance : agrandissez davantage et reprenez.",
  'plan.pdf.reserve.contour_qui_se_recoupe':
    'Le contour se croise lui-même : la surface calculée ne correspond à rien de dessiné.',
  'plan.decision.accepted': 'confirmée',
  'plan.decision.corrected': 'corrigée',
  'plan.decision.rejected': 'rejetée',
  'common.close': 'Fermer',
  'common.saving': 'Enregistrement…',

  'plan.measures': 'Mesures proposées',
  'plan.measuresToCheck': 'Mesures à vérifier',
  'plan.measuresClean': 'Mesures sans réserve',
  'plan.measuresEmpty':
    'La lecture n’a proposé aucune mesure. Le fichier a bien été lu — il ne porte '
    + 'simplement aucune cote ni aucun objet mesurable que Metreo sache reprendre.',
  'plan.exactValue': 'Valeur exacte lue dans le fichier :',
  'plan.proposedValue': 'Valeur proposée',
  'plan.retainedValue': 'Valeur retenue',
  'plan.family': 'Famille',
  'plan.handle': 'Handle DXF',
  'plan.origin': 'Origine',
  'plan.confidence': 'Confiance',
  'plan.reserves': 'Réserves',
  'plan.toCheck': 'à vérifier',
  'plan.noReserve': 'sans réserve',
  'plan.noReserveHint':
    'Lue sans réserve par le programme. Cela ne la rend pas juste : elle reste à '
    + 'confirmer par une personne.',
  'plan.imposedText':
    'Le plan affiche « {texte} » à la place de la mesure. Le dessinateur a écrit ce '
    + 'texte lui-même : il ne correspond pas forcément à la géométrie.',
  'plan.unknownPosition': 'Position inconnue dans l’image',
  'plan.locate': 'Situer sur le plan',
  'plan.located': 'Situé sur le plan',

  'plan.origin.cote_42': 'cote du logiciel',
  'plan.origin.recalcul': 'recalculée',
  'plan.family.lineaire': 'linéaire',
  'plan.family.alignee': 'alignée',
  'plan.family.diametre': 'diamètre',
  'plan.family.rayon': 'rayon',
  'plan.family.angulaire': 'angulaire',
  'plan.family.angulaire_3_points': 'angulaire (3 points)',
  'plan.family.ordonnee': 'ordonnée',
  'plan.family.inconnue': 'inconnue',

  'plan.decide.accept': 'Confirmer',
  'plan.decide.correct': 'Corriger',
  'plan.decide.reject': 'Refuser',
  'plan.decided.accepted': 'confirmée',
  'plan.decided.corrected': 'corrigée',
  'plan.decided.rejected': 'refusée',
  'plan.decide.reason': 'Motif (obligatoire)',
  'plan.decide.reasonHint':
    'Ce motif est conservé avec la décision. Il explique à qui relira le dossier '
    + 'pourquoi cette valeur a été retenue, corrigée ou écartée.',
  'plan.decide.reasonRequired': 'Un motif est obligatoire : le serveur refuse une décision sans explication.',
  'plan.decide.newValue': 'Valeur corrigée',
  'plan.decide.newValueHint':
    'Saisissez-la dans l’unité du document, rappelée à droite du champ. Rien '
    + 'n’est converti : la valeur part telle quelle.',
  'plan.decide.notANumber':
    'Ce n’est pas un nombre. Exemples : 5000 ; 2,75 ; 0.5 — la virgule est acceptée.',
  'plan.decide.noUnit':
    'Le document ne déclare aucune unité : la valeur corrigée partira sans unité, '
    + 'comme la proposition.',
  'plan.decide.submit': 'Enregistrer la décision',
  'plan.decide.notAllowed':
    'Confirmer, corriger ou refuser une mesure demande le droit de valider un document. '
    + 'Les mesures restent consultables.',
  'plan.decide.machineKept':
    'La proposition du programme n’est jamais réécrite : après une décision, elle '
    + 'reste affichée à côté de la valeur retenue.',

  'estimate.title': 'Étude de prix',
  'estimate.version': 'Version',
  'estimate.draft': 'Brouillon',
  'estimate.frozen': 'Gelée',
  'estimate.directCost': 'Déboursé sec',
  'estimate.costPrice': 'Prix de revient',
  'estimate.sellingPrice': 'Prix de vente HT',
  'estimate.unitPrice': 'P.U. HT',
  'estimate.totalHT': 'Total HT',
  'estimate.totalTTC': 'Total TTC',
  'estimate.options': 'Options et variantes (hors total)',
  'estimate.missingPrice': 'Prix manquant',
  'estimate.freeze': 'Geler cette version',
  'estimate.freezeWarning':
    'Le gel est irréversible : la version devient immuable et conserve la bibliothèque de prix utilisée.',
  'estimate.frozenAt': 'Gelée le',
  'estimate.digest': 'Empreinte',
  'estimate.newVersion': 'Créer une nouvelle version',
  'estimate.exportCsv': 'Export CSV',
  'estimate.exportInternal': 'Export interne',
  'estimate.quotePreview': 'Aperçu du devis',
  'estimate.quotePreviewHint':
    'Aperçu interne, recalculé à chaque ouverture. Le document remis au client est le '
    + 'PDF du devis émis : lui seul porte un numéro, une date et un contenu figé.',
  'estimate.documentTotalUnknown':
    "Cette version a été gelée avant l'enregistrement des totaux du document. "
    + 'Ouvrez le devis pour en connaître le montant imprimé.',
  'estimate.freezeAlreadyFrozen':
    'Cette version est déjà gelée. Créez une nouvelle version pour la modifier.',
  'estimate.subDetail': 'Sous-détail',
  'estimate.showDetail': 'Voir le détail du calcul',
  'estimate.hideDetail': 'Masquer le détail',
  'estimate.formula': 'Formule',
  'estimate.densitySource': 'Masse volumique',
  'estimate.markupChain': 'Chaîne de prix',
  'estimate.blocked':
    "Des postes sont sans prix : le gel est bloqué par la règle de l'entreprise.",
  'estimate.fromSnapshot':
    'Montants relus depuis la version gelée. Une modification ultérieure des prix de référence ne les change pas.',

  'clients.title': 'Clients',
  'clients.intro':
    'Une fiche par client, réutilisable sur tous ses chantiers. Deux fiches de même nom '
    + 'restent deux clients distincts : rapprocher deux fiches est une décision humaine.',
  'clients.new': 'Nouveau client',
  'clients.edit': 'Modifier la fiche',
  'clients.search': 'Nom ou numéro d\u2019entreprise',
  'clients.showArchived': 'Afficher les fiches archivées',
  'clients.empty': 'Aucune fiche client. Créez-en une pour pouvoir émettre un devis.',
  'clients.name': 'Nom',
  'clients.companyNumber': 'Numéro d\u2019entreprise',
  'clients.billingAddress': 'Adresse de facturation',
  'clients.postalCode': 'Code postal',
  'clients.city': 'Localité',
  'clients.contact': 'Personne de contact',
  'clients.email': 'Courriel',
  'clients.phone': 'Téléphone',
  'clients.active': 'Active',
  'clients.archived': 'Archivée',
  'clients.archive': 'Archiver',
  'clients.restore': 'Réactiver',
  'clients.homonym': 'Une autre fiche porte le même nom — vérifiez l\u2019adresse.',
  'clients.incompleteForIssuing':
    'Incomplète pour émettre',
  'clients.requiredForIssuing':
    'Pour émettre un devis, il faut au minimum un nom, une adresse, un code postal et une '
    + 'localité : ce sont les mentions du destinataire imprimées sur le document.',
  'clients.selectForProject': 'Client du chantier',
  'clients.noneSelected': 'Aucune fiche client',
  'clients.attach': 'Rattacher',
  'clients.attachHint':
    'Ce chantier n\u2019a pas encore de fiche client. Choisissez-en une, ou créez-la dans '
    + 'Clients : l\u2019émission d\u2019un devis l\u2019exige.',
  'clients.legacyName': 'Nom saisi avant le répertoire',

  'quote.issue': 'Émettre le devis',
  'quote.issueTitle': 'Émission du devis',
  'quote.issueWarning':
    'L\u2019émission est définitive : le devis reçoit un numéro, une date, et un PDF qui ne '
    + 'sera plus jamais modifié. Pour corriger, il faudra créer une nouvelle version et '
    + 'émettre un nouveau devis — l\u2019ancien restera.',
  'quote.recipient': 'Destinataire',
  'quote.validUntil': 'Valable jusqu\u2019au',
  'quote.validUntilHint': 'Par défaut, trente jours après l\u2019émission.',
  'quote.terms': 'Conditions et note au client',
  'quote.termsHint':
    'Ce texte est imprimé tel quel sur le devis. Ce dépôt n\u2019ajoute aucune mention '
    + 'légale : écrivez ici celles de votre entreprise.',
  'quote.includeInternal': 'Inclure les coûts internes (déboursé, revient, marge)',
  'quote.includeInternalWarning':
    'À n\u2019utiliser que pour un document interne : ces montants ne doivent jamais partir '
    + 'chez un client.',
  'quote.issued': 'Devis émis',
  'quote.issuedAt': 'Émis le',
  'quote.number': 'Numéro',
  'quote.download': 'Télécharger le PDF',
  'quote.history': 'Devis émis pour ce chantier',
  'quote.none': 'Aucun devis émis pour ce chantier.',
  'quote.needsFrozen':
    'Gelez d\u2019abord la version : un devis remis doit désigner un calcul qui ne bougera plus.',
  'quote.needsClient':
    'Ce chantier n\u2019a pas de fiche client. Rattachez-en une depuis la page du chantier.',
  'quote.alreadyIssued':
    'Cette version porte déjà un devis. Créez une nouvelle version pour en émettre un autre.',
  'quote.digest': 'Empreinte du PDF',
  'quote.snapshotHint':
    'Instantané figé à l\u2019émission. Modifier la fiche client ne change pas ce devis.',
  'quote.shareTitle': 'Lien de consultation',
  'quote.shareHint':
    'Le client ouvre son devis sans compte, le télécharge et répond. Copiez le lien et '
    + 'transmettez-le vous-même : aucun envoi automatique n\u2019est fait.',
  'quote.shareOnce':
    'Copiez ce lien maintenant : le secret n\u2019est affiché qu\u2019une fois et ne '
    + 'pourra plus être retrouvé.',
  'quote.copy': 'Copier',
  'quote.copied': 'Copié',
  'quote.createLink': 'Créer un lien',
  'quote.newLink': 'Créer un nouveau lien',
  'quote.revoke': 'Révoquer',
  'quote.noLink': 'Aucun lien créé pour ce devis.',
  'quote.linkActive': 'Lien actif',
  'quote.linkRevoked': 'Révoqué',
  'quote.linkExpired': 'Expiré',
  'quote.createdAt': 'Créé le',
  'quote.expiresAt': 'Expire le',
  'quote.offlineTitle': 'Enregistrer ce qui s\u2019est passé hors de l\u2019application',
  'quote.offlineHint':
    'Devis envoyé par vos soins, accord donné au téléphone, refus annoncé en réunion : '
    + 'tout cela se note ici, avec son canal et sa date.',
  'quote.markTransmitted': 'Marquer comme transmis',
  'quote.recordAccepted': 'Enregistrer une acceptation',
  'quote.recordDeclined': 'Enregistrer un refus',
  'quote.channel': 'Canal',
  'quote.respondent': 'Nom du répondant',
  'quote.noteOptional': 'Note (facultative)',
  'quote.noteRequired': 'Note ou référence (obligatoire)',
  'quote.timeline': 'Chronologie',
  'quote.timelineEmpty': 'Rien ne s\u2019est encore passé sur ce devis.',
  'quote.event': 'Événement',
  'quote.who': 'Qui',
  'quote.effectiveAt': 'Date effective',
  'quote.recordedAt': 'Enregistré le',
  'quote.correct': 'Corriger',
  'quote.correctedBecause': 'Corrigé :',
  'quote.correctionHint':
    'Rien ne s\u2019efface : la ligne restera visible, barrée, avec votre motif en regard.',
  'quote.correctionReason': 'Motif de la correction',

  'quotes.title': 'Devis émis',
  'quotes.intro':
    'Tous les devis remis, tous chantiers confondus : leur état, leur échéance et leur '
    + 'dernière activité.',
  'quotes.search': 'Numéro, client ou chantier',
  'quotes.empty': 'Aucun devis émis pour le moment.',
  'quotes.issuedFrom': 'Émis depuis le',
  'quotes.issuedTo': 'Émis jusqu\u2019au',
  'quotes.expiringSoon': 'Expirant sous 14 jours',
  'quotes.lastActivity': 'Dernière activité',
  'quotes.linkActive': 'lien actif',

  'audit.title': "Journal d'audit",
  'audit.sequence': 'N°',
  'audit.when': 'Date',
  'audit.who': 'Auteur',
  'audit.what': 'Action',
  'audit.summary': 'Résumé',
  'audit.verify': "Vérifier l'intégrité",
  'audit.valid': 'Chaîne intègre',
  'audit.invalid': 'Chaîne altérée',
  'audit.checked': 'événements vérifiés',

  'settings.title': "Paramètres de l'entreprise",
  'profile.title': "Profil de l'entreprise",
  'settings.regionAndCurrency': 'Région et devise',
  'profile.hint':
    'Ce que vos devis imprimeront en en-tête : qui les émet, où vous écrire, '
    + 'à qui téléphoner. Modifier ces informations ne change AUCUN devis déjà émis — '
    + 'chacun porte l\u2019identité qui était la vôtre le jour où vous l\u2019avez remis.',
  'profile.identity': 'Identité',
  'profile.name': 'Nom commercial',
  'profile.legalName': 'Raison sociale',
  'profile.companyNumber': "Numéro d'entreprise",
  'profile.address': 'Adresse',
  'profile.addressComplement': 'Complément (boîte, bâtiment, zoning)',
  'profile.postalCode': 'Code postal',
  'profile.city': 'Localité',
  'profile.countryCode': 'Pays (code à deux lettres)',
  'profile.contact': 'Coordonnées',
  'profile.email': 'E-mail',
  'profile.phone': 'Téléphone',
  'profile.website': 'Site web',
  'profile.optional': 'facultatif',
  'profile.logo': 'Logo',
  'profile.logoHint':
    'PNG uniquement, 2 Mio au maximum, de 16 à 2000 pixels de côté et un million '
    + 'de pixels au total. '
    + 'Le format est volontairement limité à celui que le devis sait imprimer : '
    + 'un SVG ne serait pas une image mais un document exécutable.',
  'profile.logoChoose': 'Choisir un logo',
  'profile.logoReplace': 'Remplacer le logo',
  'profile.logoRemove': 'Retirer le logo',
  'profile.logoNone': "Aucun logo. Vos devis porteront l'identité écrite seule.",
  'profile.preview': "Aperçu de l'en-tête du devis",
  'profile.previewHint':
    'Ce que le client verra en haut de la première page. Le document imprimé '
    + 'reprend exactement ces lignes.',
  'profile.incomplete': "Profil insuffisant pour émettre un nouveau devis",
  'profile.incompleteHint':
    'Un devis doit dire qui l\u2019émet et où lui répondre. Complétez ces champs : ',
  'profile.complete': 'Ce profil permet d\u2019émettre un devis.',
  'profile.readOnly':
    'Vous pouvez consulter ce profil ; sa modification demande le droit de gérer '
    + 'l\u2019entreprise.',
  'profile.field.name': 'le nom commercial',
  'profile.field.address': "l'adresse",
  'profile.field.postal_code': 'le code postal',
  'profile.field.city': 'la localité',
  'profile.field.country_code': 'le pays',
  'settings.saved': 'Enregistré',
  'settings.quoteNumbering': 'Numérotation des devis',
  'settings.quoteNumberingHint':
    'Le numéro imprimé sur chaque devis émis. {year} est l\u2019année, {sequence} le rang, '
    + 'qui repart à 1 chaque année civile. Laissez vide pour revenir au format par défaut.',
  'settings.quoteNumberPattern': 'Motif',
  'settings.quoteNumberPreview': 'Aperçu',
  'settings.quoteNumberRefused': 'motif refusé',
  'settings.rounding': 'Arrondis',
  'settings.markup': 'Coefficients',
  'settings.siteOverheads': 'Frais de chantier',
  'settings.generalOverheads': 'Frais généraux',
  'settings.contingency': 'Aléas',
  'settings.margin': 'Marge',
  'settings.marginMethod': 'Méthode de marge',
  'settings.missingPricePolicy': 'Poste sans prix',
  'scenarios.title': 'Scénarios de chiffrage',
  'scenarios.intro':
    "Une simulation temporaire : rien n'est enregistré, la version chiffrée n'est pas modifiée, et le calcul est celui du moteur — pas une estimation approchée faite dans le navigateur.",
  'scenarios.low': 'Bas',
  'scenarios.likely': 'Probable',
  'scenarios.high': 'Haut',
  'scenarios.labelsWarning':
    "« Bas », « probable » et « haut » sont des libellés que vous choisissez, pas une garantie : rien n'oblige le scénario « bas » à coûter moins cher.",
  'scenarios.price': 'Prix des ressources',
  'scenarios.priceHint':
    "S'applique aux prix unitaires, taux horaires, coûts de rotation et coûts kilométriques. Pas aux forfaits, qui sont des montants convenus.",
  'scenarios.categories': 'Limiter à certaines natures',
  'scenarios.categoriesHint':
    'Sans sélection, la variation de prix touche toutes les natures de ressource.',
  'scenarios.productivity': 'Productivité',
  'scenarios.productivityHint':
    "Sens inversé : +10 % veut dire « on produit 10 % de plus par heure », donc moins d'heures, donc un coût qui BAISSE.",
  'scenarios.distance': 'Distance de transport',
  'scenarios.distanceHint':
    "Appliquée à la distance AVANT le calcul des rotations : le nombre de rotations est un entier, donc l'effet n'est pas proportionnel.",
  'scenarios.compute': 'Calculer les trois scénarios',
  'scenarios.computing': 'Calcul en cours…',
  'scenarios.notANumber': "Ce n'est pas un pourcentage. Exemples : 10 ; -7,5 ; 0.",
  'scenarios.percentSuffix': '%',
  'scenarios.applied': 'Hypothèses appliquées par le serveur',
  'scenarios.refused': 'Ce scénario n\'a pas pu être chiffré',
  'scenarios.delta': 'Écart au scénario probable',
  'scenarios.deltaUnavailable':
    'Écart indisponible : le scénario probable vaut zéro, et une division par zéro n\'a pas de résultat.',
  'scenarios.outOfOrder':
    "Les totaux ne suivent pas l'ordre des libellés : « bas » n'est pas le moins cher, ou « haut » n'est pas le plus cher. Rien n'a été réordonné — à vous de dire si c'est une erreur de saisie ou une hypothèse voulue.",
  'scenarios.missingPriceLines': 'postes sans prix dans ce scénario',
  'scenarios.blocking': 'Chiffrage bloqué : des postes restent sans prix.',
  'scenarios.markupChain': 'Chaîne de prix, poste par poste',
  'scenarios.reference': 'Référence',
  'scenarios.beforeFirstRun':
    "Aucun calcul n'a encore été demandé. Les trois colonnes démarrent à 0 %, ce qui reproduit exactement le chiffrage ci-dessous ; les natures de ressource proposées arrivent du serveur avec le premier résultat.",
  'settings.masked':
    "Les coefficients commerciaux ne sont pas visibles avec votre rôle.",
}

const dictionaries: Record<Locale, Dictionary> = {
  fr,
  // Dutch and English are declared so the type system and the language switcher
  // already account for them. They intentionally fall back to French until a
  // translation pass happens (phase 5).
  nl: {},
  en: {},
}

export function translate(key: string, locale: Locale = DEFAULT_LOCALE): string {
  return dictionaries[locale]?.[key] ?? fr[key] ?? key
}

export const t = translate
