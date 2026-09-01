'use client'

import { useCallback, useEffect, useState } from 'react'

import { api, ApiError, type PublicQuoteView, type PublicReceipt } from '@/lib/api'

/**
 * La page que le destinataire d'un devis ouvre, sans compte Metreo.
 *
 * **Ce qui arrive au secret dans les cent premières millisecondes.** Il est lu
 * dans le fragment de l'URL — que le navigateur n'envoie jamais au serveur —
 * puis RETIRÉ de la barre d'adresse par `history.replaceState`, puis échangé
 * par `POST` contre une session courte en cookie `HttpOnly`. Il n'est écrit ni
 * dans `localStorage`, ni dans `sessionStorage`, ni nulle part ailleurs : la
 * variable qui le portait sort de portée avec la fonction.
 *
 * Hors du `Shell` volontairement : cette page n'a pas de session Metreo, pas
 * de menu, pas de bouton de déconnexion. Elle ne montre qu'un devis.
 */
export default function PublicQuotePage() {
  const [devis, setDevis] = useState<PublicQuoteView | null>(null)
  const [recu, setRecu] = useState<PublicReceipt | null>(null)
  const [erreur, setErreur] = useState<string | null>(null)
  const [pret, setPret] = useState(false)
  const [occupe, setOccupe] = useState(false)
  const [decision, setDecision] = useState<'accepted' | 'declined' | null>(null)
  const [nom, setNom] = useState('')
  const [courriel, setCourriel] = useState('')
  const [motif, setMotif] = useState('')

  const charger = useCallback(async () => {
    try {
      setDevis(await api.publicQuote())
      setErreur(null)
    } catch (attrape) {
      setDevis(null)
      setErreur(
        attrape instanceof ApiError
          ? attrape.message
          : "Ce lien n'a pas pu être ouvert.",
      )
    } finally {
      setPret(true)
    }
  }, [])

  useEffect(() => {
    let vivant = true
    async function ouvrir() {
      const fragment = window.location.hash.replace(/^#/, '')
      if (fragment) {
        // Retiré AVANT le premier appel réseau : si l'échange échoue, le
        // secret ne doit pas rester dans la barre d'adresse ni dans
        // l'historique, où un partage d'écran ou un « précédent » le
        // ressortirait.
        window.history.replaceState(null, '', window.location.pathname)
        try {
          await api.publicOpenSession(fragment)
        } catch (attrape) {
          if (!vivant) return
          setErreur(
            attrape instanceof ApiError
              ? attrape.message
              : "Ce lien n'est plus valable.",
          )
          setPret(true)
          return
        }
      }
      if (vivant) await charger()
    }
    void ouvrir()
    return () => {
      vivant = false
    }
  }, [charger])

  async function envoyer() {
    if (!decision || !devis) return
    setOccupe(true)
    setErreur(null)
    try {
      setRecu(
        await api.publicRespond({
          decision,
          respondent_name: nom.trim() || null,
          respondent_email: courriel.trim() || null,
          comment: motif.trim() || null,
          confirmed: true,
        }),
      )
      await charger()
    } catch (attrape) {
      setErreur(attrape instanceof ApiError ? attrape.message : 'Envoi impossible.')
    } finally {
      setOccupe(false)
    }
  }

  async function telecharger() {
    setErreur(null)
    try {
      const reponse = await fetch(api.publicPdfUrl(), {
        credentials: 'include',
        cache: 'no-store',
      })
      if (!reponse.ok) throw new Error(String(reponse.status))
      const url = URL.createObjectURL(await reponse.blob())
      const lien = document.createElement('a')
      lien.href = url
      lien.download = `devis-${devis?.number.replace(/[^\w.-]+/g, '-') ?? 'document'}.pdf`
      lien.click()
      setTimeout(() => URL.revokeObjectURL(url), 60_000)
    } catch {
      setErreur("Le document n'a pas pu être téléchargé.")
    }
  }

  if (!pret) return <main className="public">Chargement…</main>

  if (!devis) {
    return (
      <main className="public">
        <div className="notice error" role="alert">
          {erreur ?? "Ce lien n'est plus valable."}
        </div>
        <p className="muted">
          Contactez l&apos;entreprise qui vous a transmis ce devis pour en obtenir un
          nouveau.
        </p>
      </main>
    )
  }

  const dateFr = (valeur: string) =>
    new Date(valeur).toLocaleDateString('fr-BE', {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
    })

  return (
    <main className="public" data-testid="devis-public">
      <header className="card">
        {/* L'émetteur, tel qu'il était le jour de l'émission. Tout vient de
            l'instantané du devis : l'entreprise peut déménager ou changer de
            logo demain, ce bloc ne bougera pas. */}
        <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
          {devis.has_logo && (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={api.publicLogoUrl()}
              alt=""
              data-testid="logo-public"
              style={{ maxHeight: 64, maxWidth: 160 }}
            />
          )}
          <div data-testid="emetteur-public">
            <h1 style={{ marginTop: 0, marginBottom: 4 }}>{devis.organization_name}</h1>
            <p className="muted" style={{ margin: 0 }}>
              {devis.organization_legal_name}
              {devis.organization_company_number && (
                <>
                  {' · '}
                  <span className="mono">N° {devis.organization_company_number}</span>
                </>
              )}
            </p>
            {devis.organization_address_lines.map((ligne, rang) => (
              <div key={rang} className="muted" style={{ fontSize: 13 }}>
                {ligne}
              </div>
            ))}
            {(devis.organization_phone || devis.organization_email) && (
              <div className="muted" style={{ fontSize: 13 }}>
                {[devis.organization_phone, devis.organization_email]
                  .filter(Boolean)
                  .join(' — ')}
              </div>
            )}
            {devis.organization_website && (
              <div className="muted" style={{ fontSize: 13 }}>
                {devis.organization_website}
              </div>
            )}
          </div>
        </div>
        <h2>
          Devis <span className="mono" data-testid="numero-public">{devis.number}</span>
        </h2>
        <p>
          Émis le {dateFr(devis.issued_at)} · Valable jusqu&apos;au{' '}
          <strong>{dateFr(devis.valid_until)}</strong>
        </p>
        <p>
          <span className={`badge ${devis.state.code === 'accepted' ? 'success' : ''}`}>
            {devis.state.label}
          </span>
        </p>
      </header>

      {erreur && (
        <div className="notice error" role="alert">
          {erreur}
        </div>
      )}

      <div className="card">
        <div className="row">
          <div className="field">
            <strong>Destinataire</strong>
            <div data-testid="destinataire-public">
              {devis.client_name}
              {devis.client_address_lines.map((ligne) => (
                <div key={ligne} className="muted">
                  {ligne}
                </div>
              ))}
            </div>
          </div>
          <div className="field">
            <strong>Chantier</strong>
            <div>
              <span className="mono">{devis.project_reference}</span> — {devis.project_name}
            </div>
          </div>
        </div>
      </div>

      <div className="card" style={{ padding: 0 }}>
        <table>
          <thead>
            <tr>
              <th>Poste</th>
              <th>Désignation</th>
              <th>Unité</th>
              <th className="num">Quantité</th>
              <th className="num">P.U. HT</th>
              <th className="num">Total HT</th>
            </tr>
          </thead>
          <tbody>
            {devis.lines.map((ligne, index) => (
              <tr key={`${ligne.position}-${index}`}>
                <td className="mono">{ligne.position}</td>
                <td>{ligne.designation}</td>
                <td>{ligne.unit}</td>
                <td className="num">{ligne.quantity}</td>
                <td className="num">{ligne.unit_price_ht}</td>
                <td className="num">{ligne.total_ht}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <table className="totals">
        <tbody>
          <tr>
            <td>Total HT</td>
            <td className="num">
              {devis.total_ht} {devis.currency}
            </td>
          </tr>
          {devis.taxes.map((taxe, index) => (
            <tr key={index}>
              <td>{taxe.label}</td>
              <td className="num">
                {taxe.amount} {devis.currency}
              </td>
            </tr>
          ))}
          <tr className="grand">
            <td>Total TTC</td>
            <td className="num" data-testid="total-ttc-public">
              {devis.total_ttc} {devis.currency}
            </td>
          </tr>
        </tbody>
      </table>

      {devis.terms && (
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Conditions</h3>
          <p style={{ whiteSpace: 'pre-wrap' }}>{devis.terms}</p>
        </div>
      )}

      <div className="card">
        <div className="toolbar">
          <button className="primary" data-testid="telecharger-public" onClick={() => void telecharger()}>
            Télécharger le PDF
          </button>
        </div>
        <p className="muted mono" data-testid="empreinte-publique">
          Empreinte SHA-256 : {devis.pdf_sha256}
        </p>
      </div>

      {recu ? (
        <div className="card notice success" role="status" data-testid="recu">
          <h3 style={{ marginTop: 0 }}>Réponse enregistrée</h3>
          <p>
            Devis <span className="mono">{recu.number}</span> —{' '}
            <strong data-testid="recu-decision">{recu.decision_label}</strong> le{' '}
            {dateFr(recu.decided_at)}
            {recu.respondent_name && <> par {recu.respondent_name}</>}.
          </p>
          <p className="mono" style={{ fontSize: 12 }}>
            Empreinte du document : {recu.pdf_sha256}
          </p>
        </div>
      ) : devis.can_respond ? (
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Votre réponse</h3>
          <div className="toolbar">
            <button
              className={decision === 'accepted' ? 'primary' : undefined}
              data-testid="choisir-accepter"
              onClick={() => setDecision('accepted')}
            >
              Accepter
            </button>
            <button
              className={decision === 'declined' ? 'primary' : undefined}
              data-testid="choisir-refuser"
              onClick={() => setDecision('declined')}
            >
              Refuser
            </button>
          </div>

          {decision && (
            <>
              {/* La confirmation nomme le devis ET son montant : on ne clique
                  pas « Accepter » sans voir ce qu'on accepte. */}
              <div className="notice warning" role="alert" data-testid="confirmation">
                Je confirme répondre au devis {devis.number}, d&apos;un montant de{' '}
                {devis.total_ttc} {devis.currency}.
              </div>
              <div className="row">
                <div className="field">
                  <label htmlFor="repondant">
                    Votre nom{decision === 'accepted' ? ' (obligatoire)' : ''}
                  </label>
                  <input
                    id="repondant"
                    value={nom}
                    onChange={(evenement) => setNom(evenement.target.value)}
                  />
                </div>
                <div className="field">
                  <label htmlFor="courriel">Votre courriel</label>
                  <input
                    id="courriel"
                    value={courriel}
                    onChange={(evenement) => setCourriel(evenement.target.value)}
                  />
                </div>
              </div>
              <div className="field">
                <label htmlFor="motif">
                  {decision === 'declined' ? 'Motif (facultatif)' : 'Commentaire (facultatif)'}
                </label>
                <textarea
                  id="motif"
                  rows={3}
                  value={motif}
                  onChange={(evenement) => setMotif(evenement.target.value)}
                />
              </div>
              <p>
                <button
                  className="primary"
                  data-testid="confirmer-reponse"
                  onClick={() => void envoyer()}
                  disabled={occupe || (decision === 'accepted' && !nom.trim())}
                >
                  Confirmer ma réponse
                </button>{' '}
                <button onClick={() => setDecision(null)}>Annuler</button>
              </p>
            </>
          )}
        </div>
      ) : (
        <div className="card notice warning" role="status" data-testid="reponse-impossible">
          {devis.cannot_respond_reason}
        </div>
      )}

      {/* Dit une fois, clairement, et sans détour : ce n'est pas une signature
          électronique qualifiée, et aucune identité n'est vérifiée. */}
      <p className="muted" style={{ fontSize: 12 }} data-testid="mention-identite">
        Votre réponse est une réponse commerciale. Les nom et courriel que vous
        indiquez sont déclaratifs et ne sont pas vérifiés : il ne s&apos;agit pas
        d&apos;une signature électronique qualifiée.
      </p>
    </main>
  )
}
