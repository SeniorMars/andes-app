FROM node:22-bookworm-slim

WORKDIR /app/web

COPY web/package*.json ./
RUN npm ci

COPY web ./
RUN npm run build

ENV NODE_ENV=production

CMD ["npm", "run", "start"]
