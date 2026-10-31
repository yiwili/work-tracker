import { expect, test } from "@playwright/test";

const settingsApi = "/api/settings";

async function readSettings(page: import("@playwright/test").Page) {
  return (await page.request.get(settingsApi)).json();
}

async function restoreSettings(
  page: import("@playwright/test").Page,
  original: unknown,
) {
  await page.request.put(settingsApi, { data: original });
}

test("setup wizard walks each step, saves the model connection and reports status", async ({
  page,
}) => {
  test.setTimeout(90000);
  const original = await readSettings(page);
  try {
    const repositories = await (
      await page.request.get("/api/repositories")
    ).json();
    const repo = repositories.find(
      (item: { project: string }) => item.project === "git-user-project",
    );
    expect(repo).toBeTruthy();

    await page.goto("/");
    await page.getByRole("button", { name: /配置引导/ }).click();
    const dialog = page.getByRole("dialog");
    await expect(
      dialog.getByRole("heading", { name: "配置引导" }),
    ).toBeVisible();

    // 第 1 步：采集来源
    await expect(dialog.getByText("第 1 步 · 确认采集来源")).toBeVisible();
    await expect(dialog.getByText("已找到").first()).toBeVisible();
    await dialog.getByRole("button", { name: "保存并继续" }).click();

    // 第 2 步：AI 模型
    await expect(dialog.getByText("第 2 步 · 接通 AI 模型")).toBeVisible();
    await dialog.getByLabel("AI 分析供应商").selectOption("deepseek");
    await expect(dialog.getByLabel("完整 API 接口 URL")).toHaveValue(
      "https://api.deepseek.com/chat/completions",
    );
    await expect(dialog.getByLabel("模型名称")).toHaveValue("deepseek-chat");
    await dialog.getByLabel("模型名称").selectOption("deepseek-reasoner");
    await dialog.getByLabel(/API Key/).fill("wizard-fixture-key");
    await dialog.getByRole("button", { name: "保存并测试连接" }).click();
    await expect(
      dialog.getByText("模型连接测试成功，已开启模型分析。"),
    ).toBeVisible();
    const saved = await readSettings(page);
    expect(saved.llm_provider).toBe("deepseek");
    expect(saved.llm_model).toBe("deepseek-reasoner");
    expect(saved.llm_enabled).toBe(true);

    // 第 3 步：Git 仓库
    await expect(dialog.getByText("第 3 步 · Git 仓库（可选）")).toBeVisible();
    await dialog.getByLabel("Git 项目上级目录").fill(repo.root);
    await dialog.getByRole("button", { name: "扫描目录" }).click();
    await expect(
      dialog.getByRole("checkbox", { name: "采集 git-user-project" }),
    ).toBeChecked();
    await dialog.getByRole("button", { name: "保存并继续" }).click();

    // 第 4 步：Gitee 接入（历史 Issue + 企业工作项）
    await expect(
      dialog.getByText("第 4 步 · Gitee 接入（可选）"),
    ).toBeVisible();
    await dialog.getByLabel(/Gitee 私人令牌/).fill("wizard-fixture-token");
    await dialog.getByRole("button", { name: "保存并查看结果" }).click();

    // 完成页
    await expect(dialog.getByText("配置完成")).toBeVisible();
    await expect(dialog.getByText("已找到本地对话记录")).toBeVisible();
    await expect(dialog.locator("ul.setup-summary")).toContainText("1 个仓库");
    await dialog.getByRole("button", { name: "重新检查" }).click();
    await expect(dialog.getByText("状态已刷新。")).toBeVisible();
    await dialog.getByRole("button", { name: "完成", exact: true }).click();
    await expect(dialog).toHaveCount(0);

    // 引导保存的配置必须真的落到系统设置页，而不是只改了弹窗内的副本
    await page.getByRole("button", { name: "系统设置" }).click();
    await page.getByRole("tab", { name: "模型分析" }).click();
    await expect(page.getByLabel("AI 分析供应商")).toHaveValue("deepseek");
    await expect(page.getByLabel("模型名称")).toHaveValue("deepseek-reasoner");
    await expect(page.getByLabel("完整 API 接口 URL")).toHaveValue(
      "https://api.deepseek.com/chat/completions",
    );
  } finally {
    await restoreSettings(page, original);
  }
});

test("setup wizard keeps custom model ids and does not silently rewrite them", async ({
  page,
}) => {
  const original = await readSettings(page);
  try {
    await page.goto("/");
    await page.getByRole("button", { name: /配置引导/ }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "跳过此步" }).click();
    await expect(dialog.getByText("第 2 步 · 接通 AI 模型")).toBeVisible();
    await dialog.getByLabel("AI 分析供应商").selectOption("custom");
    await dialog
      .getByLabel("完整 API 接口 URL")
      .fill("http://127.0.0.1:9999/v1/chat/completions");
    await dialog.getByLabel("模型名称").fill("my-local-model");
    await expect(dialog.getByLabel("模型名称")).toHaveValue("my-local-model");
    await page.request.put(settingsApi, {
      data: {
        ...original,
        llm_provider: "custom",
        llm_model: "my-local-model",
      },
    });
    const stored = await readSettings(page);
    expect(stored.llm_model).toBe("my-local-model");
    expect(stored.llm_provider).toBe("custom");
  } finally {
    await restoreSettings(page, original);
  }
});

test("a failed model probe leaves the saved connection untouched", async ({
  page,
}) => {
  const original = await readSettings(page);
  try {
    await page.route("**/api/model/test", (route) =>
      route.fulfill({
        status: 503,
        json: { detail: "模型接口暂时不可用，请稍后重试" },
      }),
    );
    await page.goto("/");
    await page.getByRole("button", { name: /配置引导/ }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "跳过此步" }).click();
    await expect(dialog.getByText("第 2 步 · 接通 AI 模型")).toBeVisible();
    await dialog.getByLabel("AI 分析供应商").selectOption("glm");
    await dialog.getByLabel(/API Key/).fill("broken-fixture-key");
    await dialog.getByRole("button", { name: "保存并测试连接" }).click();
    await expect(dialog.getByRole("alert")).toContainText(
      "模型接口暂时不可用",
    );
    // 测试失败必须回滚：连接配置和开关都不能被改坏
    const after = await readSettings(page);
    expect(after.llm_provider).toBe(original.llm_provider);
    expect(after.llm_endpoint).toBe(original.llm_endpoint);
    expect(after.llm_model).toBe(original.llm_model);
    expect(after.llm_enabled).toBe(original.llm_enabled);
  } finally {
    await restoreSettings(page, original);
  }
});
