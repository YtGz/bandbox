FROM oven/bun:1 AS builder

WORKDIR /app

# Install dependencies
COPY package.json bun.lock ./
RUN bun install --frozen-lockfile

# Copy source and build
COPY . .
RUN bun run build

# ── Production image ──────────────────────────────────────

FROM oven/bun:1-slim

WORKDIR /app

# Copy built output and production deps
COPY --from=builder /app/build build/
COPY --from=builder /app/node_modules node_modules/
COPY --from=builder /app/package.json .

# Audio data volume mount point
RUN mkdir -p /data/audio

EXPOSE 3000

CMD ["bun", "run", "build/index.js"]
