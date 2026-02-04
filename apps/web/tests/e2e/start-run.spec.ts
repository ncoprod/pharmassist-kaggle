import { expect, test } from '@playwright/test'

test('start run completes (rerun with follow-up answers if needed)', async ({ page }) => {
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

  if (firstStatus === 'needs_more_info') {
    const answers = page.locator('[data-testid^="follow-up-answer-"]')
    const count = await answers.count()
    expect(count).toBeGreaterThan(0)

    for (let i = 0; i < count; i++) {
      const control = answers.nth(i)
      const tag = await control.evaluate((el) => el.tagName.toLowerCase())

      if (tag === 'select') {
        await control.selectOption('no')
        continue
      }

      if (tag === 'input') {
        const type = (await control.getAttribute('type')) ?? 'text'
        await control.fill(type === 'number' ? '7' : 'no')
        continue
      }

      await control.fill('n/a')
    }

    await page.getByTestId('follow-up-rerun').click()

    // New run_id for rerun-by-POST.
    await expect(runId).not.toHaveText(firstRunId)

    // Rerun should complete.
    await expect(runStatus).not.toHaveText('created')
    await expect(runStatus).toHaveText('completed')
  } else {
    await expect(runStatus).toHaveText('completed')
  }
})
