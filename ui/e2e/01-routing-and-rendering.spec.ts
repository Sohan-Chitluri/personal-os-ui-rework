import { test, expect } from "@playwright/test";
import {
  attachDiagnosticsCollector,
  captureScreenshot,
  setSessionAuth,
} from "./helpers";

test.describe("Scenario 1: Routing, Page Rendering, Console & Network Inspection", () => {
  const routes = [
    { path: "/", titleSubstring: "Today" },
    { path: "/focus", titleSubstring: "Focus" },
    { path: "/flow", titleSubstring: "Flow" },
    { path: "/projects", titleSubstring: "Projects" },
    { path: "/stats", titleSubstring: "Composition of Work" },
    { path: "/diary", titleSubstring: "Diary & Captures" },
    { path: "/devices", titleSubstring: "Devices" },
    { path: "/raphael", titleSubstring: "Raphael" },
  ];

  test("navigate through all routes, verifying rendering, console errors, and network stability", async ({
    page,
  }) => {
    await setSessionAuth(page, "test:acceptance_user");
    const diagnostics = attachDiagnosticsCollector(page);

    for (const route of routes) {
      await page.goto(route.path, { waitUntil: "networkidle" });
      await expect(page).toHaveTitle(new RegExp(route.titleSubstring, "i"));

      // Verify app header is rendered
      const header = page.locator("header.app-header");
      await expect(header).toBeVisible();

      // Verify page container is present
      const main = page.locator("main.page-assemble");
      await expect(main).toBeVisible();

      await captureScreenshot(page, `scenario1_route_${route.path.replace(/\//g, "root_")}`);
    }

    // Inspect collected errors
    const fatalErrors = diagnostics.consoleErrors.filter(
      (msg) =>
        !msg.includes("Multiple instances of Three.js") && // Three.js benign warning in dev
        !msg.includes("Download the React DevTools") &&
        !msg.includes("[vite]"),
    );

    // Filter out expected or benign failed requests
    const hardFailedRequests = diagnostics.failedRequests.filter(
      (r) => !r.url.includes("favicon") && !r.url.includes("fonts.gstatic.com"),
    );

    console.log("Scenario 1 Diagnostics Summary:");
    console.log(`- Routes checked: ${routes.length}`);
    console.log(`- Total console messages: ${diagnostics.consoleMessages.length}`);
    console.log(`- Filtered console errors: ${fatalErrors.length}`);
    console.log("Failed requests:", diagnostics.failedRequests);
    console.log("Console errors:", diagnostics.consoleErrors);

    // Favicon or standard missing assets (e.g. /favicon.ico) shouldn't fail app acceptance
    const genuineAppErrors = fatalErrors.filter(
      (msg) => !msg.includes("404") && !msg.includes("favicon"),
    );

    expect(genuineAppErrors).toHaveLength(0);
    expect(hardFailedRequests).toHaveLength(0);
  });
});
