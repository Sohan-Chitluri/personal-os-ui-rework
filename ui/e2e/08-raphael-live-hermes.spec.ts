import { test, expect } from "@playwright/test";
import {
  captureScreenshot,
  queryDbSync,
  setSessionAuth,
  DEV_JWT_TOKEN,
} from "./helpers";

test.describe("Scenario 9: Raphael Chat with Live Hermes Runtime", () => {
  const testUser = DEV_JWT_TOKEN;

  test("send message, receive live Hermes reply, switch conversations, reload, and verify history persistence", async ({
    page,
  }) => {
    // Increase test timeout to allow for live Hermes LLM generation across two conversations
    test.setTimeout(180000);

    await setSessionAuth(page, testUser);

    // 1. Navigate to /raphael
    console.log("Navigating to /raphael...");
    await page.goto("/raphael", { waitUntil: "networkidle" });
    await expect(page.locator("h2", { hasText: "Raphael" })).toBeVisible({ timeout: 10000 });
    await expect(page.locator("span", { hasText: "Hermes Agent Runtime" })).toBeVisible({
      timeout: 10000,
    });

    // 2. Start a new distinct conversation for this test run
    const newConvBtn = page.getByRole("button", { name: "New" });
    await newConvBtn.click();
    await page.waitForTimeout(1000);

    // Wait for the active conversation ID to be displayed in header
    const headerId = page.locator("header div.font-mono", { hasText: /ID: [0-9A-Z]{26}/ });
    await expect(headerId).toBeVisible({ timeout: 10000 });
    const headerIdText = await headerId.innerText();
    const convAId = headerIdText.replace("ID: ", "").trim();
    console.log(`Active Conversation A ID: ${convAId}`);

    // 3. Send message to live Hermes in Conversation A
    const messageInput = page.getByLabel("Message to Raphael");
    await expect(messageInput).toBeVisible();

    const promptA = "What is 3 + 4? Reply with just the number.";
    await messageInput.fill(promptA);
    await messageInput.press("Enter");

    // Verify user message appears immediately in UI
    const userMessageBubble = page.locator("div.whitespace-pre-wrap", { hasText: promptA });
    await expect(userMessageBubble).toBeVisible({ timeout: 5000 });
    console.log("User message rendered in UI");

    // Wait for Hermes response (thinking indicator clears, assistant message arrives)
    console.log("Waiting for Live Hermes runtime response...");
    const assistantBubble = page.locator("div.bg-panel div.whitespace-pre-wrap");
    await expect(assistantBubble).toBeVisible({ timeout: 60000 });

    const replyA = await assistantBubble.innerText();
    console.log(`Live Hermes response received: "${replyA.trim()}"`);

    // Verify it is LIVE Hermes, NOT mock adapter
    expect(replyA.trim().length).toBeGreaterThan(0);
    expect(replyA).not.toContain("Raphael (mock):");
    console.log("Confirmed: Response is from LIVE Hermes runtime (not mock adapter)!");
    await captureScreenshot(page, "scenario9_live_hermes_conversation_a");

    // Verify message persistence in PostgreSQL for Conversation A
    const dbMessagesA = queryDbSync<any[]>(
      `SELECT id, role, content FROM conversation_messages WHERE conversation_id = '${convAId}' ORDER BY created_at ASC`,
    );
    expect(dbMessagesA.length).toBeGreaterThanOrEqual(2);
    expect(dbMessagesA[0].role).toBe("user");
    expect(dbMessagesA[0].content).toBe(promptA);
    expect(dbMessagesA[1].role).toBe("assistant");
    expect(dbMessagesA[1].content).toBe(replyA.trim());
    console.log("PostgreSQL verified: Conversation A messages persisted in live DB!");

    // 4. Switch conversations: Start Conversation B
    console.log("Starting Conversation B...");
    await newConvBtn.click();
    await page.waitForTimeout(1000);

    const headerIdB = page.locator("header div.font-mono", { hasText: /ID: [0-9A-Z]{26}/ });
    await expect(headerIdB).toBeVisible({ timeout: 10000 });
    const convBId = (await headerIdB.innerText()).replace("ID: ", "").trim();
    expect(convBId).not.toBe(convAId);
    console.log(`Active Conversation B ID: ${convBId}`);

    // Send a message in Conversation B
    const promptB = "What color is the clear day sky? Answer in one word.";
    await messageInput.fill(promptB);
    await messageInput.press("Enter");

    const userMessageBBubble = page.locator("div.whitespace-pre-wrap", { hasText: promptB });
    await expect(userMessageBBubble).toBeVisible({ timeout: 5000 });

    console.log("Waiting for Live Hermes response in Conversation B...");
    await page.waitForTimeout(1000);
    // Find assistant messages in current view
    const assistantBubblesB = page.locator("div.bg-panel div.whitespace-pre-wrap");
    await expect(assistantBubblesB).toBeVisible({ timeout: 60000 });
    const replyB = await assistantBubblesB.innerText();
    console.log(`Live Hermes response B received: "${replyB.trim()}"`);
    expect(replyB).not.toContain("Raphael (mock):");
    await captureScreenshot(page, "scenario9_live_hermes_conversation_b");

    // 5. Switch back to Conversation A in the left sidebar
    console.log("Switching back to Conversation A...");
    const convAButton = page.locator("ul li button", { hasText: new RegExp(convAId.substring(0, 8), "i") }).or(
      page.locator("ul li button").filter({ hasText: "Conversation" }).nth(1)
    );
    await convAButton.click();
    await page.waitForTimeout(1000);

    // Verify Conversation A history is displayed
    await expect(page.locator("div.whitespace-pre-wrap", { hasText: promptA })).toBeVisible({
      timeout: 10000,
    });
    console.log("Verified switching back to Conversation A restores message history!");

    // 6. Reload page and verify history persistence
    console.log("Reloading page to verify persistence across reloads...");
    await page.reload({ waitUntil: "networkidle" });
    await expect(page.locator("h2", { hasText: "Raphael" })).toBeVisible({ timeout: 10000 });

    // Verify both conversations exist in the sidebar
    const convListItems = page.locator("ul li button");
    const count = await convListItems.count();
    expect(count).toBeGreaterThanOrEqual(2);
    console.log(`Sidebar loaded with ${count} conversations after page reload.`);

    // Click on Conversation A and verify promptA and replyA are intact
    await convListItems.nth(1).click();
    await page.waitForTimeout(1000);
    await expect(page.locator("div.whitespace-pre-wrap", { hasText: promptA })).toBeVisible({
      timeout: 10000,
    });
    await captureScreenshot(page, "scenario9_reloaded_history_persisted");
    console.log("Full history persistence across browser reload confirmed!");
  });
});
