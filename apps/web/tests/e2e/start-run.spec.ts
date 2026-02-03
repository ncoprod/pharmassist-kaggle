import { expect, test } from '@playwright/test'

test('start run creates a run and completes', async ({ page }) => {
  await page.goto('/')

  await page.getByTestId('start-run').click()

  // No error banner should appear.
  await expect(page.locator('[data-testid="error-banner"]')).toHaveCount(0)

  const runId = page.getByTestId('run-id')
  await expect(runId).toBeVisible()
  await expect(runId).toHaveText(
    /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i,
  )

  // Stub pipeline emits a finalized event at the end.
  await expect(page.getByText('finalized')).toBeVisible()
})
