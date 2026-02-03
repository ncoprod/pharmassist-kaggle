import { defineConfig } from '@playwright/test'

const webPort = process.env.WEB_PORT ?? '5174'
const baseURL = process.env.E2E_BASE_URL ?? `http://127.0.0.1:${webPort}`

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL,
    headless: true,
  },
})

