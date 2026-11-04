import { test, expect } from "@playwright/test";

const stats = {
  day: {
    period: "day",
    start: "2026-11-04T00:00:00+08:00",
    prompt_tokens: 1800,
    completion_tokens: 350,
    total_tokens: 2150,
    calls: 3,
    estimated: false,
    models: [
      {
        model: "glm-5.3",
        prompt_tokens: 1500,
        completion_tokens: 300,
        total_tokens: 1800,
        calls: 2,
        estimated: false,
        share: 1800 / 2150,
      },
      {
        model: "deepseek-chat",
        prompt_tokens: 300,
        completion_tokens: 50,
        total_tokens: 350,
        calls: 1,
        estimated: false,
        share: 350 / 2150,
      },
    ],
  },
  all: {
    period: "all",
    start: null,
    prompt_tokens: 128400,
    completion_tokens: 21600,
    total_tokens: 150000,
    calls: 126,
    estimated: true,
    models: [
      {
        model: "glm-5.3",
        prompt_tokens: 120000,
        completion_tokens: 20000,
        total_tokens: 140000,
        calls: 110,
        estimated: true,
        share: 140000 / 150000,
      },
      {
        model: "deepseek-chat",
        prompt_tokens: 8400,
        completion_tokens: 1600,
        total_tokens: 10000,
        calls: 16,
        estimated: false,
        share: 10000 / 150000,
      },
    ],
  },
};

test("usage stats live in settings model tab and switch periods", async ({ page }) => {
  const errors: string[] = [];
  const requested: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route("**/api/usage/stats**", async (route) => {
    const period = new URL(route.request().url()).searchParams.get("period") || "day";
    requested.push(period);
    await route.fulfill({
      json: period === "all" ? stats.all : stats.day,
    });
  });

  await page.goto("/");
  await page.getByRole("button", { name: "系统设置" }).click();
  await expect(page.getByRole("tab", { name: "模型分析" })).toBeVisible();
  expect(requested).toEqual([]);

  await page.getByRole("tab", { name: "模型分析" }).click();
  const panel = page.locator(".usage-panel");
  await expect(panel).toBeVisible();
  await expect(page.getByRole("heading", { name: "AI 用量统计" })).toBeVisible();
  await expect(panel).toContainText("2,150");
  await expect(panel).toContainText("输入 1,800 · 输出 350 · 3 次请求");
  await expect(panel.getByRole("tab", { name: "今天" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(panel.locator(".usage-models li")).toHaveCount(2);
  await expect(panel.locator(".usage-models li").first()).toContainText("glm-5.3");
  await expect(panel.locator(".usage-models li").first()).toContainText(
    "1,800 tokens · 84%",
  );
  await expect(panel.locator(".usage-donut")).toHaveCSS("border-radius", "50%");
  expect(requested).toContain("day");

  await page.screenshot({ path: "build/verify-shots/usage-settings-day.png" });

  await panel.getByRole("tab", { name: "总计" }).click();
  await expect(panel).toContainText("15万");
  await expect(panel).toContainText("126 次请求（含估算）");
  await expect(panel.getByRole("tab", { name: "总计" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(requested).toContain("all");

  const beforeRefresh = requested.length;
  await panel.getByRole("button", { name: "刷新用量" }).click();
  await expect.poll(() => requested.length).toBeGreaterThan(beforeRefresh);
  expect(requested[requested.length - 1]).toBe("all");
  await page.screenshot({ path: "build/verify-shots/usage-settings-all.png" });

  expect(errors).toEqual([]);
});
