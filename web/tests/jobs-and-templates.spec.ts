import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";

const JOB_TOKENS_STORAGE_KEY = "andes.jobTokens.v1";
const RUN_TEMPLATES_STORAGE_KEY = "andes.runTemplates.v1";
const API_BASE = "http://127.0.0.1:8000";

const previewPayload = {
  kind: "set_similarity",
  mode: "gene_list",
  can_submit: true,
  over_limit: false,
  max_term_pairs: 500_000,
  estimated_pair_count: 2,
  genes: {
    input_count: 2,
    matched_count: 2,
    unmatched_count: 0,
    unmatched_examples: [],
    id_type_counts: {}
  },
  cache: {
    kind: "bma",
    status: "reuse",
    hit: true
  },
  warnings: []
};

function jobResponse(jobId: string, token = "job-secret") {
  return {
    job: {
      id: jobId,
      kind: "set_similarity",
      state: "succeeded",
      created_at: "2026-06-15T12:00:00+00:00",
      started_at: "2026-06-15T12:01:00+00:00",
      finished_at: "2026-06-15T12:02:00+00:00",
      cancelled_at: null,
      error: null,
      access_token: token
    },
    queue: {
      position: null,
      queued_ahead: 0
    },
    result: {
      kind: "set_similarity",
      results: [
        {
          term: "Missing FDR",
          description: "no corrected p-value",
          size: 2,
          true_score: null,
          z_score: 1.2,
          p_value: 0.02,
          p_value_corrected: null,
          log10_p_value_corrected: null,
          significant: false
        },
        {
          term: "High FDR",
          description: "later",
          size: 2,
          true_score: null,
          z_score: 2.1,
          p_value: 0.2,
          p_value_corrected: 0.5,
          log10_p_value_corrected: 0.3,
          significant: false
        },
        {
          term: "Low FDR",
          description: "first",
          size: 2,
          true_score: null,
          z_score: 2.8,
          p_value: 0.001,
          p_value_corrected: 0.01,
          log10_p_value_corrected: 2,
          significant: true
        }
      ],
      input_gene_count: 2,
      valid_gene_count: 2,
      invalid_genes: [],
      warnings: [],
      parameters: {
        mode: "gene_list",
        total_pairs: 3
      }
    }
  };
}

function gseaJobResponse(jobId: string, token = "job-secret") {
  return {
    job: {
      id: jobId,
      kind: "gsea",
      state: "succeeded",
      created_at: "2026-06-15T12:00:00+00:00",
      started_at: "2026-06-15T12:01:00+00:00",
      finished_at: "2026-06-15T12:02:00+00:00",
      cancelled_at: null,
      error: null,
      access_token: token
    },
    queue: {
      position: null,
      queued_ahead: 0
    },
    result: {
      kind: "gsea",
      results: [
        {
          term: "TERM_A",
          description: "alpha pathway",
          size: 3,
          true_score: 0.5,
          z_score: 2.4,
          p_value: 0.01,
          p_value_corrected: 0.02,
          log10_p_value_corrected: 1.7,
          significant: true
        }
      ],
      input_gene_count: 4,
      valid_gene_count: 4,
      invalid_genes: [],
      warnings: [],
      parameters: {
        mode: "ranked_enrichment",
        total_pairs: 1,
        gsea_trace: {
          algorithm: "andes_best_match_trace_v1",
          exact: true,
          ranked_gene_count: 4,
          max_points_per_term: 600,
          terms: [
            {
              term: "TERM_A",
              description: "alpha pathway",
              size: 3,
              true_score: 0.5,
              z_score: 2.4,
              p_value_corrected: 0.02,
              es: 0.5,
              es_rank: 3,
              sampled: false,
              points: [
                {
                  rank: 1,
                  gene: "GENE1",
                  rank_score: 3,
                  best_match_gene: "GENE_A",
                  match_score: 0.4,
                  centered_score: -0.1,
                  running_es: -0.1
                },
                {
                  rank: 2,
                  gene: "GENE2",
                  rank_score: 2,
                  best_match_gene: "GENE_B",
                  match_score: 0.7,
                  centered_score: 0.2,
                  running_es: 0.1
                },
                {
                  rank: 3,
                  gene: "GENE3",
                  rank_score: 1,
                  best_match_gene: "GENE_C",
                  match_score: 0.8,
                  centered_score: 0.3,
                  running_es: 0.4
                },
                {
                  rank: 4,
                  gene: "GENE4",
                  rank_score: 0,
                  best_match_gene: "GENE_D",
                  match_score: 0.5,
                  centered_score: -0.4,
                  running_es: 0
                }
              ]
            }
          ]
        }
      }
    }
  };
}

