# Playwright runner for the browser smoke tests. Browsers come from the official image, whose tag
# matches the @playwright/test version in pnpm-lock.yaml; only the dev dependencies are installed
# here. The specs and the config are mounted at run time.
FROM mcr.microsoft.com/playwright:v1.62.1-noble

ENV HUSKY=0 \
    COREPACK_ENABLE_DOWNLOAD_PROMPT=0

RUN corepack enable

WORKDIR /app

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN --mount=type=cache,target=/root/.local/share/pnpm/store pnpm install --frozen-lockfile

CMD ["node_modules/.bin/playwright", "test"]
