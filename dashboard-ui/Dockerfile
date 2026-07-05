FROM node:22-alpine AS dependencies
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install

FROM dependencies AS builder
COPY . .
ARG API_ORIGIN=http://api:8000
ARG NEXT_PUBLIC_API_ORIGIN=http://localhost:8000
ARG NEXT_PUBLIC_WEBSOCKET_TOKEN=
ENV API_ORIGIN=$API_ORIGIN
ENV NEXT_PUBLIC_API_ORIGIN=$NEXT_PUBLIC_API_ORIGIN
ENV NEXT_PUBLIC_WEBSOCKET_TOKEN=$NEXT_PUBLIC_WEBSOCKET_TOKEN
RUN npm run build

FROM node:22-alpine AS runner
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0
WORKDIR /app
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
