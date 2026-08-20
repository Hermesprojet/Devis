"""Export artefacts: bill-of-quantities CSV and a printable quote.

The client-facing document never contains internal cost figures unless the
organisation explicitly turned that on; the internal report is a separate
export behind a separate permission.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from html import escape
from typing import Any

from metreo_domain.estimate import EstimateResult
from metreo_domain.money import RoundingPolicy

from ..models import Estimate, EstimateVersion, Organization, Project

CSV_DELIMITER = ";"

CLIENT_COLUMNS = [
    ("position", "Poste"),
    ("designation", "Désignation"),
    ("unit", "Unité"),
    ("quantity", "Quantité"),
    ("unit_price_ht", "P.U. HT"),
    ("selling_price_ht", "Total HT"),
]

INTERNAL_EXTRA_COLUMNS = [
    ("direct_cost", "Déboursé sec"),
    ("cost_price", "Prix de revient"),
    ("margin_amount", "Marge"),
]


def _line_rows(
    result: EstimateResult, rounding: RoundingPolicy, positions: dict[str, str]
) -> list[dict[str, Any]]:
    payload = result.to_dict(rounding)
    rows: list[dict[str, Any]] = []
    for line in payload["lines"]:
        price = line.get("price") or {}
        margin = ""
        for step in price.get("markup_steps", []) or []:
            if step["key"] == "margin":
                margin = step["amount"]
        rows.append(
            {
                "position": positions.get(line["line_id"], line["code"]),
                "code": line["code"],
                "designation": line["designation"],
                "kind": line["kind"],
                "unit": line["unit"],
                "quantity": line["quantity"],
                "unit_price_ht": price.get("unit_price_ht", ""),
                "selling_price_ht": price.get("selling_price_ht", ""),
                "direct_cost": price.get("direct_cost", ""),
                "cost_price": price.get("cost_price", ""),
                "margin_amount": margin,
                "missing_price": line["missing_price"],
                "included_in_total": line["included_in_total"],
            }
        )
    return rows


def estimate_to_csv(
    *,
    result: EstimateResult,
    rounding: RoundingPolicy,
    positions: dict[str, str],
    include_internal: bool,
    header: dict[str, str],
) -> str:
    """Serialise the priced bill of quantities.

    The first lines carry the identification header (reference, version, date,
    currency) required by acceptance scenario 8, so the file stands on its own
    once detached from the application.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=CSV_DELIMITER, lineterminator="\n")
    for key, value in header.items():
        writer.writerow([f"# {key}", value])
    writer.writerow([])

    columns = list(CLIENT_COLUMNS)
    if include_internal:
        columns += INTERNAL_EXTRA_COLUMNS
    writer.writerow([title for _, title in columns] + ["Statut"])

    for row in _line_rows(result, rounding, positions):
        status = ""
        if row["missing_price"]:
            status = "PRIX MANQUANT"
        elif not row["included_in_total"] and row["kind"] != "section":
            status = "HORS TOTAL (option/variante)"
        writer.writerow([row[key] for key, _ in columns] + [status])

    writer.writerow([])
    payload = result.to_dict(rounding)
    writer.writerow(["Total HT", payload["total_selling_price_ht"]])
    for tax in payload["taxes"]:
        writer.writerow([f"{tax['label']}", tax["amount"]])
    writer.writerow(["Total TTC", payload["total_ttc"]])
    if payload["options_total_ht"] != "0.00":
        writer.writerow(["Total options/variantes HT (hors total)", payload["options_total_ht"]])
    return buffer.getvalue()


