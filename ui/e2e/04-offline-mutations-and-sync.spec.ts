import { test, expect } from "@playwright/test";
import {
  captureScreenshot,
  queryDbSync,
  setSessionAuth,
  DEV_JWT_TOKEN,
} from "./helpers";

test.describe("Scenario 4 & 5: Offline Mutations, Queue Persistence & Online Sync", () => {
  const testUser = "test:offline_sync_user";

  test("queue mutations while offline, verify visibility across reload, restore network, and verify PostgreSQL sync without duplicates", async ({
    page,
  }) => {
    await setSessionAuth(page, testUser);

    // 1. Initial online load
    await page.goto("/projects", { waitUntil: "networkidle" });
    await expect(page.locator("h1")).toContainText("Projects");

    // 2. Disable network access to the API (simulate offline)
    console.log("Simulating offline network...");
    await page.route("**/api/v1/**", (route) => route.abort("failed"));

    // 3. Create Project while offline
    const offlineProjectName = `Offline_Proj_${Date.now()}`;
    await page.getByLabel("Project name").fill(offlineProjectName);
    await page.getByRole("button", { name: "Create" }).click();

    // Verify project appears in UI with 'Queued' badge
    const projectItem = page.locator("nav[aria-label='Projects'] button", {
      hasText: offlineProjectName,
    });
    await expect(projectItem).toBeVisible({ timeout: 10000 });
    await expect(projectItem.locator("span", { hasText: "Queued" })).toBeVisible({
      timeout: 5000,
    });
    await captureScreenshot(page, "scenario4_offline_project_queued");

    // Verify it is NOT yet in PostgreSQL
    const dbBeforeSync = queryDbSync<any[]>(
      `SELECT id FROM projects WHERE name = '${offlineProjectName}'`,
    );
    expect(dbBeforeSync).toHaveLength(0);

    // 4. Create Task associated with offline project while offline
    const offlineTaskTitle = `Offline_Task_${Date.now()}`;
    await page.getByLabel("Task title").fill(offlineTaskTitle);
    await page.getByRole("button", { name: "Add task" }).click();

    // Verify task appears in UI with 'Queued' badge
    const taskItem = page.locator("li", { hasText: offlineTaskTitle });
    await expect(taskItem).toBeVisible({ timeout: 5000 });
    await expect(taskItem.locator("span", { hasText: "Queued" })).toBeVisible({
      timeout: 5000,
    });
    await captureScreenshot(page, "scenario4_offline_task_queued");

    // 5. Update/Complete task while offline
    const completeBtn = taskItem.getByRole("button", {
      name: new RegExp(`Complete ${offlineTaskTitle}`, "i"),
    });
    await completeBtn.click();
    await expect(taskItem.locator("span.line-through")).toBeVisible({ timeout: 5000 });
    await captureScreenshot(page, "scenario4_offline_task_toggled");

    // 6. Reload page while STILL offline
    console.log("Reloading page while offline...");
    await page.reload({ waitUntil: "networkidle" });

    // Verify project and task remain visible from local IndexedDB queue
    const projectAfterReload = page.locator("nav[aria-label='Projects'] button", {
      hasText: offlineProjectName,
    });
    await expect(projectAfterReload).toBeVisible({ timeout: 10000 });
    await expect(projectAfterReload.locator("span", { hasText: "Queued" })).toBeVisible({
      timeout: 5000,
    });

    // Select the project to view its tasks
    await projectAfterReload.click();
    const taskAfterReload = page.locator("li", { hasText: offlineTaskTitle });
    await expect(taskAfterReload).toBeVisible({ timeout: 10000 });
    await expect(taskAfterReload.locator("span", { hasText: "Queued" })).toBeVisible({
      timeout: 5000,
    });
    await expect(taskAfterReload.locator("span.line-through")).toBeVisible({ timeout: 5000 });
    await captureScreenshot(page, "scenario4_reloaded_while_offline");

    // 7. Restore network access
    console.log("Restoring network access...");
    await page.unroute("**/api/v1/**");

    // Wait for sync coordinator to be initialized and trigger sync
    await page.waitForFunction(() => Boolean((window as any).__posCoordinator), { timeout: 10000 });
    const syncResult = await page.evaluate(async () => {
      const coordinator = (window as any).__posCoordinator;
      if (coordinator) {
        try {
          await coordinator.syncNow();
          return "SYNC_SUCCESS";
        } catch (e: any) {
          return `SYNC_ERROR: ${e?.message || e}`;
        }
      } else {
        window.dispatchEvent(new Event("online"));
        return "DISPATCHED_ONLINE";
      }
    });
    console.log("Sync trigger result:", syncResult);

    // Wait for the 'Queued' badge to clear (meaning sync succeeded and status is now synced)
    await expect(projectAfterReload.locator("span", { hasText: "Queued" })).not.toBeVisible({
      timeout: 15000,
    });
    await captureScreenshot(page, "scenario5_online_synced");

    // 8. Verify persistence in PostgreSQL without duplication
    const dbProjects = queryDbSync<any[]>(
      `SELECT id, name, status, revision FROM projects WHERE name = '${offlineProjectName}'`,
    );
    expect(dbProjects).toHaveLength(1);
    expect(dbProjects[0].name).toBe(offlineProjectName);
    const serverProjectId = dbProjects[0].id;

    const dbTasks = queryDbSync<any[]>(
      `SELECT id, title, status, project_id, revision FROM tasks WHERE title = '${offlineTaskTitle}'`,
    );
    expect(dbTasks).toHaveLength(1); // Exactly 1, no duplication!
    expect(dbTasks[0].title).toBe(offlineTaskTitle);
    expect(dbTasks[0].status).toBe("done"); // Applied the toggle update!
    expect(dbTasks[0].project_id).toBe(serverProjectId);
  });
});
