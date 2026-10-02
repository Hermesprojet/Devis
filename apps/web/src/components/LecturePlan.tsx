'use client'

import { useCallback, useEffect, useRef, useState } from 'react'

import { ErrorNotice, Loading } from '@/components/Feedback'
import {
  ApiError,
  api,
  type DecisionHumaine,
  type PlanLu,
  type PlanMesure,
} from '@/lib/api'
import { t } from '@/lib/i18n'
import { PERMISSIONS, can } from '@/lib/permissions'
import { usePermissions } from '@/lib/usePermissions'

/**
 * La lecture d'un plan DXF : le constat, le rendu, les mesures, la décision.
 *
 * **Aucun calcul métier ici.** Les valeurs sont affichées dans l'unité DU
 * DOCUMENT, telles que le serveur les rend. Pas de conversion d'unité, pas de
 * somme, pas de comparaison entre deux mesures : une arithmétique faite dans
 * le navigateur serait une seconde vérité, et c'est elle que l'utilisateur
 * croirait. Les seules opérations arithmétiques du fichier portent sur
 * l'AFFICHAGE — le facteur de zoom, et les pourcentages de position du repère.
 *
 * **La proposition du programme n'est jamais réécrite.** Après une décision,
 * `GET …/plan` rend la même `valeur_document` et renseigne `decision` : la
 * ligne montre donc les DEUX, ce que la machine a proposé et ce qu'une
 * personne a retenu. Masquer l'un des deux rendrait le dossier inauditable.
 */

/** Les bornes du zoom d'affichage, et le pas d'un clic. */
const ECHELLE_MIN = 0.25
const ECHELLE_MAX = 8
const PAS_DE_ZOOM = 1.25

/** La FORME d'un nombre décimal. Jamais une arithmétique. */
const MOTIF_DECIMAL = /^[+-]?(\d+(\.\d*)?|\.\d+)$/

/**
 * Les familles de mesure, dans le vocabulaire du serveur.
 *
 * Un code absent de cette table s'affiche TEL QUEL : la liste appartient au
 * serveur et peut s'allonger, et montrer « — » ferait croire à une absence là
 * où il y a une famille que l'écran ne sait pas encore nommer.
 */
const FAMILLES: Record<string, string> = {
  lineaire: 'plan.family.lineaire',
  alignee: 'plan.family.alignee',
  diametre: 'plan.family.diametre',
  rayon: 'plan.family.rayon',
  angulaire: 'plan.family.angulaire',
  angulaire_3_points: 'plan.family.angulaire_3_points',
  ordonnee: 'plan.family.ordonnee',
  inconnue: 'plan.family.inconnue',
}

/**
 * Les tables sont indexées par `string`, pas par l'union TypeScript.
 *
 * Le serveur déclare `famille`, `origine_de_la_mesure` et `decision` en
 * `str` : les unions écrites dans `api.ts` documentent ce qu'il envoie
 * aujourd'hui, elles ne le contraignent pas. Un code ajouté là-bas doit
 * s'afficher tel quel, pas faire tomber l'écran sur un accès à `undefined`.
 */
const ORIGINES: Record<string, string> = {
  cote_42: 'plan.origin.cote_42',
  recalcul: 'plan.origin.recalcul',
}

const DECISIONS_LUES: Record<string, { cle: string; badge: string }> = {
  accepted: { cle: 'plan.decided.accepted', badge: 'badge success' },
  // Une correction n'est ni un succès ni un refus : le badge reste neutre, et
  // c'est la valeur retenue affichée à côté qui porte l'information.
  corrected: { cle: 'plan.decided.corrected', badge: 'badge' },
  rejected: { cle: 'plan.decided.rejected', badge: 'badge danger' },
}

/** Le libellé d'un code, ou le code lui-même quand l'écran ne le connaît pas. */
function libelle(table: Record<string, string>, code: string): string {
  const cle = table[code]
  return cle ? t(cle) : code
}

function famille(code: string): string {
  return libelle(FAMILLES, code)
}

/**
 * La FORME d'un nombre saisi, rien d'autre.
 *
 * Aucune arithmétique : ni comparaison à la valeur proposée, ni conversion
 * d'unité, ni borne de vraisemblance. Le seul geste est de remplacer la
 * virgule du clavier belge par le point que le décimal de l'API attend —
 * c'est une NOTATION, pas un calcul, et le refuser obligerait l'utilisateur à
 * saisir dans une notation qui n'est pas la sienne.
 */
function decimalNormalise(saisie: string): string | null {
  const nettoye = saisie.trim().replace(',', '.')
  return MOTIF_DECIMAL.test(nettoye) ? nettoye : null
}

