# Multi-stage: build the product UI, serve it from the caddy image (S3-T07,
# same pattern as the stage-2 crude build). deploy/Caddyfile is mounted in.
FROM node:20 AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM caddy:2
COPY --from=build /app/dist /srv
