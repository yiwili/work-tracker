import { test, expect } from "@playwright/test";

test("output token budgets can be customized, saved and reset", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "系统设置" }).click();
  await page.getByRole("tab", { name: "模型分析" }).click();
  const budgets = page.getByRole("region", { name: "输出 Token 预算" });
  const writer = budgets.getByLabel("工作项归并输出 Token 上限");
  await expect(writer).toHaveValue("");
  await budgets.getByRole("button", { name: "工作项归并使用较大预设" }).click();
  await expect(writer).toHaveValue("65536");
  await budgets.getByRole("button", { name: "工时估算使用推荐值" }).click();
  await budgets.getByLabel("对话记忆压缩输出 Token 上限").fill("48000");
  await budgets.getByLabel("对话记忆压缩输出 Token 上限").blur();
  await expect(page.getByLabel("Token 预算保存状态")).toHaveText(
    "Token 预算已自动保存",
  );
  await expect
    .poll(
      async () =>
        (await (await page.request.get("/api/settings")).json())
          .output_token_budgets.task_writer,
    )
    .toBe(65536);
  await page.reload();
  await page.getByRole("button", { name: "系统设置" }).click();
  await page.getByRole("tab", { name: "模型分析" }).click();
  await expect(writer).toHaveValue("65536");
  await expect(budgets.getByLabel("工时估算输出 Token 上限")).toHaveValue(
    "16384",
  );
  await expect(budgets.getByLabel("对话记忆压缩输出 Token 上限")).toHaveValue(
    "48000",
  );
  await budgets.getByRole("button", { name: "工作项归并设为无上限" }).click();
  await expect
    .poll(
      async () =>
        (await (await page.request.get("/api/settings")).json())
          .output_token_budgets.task_writer,
    )
    .toBe("unlimited");
  await expect(writer).toHaveAttribute("placeholder", "无上限");
  await page.reload();
  await page.getByRole("button", { name: "系统设置" }).click();
  await page.getByRole("tab", { name: "模型分析" }).click();
  await expect(writer).toHaveAttribute("placeholder", "无上限");
  await budgets.getByRole("button", { name: "工作项归并恢复自动" }).click();
  await expect
    .poll(
      async () =>
        (await (await page.request.get("/api/settings")).json())
          .output_token_budgets.task_writer,
    )
    .toBeNull();
});

test("failed token budget auto-save can be retried", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "系统设置" }).click();
  await page.getByRole("tab", { name: "模型分析" }).click();
  await page.route("**/api/settings/output-token-budgets", (route) =>
    route.fulfill({ status: 503, json: { detail: "test offline" } }),
  );
  await page.getByRole("button", { name: "工时估算使用较大预设" }).click();
  await expect(page.getByLabel("Token 预算保存状态")).toContainText(
    "自动保存失败",
  );
  await page.unroute("**/api/settings/output-token-budgets");
  await page.getByRole("button", { name: "重试保存 Token 预算" }).click();
  await expect(page.getByLabel("Token 预算保存状态")).toHaveText(
    "Token 预算已自动保存",
  );
  await expect
    .poll(
      async () =>
        (await (await page.request.get("/api/settings")).json())
          .output_token_budgets.time_estimator,
    )
    .toBe(32768);
});