/**
 * Le rendu du plan, rapatrié AVEC le jeton puis exposé comme IMAGE.
 *
 * Une balise `<img src="/api/…">` ne marcherait pas : le navigateur émet une
 * requête d'image NUE, sans en-tête `Authorization`, et la route — qui exige
 * un jeton porteur — répond 401. Le patron du dépôt est donc
 * `octetsAuthentifies` → `createObjectURL` → `<img>` → révocation au
 * nettoyage, comme pour le logo de l'entreprise.
 *
 * **Pourquoi une IMAGE, et jamais un document.** `apps/api/tests/
 * test_quote_content_security_policy.py` le documente, mesuré dans Chromium :
 * une URL `blob:` hérite de l'origine de la page qui la crée et ne porte
 * aucun en-tête HTTP ; un SVG chargé comme IMAGE est inerte, chargé comme
 * DOCUMENT il ne l'est pas. L'image est donc le seul consommateur
 * admissible. Pas de `window.open`, pas d'`<a target="_blank">`, pas
 * d'`<iframe>`, pas d'`<object>`, pas de SVG inséré en ligne dans le JSX :
 * dans tous ces cas le SVG deviendrait un document dans l'origine de
 * l'application, avec la session de qui le consulte, et aucune politique
 * posée par le serveur ne l'en empêcherait — la ruse du `<meta
 * http-equiv>` de l'aperçu de devis ne s'applique pas à un SVG, qui n'est
 * pas du HTML. Pour agrandir, l'utilisateur se sert du zoom ci-dessous ; pour
 * récupérer le fichier, le téléchargement du DXF d'origine existe déjà dans
 * l'écran documentaire.
 */
type EtatDuRendu =
  | { etat: 'attente' }
  /** Le serveur n'a produit aucun rendu. Ce n'est pas une panne. */
  | { etat: 'absente' }
  | { etat: 'chargee'; url: string }
  | { etat: 'echec'; erreur: unknown }

function useRenduDuPlan(
  documentId: string,
  revisionId: string,
  disponible: boolean,
): EtatDuRendu {
  const [rendu, setRendu] = useState<EtatDuRendu>({ etat: 'attente' })

  useEffect(() => {
    if (!disponible) {
      setRendu({ etat: 'absente' })
      return
    }
    setRendu({ etat: 'attente' })
    let vivant = true
    let objet: string | null = null
    api
      .imageDuPlan(documentId, revisionId)
      .then((octets) => {
        objet = URL.createObjectURL(octets)
        if (vivant) setRendu({ etat: 'chargee', url: objet })
        else URL.revokeObjectURL(objet)
      })
      .catch((attrape: unknown) => {
        if (!vivant) return
        // Un rendu absent n'est pas une panne : le constat et les mesures
        // restent lisibles, seule la mise en situation à l'écran manque. Un
        // bandeau rouge ferait croire la lecture perdue.
        const absente =
          attrape instanceof ApiError &&
          attrape.status === 404 &&
          attrape.code === 'image_de_plan_absente'
        setRendu(absente ? { etat: 'absente' } : { etat: 'echec', erreur: attrape })
      })
    return () => {
      vivant = false
      // Sans cette révocation, chaque passage sur l'écran laisserait un objet
      // de plusieurs mégaoctets dans la mémoire de l'onglet.
      if (objet) URL.revokeObjectURL(objet)
    }
  }, [documentId, revisionId, disponible])

  return rendu
}