_STYLE = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { font-family: "Inter", "Helvetica Neue", Arial, sans-serif; color: #14181f;
       margin: 0; padding: 32px; font-size: 13px; line-height: 1.45; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 14px; text-transform: uppercase; letter-spacing: .06em;
     color: #5b6472; margin: 28px 0 8px; }
.header { display: flex; justify-content: space-between; gap: 24px;
          border-bottom: 2px solid #14181f; padding-bottom: 16px; }
.meta { font-size: 12px; color: #5b6472; }
.meta b { color: #14181f; }
table { width: 100%; border-collapse: collapse; margin-top: 8px; }
th, td { padding: 6px 8px; border-bottom: 1px solid #e3e7ec; vertical-align: top; }
th { text-align: left; font-size: 11px; text-transform: uppercase;
     letter-spacing: .04em; color: #5b6472; background: #f6f8fa; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
tr.section td { background: #f6f8fa; font-weight: 600; }
tr.excluded td { color: #5b6472; font-style: italic; }
.totals { margin-top: 16px; margin-left: auto; width: 320px; }
.totals td { border: 0; padding: 4px 8px; }
.totals tr.grand td { border-top: 2px solid #14181f; font-weight: 700; font-size: 15px; }
.notice { margin-top: 28px; padding: 12px 14px; border-left: 3px solid #b45309;
          background: #fffbeb; font-size: 12px; }
.footer { margin-top: 32px; padding-top: 12px; border-top: 1px solid #e3e7ec;
          font-size: 11px; color: #5b6472; }
@media print { body { padding: 0; } .noprint { display: none; } }
"""


def quote_html(
    *,
    organization: Organization,
    project: Project,
    estimate: Estimate,
    version: EstimateVersion,
    result: EstimateResult,
    rounding: RoundingPolicy,
    positions: dict[str, str],
    include_internal: bool,
    demo_data_warning: bool,
) -> str:
    """Printable quote preview.

    HTML on purpose in this phase: it prints to PDF from any browser, needs no
    binary toolchain, and does not pretend to be the final typeset document that
    Phase 2 will produce.
    """
    payload = result.to_dict(rounding)
    rows = _line_rows(result, rounding, positions)
    generated = datetime.utcnow().strftime("%d/%m/%Y %H:%M UTC")
    status_label = {"draft": "Brouillon", "frozen": "Version gelée", "superseded": "Remplacée"}.get(
        version.status, version.status
    )

    body: list[str] = []
    body.append(
        f"""
    <div class="header">
      <div>
        <h1>{escape(organization.name)}</h1>
        <div class="meta">
          {escape(organization.legal_name or "")}<br>
          {escape("N° d'entreprise: " + organization.company_number if organization.company_number else "")}
        </div>
      </div>
      <div class="meta" style="text-align:right">
        <b>Devis {escape(project.reference)}</b><br>
        Version {version.version_number} — {escape(status_label)}<br>
        Établi le {generated}<br>
        Devise: {escape(estimate.currency)}
      </div>
    </div>
    <h2>Chantier</h2>
    <div class="meta">
      <b>{escape(project.name)}</b><br>
      {escape(project.address or "")} {escape(project.postal_code or "")} {escape(project.city or "")}<br>
      Client: {escape(project.client_name or "—")} — Référence client: {escape(project.client_reference or "—")}<br>
      Profil réglementaire: {escape(project.country_code)} / {escape(project.region_code)}
    </div>
    """
    )

    headers = "".join(
        f'<th class="{"num" if key in ("quantity", "unit_price_ht", "selling_price_ht") else ""}">{escape(title)}</th>'
        for key, title in CLIENT_COLUMNS
    )
    if include_internal:
        headers += "".join(f'<th class="num">{escape(t)}</th>' for _, t in INTERNAL_EXTRA_COLUMNS)

    line_html: list[str] = []
    for row in rows:
        classes = []
        if row["kind"] == "section":
            classes.append("section")
        elif not row["included_in_total"]:
            classes.append("excluded")
        cells = (
            f"<td>{escape(str(row['position']))}</td>"
            f"<td>{escape(row['designation'])}"
            + (
                '<br><span class="meta">Option / variante — hors total</span>'
                if row["kind"] in ("option", "variant")
                else ""
            )
            + ('<br><span class="meta">Poste non chiffré</span>' if row["missing_price"] else "")
            + "</td>"
            f"<td>{escape(row['unit']) if row['kind'] != 'section' else ''}</td>"
            f'<td class="num">{escape(row["quantity"]) if row["kind"] != "section" else ""}</td>'
            f'<td class="num">{escape(str(row["unit_price_ht"]))}</td>'
            f'<td class="num">{escape(str(row["selling_price_ht"]))}</td>'
        )
        if include_internal:
            cells += (
                f'<td class="num">{escape(str(row["direct_cost"]))}</td>'
                f'<td class="num">{escape(str(row["cost_price"]))}</td>'
                f'<td class="num">{escape(str(row["margin_amount"]))}</td>'
            )
        line_html.append(f'<tr class="{" ".join(classes)}">{cells}</tr>')

    body.append(
        f"""
    <h2>Bordereau</h2>
    <table>
      <thead><tr>{headers}</tr></thead>
      <tbody>{"".join(line_html)}</tbody>
    </table>
    """
    )

    tax_rows = "".join(
        f'<tr><td>{escape(tax["label"])}</td><td class="num">{escape(tax["amount"])} {escape(payload["currency"])}</td></tr>'
        for tax in payload["taxes"]
    )
    options_row = (
        f'<tr><td>Options / variantes (hors total)</td><td class="num">{escape(payload["options_total_ht"])} {escape(payload["currency"])}</td></tr>'
        if payload["options_total_ht"] not in ("0.00", "0")
        else ""
    )
    body.append(
        f"""
    <table class="totals">
      <tr><td>Total HT</td><td class="num">{escape(payload["total_selling_price_ht"])} {escape(payload["currency"])}</td></tr>
      {tax_rows}
      <tr class="grand"><td>Total TTC</td><td class="num">{escape(payload["total_ttc"])} {escape(payload["currency"])}</td></tr>
      {options_row}
    </table>
    """
    )

    notices: list[str] = []
    if payload["missing_price_line_ids"]:
        notices.append(
            f"{len(payload['missing_price_line_ids'])} poste(s) sans prix. "
            "Ce document n'est pas complet."
        )
    if version.status != "frozen":
        notices.append(
            "Aperçu d'une version brouillon: les montants peuvent encore changer. "
            "Seule une version gelée fait foi."
        )
    if demo_data_warning:
        notices.append(
            "Cette estimation utilise des prix de démonstration fictifs. "
            "Ils ne représentent aucun prix de marché."
        )
    if notices:
        body.append(
            '<div class="notice"><b>Avertissements</b><ul>'
            + "".join(f"<li>{escape(n)}</li>" for n in notices)
            + "</ul></div>"
        )

    body.append(
        f"""
    <div class="footer">
      Document généré par Metreo — outil d'aide à la décision. Les quantités,
      prix et hypothèses restent sous la responsabilité de l'entreprise émettrice.
      {("Empreinte de la version gelée: " + escape(version.snapshot_sha256[:16]) + "…") if version.snapshot_sha256 else ""}
    </div>
    """
    )

    return (
        '<!doctype html><html lang="fr"><head><meta charset="utf-8">'
        f"<title>Devis {escape(project.reference)} — version {version.version_number}</title>"
        f"<style>{_STYLE}</style></head><body>{''.join(body)}</body></html>"
    )
