import { test, expect } from "@playwright/test";

for (const all of [false, true]) {
  test(`review dismissal persists (${all ? "all" : "single"})`, async ({ page }) => {
    const day = all ? "2026-11-13" : "2026-11-12";
    for (const suffix of ["校验", "刷新"]) {
      const response = await page.request.post("/api/tasks", {
        data: {
          date: day,
          title: `实现用户登录接口鉴权与令牌${suffix}`,
          project: "审核测试项目",
          category: "任务",
          status: "delivered",
          confirmed_minutes: 60,
          time_locked: true,
        },
      });
      expect(response.ok()).toBeTruthy();
    }
    await page.goto("/");
    await page.getByLabel("工作日期").fill(day);
    await page.getByRole("button", { name: /待审核提示（/ }).click();
    const dialog = page.getByRole("dialog", { name: "待审核提示", exact: true });
    await dialog.getByRole("button", {
      name: all ? "全部确认无误（通过审核）" : "确认无误 / 通过",
      exact: true,
    }).click();
    await expect(dialog).not.toBeVisible();
    await page.reload();
    await expect(page.getByRole("button", { name: /待审核提示（/ })).toHaveCount(0);
    const report = await (await page.request.get(`/api/reports/${day}`)).json();
    expect(report.review_items).toEqual([]);
    expect(report.tasks).toHaveLength(2);
    expect(report.total_minutes).toBe(120);
    expect(report.confirmed).toBeFalsy();
  });
}