test("job result URL token is stored and stripped from the address bar", async ({ page }) => {
  const seenTokens: string[] = [];

  await page.route(`${API_BASE}/jobs/token-job`, async (route) => {
    seenTokens.push(route.request().headers()["x-andes-job-token"] ?? "");
    await route.fulfill({
      body: JSON.stringify(jobResponse("token-job", "url-secret")),
      contentType: "application/json",
      status: 200
    });
  });

  await page.goto("/jobs/token-job?token=url-secret");

  await expect(page.getByRole("heading", { name: "Job results" })).toBeVisible();
  await expect(page).toHaveURL(/\/jobs\/token-job$/);
  await expect
    .poll(() =>
      page.evaluate((key) => window.localStorage.getItem(key), JOB_TOKENS_STORAGE_KEY)
    )
    .toBe(JSON.stringify({ "token-job": "url-secret" }));
  expect(seenTokens).toEqual(["url-secret"]);
});

test("download requests use the stored job token header without query tokens", async ({ page }) => {
  let downloadHeader = "";
  let downloadUrl = "";

  await page.route(`${API_BASE}/jobs/download-job`, async (route) => {
    await route.fulfill({
      body: JSON.stringify(jobResponse("download-job", "download-secret")),
      contentType: "application/json",
      status: 200
    });
  });
  await page.route(`${API_BASE}/jobs/download-job/download/results.csv`, async (route) => {
    downloadHeader = route.request().headers()["x-andes-job-token"] ?? "";
    downloadUrl = route.request().url();
    await route.fulfill({
      body: "term,z_score\nTERM_A,1.0\n",
      contentType: "text/csv",
      headers: {
        "Content-Disposition": 'attachment; filename="results.csv"'
      },
      status: 200
    });
  });

  await page.goto("/jobs/download-job?token=download-secret");
  await expect(page.getByRole("heading", { name: "Job results" })).toBeVisible();
  await page.getByRole("button", { name: "Results CSV" }).click();

  await expect.poll(() => downloadHeader).toBe("download-secret");
  expect(downloadUrl).not.toContain("token=");
});

test("mapping summary shows all collections and full excluded count", async ({ page }) => {
  const response = jobResponse("mapping-summary-job", "mapping-secret") as unknown as {
    result: {
      invalid_genes: string[];
      parameters: Record<string, unknown>;
    };
  };
  response.result.invalid_genes = ["EXAMPLE_ONLY"];
  response.result.parameters.id_mapping = {
    genes: {
      mapped_count: 2,
      submitted_record_count: 15,
      unresolved_count: 13,
      unmapped_count: 10,
      ambiguous_count: 3,
      source_counts: {
        direct_entrez: 1,
        gene_mapping: 1,
        alias_file: 0,
        unmapped: 10,
        ambiguous: 3
      },
      mapping_report: "mapping-report.csv"
    },
    target_collection: {
      mapped_count: 4,
      submitted_record_count: 5,
      unresolved_count: 1,
      unmapped_count: 1,
      ambiguous_count: 0,
      source_counts: {
        direct_entrez: 0,
        gene_mapping: 4,
        alias_file: 0,
        unmapped: 1,
        ambiguous: 0
      },
      mapping_report: "mapping-report.csv"
    }
  };

  await page.route(`${API_BASE}/jobs/mapping-summary-job`, async (route) => {
    await route.fulfill({
      body: JSON.stringify(response),
      contentType: "application/json",
      status: 200
    });
  });

  await page.goto("/jobs/mapping-summary-job?token=mapping-secret");

  await expect(page.locator(".summary-card", { hasText: "excluded genes" })).toContainText("13");
  await expect(page.getByRole("heading", { name: "Target collection" })).toBeVisible();
  await expect(page.getByText("ambiguous: 3")).toBeVisible();
  await expect(page.getByText("gene_mapping: 4")).toBeVisible();
});

