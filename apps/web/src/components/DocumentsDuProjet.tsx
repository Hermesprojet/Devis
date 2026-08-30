'use client'

import { Fragment, useCallback, useEffect, useState } from 'react'

import { ErrorNotice } from '@/components/Feedback'
import {
  api,
  type DocumentRevision,
  type DocumentSummary,
} from '@/lib/api'
import { PERMISSIONS, can } from '@/lib/permissions'
import { usePermissions } from '@/lib/usePermissions'

/**
 * Les pièces du chantier : CCTP, métré, plans, et ce qu'on en a fait.
 *
 * Avant, la page projet n'offrait aucun moyen de joindre quoi que ce soit :
 * le modèle documentaire existait en base depuis la phase 2A, sans un seul
 * écran pour l'atteindre. Un métreur qui recevait un cahier des charges le
 * laissait dans sa boîte mail.
 *
 * Rien n'est rendu à l'écran : un document se télécharge, il ne s'affiche pas.
 * Un PDF ouvert dans l'origine de l'application y exécuterait ses propres
 * scripts, avec la session de qui le consulte.
 */

/** Les catégories que le premier usage réclame, et rien de plus. */
const CATEGORIES = ['CCTP', 'Métré', 'Plan', 'Bordereau', 'Autre'] as const

const TYPES_LISIBLES: Record<string, string> = {
  'application/pdf': 'PDF',
  'image/png': 'PNG',
  'image/jpeg': 'JPEG',
  'text/csv': 'CSV',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'XLSX',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'DOCX',
}

/** Les extensions proposées au sélecteur — l'API reste seule à décider. */
const EXTENSIONS_SUGGEREES = '.pdf,.png,.jpg,.jpeg,.csv,.xlsx,.docx'

function taille(octets: number): string {
  if (octets < 1024) return `${octets} o`
  if (octets < 1024 * 1024) return `${(octets / 1024).toFixed(1)} Ko`
  return `${(octets / (1024 * 1024)).toFixed(1)} Mo`
}

