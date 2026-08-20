'use client'

import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'

import { api, clearSession, loadSession, type Health, type Me } from '@/lib/api'
import { t } from '@/lib/i18n'

const LINKS = [
  { href: '/projets', key: 'nav.projects' },
  { href: '/bibliotheque', key: 'nav.priceBooks' },
  { href: '/audit', key: 'nav.audit' },
  { href: '/parametres', key: 'nav.settings' },
]

/**
 * Authenticated shell. Redirects to the login page when there is no session
 * rather than rendering an empty screen a user would take for a bug.
 */
export function Shell({ children }: { children: React.ReactNode }) {
  const router = useRouter()
  const pathname = usePathname()
  const [me, setMe] = useState<Me | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [checked, setChecked] = useState(false)

  useEffect(() => {
    if (!loadSession()) {
      router.replace('/')
      return
    }
    let active = true
    Promise.all([api.me(), api.health()])
      .then(([profile, status]) => {
        if (!active) return
        setMe(profile)
        setHealth(status)
      })
      .catch(() => {
        clearSession()
        router.replace('/')
      })
      .finally(() => active && setChecked(true))
    return () => {
      active = false
    }
  }, [router])

  if (!checked) return <div className="skeleton">{t('common.loading')}</div>

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">
          {t('app.name')}
          <small>{t('app.tagline')}</small>
        </div>
        <nav aria-label="Navigation principale">
          {LINKS.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              aria-current={pathname.startsWith(link.href) ? 'page' : undefined}
            >
              {t(link.key)}
            </Link>
          ))}
        </nav>
        <div style={{ marginTop: 24, fontSize: 12 }} className="stack muted">
          {me && (
            <>
              <strong style={{ color: 'var(--ink)' }}>{me.organization_name}</strong>
              <span>{me.full_name}</span>
              <span className="badge">{me.role_label}</span>
            </>
          )}
          {health && (
            <span className="badge" title="Le calcul ne dépend d'aucun service d'IA.">
              IA {health.ai_enabled ? 'activée' : 'désactivée'}
            </span>
          )}
          <button
            style={{ marginTop: 8 }}
            onClick={() => {
              clearSession()
              router.replace('/')
            }}
          >
            {t('nav.signOut')}
          </button>
        </div>
      </aside>
      <main>
        <div className="notice warning" role="note">
          {t('app.demoBanner')}
        </div>
        {children}
      </main>
    </div>
  )
}