test("downloaded chart SVG is standalone and renders without app CSS", async ({ page }) => {
  await page.route(`${API_BASE}/jobs/svg-job`, async (route) => {
    await route.fulfill({
      body: JSON.stringify(gseaJobResponse("svg-job", "svg-secret")),
      contentType: "application/json",
      status: 200
    });
  });

  await page.goto("/jobs/svg-job?token=svg-secret");
  await expect(page.getByRole("heading", { name: "Running score" })).toBeVisible();

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export SVG" }).first().click();
  const download = await downloadPromise;
  const path = await download.path();
  expect(path).toBeTruthy();
  const svg = await readFile(path as string, "utf8");

  expect(svg).toContain('xmlns="http://www.w3.org/2000/svg"');
  expect(svg).not.toContain("var(");
  expect(svg).toContain("stroke=");
  expect(svg).toContain("fill=");

  const rendered = await page.evaluate(async (svgText) => {
    const encoded = btoa(unescape(encodeURIComponent(svgText)));
    const image = new Image();
    image.src = `data:image/svg+xml;base64,${encoded}`;
    await image.decode();
    const canvas = document.createElement("canvas");
    canvas.width = image.naturalWidth || 820;
    canvas.height = image.naturalHeight || 390;
    const context = canvas.getContext("2d");
    if (!context) return { painted: 0, width: 0, height: 0 };
    context.drawImage(image, 0, 0);
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
    let painted = 0;
    for (let index = 0; index < pixels.length; index += 4) {
      const alpha = pixels[index + 3];
      const red = pixels[index];
      const green = pixels[index + 1];
      const blue = pixels[index + 2];
      if (alpha > 0 && (red < 245 || green < 245 || blue < 245)) painted += 1;
    }
    return { painted, width: canvas.width, height: canvas.height };
  }, svg);

  expect(rendered.width).toBeGreaterThan(0);
  expect(rendered.height).toBeGreaterThan(0);
  expect(rendered.painted).toBeGreaterThan(100);
});

