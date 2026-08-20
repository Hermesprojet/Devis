'use client'

import { useEffect, useState } from 'react'

import { ErrorNotice, Loading } from '@/components/Feedback'
import { Shell } from '@/components/Shell'
import { api, type AuditEvent, type AuditVerify } from '@/lib/api'
import { t } from '@/lib/i18n'

export default function AuditPage() {
  const [events, setEvents] = useState<AuditEvent[] | null>(null)
  const [verification, setVerification] = useState<AuditVerify | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    api
      .auditEvents('?limit=200')
      .then((page) => setEvents(page.items))
      .catch(setError)
  }, [])

  async function verify() {
    setError(null)
    try {
      setVerification(await api.auditVerify())
    } catch (caught) {
      setError(caught)
    }
  }

  return (
    <Shell>
      <h1>{t('audit.title')}</h1>
      <ErrorNotice error={error} />

      <div className="toolbar">
        <button onClick={() => void verify()}>{t('audit.verify')}</button>
        {verification && (
          <span className={`badge ${verification.valid ? 'success' : 'danger'}`}>
            {verification.valid ? t('audit.valid') : t('audit.invalid')} — {verification.checked}{' '}
            {t('audit.checked')}
            {verification.reason ? ` (${verification.reason})` : ''}
          </span>
        )}
      </div>

      <p className="muted" style={{ fontSize: 12 }}>
        Chaque événement est chaîné au précédent par une empreinte SHA-256. Une
        suppression ou une modification rend la chaîne invérifiable — c&apos;est
        une détection d&apos;altération, pas une impossibilité d&apos;altération.
      </p>

      {events === null ? (
        <Loading />
      ) : (
        <div className="card" style={{ padding: 0 }}>
          <table>
            <thead>
              <tr>
                <th>{t('audit.sequence')}</th>
                <th>{t('audit.when')}</th>
                <th>{t('audit.who')}</th>
                <th>{t('audit.what')}</th>
                <th>{t('audit.summary')}</th>
                <th>Empreinte</th>
              </tr>
            </thead>
            <tbody>
              {events.map((event) => (
                <tr key={event.id}>
                  <td className="mono">{event.sequence}</td>
                  <td className="mono">
                    {new Date(event.occurred_at).toLocaleString('fr-BE')}
                  </td>
                  <td>{event.actor_email ?? t('common.none')}</td>
                  <td className="mono">{event.action}</td>
                  <td>{event.summary}</td>
                  <td className="mono muted">{event.hash.slice(0, 12)}…</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Shell>
  )
}
