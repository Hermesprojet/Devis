'use client'

import { useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'

import { ErrorNotice } from '@/components/Feedback'
import { api, ApiError, loadSession, storeSession } from '@/lib/api'
import { t } from '@/lib/i18n'

const DEMO_ACCOUNTS = [
  { email: 'admin@dubois.demo', role: "Administrateur — Terrassements Dubois SA (démo)" },
  { email: 'metreur@dubois.demo', role: 'Métreur / deviseur — Terrassements Dubois SA (démo)' },
  { email: 'lecteur@dubois.demo', role: 'Lecteur / auditeur — Terrassements Dubois SA (démo)' },
  { email: 'admin@janssens.demo', role: 'Administrateur — Wegenbouw Janssens NV (demo)' },
]

export default function LoginPage() {
  const router = useRouter()
  const [email, setEmail] = useState('admin@dubois.demo')
  const [organizationId, setOrganizationId] = useState('')
  const [choices, setChoices] = useState<string[]>([])
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (loadSession()) router.replace('/projets')
  }, [router])

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const token = await api.devLogin(email, organizationId || undefined)
      storeSession({
        token: token.access_token,
        userId: token.user_id,
        organizationId: token.organization_id,
        role: token.role,
      })
      router.push('/projets')
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

  return (
    <main style={{ maxWidth: 460, margin: '10vh auto' }}>
      <div className="brand" style={{ marginBottom: 16 }}>
        {t('app.name')}
        <small>{t('app.tagline')}</small>
      </div>

      <form className="card" onSubmit={submit}>
        <h1>{t('login.title')}</h1>
        <div className="notice info">{t('login.devNotice')}</div>
        <ErrorNotice error={error} />

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
    </main>
  )
}
