import { test, expect } from "@playwright/test";
import {
  captureScreenshot,
  getQueueEntriesFromBrowser,
  queryDbSync,
  setSessionAuth,
  DEV_JWT_TOKEN,
} from "./helpers";

test.describe("Scenario 6: Dependency Chains, Interrupted Requests, Retries & Partial Acknowledgements", () => {
  const testUser = DEV_JWT_TOKEN;

  test("verify project-then-task depends_on chain, 503 retryable status, and partial ack recovery", async ({
    page,
  }) => {
    await setSessionAuth(page, testUser);

    // 1. Initial load
    await page.goto("/projects", { waitUntil: "networkidle" });
    await expect(page.locator("h1")).toContainText("Projects");

    // 2. Intercept sync operations to block upload while creating offline resources
    let blockSync = true;
    await page.route("**/api/v1/sync/operations", async (route) => {
      if (blockSync) {
        await route.abort("failed");
      } else {
        await route.continue();
      }
    });

    // 3. Create Project while sync is blocked
    const depProjectName = `Dep_Chain_Proj_${Date.now()}`;
    await page.getByLabel("Project name").fill(depProjectName);
    await page.getByRole("button", { name: "Create" }).click();

    const projectItem = page.locator("nav[aria-label='Projects'] button", {
      hasText: depProjectName,
    });
    await expect(projectItem).toBeVisible({ timeout: 10000 });
    await expect(projectItem.locator("span", { hasText: "Queued" })).toBeVisible({
      timeout: 5000,
    });

    // 4. Create Task linked to this offline project
    const depTaskTitle = `Dep_Chain_Task_${Date.now()}`;
    await page.getByLabel("Task title").fill(depTaskTitle);
    await page.getByRole("button", { name: "Add task" }).click();

    const taskItem = page.locator("li", { hasText: depTaskTitle });
    await expect(taskItem).toBeVisible({ timeout: 5000 });
    await expect(taskItem.locator("span", { hasText: "Queued" })).toBeVisible({
      timeout: 5000,
    });

    await captureScreenshot(page, "scenario6_dependency_chain_enqueued");

    // 5. Inspect IndexedDB to verify dependency chain (task depends_on contains project operation_id)
    const queueEntries = await getQueueEntriesFromBrowser(page);

    const projectEntry = queueEntries.find(
      (e) => e.operation?.entity_type === "project" && e.operation?.payload?.name === depProjectName,
    );
    const taskEntry = queueEntries.find(
      (e) => e.operation?.entity_type === "task" && e.operation?.payload?.title === depTaskTitle,
    );

    expect(projectEntry).toBeDefined();
    expect(taskEntry).toBeDefined();
    console.log("Project Op ID:", projectEntry.operation_id);
    console.log("Task Op ID:", taskEntry.operation_id);
    console.log("Task depends_on:", taskEntry.operation?.depends_on);

    // Verify task depends_on contains project operation_id
    expect(taskEntry.operation.depends_on).toContain(projectEntry.operation_id);

    // 6. Test interrupted / 503 response & retry behavior
    await page.unroute("**/api/v1/sync/operations");
    await page.route("**/api/v1/sync/operations", async (route) => {
      // Simulate server temporary failure
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "PostgreSQL temporary unavailable" }),
      });
    });

    // Trigger sync
    const evalResult = await page.evaluate(async () => {
      const coordinator = (window as any).__posCoordinator;
      if (!coordinator) return "NO_COORDINATOR";
      try {
        await coordinator.syncNow();
        return "SYNC_SUCCESS";
      } catch (err: any) {
        return `SYNC_ERROR: ${err?.message || err}`;
      }
    });
    console.log("503 Eval Result:", evalResult);
    await page.waitForTimeout(500);

    // Check IndexedDB queue: project transitioned to retryable, task is waiting on dependency
    const retryableEntries = await getQueueEntriesFromBrowser(page);

    const projectRetryEntry = retryableEntries.find((e) => e.operation_id === projectEntry.operation_id);
    const taskRetryEntry = retryableEntries.find((e) => e.operation_id === taskEntry.operation_id);

    expect(projectRetryEntry).toBeDefined();
    expect(projectRetryEntry.status).toBe("retryable");
    expect(projectRetryEntry.attempts).toBeGreaterThanOrEqual(1);
    expect(projectRetryEntry.error_category).toBe("transport");
    console.log("Verified project retryable status on 503:", projectRetryEntry.status, "attempts:", projectRetryEntry.attempts);

    expect(taskRetryEntry).toBeDefined();
    expect(taskRetryEntry.status).toBe("pending");
    expect(taskRetryEntry.error_category).toBe("dependency_pending");
    expect(taskRetryEntry.last_error).toContain("waiting for prerequisite operation");
    console.log("Verified task blocked by dependency barrier:", taskRetryEntry.last_error);

    await captureScreenshot(page, "scenario6_retryable_on_503");

    // 7. Test Partial Acknowledgement handling:
    // Unroute 503, and setup intercept to omit task acknowledgement when task is sent
    await page.unroute("**/api/v1/sync/operations");
    let taskOmitted = false;
    await page.route("**/api/v1/sync/operations", async (route) => {
      const postData = route.request().postDataJSON();
      console.log("Sync route received ops:", postData?.operations?.map((o: any) => ({ type: o.entity_type, id: o.operation_id })));
      const response = await route.fetch();
      const json = await response.json();
      console.log("Server raw acks:", json.acknowledgements?.map((a: any) => ({ id: a.operation_id, status: a.status })));
      const hasTask = json.acknowledgements?.some((a: any) => a.operation_id === taskEntry.operation_id);
      if (hasTask && !taskOmitted) {
        taskOmitted = true;
        const filteredAcks = json.acknowledgements.filter((a: any) => a.operation_id !== taskEntry.operation_id);
        console.log("Omitting task ack from response!");
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            protocol_version: 1,
            acknowledgements: filteredAcks,
          }),
        });
      } else {
        await route.fulfill({ response });
      }
    });

    // Wait for project retry backoff before attempting sync
    await page.waitForTimeout(2000);

    // Trigger sync
    await page.evaluate(async () => {
      const coordinator = (window as any).__posCoordinator;
      if (coordinator) {
        try {
          await coordinator.syncNow();
        } catch {}
      }
    });
    await page.waitForTimeout(500);

    // Verify task entry is marked retryable due to missing ack
    const afterPartialAcks = await getQueueEntriesFromBrowser(page);

    const partialTask = afterPartialAcks.find((e) => e.operation_id === taskEntry.operation_id);
    expect(partialTask).toBeDefined();
    expect(partialTask.status).toBe("retryable");
    expect(partialTask.last_error).toContain("server omitted an acknowledgement");
    console.log("Verified partial ack handling:", partialTask.last_error);

    // 8. Restore normal sync and verify complete convergence
    await page.unroute("**/api/v1/sync/operations");
    // Wait for the retry backoff period to pass (base backoff is ~1000ms)
    await page.waitForTimeout(2000);
    await page.evaluate(async () => {
      const coordinator = (window as any).__posCoordinator;
      if (coordinator) {
        try {
          await coordinator.syncNow();
        } catch {}
      }
    });

    // Wait for badges to clear
    await expect(projectItem.locator("span", { hasText: "Queued" })).not.toBeVisible({
      timeout: 15000,
    });
    await expect(taskItem.locator("span", { hasText: "Queued" })).not.toBeVisible({
      timeout: 15000,
    });

    await captureScreenshot(page, "scenario6_fully_converged");

    // 9. Verify in PostgreSQL database
    const dbProjects = queryDbSync<any[]>(
      `SELECT id, name FROM projects WHERE name = '${depProjectName}'`,
    );
    expect(dbProjects).toHaveLength(1);

    const dbTasks = queryDbSync<any[]>(
      `SELECT id, title, project_id FROM tasks WHERE title = '${depTaskTitle}'`,
    );
    expect(dbTasks).toHaveLength(1);
    expect(dbTasks[0].project_id).toBe(dbProjects[0].id);
    console.log("PostgreSQL verified: task correctly linked to project in live database!");
  });
});
