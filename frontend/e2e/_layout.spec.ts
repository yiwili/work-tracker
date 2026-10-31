import { test } from "@playwright/test";

test("layout snapshot", async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto("/?date=2026-10-24");
  await page.getByRole("button", { name: "系统设置" }).click();
  await page.waitForTimeout(400);
  await page.screenshot({ path: "../build/settings-1920.png" });
  await page.getByRole("button", { name: "每日日报" }).click();
  await page.waitForTimeout(400);
  await page.screenshot({ path: "../build/daily-1920.png" });
});
