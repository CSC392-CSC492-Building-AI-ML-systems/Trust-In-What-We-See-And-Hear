# Development image for the frontend (next dev). Built by the repository's compose.yaml; see the
# root README ("Local development").
#
# AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.

FROM node:24-slim

ENV NEXT_TELEMETRY_DISABLED=1

WORKDIR /app

# Dependencies first, so editing source does not reinstall them.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 3000
CMD ["npm", "run", "dev", "--", "--hostname", "0.0.0.0", "--port", "3000"]
