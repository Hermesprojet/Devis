'use client'

import { ApiError } from '@/lib/api'
import { t } from '@/lib/i18n'

/** Renders an API failure with its actionable detail instead of a generic toast. */
export function ErrorNotice({ error }: { error: unknown }) {
  if (!error) return null
  if (error instanceof ApiError) {
    const detail = (error.detail ?? {}) as Record<string, unknown>
    const problems = Array.isArray(detail.problems) ? detail.problems : null
    return (
      <div className="notice error" role="alert">
        <strong>{error.message}</strong>
        {typeof detail.required_permission === 'string' && (
          <div className="mono">permission requise : {detail.required_permission}</div>
        )}
        {problems && (
          <ul>
            {problems.map((problem, index) => {
              const row = problem as Record<string, unknown>
              return (
                <li key={index}>
                  <strong>{String(row.position ?? '')}</strong> {String(row.designation ?? '')} —{' '}
                  {String(row.message ?? '')}
                  {typeof row.hint === 'string' && <div className="muted">{row.hint}</div>}
                </li>
              )
            })}
          </ul>
        )}
      </div>
    )
  }
  return (
    <div className="notice error" role="alert">
      {t('common.error')} : {String((error as Error)?.message ?? error)}
    </div>
  )
}

export function Loading() {
  return <div className="skeleton">{t('common.loading')}</div>
}
