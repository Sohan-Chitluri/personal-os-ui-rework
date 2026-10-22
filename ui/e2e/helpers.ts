import { execSync } from "child_process";
import { type Page } from "@playwright/test";
import * as path from "path";
import * as fs from "fs";

export const SCREENSHOT_DIR = path.resolve(
  process.cwd(),
  "test-results/screenshots",
);

export const DEV_JWT_TOKEN =
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJkZXYtdXNlciIsImVtYWlsIjoiZGV2QGV4YW1wbGUuY29tIiwidG9rZW5fdXNlIjoiYWNjZXNzIn0.signature";

if (!fs.existsSync(SCREENSHOT_DIR)) {
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
}

export function ensureUserInDb(sub: string, email?: string): void {
  const rootDir = path.resolve(process.cwd(), "..");
  const pyCode = `
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from personal_os.db.base import new_ulid

async def execute():
    engine = create_async_engine('postgresql+asyncpg://personal_os@127.0.0.1:54329/personal_os_test')
    async with engine.begin() as conn:
        res = await conn.execute(text("SELECT id FROM users WHERE cognito_sub = :sub"), {"sub": "${sub}"})
        if res.fetchone() is None:
            user_id = new_ulid()
            await conn.execute(
                text("INSERT INTO users (id, cognito_sub, email, is_active, created_at, updated_at) VALUES (:id, :sub, :email, true, NOW(), NOW())"),
                {"id": user_id, "sub": "${sub}", "email": "${email || `${sub}@example.com`}"}
            )

asyncio.run(execute())
`;
  try {
    execSync("uv run python", { cwd: rootDir, input: pyCode, encoding: "utf-8" });
  } catch {
    // best effort pre-provisioning
  }
}

export async function setSessionAuth(page: Page, token: string): Promise<void> {
  let sub = token;
  if (token.startsWith("test:") || token.startsWith("user:")) {
    sub = token.split(":", 2)[1] || token;
  } else if (token.includes(".")) {
    try {
      const payloadBase64 = token.split(".")[1];
      const decoded = JSON.parse(Buffer.from(payloadBase64, "base64").toString("utf-8"));
      if (decoded.sub) sub = decoded.sub;
    } catch {
      // ignore
    }
  }
  ensureUserInDb(sub);

  await page.addInitScript((tok) => {
    window.sessionStorage.setItem("personal-os-access-token", tok);
  }, token);
}

export interface ConsoleAndNetworkCollector {
  consoleMessages: string[];
  consoleErrors: string[];
  failedRequests: { url: string; failure: string | null; status?: number }[];
}

export function attachDiagnosticsCollector(page: Page): ConsoleAndNetworkCollector {
  const diagnostics: ConsoleAndNetworkCollector = {
    consoleMessages: [],
    consoleErrors: [],
    failedRequests: [],
  };

  page.on("console", (msg) => {
    const text = msg.text();
    diagnostics.consoleMessages.push(`[${msg.type()}] ${text}`);
    if (msg.type() === "error") {
      diagnostics.consoleErrors.push(text);
    }
  });

  page.on("requestfailed", (req) => {
    diagnostics.failedRequests.push({
      url: req.url(),
      failure: req.failure()?.errorText ?? "unknown failure",
    });
  });

  page.on("response", (res) => {
    if (res.status() >= 400) {
      diagnostics.failedRequests.push({
        url: res.url(),
        failure: `HTTP ${res.status()}`,
        status: res.status(),
      });
    }
  });

  return diagnostics;
}

export function queryDbSync<T = unknown>(sql: string): T {
  const rootDir = path.resolve(process.cwd(), "..");
  const pyCode = `
import asyncio, json
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

async def execute():
    engine = create_async_engine('postgresql+asyncpg://personal_os@127.0.0.1:54329/personal_os_test')
    async with engine.begin() as conn:
        res = await conn.execute(text("""${sql}"""))
        rows = [dict(r._mapping) for r in res.fetchall()] if res.returns_rows else []
        print(json.dumps(rows, default=str))

asyncio.run(execute())
`;
  const stdout = execSync("uv run python", {
    cwd: rootDir,
    input: pyCode,
    encoding: "utf-8",
  });
  return JSON.parse(stdout.trim()) as T;
}

export async function captureScreenshot(page: Page, name: string): Promise<string> {
  const sanitized = name.replace(/[^a-zA-Z0-9_-]/g, "_");
  const filePath = path.join(SCREENSHOT_DIR, `${sanitized}.png`);
  await page.screenshot({ path: filePath, fullPage: true });
  return filePath;
}

export async function getQueueEntriesFromBrowser(page: Page): Promise<any[]> {
  return page.evaluate(async () => {
    const dbs = await indexedDB.databases();
    const syncDb = dbs.find((d) => d.name?.startsWith("personal-os-offline-sync-v1-"));
    if (!syncDb?.name) return [];
    return new Promise<any[]>((resolve, reject) => {
      const req = indexedDB.open(syncDb.name!, 2);
      req.onerror = () => reject(req.error);
      req.onsuccess = () => {
        const db = req.result;
        const tx = db.transaction("operations", "readonly");
        const store = tx.objectStore("operations");
        const getAllReq = store.getAll();
        getAllReq.onsuccess = () => {
          db.close();
          resolve(getAllReq.result);
        };
        getAllReq.onerror = () => {
          db.close();
          reject(getAllReq.error);
        };
      };
    });
  });
}