test("template with missing uploaded files blocks preview until explicitly cleared", async ({
  page
}) => {
  let previewCalls = 0;
  let previewAllowed = false;
  await page.addInitScript(
    ({ storageKey }) => {
      const now = "2026-06-15T12:00:00.000Z";
      window.localStorage.setItem(
        storageKey,
        JSON.stringify({
          "template-1": {
            id: "template-1",
            kind: "set_similarity",
            name: "Custom target",
            fields: {
              genes: "A\nB",
              genesFileName: null,
              minSize: 1,
              maxSize: 3,
              goNamespace: "biological_process",
              queryCollection: { mode: "default" },
              targetCollection: { mode: "gmt", gmtFileName: "custom-target.gmt" }
            },
            created_at: now,
            updated_at: now
          }
        })
      );
    },
    { storageKey: RUN_TEMPLATES_STORAGE_KEY }
  );
  await page.route(`${API_BASE}/preview/set-similarity`, async (route) => {
    previewCalls += 1;
    await route.fulfill({
      body: JSON.stringify(previewAllowed ? previewPayload : { detail: "preview blocked" }),
      contentType: "application/json",
      status: previewAllowed ? 200 : 500
    });
  });

  await page.goto("/set-similarity");
  await page.getByRole("button", { name: "Run with edits" }).click();

  await expect(
    page.getByText(
      "This template originally used uploaded files. If you continue without reattaching them"
    )
  ).toBeVisible();
  await expect(page.getByText("target GMT custom-target.gmt", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Preview job" }).click();
  await expect(page.getByText("Reattach or clear required template files")).toBeVisible();
  expect(previewCalls).toBe(0);

  await page.getByRole("button", { name: "Continue without reattaching files" }).click();
  await page.getByRole("button", { name: "Preview job" }).click();
  await expect(page.getByText("Reattach or clear required template files")).toBeVisible();
  expect(previewCalls).toBe(0);

  await page.getByRole("button", { name: "Confirm use without files" }).click();
  previewAllowed = true;
  await page.getByRole("button", { name: "Preview job" }).click();

  await expect(page.getByRole("heading", { name: "Ready to queue" })).toBeVisible();
  expect(previewCalls).toBe(1);
});

test("submit succeeds when localStorage is unavailable by carrying token in redirect", async ({
  page
}) => {
  await page.addInitScript(() => {
    const blocked = () => {
      throw new Error("storage blocked");
    };
    Object.defineProperty(Storage.prototype, "getItem", { value: blocked });
    Object.defineProperty(Storage.prototype, "setItem", { value: blocked });
    Object.defineProperty(Storage.prototype, "removeItem", { value: blocked });
  });

  const seenJobTokens: string[] = [];
  await page.route(`${API_BASE}/preview/set-similarity`, async (route) => {
    await route.fulfill({
      body: JSON.stringify(previewPayload),
      contentType: "application/json",
      status: 200
    });
  });
  await page.route(`${API_BASE}/jobs/set-similarity`, async (route) => {
    await route.fulfill({
      body: JSON.stringify({
        id: "submitted-job",
        kind: "set_similarity",
        state: "queued",
        created_at: "2026-06-15T12:00:00+00:00",
        access_token: "submit-secret"
      }),
      contentType: "application/json",
      status: 202
    });
  });
  await page.route(`${API_BASE}/jobs/submitted-job`, async (route) => {
    seenJobTokens.push(route.request().headers()["x-andes-job-token"] ?? "");
    await route.fulfill({
      body: JSON.stringify(jobResponse("submitted-job", "submit-secret")),
      contentType: "application/json",
      status: 200
    });
  });

  await page.goto("/set-similarity");
  await page.getByRole("button", { name: "Preview job" }).click();
  await expect(page.getByRole("heading", { name: "Ready to queue" })).toBeVisible();
  await page.getByRole("button", { name: "Queue analysis" }).click();

  await expect(page.getByRole("heading", { name: "Job results" })).toBeVisible();
  await expect(page).toHaveURL(/\/jobs\/submitted-job$/);
  expect(seenJobTokens.length).toBeGreaterThan(0);
  expect(seenJobTokens.every((token) => token === "submit-secret")).toBe(true);
});

test("artifact downloads send admin token when no job token is available", async ({ page }) => {
  let downloadAdminHeader = "";
  let downloadJobHeader = "";

  await page.addInitScript(() => {
    window.sessionStorage.setItem("andes.adminToken", "admin-secret");
  });
  await page.route(`${API_BASE}/jobs/admin-download`, async (route) => {
    const payload = jobResponse("admin-download");
    delete (payload.job as { access_token?: string }).access_token;
    await route.fulfill({
      body: JSON.stringify(payload),
      contentType: "application/json",
      status: 200
    });
  });
  await page.route(`${API_BASE}/jobs/admin-download/download/results.csv`, async (route) => {
    downloadAdminHeader = route.request().headers()["x-andes-admin-token"] ?? "";
    downloadJobHeader = route.request().headers()["x-andes-job-token"] ?? "";
    await route.fulfill({
      body: "term,z_score\nTERM_A,1.0\n",
      contentType: "text/csv",
      headers: {
        "Content-Disposition": 'attachment; filename="results.csv"'
      },
      status: 200
    });
  });

  await page.goto("/jobs/admin-download");
  await expect(page.getByRole("heading", { name: "Job results" })).toBeVisible();
  await page.getByRole("button", { name: "Results CSV" }).click();

  await expect.poll(() => downloadAdminHeader).toBe("admin-secret");
  expect(downloadJobHeader).toBe("");
});

test("result table sorts missing FDR values after finite FDR values", async ({ page }) => {
  await page.route(`${API_BASE}/jobs/fdr-job`, async (route) => {
    await route.fulfill({
      body: JSON.stringify(jobResponse("fdr-job", "fdr-secret")),
      contentType: "application/json",
      status: 200
    });
  });

  await page.goto("/jobs/fdr-job?token=fdr-secret");
  await expect(page.getByRole("heading", { name: "Job results" })).toBeVisible();

  const rows = page.locator("tbody tr");
  await expect(rows.nth(0)).toContainText("Low FDR");
  await expect(rows.nth(1)).toContainText("High FDR");
  await expect(rows.nth(2)).toContainText("Missing FDR");
  await expect(rows.nth(2)).toContainText("NA");
});
