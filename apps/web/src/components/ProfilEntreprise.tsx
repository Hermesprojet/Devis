'use client'

import { useCallback, useEffect, useRef, useState } from 'react'

import { ErrorNotice } from '@/components/Feedback'
import { api, type Organization } from '@/lib/api'
import { t } from '@/lib/i18n'
import { PERMISSIONS, can } from '@/lib/permissions'
import { usePermissions } from '@/lib/usePermissions'

/**
 * Le profil de l'entreprise : ce que ses devis imprimeront en en-tête.
 *
 * Mesuré au navigateur depuis une organisation vide, avant ce travail :
 * l'entreprise n'avait ni adresse, ni coordonnées, ni logo, et le PDF
 * n'imprimait que son nom, sa raison sociale et son numéro d'entreprise. Un
 * client recevait un devis sans savoir où répondre.
 *
 * **La liste des manques vient du SERVEUR.** `missing_for_issue` est calculé
 * par la même fonction que l'émission consulte. Une seconde liste tenue ici
 * divergerait au premier champ ajouté, et l'écran promettrait une émission que
 * le serveur refuserait.
 */

/** Les champs modifiables, dans l'ordre où l'écran les présente. */
const CHAMPS = [
  ['name', 'profile.name', true],
  ['legal_name', 'profile.legalName', false],
  ['company_number', 'profile.companyNumber', false],
  ['address', 'profile.address', true],
  ['address_complement', 'profile.addressComplement', false],
  ['postal_code', 'profile.postalCode', true],
  ['city', 'profile.city', true],
  ['country_code', 'profile.countryCode', true],
  ['email', 'profile.email', false],
  ['phone', 'profile.phone', false],
  ['website', 'profile.website', false],
] as const

type Champ = (typeof CHAMPS)[number][0]

function saisieDe(organisation: Organization | null): Record<Champ, string> {
  const vide = Object.fromEntries(CHAMPS.map(([cle]) => [cle, ''])) as Record<Champ, string>
  if (!organisation) return vide
  for (const [cle] of CHAMPS) {
    vide[cle] = (organisation[cle] as string | null) ?? ''
  }
  return vide
}

/**
 * L'URL locale du logo, rapportée avec le jeton.
 *
 * `<img src="/organization/logo">` ne marcherait pas : le navigateur émet une
 * requête d'image NUE, sans en-tête `Authorization`, et la route répond 401 —
 * l'écran montrait une image cassée là où il annonçait un logo. On rapatrie
 * donc les octets et on en fait une URL d'objet.
 *
 * L'empreinte sert de clé : elle change à chaque remplacement, donc l'effet se
 * rejoue, et l'ancienne URL est révoquée — sans quoi chaque remplacement
 * fuirait un objet dans la mémoire de l'onglet.
 */
function useLogo(empreinte: string | null): string | null {
  const [url, setUrl] = useState<string | null>(null)
  useEffect(() => {
    if (!empreinte) {
      setUrl(null)
      return
    }
    let vivant = true
    let objet: string | null = null
    api
      .logoBlob()
      .then((octets) => {
        objet = URL.createObjectURL(octets)
        if (vivant) setUrl(objet)
        else URL.revokeObjectURL(objet)
      })
      .catch(() => {
        if (vivant) setUrl(null)
      })
    return () => {
      vivant = false
      if (objet) URL.revokeObjectURL(objet)
    }
  }, [empreinte])
  return url
}

