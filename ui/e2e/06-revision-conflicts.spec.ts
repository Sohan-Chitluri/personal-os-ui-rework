import { test, expect } from "@playwright/test";
import {
  captureScreenshot,
  getQueueEntriesFromBrowser,
  queryDbSync,
  setSessionAuth,
  DEV_JWT_TOKEN,
} from "./helpers";

test.describe("Scenario 7: Revision Conflicts & Local Change Preservation", () => {
  const testUser = DEV_JWT_TOKEN;

  test("trigger revision conflict, verify local changes preserved, verify Conflict badge and TaskConflictBanner", async ({
    page,
  }) => {
    await setSessionAuth(page, testUser);

    // 1. Load projects page
    await page.goto("/projects", { waitUntil: "networkidle" });
    await expect(page.locator("h1")).toContainText("Projects");

    // 2. Create a project and sync it
    const projectName = `Conf_Proj_${Date.now()}`;
    await page.getByLabel("Project name").fill(projectName);
    await page.getByRole("button", { name: "Create" }).click();

    const projectItem = page.locator("nav[aria-label='Projects'] button", {
      hasText: projectName,
    });
    await expect(projectItem).toBeVisible({ timeout: 10000 });
    await page.evaluate(async () => {
      const coordinator = (window as any).__posCoordinator;
      if (coordinator) await coordinator.syncNow().catch(() => {});
    });
    await expect(projectItem.locator("span", { hasText: "Queued" })).not.toBeVisible({
      timeout: 10000,
    });

    // 3. Create a task under this project and sync it
    const taskTitle = `Conf_Task_${Date.now()}`;
    await page.getByLabel("Task title").fill(taskTitle);
    await page.getByRole("button", { name: "Add task" }).click();

    const taskItem = page.locator("li", { hasText: taskTitle });
    await expect(taskItem).toBeVisible({ timeout: 5000 });
    await page.evaluate(async () => {
      const coordinator = (window as any).__posCoordinator;
      if (coordinator) await coordinator.syncNow().catch(() => {});
    });
    await expect(taskItem.locator("span", { hasText: "Queued" })).not.toBeVisible({
      timeout: 10000,
    });

    // 4. Verify task exists in PostgreSQL with revision 1
    const dbTasks = queryDbSync<any[]>(
      `SELECT id, title, revision, status FROM tasks WHERE title = '${taskTitle}'`,
    );
    expect(dbTasks).toHaveLength(1);
    const taskId = dbTasks[0].id;
    expect(dbTasks[0].revision).toBe(1);
    console.log(`Task created in DB: id=${taskId}, revision=${dbTasks[0].revision}`);

    // 5. Concurrently modify the task on the server to bump its revision to 2
    queryDbSync(
      `UPDATE tasks SET revision = 2, title = '${taskTitle} (Remote Edit)' WHERE id = '${taskId}'`,
    );
    console.log("Bumped task revision to 2 in PostgreSQL");

    // 6. Mutate the task locally in UI (toggle completion)
    const completeBtn = taskItem.getByRole("button", {
      name: new RegExp(`Complete ${taskTitle}`, "i"),
    });
    await completeBtn.click();

    // Verify local optimistic state: task shows line-through
    await expect(taskItem.locator("span.line-through")).toBeVisible({ timeout: 5000 });
    console.log("Local modification applied optimistically in UI");
    await captureScreenshot(page, "scenario7_local_modification_applied");

    // 7. Flush sync to send the update to FastAPI backend
    await page.evaluate(async () => {
      const coordinator = (window as any).__posCoordinator;
      if (coordinator) {
        try {
          await coordinator.syncNow();
        } catch {}
      }
    });
    await page.waitForTimeout(1000);

    // 8. Verify the conflict detection in UI
    // The task item should now display the red 'Conflict' badge
    const conflictBadge = taskItem.locator("span", { hasText: "Conflict" });
    await expect(conflictBadge).toBeVisible({ timeout: 10000 });

    // The TaskConflictBanner should appear with 409 indication
    const conflictBanner = page.locator("aside[aria-label='Task conflict alert']");
    await expect(conflictBanner).toBeVisible({ timeout: 10000 });
    await expect(conflictBanner).toContainText("Conflict 409");
    await expect(conflictBanner).toContainText("Revision conflict");
    console.log("Conflict alert banner and Conflict badge are visible in UI!");

    // Verify local changes are PRESERVED: task remains completed in the local UI
    await expect(taskItem.locator("span.line-through")).toBeVisible();
    await captureScreenshot(page, "scenario7_conflict_displayed_and_preserved");

    // 9. Inspect IndexedDB queue to verify conflict entry
    const queueEntries = await getQueueEntriesFromBrowser(page);
    const conflictedEntry = queueEntries.find(
      (e) => e.operation?.entity_id === taskId && e.operation?.operation_type === "update",
    );
    expect(conflictedEntry).toBeDefined();
    expect(conflictedEntry?.status).toBe("conflicted");
    console.log("IndexedDB entry is in conflicted status:", conflictedEntry.status, "conflict_id:", conflictedEntry.acknowledgement?.conflict_id);

    // 10. Verify backend created a durable conflict in PostgreSQL
    const dbConflicts = queryDbSync<any[]>(
      `SELECT conflict_id, entity_id, status FROM sync_conflicts WHERE entity_id = '${taskId}'`,
    );
    expect(dbConflicts).toHaveLength(1);
    expect(dbConflicts[0].status).toBe("unresolved");
    console.log("PostgreSQL durable conflict record verified in sync_conflicts table!");

    // 11. Test the UI resolution action: click 'Refresh' button on the banner
    const refreshBtn = conflictBanner.getByRole("button", { name: /Refresh/i });
    await expect(refreshBtn).toBeVisible();
    await refreshBtn.click();

    // Document UI usability:
    // The refresh button invokes refetchTasks, pulling latest server state.
    await page.waitForTimeout(1000);
    await captureScreenshot(page, "scenario7_after_conflict_refresh");
  });
});