export function LecturePlan({
  documentId,
  revisionId,
  nomDuFichier,
}: {
  documentId: string
  revisionId: string
  /** Le nom du fichier déposé, pour que l'écran dise de QUOI il parle. */
  nomDuFichier?: string
}) {
  const permissions = usePermissions()
  const peutAnalyser = can(permissions, PERMISSIONS.documentWrite)
  const peutValider = can(permissions, PERMISSIONS.documentValidate)

  const [plan, setPlan] = useState<PlanLu | null>(null)
  const [jamaisAnalyse, setJamaisAnalyse] = useState(false)
  const [dejaAnalyse, setDejaAnalyse] = useState(false)
  const [erreur, setErreur] = useState<unknown>(null)
  const [occupe, setOccupe] = useState(false)
  const [pret, setPret] = useState(false)

  const charger = useCallback(async () => {
    try {
      const lu = await api.lirePlan(documentId, revisionId)
      setPlan(lu)
      setJamaisAnalyse(false)
      setErreur(null)
    } catch (attrape) {
      // Un plan jamais lu n'est pas une panne : c'est l'état de départ du
      // parcours. Le confondre avec une erreur ferait afficher un bandeau
      // rouge là où l'écran doit proposer l'analyse.
      if (
        attrape instanceof ApiError &&
        attrape.status === 404 &&
        attrape.code === 'plan_non_analyse'
      ) {
        setPlan(null)
        setJamaisAnalyse(true)
        setErreur(null)
      } else {
        setErreur(attrape)
      }
    } finally {
      setPret(true)
    }
  }, [documentId, revisionId])

  useEffect(() => {
    void charger()
  }, [charger])

  async function analyser() {
    setOccupe(true)
    setErreur(null)
    setDejaAnalyse(false)
    try {
      const lu = await api.analyserLePlan(documentId, revisionId)
      setPlan(lu)
      setJamaisAnalyse(false)
    } catch (attrape) {
      // Une analyse déjà réussie n'est pas un échec : quelqu'un d'autre — ou
      // un onglet resté ouvert — l'a lancée entre-temps. On relit le constat
      // au lieu d'afficher un refus que l'utilisateur ne saurait pas traiter.
      if (
        attrape instanceof ApiError &&
        attrape.status === 409 &&
        attrape.code === 'etape_deja_reussie'
      ) {
        setDejaAnalyse(true)
        await charger()
      } else {
        setErreur(attrape)
      }
    } finally {
      setOccupe(false)
    }
  }

  if (!pret) return <Loading />

  return (
    <div data-testid="plan-lecture">
      <p className="muted">
        {t('plan.intro')}{' '}
        {nomDuFichier ? <span className="mono">{nomDuFichier}</span> : null}
      </p>
      <ErrorNotice error={erreur} />

      {dejaAnalyse && (
        <div className="notice info" role="status" data-testid="plan-deja-analyse">
          {t('plan.alreadyAnalysed')}
        </div>
      )}

      {jamaisAnalyse ? (
        <div className="card" data-testid="plan-non-analyse">
          <p>{t('plan.notAnalysed')}</p>
          {/*
            L'avertissement sur la durée est posé AVANT le bouton, et non
            après : une attente de dix secondes annoncée seulement pendant
            qu'elle court n'est plus une information, c'est une excuse.
          */}
          <div className="notice warning" role="note">
            {t('plan.analyseWarning')}
          </div>
          {peutAnalyser ? (
            <button
              type="button"
              className="primary"
              data-testid="plan-analyser"
              disabled={occupe}
              onClick={() => void analyser()}
            >
              {t('plan.analyse')}
            </button>
          ) : (
            // Commande masquée, phrase conservée : l'absence doit s'expliquer.
            <p className="muted">{t('plan.analyseNotAllowed')}</p>
          )}
          {occupe && (
            <div className="notice info" role="status" data-testid="plan-analyse-en-cours">
              {t('plan.analysing')}
            </div>
          )}
        </div>
      ) : plan ? (
        <>
          <ConstatDuPlan plan={plan} />
          <MesuresDuPlan
            documentId={documentId}
            revisionId={revisionId}
            plan={plan}
            peutValider={peutValider}
            onDecide={charger}
          />
        </>
      ) : null}
    </div>
  )
}

