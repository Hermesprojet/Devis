# Image du front, en trois étapes. La sortie `standalone` de Next produit un
# serveur autonome : l'étape finale n'a besoin ni de npm, ni des sources, ni
# des dépendances de développement.

FROM node:22-alpine AS deps
WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json ./
# `npm ci` sans repli : un verrou absent ou désynchronisé doit faire échouer
# la construction, jamais retomber silencieusement sur une résolution
# flottante qui installerait autre chose que ce que le dépôt décrit.
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY apps/web ./
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000

RUN apk add --no-cache wget

# `node` est fourni par l'image de base avec l'uid 1000. Le code appartient à
# root : le serveur ne peut pas réécrire ses propres fichiers.
COPY --from=build --chown=root:root /app/.next/standalone ./
COPY --from=build --chown=root:root /app/.next/static ./.next/static
COPY --from=build --chown=root:root /app/public ./public

USER node

EXPOSE 3000

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
  CMD wget -q --spider http://127.0.0.1:3000/ || exit 1

CMD ["node", "server.js"]
