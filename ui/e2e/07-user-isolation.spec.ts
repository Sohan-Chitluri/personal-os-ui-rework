import { test, expect } from "@playwright/test";
import {
  captureScreenshot,
  queryDbSync,
  setSessionAuth,
} from "./helpers";

test.describe("Scenario 8: User Switching & Account Isolation", () => {
  const userAlice = "test:alice_isolated";
  const userBob = "test:bob_isolated";

  test("verify strict data isolation between User Alice and User Bob across sessions", async ({
    browser,
  }) => {
    // ==========================================
    // 1. Session A: Authenticate as User Alice
    // ==========================================
    console.log("Launching Alice's browser session...");
    const aliceContext = await browser.newContext();
    const alicePage = await aliceContext.newPage();
    await setSessionAuth(alicePage, userAlice);

    await alicePage.goto("/projects", { waitUntil: "networkidle" });
    await expect(alicePage.locator("h1")).toContainText("Projects");

    // Create Alice's project
    const aliceProjectName = `Alice_Vault_${Date.now()}`;
    await alicePage.getByLabel("Project name").fill(aliceProjectName);
    await alicePage.getByRole("button", { name: "Create" }).click();

    const aliceProjectItem = alicePage.locator("nav[aria-label='Projects'] button", {
      hasText: aliceProjectName,
    });
    await expect(aliceProjectItem).toBeVisible({ timeout: 10000 });

    // Create Alice's task under her project
    const aliceTaskTitle = `Alice_Secret_Task_${Date.now()}`;
    await alicePage.getByLabel("Task title").fill(aliceTaskTitle);
    await alicePage.getByRole("button", { name: "Add task" }).click();

    const aliceTaskItem = alicePage.locator("li", { hasText: aliceTaskTitle });
    await expect(aliceTaskItem).toBeVisible({ timeout: 5000 });

    await captureScreenshot(alicePage, "scenario8_alice_workspace");

    // Verify Alice's data in PostgreSQL
    const aliceDbProjects = queryDbSync<any[]>(
      `SELECT p.id, p.name, p.owner_id, u.cognito_sub FROM projects p JOIN users u ON p.owner_id = u.id WHERE p.name = '${aliceProjectName}'`,
    );
    expect(aliceDbProjects).toHaveLength(1);
    expect(aliceDbProjects[0].cognito_sub).toBe("alice_isolated");
    const aliceOwnerId = aliceDbProjects[0].owner_id;
    console.log(`Verified Alice owner_id in DB: ${aliceOwnerId} (cognito_sub=alice_isolated)`);

    // ==========================================
    // 2. Session B: Authenticate as User Bob
    // ==========================================
    console.log("Launching Bob's isolated browser session...");
    const bobContext = await browser.newContext();
    const bobPage = await bobContext.newPage();
    await setSessionAuth(bobPage, userBob);

    await bobPage.goto("/projects", { waitUntil: "networkidle" });
    await expect(bobPage.locator("h1")).toContainText("Projects");

    // Verify Alice's project and task are NOT visible to Bob in the UI
    const aliceProjectFromBobView = bobPage.locator("nav[aria-label='Projects'] button", {
      hasText: aliceProjectName,
    });
    await expect(aliceProjectFromBobView).not.toBeVisible();

    const aliceTaskFromBobView = bobPage.locator("li", { hasText: aliceTaskTitle });
    await expect(aliceTaskFromBobView).not.toBeVisible();

    await captureScreenshot(bobPage, "scenario8_bob_view_isolated_from_alice");

    // Create Bob's own project
    const bobProjectName = `Bob_Workspace_${Date.now()}`;
    await bobPage.getByLabel("Project name").fill(bobProjectName);
    await bobPage.getByRole("button", { name: "Create" }).click();

    const bobProjectItem = bobPage.locator("nav[aria-label='Projects'] button", {
      hasText: bobProjectName,
    });
    await expect(bobProjectItem).toBeVisible({ timeout: 10000 });

    // Verify Bob's data in PostgreSQL
    const bobDbProjects = queryDbSync<any[]>(
      `SELECT p.id, p.name, p.owner_id, u.cognito_sub FROM projects p JOIN users u ON p.owner_id = u.id WHERE p.name = '${bobProjectName}'`,
    );
    expect(bobDbProjects).toHaveLength(1);
    expect(bobDbProjects[0].cognito_sub).toBe("bob_isolated");
    const bobOwnerId = bobDbProjects[0].owner_id;
    console.log(`Verified Bob owner_id in DB: ${bobOwnerId} (cognito_sub=bob_isolated)`);

    // Confirm database owner_ids are strictly distinct
    expect(aliceOwnerId).not.toBe(bobOwnerId);

    // ==========================================
    // 3. Return to Alice's session
    // ==========================================
    console.log("Checking Alice's session after Bob's activity...");
    await alicePage.reload({ waitUntil: "networkidle" });
    await expect(alicePage.locator("h1")).toContainText("Projects");

    // Verify Alice sees her project and task again
    await expect(aliceProjectItem).toBeVisible({ timeout: 10000 });
    await aliceProjectItem.click();
    await expect(aliceTaskItem).toBeVisible({ timeout: 5000 });

    // Verify Bob's project is NOT visible to Alice
    const bobProjectFromAliceView = alicePage.locator("nav[aria-label='Projects'] button", {
      hasText: bobProjectName,
    });
    await expect(bobProjectFromAliceView).not.toBeVisible();

    await captureScreenshot(alicePage, "scenario8_alice_restored_isolated");
    console.log("Strict account isolation confirmed between Alice and Bob!");

    await aliceContext.close();
    await bobContext.close();
  });
});
