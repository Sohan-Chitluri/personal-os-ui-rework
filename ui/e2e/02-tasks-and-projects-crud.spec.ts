import { test, expect } from "@playwright/test";
import {
  captureScreenshot,
  queryDbSync,
  setSessionAuth,
} from "./helpers";

test.describe("Scenario 2 & 3: Task & Project CRUD Lifecycle through the UI", () => {
  const testUser = "test:acceptance_crud_user";

  test("create, edit, complete/reopen project and task through the UI and verify PostgreSQL persistence", async ({
    page,
  }) => {
    await setSessionAuth(page, testUser);

    // 1. Navigate to /projects
    await page.goto("/projects", { waitUntil: "networkidle" });
    await expect(page.locator("h1")).toContainText("Projects");

    // 2. Create Project
    const projectName = `Proj_${Date.now()}`;
    const projectInput = page.getByLabel("Project name");
    await projectInput.fill(projectName);
    await page.getByRole("button", { name: "Create" }).click();

    // Verify project appears in project list
    const projectItem = page.locator("nav[aria-label='Projects'] button", {
      hasText: projectName,
    });
    await expect(projectItem).toBeVisible({ timeout: 10000 });
    await captureScreenshot(page, "scenario3_project_created");

    // Verify in PostgreSQL
    const dbProjects = queryDbSync<any[]>(
      `SELECT id, name, status, revision FROM projects WHERE name = '${projectName}'`,
    );
    expect(dbProjects).toHaveLength(1);
    const projectId = dbProjects[0].id;
    expect(dbProjects[0].name).toBe(projectName);

    // 3. Rename Project
    const renamedName = `${projectName}_Renamed`;
    const renameInput = page.getByLabel("Rename project");
    await renameInput.fill(renamedName);
    await page.getByRole("button", { name: "Save" }).click();

    // Verify rename in UI
    await expect(page.locator("h2.uppercase", { hasText: renamedName })).toBeVisible({
      timeout: 5000,
    });
    await captureScreenshot(page, "scenario3_project_renamed");

    // Verify rename in PostgreSQL
    const dbRenamed = queryDbSync<any[]>(
      `SELECT name, revision FROM projects WHERE id = '${projectId}'`,
    );
    expect(dbRenamed[0].name).toBe(renamedName);

    // 4. Create Task associated with project
    const taskTitle = `Task_In_${projectName}`;
    const taskInput = page.getByLabel("Task title");
    await taskInput.fill(taskTitle);
    await page.getByRole("button", { name: "Add task" }).click();

    // Verify task in UI under project tasks
    const taskListItem = page.locator("li", { hasText: taskTitle });
    await expect(taskListItem).toBeVisible({ timeout: 5000 });
    await captureScreenshot(page, "scenario3_project_task_created");

    // Verify in PostgreSQL that task is linked to project
    const dbTasks = queryDbSync<any[]>(
      `SELECT id, title, status, project_id, revision FROM tasks WHERE title = '${taskTitle}'`,
    );
    expect(dbTasks).toHaveLength(1);
    expect(dbTasks[0].project_id).toBe(projectId);
    expect(dbTasks[0].status).toBe("open");
    const taskId = dbTasks[0].id;

    // 5. Complete task through UI
    const completeButton = page.getByRole("button", {
      name: new RegExp(`Complete ${taskTitle}`, "i"),
    });
    await completeButton.click();

    // Verify task completed in UI
    const taskTitleSpan = taskListItem.locator("span.line-through");
    await expect(taskTitleSpan).toBeVisible({ timeout: 5000 });
    await captureScreenshot(page, "scenario2_task_completed");

    // Verify in PostgreSQL
    const dbCompleted = queryDbSync<any[]>(
      `SELECT status, revision FROM tasks WHERE id = '${taskId}'`,
    );
    expect(dbCompleted[0].status).toBe("done");

    // 6. Reopen task through UI
    const reopenButton = page.getByRole("button", {
      name: new RegExp(`Reopen ${taskTitle}`, "i"),
    });
    await reopenButton.click();

    // Verify reopened in UI
    await expect(taskListItem.locator("span.line-through")).not.toBeVisible({ timeout: 5000 });
    await captureScreenshot(page, "scenario2_task_reopened");

    // Verify in PostgreSQL
    const dbReopened = queryDbSync<any[]>(
      `SELECT status, revision FROM tasks WHERE id = '${taskId}'`,
    );
    expect(dbReopened[0].status).toBe("open");

    // 7. Check for Task Editing & Deletion controls in current UI
    // In current Blockday UI, task edit title & task delete buttons do not exist in the DOM
    const taskDeleteBtn = taskListItem.locator("button[aria-label*='Delete'], button.delete-task");
    const taskDeleteCount = await taskDeleteBtn.count();
    const taskEditInput = taskListItem.locator("input.edit-task, button[aria-label*='Edit']");
    const taskEditCount = await taskEditInput.count();

    console.log("Scenario 2 Task Edit/Delete UI Audit:");
    console.log(`- Task inline delete buttons found: ${taskDeleteCount}`);
    console.log(`- Task inline edit controls found: ${taskEditCount}`);
    expect(taskDeleteCount).toBe(0); // Accurately reflects current UI limitations

    // 8. Delete Project
    page.once("dialog", (dialog) => dialog.accept());
    const deleteProjectBtn = page.getByRole("button", {
      name: new RegExp(`Delete ${renamedName}`, "i"),
    });
    await deleteProjectBtn.click();

    // Verify project deleted in UI
    await expect(page.locator("nav[aria-label='Projects'] button", { hasText: renamedName })).not.toBeVisible({
      timeout: 5000,
    });
    await captureScreenshot(page, "scenario3_project_deleted");

    // Verify project deletion converges in PostgreSQL (soft-deleted with deleted_at set)
    await expect.poll(() => {
      const dbAfterDelete = queryDbSync<any[]>(
        `SELECT id, status, deleted_at FROM projects WHERE id = '${projectId}'`,
      );
      if (dbAfterDelete.length === 0) return true;
      return dbAfterDelete[0].deleted_at !== null || dbAfterDelete[0].status === "deleted";
    }, { timeout: 10000 }).toBe(true);
  });
});
