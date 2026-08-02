# Multi-stage: build the crude UI, serve it from the caddy image (S2-T08).
# Compose builds this as the `caddy` service; deploy/Caddyfile is mounted in.
FROM node:20 AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM caddy:2
COPY --from=build /app/dist /srv
