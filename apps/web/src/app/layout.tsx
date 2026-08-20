import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'Metreo — étude de prix et devis BTP',
  description:
    "Outil d'aide à la décision pour l'étude de prix, le métré et les devis dans le BTP.",
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fr">
      <body>{children}</body>
    </html>
  )
}