/** Ce que le serveur a LU dans le fichier, avant toute mesure. */
function ConstatDuPlan({ plan }: { plan: PlanLu }) {
  // Triés par nombre décroissant : le calque qui porte le plus d'entités est
  // celui qu'un métreur regarde d'abord. L'ordre d'un objet JSON n'est pas une
  // information, celui-ci en est une.
  const calques = Object.entries(plan.calques).sort((a, b) => b[1] - a[1])
  const entites = Object.entries(plan.entites).sort((a, b) => b[1] - a[1])

  return (
    <section data-testid="plan-constat">
      <h2>{t('plan.findings')}</h2>

      {plan.refuse && (
        <div className="notice error" role="alert" data-testid="plan-refus">
          <strong>{t('plan.refused')}</strong>
          {plan.motif_du_refus && <div>{plan.motif_du_refus.message}</div>}
        </div>
      )}

      {/*
        Sans unité de dessin, aucune longueur ne veut rien dire — mais le plan
        reste consultable, et les cotes écrites par le dessinateur restent
        lisibles sur le rendu. Dire « non mesurable » sans dire « consultable »
        ferait refermer l'écran à qui voulait seulement regarder.
      */}
      {!plan.mesurable && (
        <div className="notice warning" role="note" data-testid="plan-non-mesurable">
          {t('plan.notMeasurable')}
        </div>
      )}

      <div className="card">
        <div className="row">
          <div className="field">
            <label>{t('plan.sourceUnit')}</label>
            <span className="mono" data-testid="plan-unite-source">
              {plan.unite_source ?? t('common.none')}
            </span>
          </div>
          <div className="field">
            <label>{t('plan.insunits')}</label>
            <span className="mono">{plan.insunits ?? t('common.none')}</span>
          </div>
          <div className="field">
            <label>{t('plan.dxfVersion')}</label>
            <span className="mono">{plan.version_dxf ?? t('common.none')}</span>
          </div>
          <div className="field">
            <label>{t('plan.sheets')}</label>
            <span className="mono">
              {plan.feuilles.length > 0 ? plan.feuilles.join(', ') : t('common.none')}
            </span>
          </div>
        </div>

        <div className="field">
          <label>{t('plan.entities')}</label>
          <p className="plan-faits mono" style={{ margin: 0 }}>
            {entites.length === 0
              ? t('common.none')
              : entites.map(([type, nombre]) => (
                  <span key={type}>
                    {type} {nombre}
                  </span>
                ))}
          </p>
        </div>

        {calques.length > 0 && (
          <div className="field">
            <label>{t('plan.layers')}</label>
            <table data-testid="plan-calques">
              <thead>
                <tr>
                  <th>{t('plan.layer')}</th>
                  <th className="num">{t('plan.count')}</th>
                </tr>
              </thead>
              <tbody>
                {calques.map(([nom, nombre]) => (
                  <tr key={nom}>
                    <td className="mono">{nom}</td>
                    <td className="num">{nombre}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <h3>{t('plan.anomalies')}</h3>
      {plan.anomalies.length === 0 ? (
        <p className="muted">{t('plan.noAnomaly')}</p>
      ) : (
        <ul data-testid="plan-anomalies">
          {/*
            Le message est DÉJÀ rédigé en français par le serveur : on
            l'affiche, on ne le recompose pas. Le code reste visible à côté,
            pour qu'un échange avec le support désigne la même chose.
          */}
          {plan.anomalies.map((anomalie, index) => (
            <li key={`${anomalie.code}-${index}`}>
              {anomalie.message} <span className="badge mono">{anomalie.code}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

/**
 * Le rendu, son zoom, et les mesures qui s'y situent.
 *
 * **La limite, dite honnêtement :** l'image rendue pèse quelques mégaoctets,
 * et le navigateur la RASTÉRISE à chaque changement d'échelle — un zoom sur un
 * plan dense se sent déjà sur une machine de bureau. Ce qui est écrit ici
 * tient pour un plan de chantier ordinaire ; au-delà, il faudra découper le
 * rendu en TUILES servies par niveau de zoom, ce qui est un travail de
 * serveur, pas une option à ajouter dans ce composant.
 */
function MesuresDuPlan({
  documentId,
  revisionId,
  plan,
  peutValider,
  onDecide,
}: {
  documentId: string
  revisionId: string
  plan: PlanLu
  peutValider: boolean
  onDecide: () => Promise<void>
}) {
  const rendu = useRenduDuPlan(documentId, revisionId, plan.image_disponible)

  const [echelle, setEchelle] = useState(1)
  const [decalage, setDecalage] = useState({ x: 0, y: 0 })
  /** La mesure épinglée par un clic — elle survit au départ de la souris. */
  const [epinglee, setEpinglee] = useState<string | null>(null)
  /** Celle que la souris ou le clavier désigne en passant. */
  const [survolee, setSurvolee] = useState<string | null>(null)

  const glisse = useRef<{
    pointeur: number
    x: number
    y: number
    depart: { x: number; y: number }
  } | null>(null)

  const visee = survolee ?? epinglee
  const repere = plan.mesures.find((mesure) => mesure.proposal_id === visee)?.cadre ?? null

  /*
   * Deux listes, et non un tri silencieux.
   *
   * Une mesure sans réserve ne doit pas pouvoir se confondre avec une mesure
   * réservée : elles sont donc séparées par un TITRE, en plus du badge et du
   * motif. Réordonner une seule liste aurait rapproché les deux cas à une
   * ligne de distance, et la seule différence lisible aurait été une couleur.
   * L'ordre du serveur est conservé à l'intérieur de chaque groupe.
   */
  const aVerifier = plan.mesures.filter((mesure) => mesure.fiabilite === 'a_confirmer')
  const sansReserve = plan.mesures.filter((mesure) => mesure.fiabilite === 'mesurable')

  function surPointeurBas(evenement: React.PointerEvent<HTMLDivElement>) {
    if (evenement.button !== 0) return
    glisse.current = {
      pointeur: evenement.pointerId,
      x: evenement.clientX,
      y: evenement.clientY,
      depart: decalage,
    }
    // La capture garde le glissement vivant quand le curseur sort du cadre :
    // sans elle, l'image restait figée dès qu'on dépassait le bord.
    evenement.currentTarget.setPointerCapture(evenement.pointerId)
  }

  function surPointeurMouvement(evenement: React.PointerEvent<HTMLDivElement>) {
    const en_cours = glisse.current
    if (!en_cours || en_cours.pointeur !== evenement.pointerId) return
    setDecalage({
      x: en_cours.depart.x + (evenement.clientX - en_cours.x),
      y: en_cours.depart.y + (evenement.clientY - en_cours.y),
    })
  }

  function surPointeurHaut(evenement: React.PointerEvent<HTMLDivElement>) {
    const en_cours = glisse.current
    if (!en_cours || en_cours.pointeur !== evenement.pointerId) return
    glisse.current = null
    if (evenement.currentTarget.hasPointerCapture(evenement.pointerId)) {
      evenement.currentTarget.releasePointerCapture(evenement.pointerId)
    }
  }

  function zoomer(facteur: number) {
    setEchelle((actuelle) =>
      Math.min(ECHELLE_MAX, Math.max(ECHELLE_MIN, actuelle * facteur)),
    )
  }

  function ajuster() {
    setEchelle(1)
    setDecalage({ x: 0, y: 0 })
  }

  return (
    <section data-testid="plan-mesures-section">
      <h2>{t('plan.view')}</h2>
      {rendu.etat === 'echec' && <ErrorNotice error={rendu.erreur} />}
      {rendu.etat === 'absente' ? (
        <div className="notice warning" role="note" data-testid="plan-sans-image">
          {t('plan.imageUnavailable')}
        </div>
      ) : (
        <div className="card">
          <div className="toolbar">
            {/*
              Trois boutons, et le clavier les atteint : c'est ce qui rend le
              zoom utilisable sans souris. Le glissement, lui, reste une
              commodité de souris — il ne donne accès à rien que « Ajuster »
              ne rende.
            */}
            <button type="button" onClick={() => zoomer(PAS_DE_ZOOM)}>
              {t('plan.zoomIn')}
            </button>
            <button type="button" onClick={() => zoomer(1 / PAS_DE_ZOOM)}>
              {t('plan.zoomOut')}
            </button>
            <button type="button" onClick={ajuster}>
              {t('plan.zoomFit')}
            </button>
            <span className="muted mono" aria-live="polite">
              {t('plan.zoomLevel')} {Math.round(echelle * 100)} %
            </span>
          </div>
          <div
            className="plan-cadre"
            onPointerDown={surPointeurBas}
            onPointerMove={surPointeurMouvement}
            onPointerUp={surPointeurHaut}
            onPointerCancel={surPointeurHaut}
          >
            {/* Le rendu met un instant à revenir : le dire vaut mieux qu'un
                rectangle gris qu'on prendrait pour un plan vide. */}
            {rendu.etat === 'attente' && <Loading />}
            <div
              className="plan-scene"
              style={{
                transform: `translate(${decalage.x}px, ${decalage.y}px) scale(${echelle})`,
              }}
            >
              {rendu.etat === 'chargee' && (
                /*
                  `<img>`, et surtout pas un SVG en ligne ni un `<object>` :
                  voir le commentaire de `useRenduDuPlan`. Chargé comme IMAGE,
                  un SVG n'exécute ni script ni requête ; inséré en ligne, il
                  s'exécuterait dans l'origine de l'application avec la
                  session de qui le consulte.
                */
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  className="plan-image"
                  data-testid="plan-image"
                  src={rendu.url}
                  alt={t('plan.imageAlt')}
                  draggable={false}
                />
              )}
              {/*
                Le repère vit DANS la scène transformée, et ses coordonnées
                sont des POURCENTAGES : il suit donc l'image au zoom et au
                déplacement sans qu'une seule position soit recalculée ici.
                `cadre` arrive déjà normalisé dans [0,1], origine en haut à
                gauche — le même sens qu'un positionnement CSS.
              */}
              {repere && (
                <div
                  className="plan-repere"
                  data-testid="plan-repere"
                  style={{
                    left: `${Number(repere.x0) * 100}%`,
                    top: `${Number(repere.y0) * 100}%`,
                    width: `${(Number(repere.x1) - Number(repere.x0)) * 100}%`,
                    height: `${(Number(repere.y1) - Number(repere.y0)) * 100}%`,
                  }}
                />
              )}
            </div>
          </div>
          <p className="muted plan-faits">{t('plan.imageHint')}</p>
        </div>
      )}

      <h2>{t('plan.measures')}</h2>
      {/*
        La phrase est posée UNE fois pour la section, pas sur chaque ligne :
        les commandes sont masquées, mais le rôle doit savoir qu'elles
        existent et ce qui lui manque pour les obtenir.
      */}
      {!peutValider && (
        <div className="notice info" role="note" data-testid="plan-sans-droit-de-valider">
          {t('plan.decide.notAllowed')}
        </div>
      )}
      {plan.mesures.length === 0 ? (
        <div className="card">
          <p className="muted">{t('plan.measuresEmpty')}</p>
        </div>
      ) : (
        <>
          {aVerifier.length > 0 && (
            <>
              <h3>
                {t('plan.measuresToCheck')} ({aVerifier.length})
              </h3>
              <div className="card" style={{ padding: 0 }}>
                <ul className="plan-mesures" data-testid="plan-mesures-a-verifier">
                  {aVerifier.map((mesure) => (
                    <LigneDeMesure
                      key={mesure.proposal_id}
                      mesure={mesure}
                      visee={mesure.proposal_id === visee}
                      epinglee={mesure.proposal_id === epinglee}
                      peutValider={peutValider}
                      onSurvol={setSurvolee}
                      onEpingler={setEpinglee}
                      onDecide={onDecide}
                    />
                  ))}
                </ul>
              </div>
            </>
          )}
          {sansReserve.length > 0 && (
            <>
              <h3>
                {t('plan.measuresClean')} ({sansReserve.length})
              </h3>
              <div className="card" style={{ padding: 0 }}>
                <ul className="plan-mesures" data-testid="plan-mesures-sans-reserve">
                  {sansReserve.map((mesure) => (
                    <LigneDeMesure
                      key={mesure.proposal_id}
                      mesure={mesure}
                      visee={mesure.proposal_id === visee}
                      epinglee={mesure.proposal_id === epinglee}
                      peutValider={peutValider}
                      onSurvol={setSurvolee}
                      onEpingler={setEpinglee}
                      onDecide={onDecide}
                    />
                  ))}
                </ul>
              </div>
            </>
          )}
        </>
      )}
    </section>
  )
}

/** Une mesure : ce qu'elle vaut, d'où elle vient, et ce qu'on en a décidé. */
function LigneDeMesure({
  mesure,
  visee,
  epinglee,
  peutValider,
  onSurvol,
  onEpingler,
  onDecide,
}: {
  mesure: PlanMesure
  visee: boolean
  epinglee: boolean
  peutValider: boolean
  onSurvol: (identifiant: string | null) => void
  onEpingler: (identifiant: string | null) => void
  onDecide: () => Promise<void>
}) {
  const unite = mesure.unite_document
  const lue = mesure.decision ? (DECISIONS_LUES[mesure.decision] ?? null) : null

  return (
    <li
      className={visee ? 'plan-mesure visee' : 'plan-mesure'}
      data-testid="plan-mesure"
      onMouseEnter={() => onSurvol(mesure.proposal_id)}
      onMouseLeave={() => onSurvol(null)}
      // `focus` remonte dans React : tabuler jusqu'à un bouton de la ligne la
      // situe sur l'image, exactement comme un survol à la souris.
      onFocus={() => onSurvol(mesure.proposal_id)}
      onBlur={() => onSurvol(null)}
    >
      <div>
        {/*
          L'intitulé n'apparaît que lorsqu'une valeur retenue vient à côté :
          seule, la valeur proposée EST la mesure et n'a pas besoin d'être
          nommée. À deux, il faut dire laquelle est laquelle. L'intitulé reste
          HORS du repère de test, qui ne doit porter que la valeur.
        */}
        {mesure.valeur_corrigee !== null && (
          <span className="muted">{t('plan.proposedValue')} </span>
        )}
        <span className="plan-valeur mono" data-testid="plan-valeur-proposee">
          {mesure.valeur_document}
          {unite ? ` ${unite}` : ''}
        </span>{' '}
        {!unite && <span className="badge warning">{t('plan.withoutUnit')}</span>}{' '}
        {/*
          La valeur RETENUE à côté de la valeur proposée, et non à sa place :
          le serveur ne réécrit jamais la proposition du programme, et
          l'écran ne doit pas faire croire le contraire.
        */}
        {mesure.valeur_corrigee !== null && (
          <span>
            <span className="muted">→ {t('plan.retainedValue')} </span>
            <span className="plan-valeur mono" data-testid="plan-valeur-retenue">
              {mesure.valeur_corrigee}
              {unite ? ` ${unite}` : ''}
            </span>{' '}
          </span>
        )}
        {mesure.fiabilite === 'a_confirmer' ? (
          <span className="badge warning" data-testid="plan-badge-a-verifier">
            {t('plan.toCheck')}
          </span>
        ) : (
          <span className="badge" data-testid="plan-badge-sans-reserve">
            {t('plan.noReserve')}
          </span>
        )}{' '}
        {lue && (
          <span className={lue.badge} data-testid="plan-badge-decision">
            {t(lue.cle)}
          </span>
        )}
      </div>

      <p className="plan-faits muted">
        <span>
          {t('plan.family')} : {famille(mesure.famille)}
        </span>
        <span>
          {t('plan.layer')} : <span className="mono">{mesure.calque ?? t('common.none')}</span>
        </span>
        <span>
          {t('plan.handle')} :{' '}
          <span className="mono">{mesure.object_ref ?? t('common.none')}</span>
        </span>
        <span>
          {t('plan.origin')} : {libelle(ORIGINES, mesure.origine_de_la_mesure)}
        </span>
        <span>
          {t('plan.confidence')} : <span className="mono">{mesure.confiance}</span>
        </span>
      </p>

      {/*
        Les réserves du serveur, TOUTES, et telles qu'il les a rédigées. Il
        peut y en avoir plusieurs sur une même mesure, et n'en montrer qu'une
        laisserait croire le problème plus simple qu'il n'est.
      */}
      {mesure.reserves.length > 0 && (
        <>
          <p className="plan-faits">
            <strong>
              {t('plan.reserves')} ({mesure.reserves.length})
            </strong>
          </p>
          <ul className="plan-reserves" data-testid="plan-reserves">
            {mesure.reserves.map((reserve, index) => (
              <li key={`${reserve.code}-${index}`}>
                {reserve.message} <span className="badge mono">{reserve.code}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      {mesure.fiabilite === 'mesurable' && (
        <p className="plan-faits muted">{t('plan.noReserveHint')}</p>
      )}

      {/*
        Une divergence, pas un détail : le dessinateur a écrit un texte À LA
        PLACE de la mesure, et ce texte ne correspond pas forcément à la
        géométrie du dessin.
      */}
      {mesure.texte_impose !== null && (
        <div className="notice warning" role="note" data-testid="plan-texte-impose">
          {t('plan.imposedText').replace('{texte}', mesure.texte_impose)}
        </div>
      )}

      {mesure.cadre === null ? (
        // Une mesure sans cadre ne peut PAS être située. La poser au milieu de
        // l'image serait inventer une position que personne ne connaît.
        <p className="plan-faits muted" data-testid="plan-position-inconnue">
          {t('plan.unknownPosition')}
        </p>
      ) : (
        <button
          type="button"
          aria-pressed={epinglee}
          onClick={() => onEpingler(epinglee ? null : mesure.proposal_id)}
        >
          {epinglee ? t('plan.located') : t('plan.locate')}
        </button>
      )}

      {/* Une mesure décidée ne propose plus ses trois commandes. */}
      {peutValider && mesure.decision === null && (
        <DecisionDeMesure mesure={mesure} onDecide={onDecide} />
      )}
    </li>
  )
}

/**
 * Confirmer, corriger ou refuser — avec son motif, obligatoire.
 *
 * Même patron que `PrixDuPoste` : vue en lecture, bouton qui ouvre un
 * formulaire en place, `ErrorNotice` LOCALE, appel, puis rechargement du
 * parent. L'erreur reste confinée à la ligne : un refus sur une mesure ne doit
 * pas faire disparaître les quarante autres.
 */
function DecisionDeMesure({
  mesure,
  onDecide,
}: {
  mesure: PlanMesure
  onDecide: () => Promise<void>
}) {
  const [ouverte, setOuverte] = useState<DecisionHumaine | null>(null)
  const [motif, setMotif] = useState('')
  const [valeur, setValeur] = useState('')
  const [motifManquant, setMotifManquant] = useState(false)
  const [formeInvalide, setFormeInvalide] = useState(false)
  const [erreur, setErreur] = useState<unknown>(null)
  const [occupe, setOccupe] = useState(false)

  const base = `plan-decision-${mesure.proposal_id}`

  function ouvrir(decision: DecisionHumaine) {
    setOuverte(decision)
    setMotif('')
    setValeur('')
    setMotifManquant(false)
    setFormeInvalide(false)
    setErreur(null)
  }

  async function enregistrer() {
    if (ouverte === null) return
    const raison = motif.trim()
    const manque = raison.length === 0
    setMotifManquant(manque)

    if (ouverte === 'corrected') {
      const normalisee = decimalNormalise(valeur)
      setFormeInvalide(normalisee === null)
      if (manque || normalisee === null) return
      setOccupe(true)
      setErreur(null)
      try {
        // `before_value` et `after_value` sont OBLIGATOIRES ici, et interdits
        // pour les deux autres décisions : c'est la règle du serveur, et le
        // type de `deciderProposition` l'impose au lieu de la rappeler.
        await api.deciderProposition(mesure.proposal_id, {
          decision: 'corrected',
          reason: raison,
          before_value: { valeur_document: mesure.valeur_document },
          after_value: { valeur_document: normalisee },
        })
        setOuverte(null)
        await onDecide()
      } catch (attrape) {
        setErreur(attrape)
      } finally {
        setOccupe(false)
      }
      return
    }

    if (manque) return
    setOccupe(true)
    setErreur(null)
    try {
      await api.deciderProposition(mesure.proposal_id, { decision: ouverte, reason: raison })
      setOuverte(null)
      await onDecide()
    } catch (attrape) {
      setErreur(attrape)
    } finally {
      setOccupe(false)
    }
  }

  if (ouverte === null) {
    return (
      <p style={{ margin: '6px 0 0' }}>
        <button type="button" data-testid="plan-confirmer" onClick={() => ouvrir('accepted')}>
          {t('plan.decide.accept')}
        </button>{' '}
        <button type="button" data-testid="plan-corriger" onClick={() => ouvrir('corrected')}>
          {t('plan.decide.correct')}
        </button>{' '}
        <button type="button" data-testid="plan-refuser" onClick={() => ouvrir('rejected')}>
          {t('plan.decide.reject')}
        </button>
      </p>
    )
  }

  return (
    <div data-testid="plan-formulaire-decision" style={{ marginTop: 6 }}>
      <ErrorNotice error={erreur} />
      <p className="muted plan-faits">{t('plan.decide.machineKept')}</p>

      {ouverte === 'corrected' && (
        <div className="field">
          <label htmlFor={`${base}-valeur`}>{t('plan.decide.newValue')}</label>
          <div className="row" style={{ alignItems: 'center' }}>
            {/*
              Aucun `placeholder` chiffré : sur un champ de valeur il se lirait
              comme une valeur usuelle, et quelqu'un la reprendrait.
            */}
            <input
              id={`${base}-valeur`}
              data-testid="plan-valeur-corrigee"
              className={formeInvalide ? 'invalide' : undefined}
              inputMode="decimal"
              autoComplete="off"
              aria-describedby={`${base}-valeur-aide`}
              aria-invalid={formeInvalide || undefined}
              value={valeur}
              onChange={(evenement) => setValeur(evenement.target.value)}
            />
            {/*
              L'unité du document, rappelée À CÔTÉ du champ : on doit savoir en
              quoi on saisit. Rien n'est converti — la valeur part telle quelle.
            */}
            <span className="mono" style={{ flex: '0 0 auto' }}>
              {mesure.unite_document ?? t('plan.withoutUnit')}
            </span>
          </div>
          <p id={`${base}-valeur-aide`} className="muted plan-faits">
            {mesure.unite_document === null
              ? t('plan.decide.noUnit')
              : t('plan.decide.newValueHint')}
          </p>
          {formeInvalide && (
            <p className="notice error" role="alert">
              {t('plan.decide.notANumber')}
            </p>
          )}
        </div>
      )}

      <div className="field">
        <label htmlFor={`${base}-motif`}>{t('plan.decide.reason')}</label>
        <textarea
          id={`${base}-motif`}
          data-testid="plan-motif"
          rows={2}
          maxLength={2000}
          aria-describedby={`${base}-motif-aide`}
          aria-invalid={motifManquant || undefined}
          className={motifManquant ? 'invalide' : undefined}
          value={motif}
          onChange={(evenement) => setMotif(evenement.target.value)}
        />
        <p id={`${base}-motif-aide`} className="muted plan-faits">
          {t('plan.decide.reasonHint')}
        </p>
        {motifManquant && (
          <p className="notice error" role="alert">
            {t('plan.decide.reasonRequired')}
          </p>
        )}
      </div>

      <p style={{ margin: 0 }}>
        <button
          type="button"
          className="primary"
          data-testid="plan-enregistrer-decision"
          disabled={occupe}
          onClick={() => void enregistrer()}
        >
          {t('plan.decide.submit')}
        </button>{' '}
        <button type="button" disabled={occupe} onClick={() => setOuverte(null)}>
          {t('common.cancel')}
        </button>
      </p>
    </div>
  )
}
