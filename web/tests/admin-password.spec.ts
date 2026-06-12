import { expect, test } from "@playwright/test";

const statusPayload = {
  ready: true,
  checks: {
    original_src: true,
    embedding_path: true,
    gene_list_path: true,
    default_gene_set_path: true
  },
  cache: {
    exists: true,
    bma: {
      exists: true,
      files: 1,
      bytes: 2048,
      newest_mtime: 1_700_000_000
    },
    es: {
      exists: true,
      files: 0,
      bytes: 0,
      newest_mtime: null
    }
  },
  jobs: {
    job_counts: {
      queued: 0,
      running: 0,
      succeeded: 0,
      failed: 0,
      cancelled: 0
    },
    run_directories: 0,
    run_bytes: 0
  },
  config: {
    workers: 8,
    admin_token_configured: true,
    alias_file_configured: false
  }
};

test("admin status prompts for password and sends it as an admin header", async ({ page }) => {
  const seenTokens: Array<string | undefined> = [];

  await page.route("**/data/status", async (route) => {
    const token = route.request().headers()["x-andes-admin-token"];
    seenTokens.push(token);
    if (token !== "secret") {
      await route.fulfill({
        body: JSON.stringify({ detail: "admin token required" }),
        contentType: "application/json",
        status: 403
      });
      return;
    }
    await route.fulfill({
      body: JSON.stringify(statusPayload),
      contentType: "application/json",
      status: 200
    });
  });

  await page.goto("/admin");
  await expect(page.getByLabel("Admin password")).toBeVisible();

  await page.getByLabel("Admin password").fill("secret");
  await page.getByRole("button", { name: "Unlock admin" }).click();

  await expect(page.getByRole("heading", { name: "Server status" })).toBeVisible();
  await expect(page.getByText("Null-cache storage")).toBeVisible();
  expect(seenTokens).toEqual([undefined, "secret"]);

  await page.reload();

  await expect(page.getByRole("heading", { name: "Server status" })).toBeVisible();
  await expect(page.getByLabel("Admin password")).toBeHidden();
  expect(seenTokens).toEqual([undefined, "secret", "secret"]);
});
