'use client'

/**
 * L'écran d'un plan PDF : le voir, le calibrer, le mesurer, le confirmer.
 *
 * Composant distinct de `LecturePlan`, qui sert les DXF, et ce n'est pas un
 * doublon : les deux formats ne demandent pas le même geste. Un DXF porte ses
 * cotations et son unité ; on les LIT. Un PDF ne porte rien de tel ; on y
 * POINTE. Fondre les deux dans un composant produirait un écran dont la moitié
 * des commandes serait désactivée selon le format.
 *
 * **Les trois chiffres mesurés qui dictent cette interface.**
 *
 * 1. Sur l'aperçu pleine page, un pixel vaut **12 à 42 millimètres d'ouvrage**
 *    (quatre plans réels de grand format, 2 000 px de grand côté). On ne peut donc pas y
 *    pointer utilement : l'aperçu sert à TROUVER, la tuile agrandie à POINTER.
 * 2. La hauteur médiane d'une ligne de texte y est de **2,4 à 3,6 pixels**,
 *    celle d'un caractère de 2,1 à 2,5. On ne peut pas non plus y relire une
 *    cote.
 * 3. Une tuile coûte **0,2 à 5,3 secondes** la première fois, puis **0,3 ms**.
 *    Elle se demande donc explicitement, et jamais au fil de la souris.
 *
 * D'où le parcours : on navigue sur l'aperçu, on ouvre une loupe sur la zone
 * qui intéresse, et c'est DANS la loupe qu'on calibre et qu'on mesure.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  api,
  type CalibrationDePlan,
  type FragmentDeTexte,
  type MesureDePdf,
  type MesuresDePdf,
  type PlanLu,
  type PointDEcran,
  type TextesDePlan,
} from '@/lib/api'
import { t } from '@/lib/i18n'
import { ErrorNotice } from '@/components/Feedback'

/** Les unités de longueur que la calibration accepte, dans l'ordre d'usage. */
const UNITES = ['mm', 'cm', 'm'] as const

/**
 * La taille d'une loupe, en fraction de la page.
 *
 * Un vingtième de la largeur : sur un A0 de 1 189 mm, cela fait 59 mm de
 * papier, soit environ 3 mètres d'ouvrage au 1:50. C'est l'ordre de grandeur
 * d'une cote de mur, donc ce qu'on veut voir d'un coup.
 */
const TAILLE_DE_LA_LOUPE = 0.05

/**
 * Ce qu'un pixel de la LOUPE vaut en points PostScript de la page.
 *
 * La tuile fait 512 pixels pour une zone de `TAILLE_DE_LA_LOUPE` de page. Sur
 * une page de 3 370 points de large, cela donne 168 points rendus sur 512
 * pixels, soit 0,33 point par pixel.
 *
 * **C'est ce nombre qui part au serveur comme `resolution_du_pointage`**, et
 * c'est lui qui décide de l'incertitude de toutes les mesures. Le calculer
 * plutôt que de l'affirmer est ce qui rend l'incertitude honnête.
 */
function resolutionDeLaLoupe(largeurDeLaPageEnPoints: number): number {
  const pointsRendus = largeurDeLaPageEnPoints * TAILLE_DE_LA_LOUPE
  return pointsRendus / 512
}

type Outil = 'naviguer' | 'calibrer' | 'segment' | 'surface'

/**
 * Le tracé à dessiner : fermé pour une surface, ouvert pour une longueur.
 *
 * Écrit une fois et appelé des deux côtés — l'aperçu et la loupe — parce que
 * deux fermetures écrites séparément finiraient par diverger, et qu'un contour
 * fermé d'un côté et ouvert de l'autre montrerait deux formes pour une seule
 * mesure.
 */
function fermerSiSurface(points: PointDEcran[], type: string): PointDEcran[] {
  const premier = points[0]
  if (type !== 'surface' || premier === undefined) return points
  return [...points, premier]
}

const OUTILS: { cle: Outil; libelle: string; aide: string }[] = [
  { cle: 'naviguer', libelle: 'Naviguer', aide: 'Cliquez pour ouvrir la loupe.' },
  {
    cle: 'calibrer',
    libelle: "Déclarer l'échelle",
    aide: 'Dans la loupe, cliquez les deux extrémités d’une cote connue.',
  },
  {
    cle: 'segment',
    libelle: 'Mesurer une longueur',
    aide: 'Dans la loupe, cliquez les points du tracé.',
  },
  {
    cle: 'surface',
    libelle: 'Mesurer une surface',
    aide: 'Dans la loupe, cliquez au moins trois points. Le contour se ferme seul.',
  },
]