function date(valeur: string): string {
  return new Date(valeur).toLocaleString('fr-BE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function DocumentsDuProjet({ projectId }: { projectId: string }) {
  const permissions = usePermissions()
  const ecrire = can(permissions, PERMISSIONS.documentWrite)

  const [documents, setDocuments] = useState<DocumentSummary[] | null>(null)
  const [revisions, setRevisions] = useState<Record<string, DocumentRevision[]>>({})
  const [ouverts, setOuverts] = useState<Record<string, boolean>>({})
  const [archives, setArchives] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [progression, setProgression] = useState<number | null>(null)
  const [titre, setTitre] = useState('')
  const [categorie, setCategorie] = useState<string>(CATEGORIES[0])
  const [aConfirmer, setAConfirmer] = useState<string | null>(null)

  const charger = useCallback(async () => {
    try {
      const liste = await api.documents(projectId, archives)
      setDocuments(liste)
      const paires = await Promise.all(
        liste.map(async (doc) => [doc.id, await api.documentRevisions(doc.id)] as const),
      )
      setRevisions(Object.fromEntries(paires))
    } catch (caught) {
      setError(caught)
      setDocuments([])
    }
  }, [projectId, archives])

  useEffect(() => {
    void charger()
  }, [charger])

  /**
   * Créer le document logique et y joindre le fichier, d'un seul geste.
   *
   * Un document sans fichier est une coquille vide que personne ne saurait
   * quoi faire ; l'écran ne permet donc pas d'en fabriquer.
   */
  async function deposer(fichier: File, documentId?: string) {
    setBusy(true)
    setError(null)
    setProgression(0)
    try {
      const cible =
        documentId ??
        (
          await api.createDocument(projectId, {
            title: `${categorie} — ${titre.trim() || fichier.name}`,
          })
        ).id
      await api.uploadRevision(cible, fichier, setProgression)
      setTitre('')
      await charger()
      if (!documentId) setOuverts((etat) => ({ ...etat, [cible]: true }))
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
      setProgression(null)
    }
  }

  async function telecharger(revision: DocumentRevision) {
    setError(null)
    try {
      const blob = await api.fetchExport(
        api.revisionContentUrl(revision.document_id, revision.id),
      )
      const url = URL.createObjectURL(blob)
      const lien = document.createElement('a')
      lien.href = url
      lien.download = revision.original_filename
      lien.click()
      setTimeout(() => URL.revokeObjectURL(url), 60_000)
    } catch (caught) {
      setError(caught)
    }
  }

  async function basculerArchive(doc: DocumentSummary) {
    setBusy(true)
    setError(null)
    try {
      await api.setDocumentStatus(doc.id, doc.status === 'archived' ? 'active' : 'archived')
      setAConfirmer(null)
      await charger()
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  if (documents === null) return <div className="card muted">Chargement des documents…</div>

  return (
    <div data-testid="documents">
      <h2>Documents</h2>
      <ErrorNotice error={error} />

      {ecrire && (
        <div className="card">
          <div className="row">
            <div className="field">
              <label htmlFor="doc-categorie">Catégorie</label>
              <select
                id="doc-categorie"
                value={categorie}
                onChange={(e) => setCategorie(e.target.value)}
              >
                {CATEGORIES.map((valeur) => (
                  <option key={valeur} value={valeur}>
                    {valeur}
                  </option>
                ))}
              </select>
            </div>
            <div className="field" style={{ flex: '3 1 320px' }}>
              <label htmlFor="doc-titre">Libellé (facultatif)</label>
              <input
                id="doc-titre"
                maxLength={200}
                placeholder="Lot 2 — terrassements"
                value={titre}
                onChange={(e) => setTitre(e.target.value)}
              />
            </div>
            <div className="field" style={{ flex: '2 1 260px' }}>
              {/*
                Un `<input type="file">` avec son `<label>` : c'est le seul
                sélecteur que le clavier et les lecteurs d'écran savent
                actionner. Une zone de dépôt seule les exclurait.
              */}
              <label htmlFor="doc-fichier">Fichier à joindre</label>
              <input
                id="doc-fichier"
                type="file"
                accept={EXTENSIONS_SUGGEREES}
                disabled={busy}
                onChange={(e) => {
                  const fichier = e.target.files?.[0]
                  e.target.value = ''
                  if (fichier) void deposer(fichier)
                }}
              />
            </div>
          </div>
          <p className="muted" style={{ fontSize: 12, margin: 0 }}>
            PDF, PNG, JPEG, CSV, XLSX ou DOCX. Le contenu est vérifié à la réception :
            l&apos;extension seule ne suffit pas.
          </p>
          {progression !== null && (
            <div className="notice info" role="status" style={{ marginTop: 12 }}>
              Envoi en cours — {progression} %
            </div>
          )}
        </div>
      )}

      {documents.length === 0 ? (
        <div className="card">
          <p className="muted">
            {archives
              ? 'Aucun document archivé.'
              : "Aucun document joint à ce projet. Le cahier des charges, le métré du client et les plans se déposent ici ; ils y restent liés au chantier plutôt qu'à une boîte mail."}
          </p>
        </div>
      ) : (
        <div className="card" style={{ padding: 0 }}>
          <table>
            <thead>
              <tr>
                <th>Document</th>
                <th>Fichier</th>
                <th>Type</th>
                <th className="num">Taille</th>
                <th>Déposé le</th>
                <th>Par</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {documents.map((doc) => {
                const liste = revisions[doc.id] ?? []
                const derniere = liste[0]
                const deplie = ouverts[doc.id] ?? false
                return (
                  <Fragment key={doc.id}>
                    <tr>
                      <td>
                        {doc.title}{' '}
                        {doc.status === 'archived' && <span className="badge">archivé</span>}
                      </td>
                      <td className="mono">{derniere?.original_filename ?? '—'}</td>
                      <td>{derniere ? (TYPES_LISIBLES[derniere.media_type] ?? '—') : '—'}</td>
                      <td className="num">{derniere ? taille(derniere.byte_size) : '—'}</td>
                      <td>{derniere ? date(derniere.created_at) : '—'}</td>
                      <td className="muted" style={{ fontSize: 12 }}>
                        {derniere?.author_email ?? '—'}
                      </td>
                      <td className="num" style={{ whiteSpace: 'nowrap' }}>
                        {derniere && (
                          <button type="button" onClick={() => void telecharger(derniere)}>
                            Télécharger
                          </button>
                        )}{' '}
                        <button
                          type="button"
                          aria-expanded={deplie}
                          onClick={() =>
                            setOuverts((etat) => ({ ...etat, [doc.id]: !deplie }))
                          }
                        >
                          {liste.length > 1 ? `Révisions (${liste.length})` : 'Révisions'}
                        </button>
                      </td>
                    </tr>
                    {deplie && (
                      <tr>
                        <td colSpan={7} style={{ background: 'rgba(0,0,0,.02)' }}>
                          <table>
                            <tbody>
                              {liste.map((revision) => (
                                <tr key={revision.id}>
                                  <td style={{ width: 90 }}>
                                    Révision {revision.revision_number}
                                  </td>
                                  <td className="mono">{revision.original_filename}</td>
                                  <td className="num">{taille(revision.byte_size)}</td>
                                  <td>{date(revision.created_at)}</td>
                                  <td
                                    className="mono muted"
                                    style={{ fontSize: 11 }}
                                    title={revision.sha256}
                                  >
                                    {revision.sha256.slice(0, 16)}…
                                  </td>
                                  <td className="num">
                                    <button
                                      type="button"
                                      onClick={() => void telecharger(revision)}
                                    >
                                      Télécharger
                                    </button>
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                          {ecrire && doc.status === 'active' && (
                            <div className="row" style={{ marginTop: 8, alignItems: 'flex-end' }}>
                              <div className="field" style={{ flex: '2 1 260px' }}>
                                <label htmlFor={`revision-${doc.id}`}>
                                  Joindre une nouvelle révision
                                </label>
                                <input
                                  id={`revision-${doc.id}`}
                                  type="file"
                                  accept={EXTENSIONS_SUGGEREES}
                                  disabled={busy}
                                  onChange={(e) => {
                                    const fichier = e.target.files?.[0]
                                    e.target.value = ''
                                    if (fichier) void deposer(fichier, doc.id)
                                  }}
                                />
                              </div>
                              <div className="field">
                                {aConfirmer === doc.id ? (
                                  <div className="notice warning" role="status">
                                    L&apos;archivage retire ce document des listes courantes ;
                                    ses révisions restent téléchargeables.{' '}
                                    <button
                                      type="button"
                                      className="danger"
                                      disabled={busy}
                                      onClick={() => void basculerArchive(doc)}
                                    >
                                      Confirmer l&apos;archivage
                                    </button>{' '}
                                    <button type="button" onClick={() => setAConfirmer(null)}>
                                      Annuler
                                    </button>
                                  </div>
                                ) : (
                                  <button type="button" onClick={() => setAConfirmer(doc.id)}>
                                    Archiver
                                  </button>
                                )}
                              </div>
                            </div>
                          )}
                          {ecrire && doc.status === 'archived' && (
                            <button
                              type="button"
                              style={{ marginTop: 8 }}
                              disabled={busy}
                              onClick={() => void basculerArchive(doc)}
                            >
                              Réactiver
                            </button>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      <button type="button" style={{ marginTop: 8 }} onClick={() => setArchives((v) => !v)}>
        {archives ? 'Revenir aux documents actifs' : 'Voir les documents archivés'}
      </button>
    </div>
  )
}
