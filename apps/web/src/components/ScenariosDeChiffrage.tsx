'use client'

import { useCallback, useMemo, useState } from 'react'

import { ErrorNotice } from '@/components/Feedback'
import {
  api,
  type Scenario,
  type ScenarioHypotheses,
  type ScenariosSimulation,
} from '@/lib/api'
import { t } from '@/lib/i18n'
import { can, PERMISSIONS } from '@/lib/permissions'
import { usePermissions } from '@/lib/usePermissions'

/**
 * Panneau « Scénarios de chiffrage » : trois hypothèses, un seul moteur.
 *
 * **Aucune arithmétique financière ici.** Le navigateur envoie des hypothèses
 * et affiche la réponse. Recalculer un total, un écart ou un pourcentage en
 * TypeScript créerait une seconde vérité qui divergerait du devis au premier
 * arrondi — c'est déjà la raison d'être de l'aperçu de sous-détail côté
 * serveur. Les seules transformations faites ici portent sur la SAISIE : « 10 »
 * lu comme « 10 % » devient l'écart relatif `0.10`, par décalage de virgule sur
 * la chaîne, sans jamais passer par un flottant.
 *
 * **Les libellés ne garantissent rien.** Si les totaux n'arrivent pas dans
 * l'ordre que « bas / probable / haut » laisse croire, le panneau le SIGNALE.
 * Il ne réordonne pas les colonnes : masquer ce désordre supprimerait
 * l'information la plus utile — que les hypothèses saisies ne disent pas ce
 * que leur nom laisse croire.
 *
 * **Aucun pourcentage n'est proposé.** Les trois colonnes démarrent à 0 %.
 * Souffler « -10 / 0 / +10 » reviendrait à inventer une dispersion que
 * personne ici n'est en position de connaître.
 */

const NOMS = ['bas', 'probable', 'haut'] as const
type Nom = (typeof NOMS)[number]

const LIBELLES: Record<Nom, string> = {
  bas: 'scenarios.low',
  probable: 'scenarios.likely',
  haut: 'scenarios.high',
}

const AXES = ['prix', 'productivite', 'distance'] as const
type Axe = (typeof AXES)[number]

type Saisie = {
  prix: string
  productivite: string
  distance: string
  categories: string[]
}

/** Zéro pour les trois axes, aucune catégorie : le calcul de référence. */
const SAISIE_NEUTRE: Saisie = { prix: '0', productivite: '0', distance: '0', categories: [] }

/**
 * Découpe un nombre décimal saisi, en acceptant la virgule ET le point.
 *
 * Rend `null` sur tout le reste. Le clavier belge produit une virgule ; la
 * refuser obligerait l'utilisateur à saisir dans une notation qui n'est pas la
 * sienne, et un `parseFloat` silencieux lirait « 7,5 » comme 7.
 */
function decouper(saisie: string): { signe: string; entier: string; decimales: string } | null {
  const nettoye = saisie.trim().replace(',', '.')
  if (!/^[+-]?(\d+(\.\d*)?|\.\d+)$/.test(nettoye)) return null
  const signe = nettoye.startsWith('-') ? '-' : ''
  const sansSigne = nettoye.replace(/^[+-]/, '')
  const [entier = '', decimales = ''] = sansSigne.split('.')
  return { signe, entier: entier || '0', decimales }
}

/**
 * « 10 » (pour cent) devient l'écart relatif « 0.10 ».
 *
 * Un DÉCALAGE DE VIRGULE sur la chaîne, pas une division. `10 / 100` en
 * virgule flottante rend 0.1 exactement, mais `7.3 / 100` rend
 * 0.07299999999999999 — et cette valeur-là partirait telle quelle au serveur,
 * où tout le reste du calcul est en décimal exact.
 */
export function enFraction(saisie: string): string | null {
  const morceaux = decouper(saisie)
  if (!morceaux) return null
  const { signe, entier, decimales } = morceaux
  const chiffres = entier + decimales
  const virgule = entier.length - 2
  const resultat =
    virgule <= 0
      ? `0.${'0'.repeat(-virgule)}${chiffres}`
      : `${chiffres.slice(0, virgule)}.${chiffres.slice(virgule)}`
  return signe + resultat
}

/**
 * L'inverse : l'écart relatif « 0.1 » relu du serveur devient « 10 ».
 *
 * Sert à afficher les hypothèses TELLES QUE LE SERVEUR LES A APPLIQUÉES, et
 * non telles que l'écran croit les avoir envoyées. Même principe : décalage de
 * virgule, jamais de multiplication en virgule flottante.
 */
