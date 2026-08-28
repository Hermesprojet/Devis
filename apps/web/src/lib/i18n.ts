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

  'common.loading': 'Chargement…',
  'common.error': 'Erreur',
  'common.retry': 'Réessayer',
  'common.cancel': 'Annuler',
  'common.confirm': 'Confirmer',
  'common.save': 'Enregistrer',
  'common.create': 'Créer',
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

  'import.title': 'Import de prix',
  'import.step1': '1. Choisir le fichier',
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
  'estimate.exportCsv': 'Export CSV',
  'estimate.exportInternal': 'Export interne',
  'estimate.quotePreview': 'Aperçu du devis',
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
  'settings.rounding': 'Arrondis',
  'settings.markup': 'Coefficients',
  'settings.siteOverheads': 'Frais de chantier',
  'settings.generalOverheads': 'Frais généraux',
  'settings.contingency': 'Aléas',
  'settings.margin': 'Marge',
  'settings.marginMethod': 'Méthode de marge',
  'settings.missingPricePolicy': 'Poste sans prix',
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