export function ProfilEntreprise() {
  const [organisation, setOrganisation] = useState<Organization | null>(null)
  const [saisie, setSaisie] = useState<Record<Champ, string>>(saisieDe(null))
  const [erreur, setErreur] = useState<unknown>(null)
  const [enregistre, setEnregistre] = useState(false)
  const [occupe, setOccupe] = useState(false)
  const fichier = useRef<HTMLInputElement>(null)
  const permissions = usePermissions()
  const ecrire = can(permissions, PERMISSIONS.orgManage)
  const logo = useLogo(organisation?.logo?.sha256 ?? null)

  const recharger = useCallback(async () => {
    try {
      const lue = await api.organization()
      setOrganisation(lue)
      setSaisie(saisieDe(lue))
      setErreur(null)
    } catch (attrape) {
      setErreur(attrape)
    }
  }, [])

  useEffect(() => {
    void recharger()
  }, [recharger])

  async function enregistrer() {
    setOccupe(true)
    setErreur(null)
    setEnregistre(false)
    try {
      const lue = await api.updateOrganization(saisie)
      setOrganisation(lue)
      setSaisie(saisieDe(lue))
      setEnregistre(true)
    } catch (attrape) {
      setErreur(attrape)
    } finally {
      setOccupe(false)
    }
  }

  async function charger(evenement: React.ChangeEvent<HTMLInputElement>) {
    const choisi = evenement.target.files?.[0]
    // Le champ est remis à zéro tout de suite : sans cela, rechoisir LE MÊME
    // fichier après un refus ne déclencherait aucun événement, et l'écran
    // paraîtrait ignorer le clic.
    evenement.target.value = ''
    if (!choisi) return
    setOccupe(true)
    setErreur(null)
    try {
      setOrganisation(await api.uploadLogo(choisi))
    } catch (attrape) {
      setErreur(attrape)
    } finally {
      setOccupe(false)
    }
  }

  async function retirer() {
    setOccupe(true)
    setErreur(null)
    try {
      setOrganisation(await api.deleteLogo())
    } catch (attrape) {
      setErreur(attrape)
    } finally {
      setOccupe(false)
    }
  }

  const manques = organisation?.missing_for_issue ?? []

  return (
    <div className="card" data-testid="profil-entreprise">
      <h2 style={{ marginTop: 0 }}>{t('profile.title')}</h2>
      <p className="muted">{t('profile.hint')}</p>
      <ErrorNotice error={erreur} />

      {!ecrire && <div className="notice info">{t('profile.readOnly')}</div>}

      {organisation && manques.length > 0 && (
        <div className="notice warning" role="alert" data-testid="profil-insuffisant">
          <strong>{t('profile.incomplete')}</strong>
          <br />
          {t('profile.incompleteHint')}
          {manques.map((champ) => t(`profile.field.${champ}`)).join(', ')}.
        </div>
      )}
      {organisation && manques.length === 0 && (
        <div className="notice success" data-testid="profil-suffisant">
          {t('profile.complete')}
        </div>
      )}
      {enregistre && <div className="notice success">{t('settings.saved')}</div>}

      <h3>{t('profile.identity')}</h3>
      <div className="row">
        {CHAMPS.map(([cle, libelle, requis]) => (
          <div className="field" key={cle} style={{ flex: '1 1 220px' }}>
            <label htmlFor={`profil-${cle}`}>
              {t(libelle)}
              {!requis && <span className="muted"> — {t('profile.optional')}</span>}
            </label>
            <input
              id={`profil-${cle}`}
              value={saisie[cle]}
              disabled={!ecrire}
              // Surligné quand il manque ET qu'il est requis : l'utilisateur
              // voit d'un coup d'œil où porter la main.
              className={manques.includes(cle) ? 'invalide' : undefined}
              onChange={(evenement) => {
                setEnregistre(false)
                setSaisie({ ...saisie, [cle]: evenement.target.value })
              }}
            />
          </div>
        ))}
      </div>
      {ecrire && (
        <p>
          <button className="primary" onClick={() => void enregistrer()} disabled={occupe}>
            {t('common.save')}
          </button>
        </p>
      )}

      <h3>{t('profile.logo')}</h3>
      <p className="muted">{t('profile.logoHint')}</p>
      {organisation?.logo ? (
        <p>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={logo ?? undefined}
            alt={t('profile.logo')}
            data-testid="logo-actuel"
            style={{ maxHeight: 72, maxWidth: 240, verticalAlign: 'middle' }}
          />{' '}
          <span className="muted mono">
            {organisation.logo.width}×{organisation.logo.height}
          </span>
        </p>
      ) : (
        <p className="muted" data-testid="logo-absent">
          {t('profile.logoNone')}
        </p>
      )}
      {ecrire && (
        <p>
          <input
            ref={fichier}
            id="profil-logo"
            type="file"
            accept="image/png"
            onChange={(evenement) => void charger(evenement)}
            disabled={occupe}
          />{' '}
          {organisation?.logo && (
            <button onClick={() => void retirer()} disabled={occupe}>
              {t('profile.logoRemove')}
            </button>
          )}
        </p>
      )}

      <h3>{t('profile.preview')}</h3>
      <p className="muted">{t('profile.previewHint')}</p>
      <ApercuEntete logo={logo} saisie={saisie} />
    </div>
  )
}

/**
 * L'en-tête tel que le devis l'imprimera.
 *
 * Il reprend l'ORDRE et le contenu de `quote_pdf._lignes_d_identite` : nom,
 * raison sociale, numéro d'entreprise, adresse, complément, localité, pays,
 * puis contacts. Un aperçu qui montrerait autre chose serait pire que pas
 * d'aperçu — il ferait valider une mise en page que le document ne tiendrait
 * pas.
 *
 * Il lit la SAISIE en cours, pas la dernière réponse du serveur : on voit
 * l'effet de ce qu'on tape avant d'enregistrer, ce qui est tout l'intérêt.
 */
function ApercuEntete({ logo, saisie }: { logo: string | null; saisie: Record<Champ, string> }) {
  const propre = (cle: Champ) => saisie[cle].trim()
  const localite = [propre('postal_code'), propre('city')].filter(Boolean).join(' ')
  const contacts = [propre('phone'), propre('email')].filter(Boolean).join(' — ')
  const lignes = [
    propre('legal_name'),
    propre('company_number') ? `N° d'entreprise : ${propre('company_number')}` : '',
    propre('address'),
    propre('address_complement'),
    localite,
    propre('country_code'),
    contacts,
    propre('website'),
  ].filter(Boolean)

  return (
    <div className="card" data-testid="apercu-entete" style={{ background: '#fff' }}>
      <div style={{ display: 'flex', gap: 14, alignItems: 'flex-start' }}>
        {logo && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={logo} alt="" style={{ maxHeight: 46, maxWidth: 108 }} />
        )}
        <div>
          <div style={{ fontWeight: 700, fontSize: logo ? 15 : 18 }}>
            {propre('name')}
          </div>
          {lignes.map((ligne, rang) => (
            <div key={rang} style={{ fontSize: 12 }}>
              {ligne}
            </div>
          ))}
        </div>
        <div className="spacer" />
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontWeight: 700, fontSize: 20 }}>DEVIS</div>
          <div className="muted mono">N° …</div>
        </div>
      </div>
    </div>
  )
}