export function enPourcent(fraction: string): string {
  const morceaux = decouper(fraction)
  if (!morceaux) return fraction
  const { signe, entier, decimales } = morceaux
  const virgule = entier.length + 2
  const chiffres = (entier + decimales).padEnd(virgule, '0')
  const partieEntiere = chiffres.slice(0, virgule).replace(/^0+(?=\d)/, '')
  const partieDecimale = chiffres.slice(virgule).replace(/0+$/, '')
  return signe + partieEntiere + (partieDecimale ? `.${partieDecimale}` : '')
}

function estCalcule(scenario: Scenario): scenario is Extract<Scenario, { status: 'success' }> {
  return scenario.status === 'success'
}

export function ScenariosDeChiffrage({
  estimateId,
  versionId,
}: {
  estimateId: string
  versionId: string
}) {
  const permissions = usePermissions()
  const autorise = can(permissions, PERMISSIONS.costRead)

  const [saisies, setSaisies] = useState<Record<Nom, Saisie>>({
    bas: SAISIE_NEUTRE,
    probable: SAISIE_NEUTRE,
    haut: SAISIE_NEUTRE,
  })
  const [invalides, setInvalides] = useState<Record<string, true>>({})
  const [simulation, setSimulation] = useState<ScenariosSimulation | null>(null)
  const [erreur, setErreur] = useState<unknown>(null)
  const [occupe, setOccupe] = useState(false)

  const simuler = useCallback(
    async (corps: Record<string, unknown>) => {
      setOccupe(true)
      setErreur(null)
      try {
        setSimulation(await api.scenarios(estimateId, versionId, corps))
      } catch (attrape) {
        setErreur(attrape)
      } finally {
        setOccupe(false)
      }
    },
    [estimateId, versionId],
  )

  const parNom = useMemo(() => {
    const table: Partial<Record<Nom, Scenario>> = {}
    for (const scenario of simulation?.scenarios ?? []) {
      table[scenario.nom as Nom] = scenario
    }
    return table
  }, [simulation])

  // Rien du tout sans `cost:read` : les scénarios comparent des déboursés.
  // L'API refuse de toute façon — c'est elle l'autorité —, mais afficher un
  // panneau qui ne pourra jamais rien montrer n'apprend rien.
  if (!autorise) return null

  function modifier(nom: Nom, axe: Axe, valeur: string) {
    setSaisies((precedent) => ({ ...precedent, [nom]: { ...precedent[nom], [axe]: valeur } }))
    setInvalides((precedent) => {
      const suite = { ...precedent }
      delete suite[`${nom}.${axe}`]
      return suite
    })
  }

  function basculerCategorie(nom: Nom, categorie: string) {
    setSaisies((precedent) => {
      const actuelles = precedent[nom].categories
      const suivantes = actuelles.includes(categorie)
        ? actuelles.filter((autre) => autre !== categorie)
        : [...actuelles, categorie]
      return { ...precedent, [nom]: { ...precedent[nom], categories: suivantes } }
    })
  }

  /**
   * Ne part au serveur que ce qui est LISIBLE.
   *
   * Une saisie qui n'est pas un nombre est signalée sous son propre champ, dans
   * sa propre colonne : les deux autres scénarios n'ont rien à se reprocher et
   * ne doivent pas perdre leur résultat pour une faute de frappe voisine.
   */
  function envoyer() {
    const fautifs: Record<string, true> = {}
    const corps: Record<string, unknown> = {}
    for (const nom of NOMS) {
      const saisie = saisies[nom]
      const hypotheses: Record<string, unknown> = { prix_categories: saisie.categories }
      for (const axe of AXES) {
        const fraction = enFraction(saisie[axe])
        if (fraction === null) {
          fautifs[`${nom}.${axe}`] = true
        } else {
          hypotheses[axe] = fraction
        }
      }
      corps[nom] = hypotheses
    }
    setInvalides(fautifs)
    if (Object.keys(fautifs).length > 0) return
    void simuler(corps)
  }

  const categories = simulation?.categories ?? {}
  const devise = simulation?.currency ?? ''
  const couts = simulation?.includes_internal_costs ?? false
  const marges = simulation?.includes_margin_steps ?? false

  return (
    <section className="card scenarios" aria-labelledby="scenarios-titre">
      <h2 id="scenarios-titre">{t('scenarios.title')}</h2>
      <p className="muted">{t('scenarios.intro')}</p>
      <div className="notice info">{t('scenarios.labelsWarning')}</div>

      <ErrorNotice error={erreur} />

      {simulation?.ordre_incoherent && (
        <div className="notice warning" role="status" data-testid="scenarios-ordre">
          {t('scenarios.outOfOrder')}
        </div>
      )}

      {simulation === null && !occupe && (
        <p className="muted">{t('scenarios.beforeFirstRun')}</p>
      )}

      <div className="scenarios-colonnes">
        {NOMS.map((nom) => {
          const scenario = parNom[nom]
          return (
            <fieldset key={nom} className="scenarios-colonne" data-testid={`scenario-${nom}`}>
              <legend>{t(LIBELLES[nom])}</legend>

              {AXES.map((axe) => (
                <ChampDePourcentage
                  key={axe}
                  nom={nom}
                  axe={axe}
                  valeur={saisies[nom][axe]}
                  invalide={Boolean(invalides[`${nom}.${axe}`])}
                  onChange={(valeur) => modifier(nom, axe, valeur)}
                />
              ))}

              {Object.keys(categories).length > 0 && (
                <details className="subdetail" data-testid={`categories-${nom}`}>
                  <summary>{t('scenarios.categories')}</summary>
                  <p className="muted">{t('scenarios.categoriesHint')}</p>
                  {/* Les natures ET leurs libellés viennent du serveur : une
                      seconde liste ici divergerait à la première nature
                      ajoutée au domaine. */}
                  {Object.entries(categories).map(([valeur, libelle]) => (
                    <label key={valeur} className="scenarios-case">
                      <input
                        type="checkbox"
                        checked={saisies[nom].categories.includes(valeur)}
                        onChange={() => basculerCategorie(nom, valeur)}
                      />{' '}
                      {libelle}
                    </label>
                  ))}
                </details>
              )}

              {scenario && (
                <div className="scenarios-resultat">
                  <ResultatDeScenario scenario={scenario} devise={devise} couts={couts} />
                </div>
              )}
            </fieldset>
          )
        })}
      </div>

      <div className="toolbar">
        <button className="primary" onClick={envoyer} disabled={occupe}>
          {occupe ? t('scenarios.computing') : t('scenarios.compute')}
        </button>
      </div>

      {/* Les étapes commerciales — frais généraux, aléas, marge — n'existent
          dans la réponse QUE pour un porteur de `margin:read`. Le serveur les
          retire pour les autres ; l'écran ne prétend donc pas les avoir. */}
      {marges &&
        NOMS.map((nom) => {
          const scenario = parNom[nom]
          if (!scenario || !estCalcule(scenario)) return null
          const lignes = scenario.totaux.lines.filter(
            (ligne) => (ligne.price?.markup_steps ?? []).length > 0,
          )
          if (lignes.length === 0) return null
          return (
            <details key={nom} className="subdetail" data-testid={`scenario-marges-${nom}`}>
              <summary>
                {t(LIBELLES[nom])} — {t('scenarios.markupChain')}
              </summary>
              <table>
                <thead>
                  <tr>
                    <th>{t('boq.designation')}</th>
                    <th>{t('estimate.markupChain')}</th>
                    <th className="num">Taux</th>
                    <th className="num">Cumul</th>
                  </tr>
                </thead>
                <tbody>
                  {lignes.flatMap((ligne) =>
                    (ligne.price?.markup_steps ?? []).map((etape) => (
                      <tr key={`${ligne.line_id}-${etape.key}`}>
                        <td>{ligne.designation}</td>
                        <td>{etape.label}</td>
                        <td className="num">{etape.rate}</td>
                        <td className="num">{etape.running_total}</td>
                      </tr>
                    )),
                  )}
                </tbody>
              </table>
            </details>
          )
        })}
    </section>
  )
}

