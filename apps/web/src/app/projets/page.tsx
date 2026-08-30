'use client'

import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'

import { ErrorNotice, Loading } from '@/components/Feedback'
import { MiseEnRoute } from '@/components/MiseEnRoute'
import { Shell } from '@/components/Shell'
import { api, type Project } from '@/lib/api'
import { t } from '@/lib/i18n'

function formatDate(value: string | null): string {
  if (!value) return t('common.none')
  return new Date(value).toLocaleDateString('fr-BE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  })
}

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [search, setSearch] = useState('')
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState({ reference: '', name: '', client_name: '', city: '' })

  const reload = useCallback(async (query: string) => {
    try {
      const page = await api.projects(query ? `?q=${encodeURIComponent(query)}` : '')
      setProjects(page.items)
    } catch (caught) {
      setError(caught)
    }
  }, [])

  useEffect(() => {
    void reload('')
  }, [reload])

  async function create(event: React.FormEvent) {
    event.preventDefault()
    setError(null)
    try {
      await api.createProject(form)
      setCreating(false)
      setForm({ reference: '', name: '', client_name: '', city: '' })
      await reload(search)
    } catch (caught) {
      setError(caught)
    }
  }

  return (
    <Shell>
      <h1>{t('projects.title')}</h1>
      <ErrorNotice error={error} />

      <MiseEnRoute />

      <div className="toolbar">
        <input
          style={{ maxWidth: 320 }}
          placeholder={t('common.search')}
          value={search}
          onChange={(event) => {
            setSearch(event.target.value)
            void reload(event.target.value)
          }}
        />
        <div className="spacer" />
        <button className="primary" onClick={() => setCreating((open) => !open)}>
          {t('projects.new')}
        </button>
      </div>

      {creating && (
        <form className="card" onSubmit={create}>
          <div className="row">
            <div className="field">
              <label htmlFor="reference">{t('common.reference')}</label>
              <input
                id="reference"
                required
                value={form.reference}
                onChange={(event) => setForm({ ...form, reference: event.target.value })}
              />
            </div>
            <div className="field">
              <label htmlFor="name">{t('projects.name')}</label>
              <input
                id="name"
                required
                value={form.name}
                onChange={(event) => setForm({ ...form, name: event.target.value })}
              />
            </div>
            <div className="field">
              <label htmlFor="client">{t('projects.client')}</label>
              <input
                id="client"
                value={form.client_name}
                onChange={(event) => setForm({ ...form, client_name: event.target.value })}
              />
            </div>
            <div className="field">
              <label htmlFor="city">Localité</label>
              <input
                id="city"
                value={form.city}
                onChange={(event) => setForm({ ...form, city: event.target.value })}
              />
            </div>
          </div>
          <button className="primary" type="submit">
            {t('common.create')}
          </button>{' '}
          <button type="button" onClick={() => setCreating(false)}>
            {t('common.cancel')}
          </button>
        </form>
      )}

      {projects === null ? (
        <Loading />
      ) : projects.length === 0 ? (
        <div className="card muted">{t('projects.empty')}</div>
      ) : (
        <div className="card" style={{ padding: 0 }}>
          <table>
            <thead>
              <tr>
                <th>{t('common.reference')}</th>
                <th>{t('projects.name')}</th>
                <th>{t('projects.client')}</th>
                <th>{t('projects.region')}</th>
                <th>{t('projects.deadline')}</th>
                <th>{t('common.status')}</th>
              </tr>
            </thead>
            <tbody>
              {projects.map((project) => (
                <tr key={project.id}>
                  <td className="mono">
                    <Link href={`/projets/${project.id}`}>{project.reference}</Link>
                  </td>
                  <td>{project.name}</td>
                  <td>{project.client_name ?? t('common.none')}</td>
                  <td>
                    <span className="badge">
                      {project.country_code} / {project.region_code}
                    </span>
                  </td>
                  <td>{formatDate(project.submission_deadline)}</td>
                  <td>
                    <span className="badge">{project.status}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Shell>
  )
}
