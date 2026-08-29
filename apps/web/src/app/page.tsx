'use client'

import { useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useCallback, useEffect, useState } from 'react'

import { ErrorNotice } from '@/components/Feedback'
import { api, ApiError, loadSession, storeSession, type Health } from '@/lib/api'
import { t } from '@/lib/i18n'

const DEMO_ACCOUNTS = [
  { email: 'admin@dubois.demo', role: "Administrateur — Terrassements Dubois SA (démo)" },
  { email: 'metreur@dubois.demo', role: 'Métreur / deviseur — Terrassements Dubois SA (démo)' },
  { email: 'lecteur@dubois.demo', role: 'Lecteur / auditeur — Terrassements Dubois SA (démo)' },
  { email: 'admin@janssens.demo', role: 'Administrateur — Wegenbouw Janssens NV (demo)' },
]

/** Traduit un code d'erreur de retour en phrase, sans jamais afficher le code brut. */
function messageDeRetour(code: string): string {
  const cle = `login.error.${code}`
  const traduit = t(cle)
  return traduit === cle ? t('login.error.generic') : traduit
}

function LoginPage() {
  const router = useRouter()
  const parametres = useSearchParams()
  const [email, setEmail] = useState('admin@dubois.demo')
  const [organizationId, setOrganizationId] = useState('')
  const [choices, setChoices] = useState<string[]>([])
  const [error, setError] = useState<unknown>(null)
  const [retour, setRetour] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [health, setHealth] = useState<Health | null>(null)

  const loginCode = parametres.get('login_code')
  const loginError = parametres.get('login_error')
  const returnTo = parametres.get('return_to')

  useEffect(() => {
    if (loadSession()) router.replace('/projets')
  }, [router])

  useEffect(() => {
    api
      .health()
      .then(setHealth)
      .catch(() => setHealth(null))
  }, [])

  useEffect(() => {
    if (loginError) setRetour(messageDeRetour(loginError))
  }, [loginError])

  const arriver = useCallback(
    (token: { access_token: string; user_id: string; organization_id: string; role: string }) => {
      storeSession({
        token: token.access_token,
        userId: token.user_id,
        organizationId: token.organization_id,
        role: token.role,
      })
      router.push(returnTo && returnTo.startsWith('/') ? returnTo : '/projets')
    },
    [router, returnTo],
  )

  // Le code de connexion vaut une seule fois. On le retire de la barre
  // d'adresse dès qu'il est échangé, pour qu'un rechargement ne rejoue pas un
  // code déjà consommé et n'affiche pas une erreur qui n'en est pas une.
  useEffect(() => {
    if (!loginCode) return
    let annule = false
    setBusy(true)
    api
      .oidcExchange(loginCode)
      .then((token) => {
        if (!annule) arriver(token)
      })
      .catch((caught) => {
        if (annule) return
        if (caught instanceof ApiError && caught.code === 'organization_required') {
          const detail = caught.detail as { organization_ids?: string[] }
          setChoices(detail.organization_ids ?? [])
        }
        setError(caught)
        window.history.replaceState(null, '', window.location.pathname)
      })
      .finally(() => {
        if (!annule) setBusy(false)
      })
    return () => {
      annule = true
    }
  }, [loginCode, arriver])

  async function commencerOidc() {
    setBusy(true)
    setError(null)
    setRetour(null)
    try {
      const depart = await api.oidcStart(returnTo ?? undefined)
      window.location.assign(depart.authorization_url)
    } catch (caught) {
      setError(caught)
      setBusy(false)
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      arriver(await api.devLogin(email, organizationId || undefined))
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === 'organization_required') {
        const detail = caught.detail as { organization_ids?: string[] }
        setChoices(detail.organization_ids ?? [])
      }
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  // Tant que /health n'a pas répondu, on ne suppose rien : proposer un
  // formulaire qui n'aboutira pas coûte plus cher qu'attendre une seconde.
  const methodes = health?.login_methods ?? null
  const oidc = methodes?.includes('oidc') ?? false
  const dev = methodes?.includes('dev') ?? false

  return (
    <main style={{ maxWidth: 460, margin: '10vh auto' }}>
      <div className="brand" style={{ marginBottom: 16 }}>
        {t('app.name')}
        <small>{t('app.tagline')}</small>
      </div>

      <div className="card">
        <h1>{t('login.title')}</h1>
        {retour && (
          <div className="notice warning" role="alert">
            {retour}
          </div>
        )}
        <ErrorNotice error={error} />

        {methodes === null && <p className="muted">{t('common.loading')}</p>}

        {oidc && (
          <>
            <div className="notice info">{t('login.oidcNotice')}</div>
            <button className="primary" type="button" disabled={busy} onClick={commencerOidc}>
              {busy ? t('login.oidcPending') : t('login.oidcSubmit')}
            </button>
          </>
        )}

        {methodes !== null && methodes.length === 0 && (
          <div className="notice warning">{t('login.noMethod')}</div>
        )}
      </div>

      {dev && (
        <>
          <form className="card" onSubmit={submit}>
            <div className="notice info">{t('login.devNotice')}</div>

            <div className="field">
              <label htmlFor="email">{t('login.email')}</label>
              <input
                id="email"
                type="email"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            </div>

            {choices.length > 0 && (
              <div className="field">
                <label htmlFor="org">{t('login.organization')}</label>
                <select
                  id="org"
                  value={organizationId}
                  onChange={(event) => setOrganizationId(event.target.value)}
                >
                  <option value="">—</option>
                  {choices.map((id) => (
                    <option key={id} value={id}>
                      {id}
                    </option>
                  ))}
                </select>
              </div>
            )}

            <button className="primary" type="submit" disabled={busy}>
              {busy ? t('common.loading') : t('login.submit')}
            </button>
          </form>

          <div className="card">
            <h2 style={{ marginTop: 0 }}>{t('login.demoAccounts')}</h2>
            <table>
              <tbody>
                {DEMO_ACCOUNTS.map((account) => (
                  <tr key={account.email}>
                    <td>
                      <button type="button" onClick={() => setEmail(account.email)}>
                        {account.email}
                      </button>
                    </td>
                    <td className="muted">{account.role}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="muted" style={{ fontSize: 12 }}>
              Ces comptes n&apos;existent qu&apos;après exécution de{' '}
              <span className="mono">python -m metreo_api.seed</span>.
            </p>
          </div>
        </>
      )}
    </main>
  )
}

export default function Page() {
  return (
    <Suspense fallback={<main style={{ maxWidth: 460, margin: '10vh auto' }} />}>
      <LoginPage />
    </Suspense>
  )
}