/** Charge une image authentifiée et rend son URL d'objet. */
function useImageAuthentifiee(
  charger: () => Promise<Blob>,
  actif: boolean,
  cle: string,
): { url: string | null; enCours: boolean; erreur: unknown } {
  const [url, setUrl] = useState<string | null>(null)
  const [enCours, setEnCours] = useState(false)
  const [erreur, setErreur] = useState<unknown>(null)

  useEffect(() => {
    if (!actif) {
      setUrl(null)
      return
    }
    let abandonne = false
    let objet: string | null = null
    setEnCours(true)
    setErreur(null)
    charger()
      .then((blob) => {
        if (abandonne) return
        objet = URL.createObjectURL(blob)
        setUrl(objet)
      })
      .catch((cause) => {
        if (!abandonne) setErreur(cause)
      })
      .finally(() => {
        if (!abandonne) setEnCours(false)
      })
    return () => {
      abandonne = true
      // Une URL d'objet non révoquée retient son blob jusqu'au rechargement de
      // la page. Sur un plan de 1,2 Mo parcouru page à page, cela se voit.
      if (objet) URL.revokeObjectURL(objet)
    }
    // `charger` est recréée à chaque rendu : la clé porte ce qui change
    // vraiment, sans quoi l'image serait rechargée en boucle.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [actif, cle])

  return { url, enCours, erreur }
}

export function LecturePdf({
  documentId,
  revisionId,
  plan,
  onRelire,
}: {
  documentId: string
  revisionId: string
  plan: PlanLu
  onRelire: () => void
}) {
  const [page, setPage] = useState(1)
  const [outil, setOutil] = useState<Outil>('naviguer')
  const [loupe, setLoupe] = useState<[number, number, number, number] | null>(null)
  const [points, setPoints] = useState<PointDEcran[]>([])
  const [textes, setTextes] = useState<TextesDePlan | null>(null)
  const [travail, setTravail] = useState<MesuresDePdf | null>(null)
  const [erreur, setErreur] = useState<unknown>(null)
  const [occupe, setOccupe] = useState(false)

  const apercuDisponible = plan.apercus.includes(page)

  const apercu = useImageAuthentifiee(
    () => api.renduDeLaPage(documentId, revisionId, page),
    apercuDisponible,
    `apercu-${revisionId}-${page}`,
  )
  const tuile = useImageAuthentifiee(
    () => api.tuileDuPlan(documentId, revisionId, page, loupe!),
    loupe !== null,
    `tuile-${revisionId}-${page}-${loupe?.join(',') ?? ''}`,
  )

  useEffect(() => {
    api
      .textesDuPlan(documentId, revisionId, page)
      .then(setTextes)
      .catch(() => setTextes(null))
  }, [documentId, revisionId, page])

  const rechargerLeTravail = useCallback(() => {
    api
      .mesuresDuPdf(documentId, revisionId)
      .then(setTravail)
      .catch((cause) => setErreur(cause))
  }, [documentId, revisionId])

  useEffect(rechargerLeTravail, [rechargerLeTravail])

  /** La largeur de la page courante, en points PostScript. */
  const largeurDeLaPage = plan.dimensions_des_pages[page - 1]?.[0] ?? 0

  // Changer d'outil ou de page abandonne le tracé en cours : garder des points
  // désignés à un autre endroit produirait une mesure que personne n'a voulue.
  useEffect(() => setPoints([]), [outil, page])

  const calibrationDeLaPage = useMemo(
    () => travail?.calibrations.find((c) => c.page === page) ?? null,
    [travail, page],
  )
  const mesuresDeLaPage = useMemo(
    () => (travail?.mesures ?? []).filter((m) => m.page === page),
    [travail, page],
  )

  function cliquerSurLApercu(evenement: React.MouseEvent<HTMLDivElement>) {
    const boite = evenement.currentTarget.getBoundingClientRect()
    const x = (evenement.clientX - boite.left) / boite.width
    const y = (evenement.clientY - boite.top) / boite.height
    const demi = TAILLE_DE_LA_LOUPE / 2
    setLoupe([
      Math.max(0, x - demi),
      Math.max(0, y - demi),
      Math.min(1, x + demi),
      Math.min(1, y + demi),
    ])
  }

  function cliquerDansLaLoupe(evenement: React.MouseEvent<HTMLDivElement>) {
    if (!loupe || outil === 'naviguer') return
    const boite = evenement.currentTarget.getBoundingClientRect()
    const dansLaLoupeX = (evenement.clientX - boite.left) / boite.width
    const dansLaLoupeY = (evenement.clientY - boite.top) / boite.height
    // De la loupe vers la page : la loupe est une fenêtre sur [0,1]².
    const point = {
      x: loupe[0] + dansLaLoupeX * (loupe[2] - loupe[0]),
      y: loupe[1] + dansLaLoupeY * (loupe[3] - loupe[1]),
    }
    const maximum = outil === 'calibrer' ? 2 : 200
    setPoints((anciens) => (anciens.length >= maximum ? [point] : [...anciens, point]))
  }

  return (
    <section className="card" data-testid="pdf-lecture">
      <h2>{t('plan.pdf.titre')}</h2>
      <ErrorNotice error={erreur} />

      <EnTetePdf plan={plan} calibration={calibrationDeLaPage} />

      {plan.pages > 1 && (
        <nav className="pdf-pages" aria-label={t('plan.pdf.pages')} data-testid="pdf-pages">
          <button
            type="button"
            data-testid="pdf-page-precedente"
            disabled={page <= 1}
            onClick={() => {
              setPage((n) => n - 1)
              setLoupe(null)
            }}
          >
            ‹
          </button>
          <span data-testid="pdf-page-courante">
            {t('plan.pdf.pageSur')
              .replace('{page}', String(page))
              .replace('{total}', String(plan.pages))}
          </span>
          <button
            type="button"
            data-testid="pdf-page-suivante"
            disabled={page >= plan.pages}
            onClick={() => {
              setPage((n) => n + 1)
              setLoupe(null)
            }}
          >
            ›
          </button>
        </nav>
      )}

      <div className="pdf-outils" role="group" aria-label={t('plan.pdf.outils')}>
        {OUTILS.map((choix) => (
          <button
            key={choix.cle}
            type="button"
            data-testid={`pdf-outil-${choix.cle}`}
            className={outil === choix.cle ? 'primary' : undefined}
            aria-pressed={outil === choix.cle}
            onClick={() => setOutil(choix.cle)}
          >
            {choix.libelle}
          </button>
        ))}
      </div>
      <p className="muted">{OUTILS.find((o) => o.cle === outil)?.aide}</p>

      {!apercuDisponible && (
        <div className="notice warning" role="alert">
          {t('plan.pdf.sansApercu')}
        </div>
      )}

      {apercu.enCours && <p className="muted">{t('common.loading')}</p>}
      {apercu.url && (
        <div className="pdf-apercu" data-testid="pdf-apercu" onClick={cliquerSurLApercu}>
          {/* Chargé comme IMAGE, jamais en ligne : un PNG est inerte, et le
              rester explicitement vaut mieux que le rester par hasard. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={apercu.url}
            alt={t('plan.pdf.apercuAlt')}
            data-testid="pdf-apercu-image"
          />
          <Surlignages
            fragments={textes?.fragments ?? []}
            mesures={mesuresDeLaPage}
            loupe={loupe}
          />
        </div>
      )}

      {loupe && (
        <Loupe
          url={tuile.url}
          enCours={tuile.enCours}
          erreur={tuile.erreur}
          points={points}
          loupe={loupe}
          outil={outil}
          onCliquer={cliquerDansLaLoupe}
          onFermer={() => {
            setLoupe(null)
            setPoints([])
          }}
        />
      )}

      {outil === 'calibrer' && points.length === 2 && (
        <FormulaireDeCalibration
          occupe={occupe}
          onValider={async (distance, unite, motif) => {
            setOccupe(true)
            setErreur(null)
            try {
              // Destructuré avec une garde explicite : TypeScript ne déduit
              // pas de `points.length === 2` que les deux cases existent, et
              // il a raison — un tableau peut être modifié entre les deux.
              const [premier, second] = points
              if (!premier || !second) return
              await api.calibrerLePlan(documentId, revisionId, {
                page,
                premier,
                second,
                distance_reelle: distance,
                unite,
                resolution_du_pointage: String(resolutionDeLaLoupe(largeurDeLaPage)),
                motif,
              })
              setPoints([])
              setOutil('naviguer')
              rechargerLeTravail()
            } catch (cause) {
              setErreur(cause)
            } finally {
              setOccupe(false)
            }
          }}
        />
      )}

      {(outil === 'segment' || outil === 'surface') &&
        points.length >= (outil === 'surface' ? 3 : 2) && (
          <FormulaireDeMesure
            type={outil}
            nombreDePoints={points.length}
            occupe={occupe}
            sansCalibration={calibrationDeLaPage === null}
            onValider={async (libelle) => {
              setOccupe(true)
              setErreur(null)
              try {
                await api.mesurerSurLePdf(documentId, revisionId, {
                  page,
                  type: outil,
                  points,
                  libelle,
                })
                setPoints([])
                rechargerLeTravail()
                onRelire()
              } catch (cause) {
                setErreur(cause)
              } finally {
                setOccupe(false)
              }
            }}
          />
        )}

      <ListeDesMesures
        mesures={travail?.mesures ?? []}
        onDecidee={() => {
          rechargerLeTravail()
          onRelire()
        }}
        onMontrer={(mesure) => {
          setPage(mesure.page)
          if (mesure.cadre) {
            const demi = TAILLE_DE_LA_LOUPE / 2
            const cx = (Number(mesure.cadre.x0) + Number(mesure.cadre.x1)) / 2
            const cy = (Number(mesure.cadre.y0) + Number(mesure.cadre.y1)) / 2
            setLoupe([
              Math.max(0, cx - demi),
              Math.max(0, cy - demi),
              Math.min(1, cx + demi),
              Math.min(1, cy + demi),
            ])
          }
        }}
      />

      {textes && (
        <p className="muted" data-testid="pdf-textes-lus">
          {t('plan.pdf.textesLus')
            .replace('{rendus}', String(textes.fragments.length))
            .replace('{total}', String(textes.total))}
        </p>
      )}
    </section>
  )
}

function EnTetePdf({
  plan,
  calibration,
}: {
  plan: PlanLu
  calibration: CalibrationDePlan | null
}) {
  return (
    <dl className="pdf-entete" data-testid="pdf-entete">
      <div>
        <dt>{t('plan.pdf.pagesLabel')}</dt>
        <dd>{plan.pages}</dd>
      </div>
      <div>
        <dt>{t('plan.pdf.texteLabel')}</dt>
        <dd>{plan.porte_du_texte ? plan.fragments_lus : t('plan.pdf.sansTexte')}</dd>
      </div>
      <div>
        <dt>{t('plan.pdf.echelleLabel')}</dt>
        {/* Le point le plus important de cet en-tête : sans échelle confirmée,
            aucune mesure n'est possible, et le dire d'emblée évite de laisser
            quelqu'un chercher pourquoi le bouton « mesurer » ne donne rien. */}
        <dd data-testid="pdf-echelle">
          {calibration ? (
            <>
              <strong data-testid="pdf-facteur">{calibration.facteur_lisible}</strong>
              <br />
              <span className="muted">{calibration.motif}</span>
            </>
          ) : (
            <span className="badge warning">{t('plan.pdf.sansEchelle')}</span>
          )}
        </dd>
      </div>
    </dl>
  )
}

/** Les boîtes posées sur l'aperçu : textes lus, mesures prises, loupe ouverte. */
function Surlignages({
  fragments,
  mesures,
  loupe,
}: {
  fragments: FragmentDeTexte[]
  mesures: MesureDePdf[]
  loupe: [number, number, number, number] | null
}) {
  return (
    <svg className="pdf-surlignages" viewBox="0 0 1 1" preserveAspectRatio="none">
      {fragments
        .filter((fragment) => fragment.cadre !== null)
        .map((fragment, rang) => {
          const c = fragment.cadre!
          return (
            <rect
              key={`t${rang}`}
              className="pdf-texte"
              data-testid="pdf-texte"
              data-texte={fragment.texte}
              x={Number(c.x0)}
              y={Number(c.y0)}
              width={Number(c.x1) - Number(c.x0)}
              height={Number(c.y1) - Number(c.y0)}
            >
              {/*
                Le texte lu, porté par le surlignage lui-même. À 2,4 pixels de
                haut, le propriétaire voit QU'IL Y A une information sans
                pouvoir la lire : ce `<title>` la lui donne au survol, et la
                donne aussi aux lecteurs d'écran, pour qui un rectangle sans
                nom n'existe pas.
              */}
              <title>{fragment.texte}</title>
            </rect>
          )
        })}
      {mesures.map((mesure) => (
        <polyline
          key={mesure.proposal_id}
          className={
            mesure.fiabilite === 'mesurable' ? 'pdf-mesure' : 'pdf-mesure douteuse'
          }
          points={fermerSiSurface(mesure.points, mesure.type)
            .map((p) => `${p.x},${p.y}`)
            .join(' ')}
        />
      ))}
      {loupe && (
        <rect
          className="pdf-loupe"
          x={loupe[0]}
          y={loupe[1]}
          width={loupe[2] - loupe[0]}
          height={loupe[3] - loupe[1]}
        />
      )}
    </svg>
  )
}

function Loupe({
  url,
  enCours,
  erreur,
  points,
  loupe,
  outil,
  onCliquer,
  onFermer,
}: {
  url: string | null
  enCours: boolean
  erreur: unknown
  points: PointDEcran[]
  loupe: [number, number, number, number]
  outil: Outil
  onCliquer: (evenement: React.MouseEvent<HTMLDivElement>) => void
  onFermer: () => void
}) {
  return (
    <div className="pdf-loupe-panneau" data-testid="pdf-loupe-panneau">
      <div className="pdf-loupe-entete">
        <strong>{t('plan.pdf.loupe')}</strong>
        <button type="button" data-testid="pdf-loupe-fermer" onClick={onFermer}>
          {t('common.close')}
        </button>
      </div>
      {/* L'attente est ANNONCÉE, parce qu'elle est longue et qu'elle a une
          cause : charger une page de PDF coûte jusqu'à 5,3 secondes la
          première fois. Les suivantes viennent du cache, en 0,3 ms. */}
      {enCours && <p className="muted">{t('plan.pdf.loupeEnCours')}</p>}
      <ErrorNotice error={erreur} />
      {url && (
        <div className="pdf-loupe-image" data-testid="pdf-loupe-image" onClick={onCliquer}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={url} alt={t('plan.pdf.loupeAlt')} data-testid="pdf-loupe-rendu" />
          <svg className="pdf-surlignages" viewBox="0 0 1 1" preserveAspectRatio="none">
            {points.map((point, rang) => (
              <circle
                key={rang}
                className="pdf-point"
                cx={(point.x - loupe[0]) / (loupe[2] - loupe[0])}
                cy={(point.y - loupe[1]) / (loupe[3] - loupe[1])}
                r={0.012}
              />
            ))}
            {points.length >= 2 && (
              <polyline
                className="pdf-trace"
                points={fermerSiSurface(points, outil)
                  .map(
                    (p) =>
                      `${(p.x - loupe[0]) / (loupe[2] - loupe[0])},` +
                      `${(p.y - loupe[1]) / (loupe[3] - loupe[1])}`,
                  )
                  .join(' ')}
              />
            )}
          </svg>
        </div>
      )}
      {outil !== 'naviguer' && (
        <p className="muted">
          {t('plan.pdf.pointsPoses').replace('{nombre}', String(points.length))}
        </p>
      )}
    </div>
  )
}

function FormulaireDeCalibration({
  occupe,
  onValider,
}: {
  occupe: boolean
  onValider: (distance: string, unite: string, motif: string) => void
}) {
  const [distance, setDistance] = useState('')
  const [unite, setUnite] = useState<string>('mm')
  const [motif, setMotif] = useState('')

  return (
    <form
      className="pdf-formulaire"
      data-testid="pdf-formulaire-calibration"
      onSubmit={(evenement) => {
        evenement.preventDefault()
        onValider(distance.trim(), unite, motif.trim())
      }}
    >
      <h3>{t('plan.pdf.confirmerEchelle')}</h3>
      {/* La phrase qui évite le contresens le plus coûteux : ce n'est pas une
          échelle au sens « 1:50 » qu'on saisit, c'est la distance RÉELLE entre
          les deux points qu'on vient de désigner. */}
      <p className="muted">{t('plan.pdf.aideEchelle')}</p>
      <label htmlFor="calib-distance">{t('plan.pdf.distanceReelle')}</label>
      <input
        id="calib-distance"
        value={distance}
        onChange={(e) => setDistance(e.target.value)}
        inputMode="decimal"
        required
      />
      <label htmlFor="calib-unite">{t('plan.pdf.unite')}</label>
      <select id="calib-unite" value={unite} onChange={(e) => setUnite(e.target.value)}>
        {UNITES.map((code) => (
          <option key={code} value={code}>
            {code}
          </option>
        ))}
      </select>
      <label htmlFor="calib-motif">{t('plan.pdf.motif')}</label>
      <input
        id="calib-motif"
        value={motif}
        onChange={(e) => setMotif(e.target.value)}
        placeholder={t('plan.pdf.motifExemple')}
        required
      />
      <button
        className="primary"
        type="submit"
        data-testid="pdf-calibrer"
        disabled={occupe}
      >
        {occupe ? t('common.saving') : t('plan.pdf.confirmer')}
      </button>
    </form>
  )
}

function FormulaireDeMesure({
  type,
  nombreDePoints,
  occupe,
  sansCalibration,
  onValider,
}: {
  type: 'segment' | 'surface'
  nombreDePoints: number
  occupe: boolean
  sansCalibration: boolean
  onValider: (libelle: string) => void
}) {
  const [libelle, setLibelle] = useState('')
  return (
    <form
      className="pdf-formulaire"
      data-testid="pdf-formulaire-mesure"
      onSubmit={(evenement) => {
        evenement.preventDefault()
        onValider(libelle.trim())
      }}
    >
      <h3>
        {type === 'segment' ? t('plan.pdf.nommerLongueur') : t('plan.pdf.nommerSurface')}
      </h3>
      {sansCalibration && (
        <div className="notice warning" role="alert">
          {t('plan.pdf.sansEchelleMesure')}
        </div>
      )}
      <p className="muted">
        {t('plan.pdf.pointsPoses').replace('{nombre}', String(nombreDePoints))}
      </p>
      <label htmlFor="mesure-libelle">{t('plan.pdf.libelle')}</label>
      <input
        id="mesure-libelle"
        value={libelle}
        onChange={(e) => setLibelle(e.target.value)}
        placeholder={t('plan.pdf.libelleExemple')}
        required
      />
      <button
        className="primary"
        type="submit"
        data-testid="pdf-mesurer"
        disabled={occupe || sansCalibration}
      >
        {occupe ? t('common.saving') : t('plan.pdf.mesurer')}
      </button>
    </form>
  )
}

function ListeDesMesures({
  mesures,
  onDecidee,
  onMontrer,
}: {
  mesures: MesureDePdf[]
  onDecidee: () => void
  onMontrer: (mesure: MesureDePdf) => void
}) {
  if (mesures.length === 0) {
    return <p className="muted">{t('plan.pdf.aucuneMesure')}</p>
  }
  return (
    <table className="pdf-mesures" data-testid="pdf-mesures">
      <caption>{t('plan.pdf.mesuresPrises')}</caption>
      <thead>
        <tr>
          <th scope="col">{t('plan.pdf.colLibelle')}</th>
          <th scope="col">{t('plan.pdf.colValeur')}</th>
          <th scope="col">{t('plan.pdf.colFiabilite')}</th>
          <th scope="col">{t('plan.pdf.colDecision')}</th>
          <th scope="col">{t('plan.pdf.colActions')}</th>
        </tr>
      </thead>
      <tbody>
        {mesures.map((mesure) => (
          <LigneDeMesurePdf
            key={mesure.proposal_id}
            mesure={mesure}
            onDecidee={onDecidee}
            onMontrer={() => onMontrer(mesure)}
          />
        ))}
      </tbody>
    </table>
  )
}

function LigneDeMesurePdf({
  mesure,
  onDecidee,
  onMontrer,
}: {
  mesure: MesureDePdf
  onDecidee: () => void
  onMontrer: () => void
}) {
  const [correction, setCorrection] = useState('')
  const [motif, setMotif] = useState('')
  const [occupe, setOccupe] = useState(false)
  const [erreur, setErreur] = useState<unknown>(null)

  async function decider(decision: 'accepted' | 'corrected' | 'rejected') {
    setOccupe(true)
    setErreur(null)
    try {
      await api.deciderProposition(
        mesure.proposal_id,
        decision === 'corrected'
          ? {
              decision,
              reason: motif.trim(),
              before_value: { valeur: mesure.valeur },
              after_value: { valeur: correction.trim() },
            }
          : { decision, reason: motif.trim() },
      )
      onDecidee()
    } catch (cause) {
      setErreur(cause)
    } finally {
      setOccupe(false)
    }
  }

  return (
    <tr data-testid="pdf-mesure">
      <td data-testid="pdf-libelle">
        {mesure.libelle}
        <br />
        <span className="muted">
          {t('plan.pdf.pageCourte').replace('{page}', String(mesure.page))} ·{' '}
          {mesure.type === 'segment' ? t('plan.pdf.longueur') : t('plan.pdf.surface')}
        </span>
      </td>
      <td>
        <strong data-testid="pdf-valeur">
          {mesure.valeur} {mesure.unite}
        </strong>
        {/* L'incertitude, dans la MÊME unité que la valeur. En pourcentage,
            elle demanderait une multiplication que personne ne fait. */}
        <br />
        <span className="muted">
          ± {mesure.incertitude} {mesure.unite}
        </span>
        {mesure.valeur_corrigee && (
          <>
            <br />
            <span className="badge" data-testid="pdf-valeur-retenue">
              {t('plan.pdf.corrigeeEn')} {mesure.valeur_corrigee}
            </span>
          </>
        )}
      </td>
      <td>
        <span
          data-testid="pdf-fiabilite"
          className={mesure.fiabilite === 'mesurable' ? 'badge' : 'badge warning'}
        >
          {mesure.fiabilite === 'mesurable'
            ? t('plan.pdf.mesurable')
            : t('plan.pdf.aVerifier')}
        </span>
        {mesure.reserves.map((reserve) => (
          <div key={reserve} className="muted">
            {t(`plan.pdf.reserve.${reserve}`)}
          </div>
        ))}
        <div className="muted">
          {t('plan.pdf.depuis').replace(
            '{motif}',
            String(mesure.calibration.motif ?? ''),
          )}
        </div>
      </td>
      <td data-testid="pdf-decision">
        {mesure.decision ? t(`plan.decision.${mesure.decision}`) : '—'}
      </td>
      <td>
        <ErrorNotice error={erreur} />
        <button type="button" data-testid="pdf-montrer" onClick={onMontrer}>
          {t('plan.pdf.montrer')}
        </button>
        <input
          aria-label={t('plan.pdf.motifDecision')}
          data-testid="pdf-motif-decision"
          value={motif}
          onChange={(e) => setMotif(e.target.value)}
          placeholder={t('plan.pdf.motifDecision')}
        />
        <button
          type="button"
          data-testid="pdf-confirmer-mesure"
          disabled={occupe || !motif.trim()}
          onClick={() => decider('accepted')}
        >
          {t('plan.pdf.confirmerMesure')}
        </button>
        <input
          aria-label={t('plan.pdf.valeurCorrigee')}
          data-testid="pdf-correction"
          value={correction}
          onChange={(e) => setCorrection(e.target.value)}
          placeholder={t('plan.pdf.valeurCorrigee')}
          inputMode="decimal"
        />
        <button
          type="button"
          data-testid="pdf-corriger"
          disabled={occupe || !motif.trim() || !correction.trim()}
          onClick={() => decider('corrected')}
        >
          {t('plan.pdf.corriger')}
        </button>
        {/*
          Le rejet manquait, et son absence était un piège. `decider` l'accepte
          depuis le début, la clé `plan.decision.rejected` existe, et la colonne
          « Décision » sait l'afficher : l'écran annonçait donc un vocabulaire
          qu'aucun bouton ne pouvait produire. Sans lui, une mesure visiblement
          fausse ne laissait que deux issues — la confirmer, ou la « corriger »
          vers une valeur que la personne ne connaît pas.

          Comme les deux autres, il exige un motif : une décision sans raison
          n'est pas une décision, et c'est elle qui sera relue dans six mois.
        */}
        <button
          type="button"
          data-testid="pdf-rejeter"
          disabled={occupe || !motif.trim()}
          onClick={() => decider('rejected')}
          title={t('plan.pdf.aideRejeter')}
        >
          {t('plan.pdf.rejeter')}
        </button>
      </td>
    </tr>
  )
}