function ChampDePourcentage({
  nom,
  axe,
  valeur,
  invalide,
  onChange,
}: {
  nom: Nom
  axe: Axe
  valeur: string
  invalide: boolean
  onChange: (valeur: string) => void
}) {
  const identifiant = `scenario-${nom}-${axe}`
  const aide = `${identifiant}-aide`
  const libelles: Record<Axe, string> = {
    prix: 'scenarios.price',
    productivite: 'scenarios.productivity',
    distance: 'scenarios.distance',
  }
  const aides: Record<Axe, string> = {
    prix: 'scenarios.priceHint',
    productivite: 'scenarios.productivityHint',
    distance: 'scenarios.distanceHint',
  }
  return (
    <div className="field">
      <label htmlFor={identifiant}>
        {t(libelles[axe])} ({t('scenarios.percentSuffix')})
      </label>
      {/*
        `inputMode="decimal"` fait apparaître le pavé numérique sur mobile sans
        interdire la virgule, que `type="number"` refuse selon la locale du
        navigateur. Le contrôle de format est fait à l'envoi, une fois, et
        nommé par colonne.
      */}
      <input
        id={identifiant}
        className={invalide ? 'invalide' : undefined}
        inputMode="decimal"
        autoComplete="off"
        aria-describedby={aide}
        aria-invalid={invalide || undefined}
        value={valeur}
        onChange={(evenement) => onChange(evenement.target.value)}
      />
      <p id={aide} className="muted scenarios-aide">
        {t(aides[axe])}
      </p>
      {invalide && (
        <p className="notice error" role="alert">
          {t('scenarios.notANumber')}
        </p>
      )}
    </div>
  )
}

