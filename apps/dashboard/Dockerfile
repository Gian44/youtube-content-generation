FROM node:20-alpine AS builder

WORKDIR /app

# Copy root package files
COPY package.json ./
COPY apps/dashboard/package.json apps/dashboard/
COPY packages/shared/package.json packages/shared/

# Install dependencies
RUN npm install --workspace=apps/dashboard

# Copy source
COPY apps/dashboard/ apps/dashboard/
COPY packages/shared/ packages/shared/

# Build
WORKDIR /app/apps/dashboard
RUN npm run build

# Production image
FROM node:20-alpine AS runner

WORKDIR /app

ENV NODE_ENV=production

COPY --from=builder /app/apps/dashboard/.next/standalone ./
COPY --from=builder /app/apps/dashboard/.next/static ./apps/dashboard/.next/static
COPY --from=builder /app/apps/dashboard/public ./apps/dashboard/public

EXPOSE 3000

CMD ["node", "apps/dashboard/server.js"]
