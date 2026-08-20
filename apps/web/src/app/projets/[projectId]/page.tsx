'use client'

import Link from 'next/link'
import { useParams } from 'next/navigation'
import { useCallback, useEffect, useState } from 'react'

import { ErrorNotice, Loading } from '@/components/Feedback'
import { Shell } from '@/components/Shell'
import { api, type Boq, type BoqItem, type Estimate, type EstimateVersion, type Project } from '@/lib/api'
import { t } from '@/lib/i18n'

type VersionsByEstimate = Record<string, EstimateVersion[]>

export default function ProjectPage() {
  const params = useParams<{ projectId: string }>()
  const projectId = params.projectId

  const [project, setProject] = useState<Project | null>(null)
  const [boqs, setBoqs] = useState<Boq[]>([])
  const [items, setItems] = useState<BoqItem[]>([])
  const [estimates, setEstimates] = useState<Estimate[]>([])
  const [versions, setVersions] = useState<VersionsByEstimate>({})
  const [error, setError] = useState<unknown>(null)
  const [ready, setReady] = useState(false)
  const [newItem, setNewItem] = useState({
    position: '',
    designation: '',
    unit_code: 'm3',
    quantity: '0',
    kind: 'item',
  })

  const load = useCallback(async () => {
    try {
      const [loadedProject, loadedBoqs, loadedEstimates] = await Promise.all([
        api.project(projectId),
        api.boqs(projectId),
        api.estimates(projectId),
      ])
      setProject(loadedProject)
      setBoqs(loadedBoqs)
      setEstimates(loadedEstimates)
      const firstBoq = loadedBoqs[0]
      setItems(firstBoq ? await api.boqItems(firstBoq.id) : [])
      const versionEntries = await Promise.all(
        loadedEstimates.map(async (estimate) => [estimate.id, await api.estimateVersions(estimate.id)] as const),
      )
      setVersions(Object.fromEntries(versionEntries))
    } catch (caught) {
      setError(caught)
    } finally {
      setReady(true)
    }
  }, [projectId])

  useEffect(() => {
    void load()
  }, [load])

  async function createBoq() {
    setError(null)
    try {
      await api.createBoq(projectId, { name: 'Métré interne', source: 'manual' })
      await load()
    } catch (caught) {
      setError(caught)
    }
  }

  async function addItem(event: React.FormEvent) {
    event.preventDefault()
    const boq = boqs[0]
    if (!boq) return
    setError(null)
    try {
      await api.createBoqItem(boq.id, newItem)
      setNewItem({ position: '', designation: '', unit_code: 'm3', quantity: '0', kind: 'item' })
      setItems(await api.boqItems(boq.id))
    } catch (caught) {
      setError(caught)
    }
  }

  if (!ready) {
    return (
      <Shell>
        <Loading />
      </Shell>
    )
  }

  return (
    <Shell>
      <h1>
        <span className="mono">{project?.reference}</span> — {project?.name}
      </h1>
      <p className="muted">
        {project?.client_name} · {project?.city} ·{' '}
        <span className="badge">
          {project?.country_code} / {project?.region_code}
        </span>
      </p>
      <ErrorNotice error={error} />

      <h2>{t('boq.title')}</h2>
      {boqs.length === 0 ? (
        <div className="card">
          <p className="muted">{t('boq.empty')}</p>
          <button className="primary" onClick={createBoq}>
            {t('common.create')}
          </button>
        </div>
      ) : (
        <>
          <div className="card" style={{ padding: 0 }}>
            <table>
              <thead>
                <tr>
                  <th>{t('boq.position')}</th>
                  <th>{t('boq.designation')}</th>
                  <th>{t('common.unit')}</th>
                  <th className="num">{t('common.quantity')}</th>
                  <th>{t('common.status')}</th>
                  <th>Prix</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.id} className={item.kind === 'section' ? 'section' : undefined}>
                    <td className="mono">{item.position}</td>
                    <td>
                      {item.designation}
                      {item.formula && (
                        <div className="muted mono" style={{ fontSize: 11 }}>
                          {item.formula}
                        </div>
                      )}
                    </td>
                    <td>{item.kind === 'section' ? '' : item.unit_code}</td>
                    <td className="num">{item.kind === 'section' ? '' : item.quantity}</td>
                    <td>
                      <span className={`badge ${item.status === 'approved' ? 'success' : ''}`}>
                        {item.status}
                      </span>
                    </td>
                    <td>
                      {item.kind === 'section' ? (
                        ''
                      ) : item.composite_price_id ? (
                        <span className="badge">sous-détail</span>
                      ) : item.price_item_id ? (
                        <span className="badge">bibliothèque</span>
                      ) : (
                        <span className="badge danger">{t('estimate.missingPrice')}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <form className="card" onSubmit={addItem}>
            <h3 style={{ marginTop: 0 }}>{t('boq.addItem')}</h3>
            <div className="row">
              <div className="field">
                <label htmlFor="position">{t('boq.position')}</label>
                <input
                  id="position"
                  required
                  value={newItem.position}
                  onChange={(event) => setNewItem({ ...newItem, position: event.target.value })}
                />
              </div>
              <div className="field" style={{ flex: '3 1 300px' }}>
                <label htmlFor="designation">{t('boq.designation')}</label>
                <input
                  id="designation"
                  required
                  value={newItem.designation}
                  onChange={(event) => setNewItem({ ...newItem, designation: event.target.value })}
                />
              </div>
              <div className="field">
                <label htmlFor="unit">{t('common.unit')}</label>
                <input
                  id="unit"
                  required
                  value={newItem.unit_code}
                  onChange={(event) => setNewItem({ ...newItem, unit_code: event.target.value })}
                />
              </div>
              <div className="field">
                <label htmlFor="quantity">{t('common.quantity')}</label>
                <input
                  id="quantity"
                  inputMode="decimal"
                  value={newItem.quantity}
                  onChange={(event) => setNewItem({ ...newItem, quantity: event.target.value })}
                />
              </div>
              <div className="field">
                <label htmlFor="kind">Type</label>
                <select
                  id="kind"
                  value={newItem.kind}
                  onChange={(event) => setNewItem({ ...newItem, kind: event.target.value })}
                >
                  <option value="item">Poste</option>
                  <option value="section">Chapitre</option>
                  <option value="option">Option</option>
                  <option value="variant">Variante</option>
                  <option value="provisional">Quantité présumée</option>
                </select>
              </div>
            </div>
            <button className="primary" type="submit">
              {t('common.create')}
            </button>
          </form>
        </>
      )}

      <h2>{t('estimate.title')}</h2>
      {estimates.length === 0 ? (
        <div className="card muted">Aucune estimation pour ce projet.</div>
      ) : (
        <div className="card" style={{ padding: 0 }}>
          <table>
            <thead>
              <tr>
                <th>Estimation</th>
                <th>{t('estimate.version')}</th>
                <th>{t('common.status')}</th>
                <th className="num">{t('estimate.totalHT')}</th>
                <th>{t('common.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {estimates.flatMap((estimate) =>
                (versions[estimate.id] ?? []).map((version) => (
                  <tr key={version.id}>
                    <td>{estimate.name}</td>
                    <td>v{version.version_number}</td>
                    <td>
                      <span className={`badge ${version.status === 'frozen' ? 'success' : ''}`}>
                        {version.status === 'frozen' ? t('estimate.frozen') : t('estimate.draft')}
                      </span>
                    </td>
                    <td className="num">
                      {version.total_selling_price_ht_display ?? t('common.none')}
                    </td>
                    <td>
                      <Link href={`/estimations/${estimate.id}/${version.id}`}>Ouvrir</Link>
                    </td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        </div>
      )}
    </Shell>
  )
}