/**
 * Ce qu'une colonne montre : ses totaux, ou son refus. Jamais les deux.
 *
 * Un refus reste CONFINÉ à sa colonne. Laisser l'erreur emporter le panneau
 * ferait perdre à l'utilisateur une comparaison entière pour une hypothèse mal
 * saisie sur un tiers de l'écran.
 */
function ResultatDeScenario({
  scenario,
  devise,
  couts,
}: {
  scenario: Scenario
  devise: string
  couts: boolean
}) {
  if (!estCalcule(scenario)) {
    return (
      <div className="notice error" role="alert">
        <strong>{t('scenarios.refused')}</strong>
        <div>{scenario.refus.message ?? ''}</div>
        {scenario.refus.code && <div className="mono">{scenario.refus.code}</div>}
        <HypothesesAppliquees hypotheses={scenario.hypotheses} />
      </div>
    )
  }

  const totaux = scenario.totaux
  return (
    <>
      {/*
        Une classe À PART, et non `totals`. Le tableau des totaux de l'étude
        porte déjà cette classe sur le même écran ; trois colonnes de plus
        rendraient « la ligne Déboursé sec » ambiguë, pour un lecteur comme
        pour un test.
      */}
      <table className="scenarios-totaux">
        <tbody>
          {couts && totaux.total_direct_cost && (
            <tr>
              <td>{t('estimate.directCost')}</td>
              <td className="num" data-testid={`debourse-${scenario.nom}`}>
                {totaux.total_direct_cost} {devise}
              </td>
            </tr>
          )}
          <tr>
            <td>{t('estimate.totalHT')}</td>
            <td className="num" data-testid={`total-ht-${scenario.nom}`}>
              {totaux.total_selling_price_ht} {devise}
            </td>
          </tr>
          <tr className="scenarios-grand">
            <td>{t('estimate.totalTTC')}</td>
            <td className="num" data-testid={`total-ttc-${scenario.nom}`}>
              {totaux.total_ttc} {devise}
            </td>
          </tr>
        </tbody>
      </table>

      {/* L'écart est CALCULÉ PAR LE SERVEUR, valeur absolue et pourcentage.
          Le refaire ici rendrait un second chiffre, arrondi autrement. */}
      {scenario.ecart && (
        <p className="muted" data-testid={`ecart-${scenario.nom}`}>
          {t('scenarios.delta')} : {scenario.ecart.absolu_display} {devise}
          {scenario.ecart.pourcentage !== null ? (
            <> ({scenario.ecart.pourcentage} %)</>
          ) : (
            <>
              {' — '}
              {t('scenarios.deltaUnavailable')}
            </>
          )}
        </p>
      )}

      {scenario.bloquant && <div className="notice warning">{t('scenarios.blocking')}</div>}
      {scenario.lignes_sans_prix.length > 0 && (
        <p className="muted">
          {scenario.lignes_sans_prix.length} {t('scenarios.missingPriceLines')}
        </p>
      )}

      <HypothesesAppliquees hypotheses={scenario.hypotheses} />
    </>
  )
}

/** Les hypothèses relues du serveur, en pourcentages, pas celles qu'on croit avoir envoyées. */
function HypothesesAppliquees({ hypotheses }: { hypotheses: ScenarioHypotheses }) {
  return (
    <p className="muted scenarios-aide">
      {t('scenarios.applied')} : {t('scenarios.price')} {enPourcent(hypotheses.prix)} %
      {' · '}
      {t('scenarios.productivity')} {enPourcent(hypotheses.productivite)} %{' · '}
      {t('scenarios.distance')} {enPourcent(hypotheses.distance)} %
      {hypotheses.prix_categories.length > 0 && (
        <>
          {' · '}
          {hypotheses.prix_categories.join(', ')}
        </>
      )}
    </p>
  )
}
