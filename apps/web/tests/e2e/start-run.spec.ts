import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test } from '@playwright/test'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)

test('start run completes (default case)', async ({ page }) => {
  await page.goto('/')

  await page.getByTestId('start-run').click()

  // No error banner should appear.
  await expect(page.locator('[data-testid="error-banner"]')).toHaveCount(0)

  const runId = page.getByTestId('run-id')
  await expect(runId).toBeVisible()
  await expect(runId).toHaveText(
    /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i,
  )

  const runStatus = page.getByTestId('run-status')
  await expect(runStatus).toBeVisible()

  // UI refreshes run details after SSE "finalized".
  await expect(runStatus).not.toHaveText('created')

  const firstRunId = (await runId.textContent())?.trim() ?? ''
  const firstStatus = (await runStatus.textContent())?.trim() ?? ''

  // Default case should complete without blocking.
  expect(firstRunId).not.toEqual('')
  expect(firstStatus).toEqual('completed')
})

test('low-info case requires follow-up, rerun completes', async ({ page }) => {
  await page.goto('/')

  // Force a case that should require follow-up.
  await page.getByLabel('Case').fill('case_lowinfo_000102')

  await page.getByTestId('start-run').click()

  // No error banner should appear.
  await expect(page.locator('[data-testid="error-banner"]')).toHaveCount(0)

  const runId = page.getByTestId('run-id')
  await expect(runId).toBeVisible()

  const runStatus = page.getByTestId('run-status')
  await expect(runStatus).toBeVisible()

  // UI refreshes run details after SSE "finalized".
  await expect(runStatus).not.toHaveText('created')
  await expect(runStatus).toHaveText('needs_more_info')

  const firstRunId = (await runId.textContent())?.trim() ?? ''
  expect(firstRunId).not.toEqual('')

  const answers = page.locator('[data-testid^="follow-up-answer-"]')
  const count = await answers.count()
  expect(count).toBeGreaterThan(0)

  // Low-info funnel should include a domain selector + basic safety screens.
  await page.getByTestId('follow-up-answer-q_primary_domain').selectOption('digestive')
  await page.getByTestId('follow-up-answer-q_overall_severity').selectOption('mild')
  await page.getByTestId('follow-up-answer-q_fever').selectOption('no')
  await page.getByTestId('follow-up-answer-q_breathing').selectOption('no')
  await page.getByTestId('follow-up-answer-q_chest_pain').selectOption('no')

  await page.getByTestId('follow-up-rerun').click()

  // New run_id for rerun-by-POST.
  await expect(runId).not.toHaveText(firstRunId)

  // Rerun should complete.
  await expect(runStatus).not.toHaveText('created')
  await expect(runStatus).toHaveText('completed')
})

test('red-flag case escalates and does not recommend products', async ({ page }) => {
  await page.goto('/')

  await page.getByLabel('Case').fill('case_redflag_000101')
  await page.getByTestId('start-run').click()

  await expect(page.locator('[data-testid="error-banner"]')).toHaveCount(0)

  const runStatus = page.getByTestId('run-status')
  await expect(runStatus).toBeVisible()
  await expect(runStatus).not.toHaveText('created')
  await expect(runStatus).toHaveText('completed')

  // Escalation callout should be visible in the recommendation panel.
  await expect(page.getByText('Escalade recommandee')).toBeVisible()

  // Safety: no products should be recommended when escalation is recommended.
  await expect(page.locator('.productCard')).toHaveCount(0)
})

test('patients flow: search -> open -> start run from visit', async ({ page }) => {
  await page.goto('/')

  await page.getByTestId('tab-patients').click()

  await page.getByTestId('patient-search').fill('pt_000000')
  await page.getByTestId('patient-search-btn').click()

  await expect(page.getByTestId('patient-result-pt_000000')).toBeVisible()
  await page.getByTestId('patient-result-pt_000000').click()

  await expect(page.getByTestId('patient-detail-ref')).toHaveText('pt_000000')

  const startButtons = page.locator('[data-testid^="start-run-visit-"]')
  await expect(startButtons.first()).toBeVisible()
  await startButtons.first().click()

  const runStatus = page.getByTestId('run-status')
  await expect(runStatus).toBeVisible()
  await expect(runStatus).not.toHaveText('created')
  await expect(runStatus).toHaveText('completed')
})

test('db viewer loads redacted rows', async ({ page }) => {
  await page.goto('/')

  await page.getByTestId('tab-db').click()
  await page.getByTestId('db-table-select').selectOption('patients')
  await page.getByTestId('db-load-btn').click()

  await expect(page.getByTestId('db-preview-count')).toBeVisible()
  await expect(page.locator('[data-testid="db-row"]').first()).toBeVisible()
})

test('patients flow: upload prescription PDF and start run', async ({ page }) => {
  await page.goto('/')

  await page.getByTestId('tab-patients').click()
  await page.getByTestId('patient-search').fill('pt_000000')
  await page.getByTestId('patient-search-btn').click()
  await page.getByTestId('patient-result-pt_000000').click()

  const pdfPath = path.join(__dirname, '../fixtures/rx_phi_free.pdf')
  await page.getByTestId('patient-prescription-file').setInputFiles(pdfPath)
  await page.getByTestId('patient-prescription-upload-btn').click()

  await expect(page.locator('[data-testid="error-banner"]')).toHaveCount(0)
  await expect(page.getByTestId('patient-prescription-receipt')).toBeVisible()

  await page.getByTestId('patient-prescription-start-run').click()
  const runStatus = page.getByTestId('run-status')
  await expect(runStatus).toBeVisible()
  await expect(runStatus).toHaveText('completed')
})

test('patients flow: upload PHI-like PDF is blocked', async ({ page }) => {
  await page.goto('/')

  await page.getByTestId('tab-patients').click()
  await page.getByTestId('patient-search').fill('pt_000000')
  await page.getByTestId('patient-search-btn').click()
  await page.getByTestId('patient-result-pt_000000').click()

  const pdfPath = path.join(__dirname, '../fixtures/rx_phi_present.pdf')
  await page.getByTestId('patient-prescription-file').setInputFiles(pdfPath)
  await page.getByTestId('patient-prescription-upload-btn').click()

  await expect(page.getByTestId('error-banner')).toContainText('PHI detected')
})
