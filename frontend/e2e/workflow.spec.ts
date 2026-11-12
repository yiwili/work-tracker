import { test, expect } from "@playwright/test";

test("collect → analyze → edit → manual activity → 480 min confirmation → export → persist", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await page.getByLabel("工作日期").fill("2026-10-24");
  await expect(
    page.getByRole("heading", { name: "今天的工作，等你来记录" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "采集当天数据", exact: true }).click();
  await expect(page.getByText("本次范围：")).toBeVisible();
  await expect(page.locator("tbody tr")).toHaveCount(0);
  await page
    .getByRole("button", { name: "AI 分析全天对话", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: /^#\d+\s+(?:【[^】]+】)?修复订单查询接口超时$/ }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: /^#\d+\s+(?:【[^】]+】)?修复订单查询接口超时$/ })
    .click();
  let dialog = page.getByRole("dialog");
  await expect(page.getByLabel("AI 思考与输出")).toContainText("AI 分析已完成");
  await expect(dialog.getByLabel(/预计工时/)).toHaveCount(0);
  await dialog
    .getByRole("group", { name: "工时模板 1" })
    .getByRole("button", { name: "1.5h", exact: true })
    .click();
  await expect(dialog.getByLabel("完成工时小时 1")).toHaveValue("1.5");
  await dialog.getByLabel("完成工时小时 1").fill("6");
  await dialog.getByRole("button", { name: "保存记录" }).click();
  await expect(dialog).not.toBeVisible();
  await page.getByRole("button", { name: "添加人工事项", exact: true }).click();
  dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel(/预计工时/)).toHaveCount(0);
  await dialog.getByLabel("任务标题").fill("与客户确认接口联调方案");
  await dialog.getByLabel("所属项目").fill("客户 A");
  await dialog
    .getByLabel("详细工作内容")
    .fill("沟通接口变更，确认联调时间和待办事项。");
  await dialog.getByLabel("工作分类").selectOption("任务");
  await dialog
    .getByRole("group", { name: "工时模板 1" })
    .getByRole("button", { name: "2h", exact: true })
    .click();
  await expect(dialog.getByLabel("完成工时小时 1")).toHaveValue("2");
  await dialog.getByRole("button", { name: "保存记录" }).click();
  await expect(dialog).not.toBeVisible();
  const dayTasks = await (await page.request.get("/api/tasks?date=2026-10-24")).json();
  expect(dayTasks.length).toBe(2);
  for (const item of dayTasks) {
    expect(item.suggested_minutes).toBe(item.confirmed_minutes);
  }
  await expect(
    page.getByRole("button", { name: "推送至 Gitee", exact: true }),
  ).toBeEnabled();
  await page.request.post("/api/reports/2026-10-24/confirm", { data: {} });
  await page.reload();
  await expect(page.getByLabel("工作日期")).toHaveValue("2026-10-24");
  await expect(
    page.getByRole("button", { name: "推送至 Gitee", exact: true }),
  ).toBeEnabled();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出 Markdown" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("worklog-2026-10-24.md");
  const stream = await download.createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream!) chunks.push(Buffer.from(chunk));
  const markdown = Buffer.concat(chunks).toString("utf-8");
  expect(markdown).toContain("状态：已确认");
  expect(markdown).toContain("**合计 8h / 目标 8h**");
  expect(markdown).toContain("与客户确认接口联调方案");
  expect(markdown).toContain("browser-test-session");
  await page.screenshot({
    path: process.env.AWT_SCREENSHOT || "test-results/daily.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "活动时间线" }).click();
  await expect(page.getByLabel("来源筛选")).toBeVisible();
  await page.getByLabel("来源筛选").selectOption("codex");
  await expect(
    page.getByText("3 passed in 1.23s", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "系统设置" }).click();
  await page.getByRole("tab", { name: "模型分析" }).click();
  await expect(
    page.getByLabel("我了解发送范围，显式开启模型分析"),
  ).toBeChecked();
  await page.getByRole("button", { name: "保存设置" }).click();
  await expect(
    page
      .getByRole("status", { name: "模型连接反馈" })
      .filter({ hasText: "设置已保存" }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});

test("mobile empty layout, split, merge, evidence and reanalysis preserve review", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByLabel("工作日期").fill("2026-10-23");
  await expect(
    page.getByRole("heading", { name: "今天的工作，等你来记录" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await page.getByRole("button", { name: "添加人工事项", exact: true }).click();
  await page.getByLabel("任务标题").fill("评审需求并整理方案");
  await page.getByLabel("完成工时小时 1").fill("2");
  await page.getByRole("button", { name: "保存记录" }).click();
  await page.getByLabel(/^拆分 (?:【[^】]+】)?评审需求并整理方案$/).click();
  let dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel("完成工时小时 1")).toHaveValue("1");
  await expect(dialog.getByLabel("完成工时小时 2")).toHaveValue("1");
  await dialog.getByRole("button", { name: "保存记录" }).click();
  await expect(dialog).not.toBeVisible();
  await page.getByLabel("选择全部任务").check();
  await page.getByRole("button", { name: "合并 2 项" }).click();
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await page.getByRole("button", { name: "0 条证据" }).click();
  dialog = page.getByRole("dialog");
  await expect(
    dialog.getByText("人工事项未关联 AI 日志，以用户填写内容为依据。"),
  ).toBeVisible();
  await dialog.getByLabel("关闭证据").click();
  await page.getByRole("button", { name: "采集当天数据", exact: true }).click();
  await expect(page.locator("tbody tr")).toHaveCount(1);
});

test("provider presets, encrypted key input, test connection and CCSwitch options", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "系统设置" }).click();
  await page.getByRole("tab", { name: "模型分析" }).click();
  await page.getByLabel("AI 分析供应商").selectOption("deepseek");
  await expect(page.getByLabel("完整 API 接口 URL")).toHaveValue(
    "https://api.deepseek.com/chat/completions",
  );
  await page
    .getByLabel("API Key（留空保留已保存的密钥）")
    .fill("ui-test-key-for-storage");
  await page.getByRole("button", { name: "保存并测试连接" }).click();
  await expect(
    page
      .getByRole("status", { name: "模型连接反馈" })
      .filter({ hasText: "真实模型连接测试成功" }),
  ).toBeVisible();
  await expect(page.getByLabel("API Key（留空保留已保存的密钥）")).toHaveValue(
    "",
  );
  await page.getByLabel("AI 分析供应商").selectOption("glm");
  await expect(page.getByLabel("完整 API 接口 URL")).toHaveValue(
    "https://open.bigmodel.cn/api/paas/v4/chat/completions",
  );
  await page.getByLabel("AI 分析供应商").selectOption("ccswitch");
  await expect(
    page.getByLabel("Claude Code settings.json 路径（留空自动检测）"),
  ).toBeVisible();
  await expect(page.getByLabel("API Key（留空保留已保存的密钥）")).toHaveCount(
    0,
  );
});

test("connection feedback stays beside button during failure and successful retry", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "系统设置" }).click();
  await page.getByRole("tab", { name: "模型分析" }).click();
  await page.getByLabel("AI 分析供应商").selectOption("deepseek");
  await page
    .getByLabel("API Key（留空保留已保存的密钥）")
    .fill("inline-test-fixture-key");
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let requests = 0;
  await page.route("**/api/model/test", async (route) => {
    requests++;
    if (requests === 1) {
      await gate;
      await route.fulfill({
        status: 503,
        json: { detail: "模型接口暂时不可用，请稍后重试" },
      });
    } else {
      await route.fulfill({ json: { ok: true, message: "连接成功" } });
    }
  });
  await page.getByRole("button", { name: "保存并测试连接" }).click();
  const progress = page.getByRole("status", { name: "模型连接反馈" });
  await expect(progress).toContainText("正在保存并测试模型连接");
  await expect(progress).toBeInViewport();
  await expect(
    page.getByRole("button", { name: "正在保存与测试…" }),
  ).toBeDisabled();
  await expect(page.getByLabel("完整 API 接口 URL")).toBeDisabled();
  release();
  const failure = page.getByRole("alert", { name: "模型连接反馈" });
  await expect(failure).toContainText(
    "设置已保存，连接测试失败：模型接口暂时不可用",
  );
  await expect(failure).toBeInViewport();
  await expect(page.getByLabel("API Key（留空保留已保存的密钥）")).toHaveValue(
    "",
  );
  await page.getByRole("button", { name: "保存并测试连接" }).click();
  await expect(progress).toContainText("真实模型连接测试成功");
  expect(requests).toBe(2);
});

test("settings save failure shows local reason and does not test the unsaved connection", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "系统设置" }).click();
  await page.getByRole("tab", { name: "模型分析" }).click();
  await page.getByLabel("AI 分析供应商").selectOption("deepseek");
  const key = page.getByLabel("API Key（留空保留已保存的密钥）");
  await key.fill("unsaved-fixture-key");
  let tested = false;
  await page.route("**/api/settings", async (route) => {
    if (route.request().method() === "PUT")
      await route.fulfill({
        status: 400,
        json: { detail: "设置保存遇到错误" },
      });
    else await route.continue();
  });
  await page.route("**/api/model/test", async (route) => {
    tested = true;
    await route.abort();
  });
  await page.getByRole("button", { name: "保存并测试连接" }).click();
  await expect(page.getByRole("alert", { name: "模型连接反馈" })).toContainText(
    "设置保存未完成，尚未测试连接：设置保存遇到错误",
  );
  await expect(key).toHaveValue("unsaved-fixture-key");
  expect(tested).toBe(false);
});

test("invalid url settings explain why connection testing did not start", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "系统设置" }).click();
  await page.getByRole("tab", { name: "模型分析" }).click();
  await page.getByLabel("AI 分析供应商").selectOption("deepseek");
  await page.getByLabel("完整 API 接口 URL").fill("invalid-url");
  let writes = 0;
  page.on("request", (request) => {
    if (request.method() !== "GET") writes++;
  });
  await page.getByRole("button", { name: "保存并测试连接" }).click();
  const feedback = page.getByRole("alert", { name: "模型连接反馈" });
  await expect(feedback).toContainText("无效字段");
  expect(writes).toBe(0);
});

test("AI time allocation preserves pinned communication and applies AI time", async ({
  page,
}) => {
  await page.goto("/?date=2026-10-22");
  await page.getByRole("button", { name: "添加人工事项", exact: true }).click();
  await page.getByLabel("任务标题").fill("客户需求沟通");
  await page.getByLabel("完成工时小时 1").fill("1");
  await expect(page.getByLabel("固定此项工时，AI分配时保留")).toBeChecked();
  await page.getByRole("button", { name: "保存记录" }).click();
  await page.getByRole("button", { name: "添加人工事项", exact: true }).click();
  await page.getByLabel("任务标题").fill("优化档案查询功能");
  await page
    .getByLabel("详细工作内容")
    .fill("整理需求，调整查询逻辑并验证结果");
  await page.getByRole("button", { name: "保存记录" }).click();
  await page
    .getByRole("button", { name: "AI 估算并分配 8h", exact: true })
    .click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "开始 AI 分配", exact: true })
    .click();
  const toast = page.locator(".job-toast");
  await expect(toast).toContainText("AI 工时已分配");
  await toast.getByRole("button", { name: "预览工时" }).click();
  const preview = page.getByRole("dialog");
  await expect(preview).toContainText("AI 工时预览");
  await expect(preview).toContainText("已应用");
  await preview.getByRole("button", { name: "关闭", exact: true }).click();
  const meeting = page.locator("tbody tr").filter({
    has: page.getByRole("button", { name: /^#\d+\s+(?:【[^】]+】)?客户需求沟通$/ }),
  });
  await expect(meeting.getByText("固定工时", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /^#\d+\s+(?:【[^】]+】)?客户需求沟通$/ }).click();
  await expect(page.getByLabel("完成工时小时 1")).toHaveValue("1");
  await page.getByLabel("关闭编辑").click();
  await page
    .getByRole("button", { name: /^#\d+\s+(?:【[^】]+】)?优化档案查询功能$/ })
    .click();
  await expect(page.getByLabel("完成工时小时 1")).toHaveValue("7");
  await expect(page.getByLabel("固定此项工时，AI分配时保留")).not.toBeChecked();
  await page.getByLabel("关闭编辑").click();
  await expect(
    page.getByRole("button", { name: "推送至 Gitee", exact: true }),
  ).toBeEnabled();
  await page.reload();
  await expect(
    page.getByText("AI 工时分配已应用", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "系统设置" }).click();
  await page.getByRole("tab", { name: "提示词与风格" }).click();
  await page.getByText("业务工作项归并与标题", { exact: true }).click();
  await page
    .getByLabel("业务工作项归并与标题补充提示词")
    .fill("标题按业务功能概括，技术原因放入详情");
  await page.getByRole("button", { name: "保存设置", exact: true }).click();
  await expect(
    page
      .getByRole("status", { name: "设置保存反馈" })
      .filter({ hasText: "设置已保存" }),
  ).toBeVisible();
});

test("allocation modal adds quick manual activities and preserves specified time", async ({
  page,
}) => {
  await page.goto("/?date=2026-10-21");
  await page.getByRole("button", { name: "添加人工事项", exact: true }).click();
  await page.getByLabel("任务标题").fill("优化附件管理功能");
  await page.getByRole("button", { name: "保存记录" }).click();
  await page
    .getByRole("button", { name: "AI 估算并分配 8h", exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("添加人工事项（运维 / 沟通）").check();
  await dialog
    .getByLabel("人工事项名称 1", { exact: true })
    .fill("附件挂接需求");
  await dialog.getByLabel("人工事项时长 1", { exact: true }).fill("1.5");
  await dialog.getByRole("button", { name: "继续添加人工事项" }).click();
  await dialog.getByLabel("人工事项名称 2", { exact: true }).fill("服务部署");
  await dialog.getByLabel("人工事项时长 2", { exact: true }).fill("0.5");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await dialog.evaluate((el) => el.scrollWidth <= el.clientWidth),
  ).toBeTruthy();
  await dialog.screenshot({
    path:
      process.env.AWT_MODAL_SCREENSHOT || "test-results/allocation-modal.png",
  });
  await dialog.getByRole("button", { name: "生成条目并分配工时" }).click();
  const toast = page.locator(".job-toast");
  await expect(toast).toContainText("AI 工时已分配");
  await toast.getByRole("button", { name: "预览工时" }).click();
  const preview = page.getByRole("dialog");
  await expect(
    preview.getByText("沟通附件挂接需求", { exact: false }),
  ).toBeVisible();
  await expect(preview.getByText("1.5h", { exact: true })).toBeVisible();
  await preview.getByRole("button", { name: "关闭", exact: true }).click();
  const result = await page.request
    .get("/api/reports/2026-10-21")
    .then((r) => r.json());
  expect(result.total_minutes).toBe(480);
  expect(result.confirmed).toBe(false);
  expect(
    result.tasks
      .filter((t: { time_locked: boolean }) => t.time_locked)
      .map((t: { confirmed_minutes: number }) => t.confirmed_minutes)
      .sort((a: number, b: number) => a - b),
  ).toEqual([30, 90]);
});

test("file import generates reusable style template and Gitee sync enters history only", async ({
  page,
}) => {
  await page.goto("/?date=2026-10-20");
  await page.getByRole("button", { name: "系统设置" }).click();
  await page.getByRole("tab", { name: "提示词与风格" }).click();
  await page
    .getByLabel("历史文件（JSON / CSV / Excel，最大20MB）")
    .setInputFiles({
      name: "history.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(
        "标题,项目,日期\n修复附件管理提取问题,2026,2026-10-19\n",
      ),
    });
  await page
    .getByRole("button", { name: "导入历史工作项", exact: true })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "历史库 1 条" }),
  ).toBeVisible();
  await expect(page.getByLabel("命名风格提示模板（随设置保存）")).toContainText(
    "按业务功能概括",
  );
  await page.getByText("查看历史库（1 条）", { exact: true }).click();
  await expect(
    page
      .locator(".history-list")
      .getByText("修复附件管理提取问题", { exact: true }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Gitee 企业版" }).click();
  await page
    .getByLabel("MCP 企业令牌（留空保留）")
    .fill("isolated-e2e-gitee-token");
  await page.getByRole("button", { name: "读取企业列表", exact: true }).click();
  await expect(page.getByLabel("MCP 企业令牌（留空保留）")).toHaveValue("");
  await page.getByRole("tab", { name: "提示词与风格" }).click();
  await page
    .getByRole("button", { name: "立即抓取企业历史工作项", exact: true })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: /已抓取 1 条.*新增 1 条/ }),
  ).toBeVisible();
  const history = await page.request.get("/api/history").then((r) => r.json());
  expect(history.total).toBe(2);
  expect(
    await page.request.get("/api/tasks?date=2026-10-20").then((r) => r.json()),
  ).toEqual([]);
  await page.reload();
  await page.getByRole("button", { name: "系统设置" }).click();
  await page.getByRole("tab", { name: "提示词与风格" }).click();
  await expect(
    page.getByText("查看历史库（2 条）", { exact: true }),
  ).toBeVisible();
});

test("failed allocation keeps three manual rows across reload and retries the same request", async ({
  page,
}) => {
  await page.goto("/?date=2026-10-19");
  await page.getByRole("button", { name: "添加人工事项", exact: true }).click();
  await page.getByLabel("任务标题").fill("排查附件问题");
  await page.getByRole("button", { name: "保存记录", exact: true }).click();
  await page
    .getByRole("button", { name: "AI 估算并分配 8h", exact: true })
    .click();
  let dialog = page.getByRole("dialog");
  await dialog.getByLabel("添加人工事项（运维 / 沟通）").check();
  await dialog.getByLabel("人工事项名称 1", { exact: true }).fill("需求对接");
  await dialog.getByLabel("人工事项时长 1", { exact: true }).fill("1.5");
  for (let i = 2; i <= 3; i++) {
    await dialog.getByRole("button", { name: "继续添加人工事项" }).click();
    await dialog
      .getByLabel(`人工事项名称 ${i}`, { exact: true })
      .fill(`服务维护${i}`);
    await dialog
      .getByLabel(`人工事项时长 ${i}`, { exact: true })
      .fill(i === 2 ? "0.5" : "1");
  }
  let firstBody: unknown;
  await page.route("**/api/reports/2026-10-19/estimate-time", async (route) => {
    firstBody = route.request().postDataJSON();
    await route.fulfill({
      status: 400,
      contentType: "application/json",
      body: JSON.stringify({ detail: "工时估算格式无效，请重试" }),
    });
  });
  await dialog.getByRole("button", { name: "生成条目并分配工时" }).click();
  const allocationBar = page.locator(".allocation-bar");
  await expect(allocationBar.getByRole("alert")).toContainText(
    "工时估算格式无效",
  );
  expect(
    await page.request.get("/api/tasks?date=2026-10-19").then((r) => r.json()),
  ).toHaveLength(1);
  await page.reload();
  await page
    .getByRole("button", { name: "AI 估算并分配 8h", exact: true })
    .click();
  dialog = page.getByRole("dialog");
  await expect(
    dialog.getByLabel("人工事项名称 3", { exact: true }),
  ).toHaveValue("服务维护3");
  await page.unroute("**/api/reports/2026-10-19/estimate-time");
  const next = page.waitForRequest((r) =>
    r.url().endsWith("/api/reports/2026-10-19/estimate-time"),
  );
  await dialog.getByRole("button", { name: "生成条目并分配工时" }).click();
  expect((await next).postDataJSON()).toEqual(firstBody);
  await expect(page.locator(".job-toast")).toContainText("AI 工时已分配");
  const report = await page.request
    .get("/api/reports/2026-10-19")
    .then((r) => r.json());
  expect(report.total_minutes).toBe(480);
  expect(report.tasks).toHaveLength(4);
  expect(
    report.tasks
      .filter((t: { time_locked: boolean }) => t.time_locked)
      .map((t: { confirmed_minutes: number }) => t.confirmed_minutes)
      .sort((a: number, b: number) => a - b),
  ).toEqual([30, 60, 90]);
});

test("allocation preview waits for confirmation when direct apply is disabled", async ({
  page,
}) => {
  const settings = await page.request
    .get("/api/settings")
    .then((r) => r.json());
  await page.request.put("/api/settings", {
    data: { ...settings, auto_apply_allocation: false },
  });
  await page.goto("/?date=2026-10-15");
  await page.getByRole("button", { name: "添加人工事项", exact: true }).click();
  await page.getByLabel("任务标题").fill("补录历史工时");
  await page.getByRole("button", { name: "保存记录", exact: true }).click();
  await page
    .getByRole("button", { name: "AI 估算并分配 8h", exact: true })
    .click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "开始 AI 分配", exact: true })
    .click();
  const toast = page.locator(".job-toast");
  await expect(toast).toContainText("工时建议已生成");
  await toast.getByRole("button", { name: "预览工时" }).click();
  const preview = page.getByRole("dialog");
  await expect(preview).toContainText("预览并应用 AI 工时");
  await expect(preview).toContainText("这些工时尚未写入工作项");
  const before = await page.request
    .get("/api/reports/2026-10-15")
    .then((r) => r.json());
  expect(before.tasks[0].confirmed_minutes).toBe(0);
  await preview.getByRole("button", { name: "应用这些工时" }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "已应用 1 个工作项" }),
  ).toBeVisible();
  const after = await page.request
    .get("/api/reports/2026-10-15")
    .then((r) => r.json());
  expect(after.time_allocation.pending).toBe(false);
  expect(after.total_minutes).toBe(480);
  await page.request.put("/api/settings", { data: settings });
});

test("enterprise title mappings persist and confirmed publishing skips existing work items", async ({
  page,
}) => {
  await page.goto("/?date=2026-10-18");
  await page.getByRole("button", { name: "系统设置", exact: true }).click();
  await page.getByRole("tab", { name: "Gitee 企业版" }).click();
  await page.getByRole("button", { name: "读取企业列表", exact: true }).click();
  await page.getByLabel("目标企业", { exact: true }).selectOption("7");
  await page.getByRole("button", { name: "读取企业项目", exact: true }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "项目列表已更新" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "添加映射规则", exact: true }).click();
  await page.getByLabel("映射文字 1", { exact: true }).fill("上海工程");
  await page.getByLabel("映射项目 1", { exact: true }).selectOption("11");
  await page.getByRole("button", { name: "添加映射规则", exact: true }).click();
  await page.getByLabel("映射方式 2", { exact: true }).selectOption("contains");
  await page.getByLabel("映射文字 2", { exact: true }).fill("批量打印");
  await page.getByLabel("映射项目 2", { exact: true }).selectOption("11");
  await page
    .getByRole("button", { name: "保存企业与项目映射", exact: true })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "企业项目和映射已保存" }),
  ).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "系统设置", exact: true }).click();
  await page.getByRole("tab", { name: "Gitee 企业版" }).click();
  await expect(page.getByLabel("映射文字 2", { exact: true })).toHaveValue(
    "批量打印",
  );
  await expect(page.getByLabel("映射项目 2", { exact: true })).toHaveValue(
    "11",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("button", { name: "每日日报", exact: true }).click();
  for (const [title, hoursStr] of [
    ["【上海工程】修复附件提取问题", "1.5"],
    ["【2026】新增附件批量打印", "2"],
  ]) {
    await page
      .getByRole("button", { name: "添加人工事项", exact: true })
      .click();
    await page.getByLabel("任务标题").fill(title);
    await page.getByLabel("详细工作内容").fill("整理业务需求并验证功能。");
    await page.getByLabel("完成工时小时 1").fill(hoursStr);
    await page.getByRole("button", { name: "保存记录", exact: true }).click();
  }
  await page.getByRole("button", { name: "推送至 Gitee", exact: true }).click();
  let dialog = page.getByRole("dialog");
  await expect(
    dialog.getByLabel("提交目标项目 1", { exact: true }),
  ).toHaveValue("11");
  await expect(
    dialog.getByLabel("提交目标项目 2", { exact: true }),
  ).toHaveValue("11");
  await dialog
    .getByLabel("提交工作项类型 1", { exact: true })
    .selectOption("9");
  await expect(
    dialog.getByRole("button", { name: "确认创建 2 条工作项", exact: true }),
  ).toBeDisabled();
  await dialog
    .getByLabel("我已核对目标项目与工作内容，确认在Gitee创建上述工作项")
    .check();
  await dialog.screenshot({
    path: process.env.AWT_GITEE_SCREENSHOT || "test-results/gitee-publish.png",
  });
  await dialog
    .getByRole("button", { name: "确认创建 2 条工作项", exact: true })
    .click();
  await expect(
    dialog.getByText("已创建 #8001", { exact: false }),
  ).toBeVisible();
  await expect(
    dialog.getByText("已创建 #8002", { exact: false }),
  ).toBeVisible();
  await dialog.getByRole("button", { name: "关闭", exact: true }).click();
  await page.getByRole("button", { name: "推送至 Gitee", exact: true }).click();
  dialog = page.getByRole("dialog");
  await expect(
    dialog.getByLabel("提交工作项 1", { exact: true }),
  ).not.toBeChecked();
  await expect(
    dialog.getByRole("button", { name: "确认创建 0 条工作项", exact: true }),
  ).toBeDisabled();
  expect(
    (await page.request.get("/api/reports/2026-10-18").then((r) => r.json()))
      .total_minutes,
  ).toBe(210);
});

test("enterprise publishing button is directly accessible on daily report without separate enterprise page", async ({
  page,
}) => {
  await page.goto("/?date=2026-10-17");
  await expect(
    page.getByRole("button", { name: "推送至 Gitee", exact: true }),
  ).toBeDisabled();
  await page.request.post("/api/tasks", {
    data: { date: "2026-10-16", title: "终端入口验证事项" },
  });
  await page.getByLabel("工作日期", { exact: true }).fill("2026-10-16");
  await page
    .getByRole("button", { name: "推送至 Gitee", exact: true })
    .click();
  await expect(
    page
      .getByRole("dialog")
      .getByRole("heading", { name: "创建 Gitee 企业工作项", exact: true }),
  ).toBeVisible();
});

test("Git author selection overrides wrong config and filters previously collected coworkers", async ({
  page,
}) => {
  test.setTimeout(60000);
  const repositories = await (
    await page.request.get("/api/repositories")
  ).json();
  const repo = repositories.find(
    (r: { project: string }) => r.project === "git-user-project",
  );
  expect(repo).toBeTruthy();
  const settings = await (await page.request.get("/api/settings")).json();
  await page.request.put("/api/settings", {
    data: {
      ...settings,
      git_roots: [repo.root],
      git_author: null,
      git_author_overrides: {},
    },
  });
  await page.goto("/?date=2026-10-24");
  await page.getByRole("button", { name: "系统设置" }).click();
  await expect(
    page.getByRole("heading", { name: "Git 提交用户" }),
  ).toBeVisible();
  const author = page.getByLabel(`Git 提交作者 ${repo.root}`, { exact: true });
  const expandRepo = async () => {
    await page
      .getByRole("button", { name: "展开 git-user-project 设置", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "展开 git-user-project 设置", exact: true }),
    ).toHaveAttribute("aria-expanded", "true");
  };
  await expandRepo();
  await expect(author).toHaveValue("inherit");
  const save = async () => {
    await page.getByRole("button", { name: "保存设置", exact: true }).click();
    await expect(
      page.getByRole("status", { name: "设置保存反馈" }),
    ).toContainText("设置已保存");
    await expect(
      page.getByRole("button", { name: "保存设置", exact: true }),
    ).toBeEnabled();
  };
  await author.selectOption("custom");
  await page.getByLabel("git-user-project 作者姓名").fill("Unlisted User");
  await page
    .getByLabel("git-user-project 作者邮箱")
    .fill("wrong@example.invalid");
  await save();
  await page.reload();
  await page.getByRole("button", { name: "系统设置" }).click();
  await expect(
    page.getByRole("heading", { name: "Git 提交用户" }),
  ).toBeVisible();
  await expandRepo();
  await expect(author).toHaveValue("custom");
  await expect(page.getByLabel("git-user-project 作者邮箱")).toHaveValue(
    "wrong@example.invalid",
  );
  let result = await (
    await page.request.post("/api/collect?date=2026-10-24", { data: {} })
  ).json();
  expect(result.git_events).toBe(0);
  expect(result.warnings.join(" ")).toContain("未匹配所选Git用户");
  await author.selectOption({
    label: "Colleague <coworker@example.invalid> · 1条提交",
  });
  await page.getByLabel("Git 默认用户策略").selectOption("custom");
  await page
    .getByLabel("默认 Git 用户邮箱")
    .fill("another-wrong@example.invalid");
  await save();
  const stored = await (await page.request.get("/api/settings")).json();
  expect(stored.git_author_overrides[repo.root].email).toBe(
    "coworker@example.invalid",
  );
  result = await (
    await page.request.post("/api/collect?date=2026-10-24", { data: {} })
  ).json();
  expect(result.git_events).toBe(1);
  let events = await (
    await page.request.get("/api/events?date=2026-10-24")
  ).json();
  expect(
    events
      .filter((e: { source: string }) => e.source === "git")
      .map((e: { summary: string }) => e.summary),
  ).toEqual(["Colleague fixture work"]);
  await author.selectOption("inherit");
  await page.getByLabel("Git 默认用户策略").selectOption("auto");
  await save();
  await page.request.post("/api/collect?date=2026-10-24", { data: {} });
  events = await (await page.request.get("/api/events?date=2026-10-24")).json();
  expect(
    events
      .filter((e: { source: string }) => e.source === "git")
      .map((e: { summary: string }) => e.summary),
  ).toEqual(["Local User fixture work"]);
  await page.request.put("/api/settings", { data: settings });
});

test("collect details modal lets the user adopt a Git commit as a work item", async ({
  page,
}) => {
  const repositories = await (
    await page.request.get("/api/repositories")
  ).json();
  const repo = repositories.find(
    (r: { project: string }) => r.project === "git-user-project",
  );
  expect(repo).toBeTruthy();
  const original = await (await page.request.get("/api/settings")).json();
  await page.request.put("/api/settings", {
    data: {
      ...original,
      git_roots: [repo.root],
      git_author: null,
      git_author_overrides: {},
    },
  });
  try {
    await page.goto("/?date=2026-10-24");
    await page
      .getByRole("button", { name: "采集当天数据", exact: true })
      .click();
    const toast = page.locator(".job-toast");
    await expect(toast).toContainText("采集完成");
    await toast.getByRole("button", { name: "查看采集详情" }).click();
    const dialog = page.getByRole("dialog", { name: "采集详情" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText("Git 本地提交")).toBeVisible();
    await expect(
      dialog.getByLabel("选择提交 Local User fixture work"),
    ).not.toBeChecked();
    await dialog.getByLabel("选择提交 Local User fixture work").check();
    await dialog.getByRole("button", { name: "添加选中 1 条" }).click();
    await expect(dialog.getByText("已添加 1 个工作项")).toBeVisible();
    await expect(dialog.getByText("已加入工作项")).toBeVisible();
    await expect(
      dialog.getByLabel("选择提交 Local User fixture work"),
    ).toBeDisabled();
    await dialog.getByRole("button", { name: "关闭采集详情" }).click();
    await expect(
      page.getByRole("button", {
        name: /^#\d+\s+Local User fixture work$/,
      }),
    ).toBeVisible();
  } finally {
    await page.request.put("/api/settings", { data: original });
  }
});

test("deferred AI analysis waits for the user to review and import candidates", async ({
  page,
}) => {
  const original = await (await page.request.get("/api/settings")).json();
  await page.request.put("/api/settings", {
    data: { ...original, auto_import_analysis: false },
  });
  try {
    await page.goto("/?date=2026-10-25");
    await page
      .getByRole("button", { name: "采集当天数据", exact: true })
      .click();
    const collectToast = page.locator(".job-toast");
    await expect(collectToast).toContainText("采集完成");
    await collectToast.getByRole("button", { name: "关闭提示" }).click();
    await expect(page.locator("tbody tr")).toHaveCount(0);
    await page
      .getByRole("button", { name: "AI 分析全天对话", exact: true })
      .click();
    const analysisToast = page.locator(".job-toast");
    await expect(analysisToast).toContainText("AI 分析完成");
    await expect(analysisToast).toContainText("尚未写入今日清单");
    await expect(
      analysisToast.getByRole("button", { name: "选择替换或新建" }),
    ).toBeVisible();
    await analysisToast.getByRole("button", { name: "关闭提示" }).click();
    await expect(page.locator("tbody tr")).toHaveCount(0);
    await page.getByRole("button", { name: "分析结果（1）" }).click();
    const dialog = page.getByRole("dialog", { name: "AI 分析结果" });
    await expect(dialog).toBeVisible();
    const rowCheckbox = dialog.getByLabel(/导入 .*修复订单查询接口超时/);
    await expect(rowCheckbox).toBeChecked();
    await dialog.getByLabel("工作项标题").fill("优化档案检索筛选条件");
    await dialog.getByRole("button", { name: "导入选中的 1 项" }).click();
    await expect(dialog.getByText("已导入 1 项工作")).toBeVisible();
    await dialog.getByRole("button", { name: "关闭分析结果" }).click();
    await expect(
      page.getByRole("button", {
        name: /^#\d+\s+(?:【[^】]+】)?优化档案检索筛选条件$/,
      }),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "分析结果" })).toBeVisible();
  } finally {
    await page.request.put("/api/settings", { data: original });
  }
});
test("analysis log updates during a pending request and isolates dates", async ({ page }) => {
  let state = "idle";
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/analysis/progress?*", async (route) => {
    const selected = new URL(route.request().url()).searchParams.get("date") === "2026-10-24";
    const current = selected ? state : "idle";
    await route.fulfill({ json: {
      state: current, elapsed_seconds: current === "idle" ? 0 : 12,
      logs: current === "idle" ? [] : [{ seconds: 1, message: "归纳工作项：第 1/2 批" },
        { seconds: 12, message: current === "running" ? "模型正在输出结果" : "分析完成，结果已保存" }],
      thinking: current === "idle" ? "" : "正在核对当天对话与工作目标",
      thinking_live: current === "running",
      model: "glm-5.3-flash",
      thinking_seconds: current === "idle" ? 0 : 12,
      tokens: current === "idle" ? undefined : {
        prompt_tokens: 3758,
        completion_tokens: current === "running" ? 128 : 256,
        total_tokens: current === "running" ? 3886 : 4014,
        estimated: current === "running",
      },
    } });
  });
  await page.route("**/api/analyze?*", async (route) => {
    state = "running";
    await gate;
    state = "success";
    await route.continue();
  });
  await page.goto("/");
  await page.getByLabel("工作日期").fill("2026-10-24");
  await page.getByRole("button", { name: "采集当天数据", exact: true }).click();
  await page.getByRole("button", { name: "AI 分析全天对话", exact: true }).click();
  try {
    await expect(page.getByLabel("AI 思考与输出")).toContainText("正在核对当天对话与工作目标");
    await expect(page.getByLabel("AI 思考与输出")).toContainText("思考中… 12 秒");
    await expect(page.getByLabel("AI 思考与输出")).toContainText("3,758");
  } finally { release(); }
  // Thinking folds up once the answer starts; the user can reopen it.
  await expect(page.getByLabel("AI 思考与输出")).toContainText("AI 分析已完成");
  await expect(page.getByLabel("AI 思考与输出")).not.toContainText("正在核对当天对话与工作目标");
  await page.getByRole("button", { name: "已思考（用时 12 秒）" }).click();
  await expect(page.getByLabel("AI 思考与输出")).toContainText("正在核对当天对话与工作目标");
  await page.reload();
  await page.getByLabel("工作日期").fill("2026-10-24");
  await expect(page.getByLabel("AI 思考与输出")).toContainText("AI 分析已完成");
  await page.getByLabel("工作日期").fill("2026-10-25");
  await expect(page.getByLabel("AI 思考与输出")).toHaveCount(0);
});

test("clear all AI tasks excludes conversations and session controls can exclude and restore", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/?date=2026-10-25");
  const aiTaskBtn = page.getByRole("button", {
    name: /^#\d+\s+(?:【[^】]+】)?优化档案检索筛选条件$/,
  });
  await expect(aiTaskBtn).toBeVisible();

  // Add a manual task first to verify clearing all AI tasks preserves manual items.
  await page.getByRole("button", { name: "添加人工事项", exact: true }).click();
  let dialog = page.getByRole("dialog");
  await dialog.getByLabel("任务标题").fill("整理发布清单");
  await dialog.getByLabel("完成工时小时 1").fill("2");
  await dialog.getByRole("button", { name: "保存记录", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  const manualTaskBtn = page.getByRole("button", {
    name: /^#\d+\s+(?:【[^】]+】)?整理发布清单$/,
  });
  await expect(manualTaskBtn).toBeVisible();

  const clearBtn = page.getByRole("button", { name: /^清空全部 AI 项/ });
  await expect(clearBtn).toBeEnabled();
  await expect(clearBtn).toHaveText("清空全部 AI 项（1）");

  // Dismissing confirmation leaves the AI task intact.
  page.once("dialog", (d) => d.dismiss());
  await clearBtn.click();
  await expect(aiTaskBtn).toBeVisible();

  // Accepting confirmation clears AI tasks, keeps manual items, and excludes linked turns.
  page.once("dialog", (d) => d.accept());
  await clearBtn.click();
  await expect(
    page.getByRole("status").filter({ hasText: "已清空 1 项 AI 工作项" }),
  ).toBeVisible();
  await expect(aiTaskBtn).toHaveCount(0);
  await expect(manualTaskBtn).toBeVisible();
  await expect(clearBtn).toBeDisabled();
  await expect(clearBtn).toHaveText("清空全部 AI 项");

  const banner = page.locator(".exclusion-banner");
  await expect(banner).toBeVisible();
  await expect(banner).toContainText("已采集对话已全部排除");
  const analyzeBtn = page.getByRole("button", {
    name: "AI 分析全天对话",
    exact: true,
  });
  await expect(analyzeBtn).toBeDisabled();

  // Inspect excluded state in the session list and check mobile layout without overflow.
  const sessionDetails = page.locator("details.session-list");
  await sessionDetails.locator("summary").click();
  const sessionCard = sessionDetails
    .locator("article")
    .filter({ hasText: "browser-test-session-2" });
  await expect(sessionCard).toBeVisible();
  await expect(sessionCard).toContainText("已排除 · 不参与下次 AI 分析");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await page.setViewportSize({ width: 1440, height: 1000 });

  // Restore session, manually exclude it, and restore again before re-analyzing.
  await sessionCard
    .getByRole("button", { name: "恢复参与分析", exact: true })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "已恢复会话" }),
  ).toBeVisible();
  await expect(banner).toHaveCount(0);
  await expect(analyzeBtn).toBeEnabled();

  await sessionCard
    .getByRole("button", { name: "不参与下次 AI 分析", exact: true })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "已排除会话" }),
  ).toBeVisible();
  await expect(banner).toBeVisible();
  await expect(analyzeBtn).toBeDisabled();

  await sessionCard
    .getByRole("button", { name: "恢复参与分析", exact: true })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "已恢复会话" }),
  ).toBeVisible();
  await expect(banner).toHaveCount(0);
  await expect(analyzeBtn).toBeEnabled();

  // Re-analyzing after restore regenerates the AI task cleanly without triggering deleted-recovery.
  await analyzeBtn.click();
  await expect(
    page.getByRole("button", {
      name: /^#\d+\s+(?:【[^】]+】)?修复订单查询接口超时$/,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("dialog", { name: "历史删除工作项确认" }),
  ).toHaveCount(0);
  await expect(manualTaskBtn).toBeVisible();
  await expect(clearBtn).toHaveText("清空全部 AI 项（1）");
  expect(errors).toEqual([]);
});

test("hour template chips unify completed and estimated hours across editor and quick modal", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/?date=2026-10-14");

  await page.getByRole("button", { name: "添加人工事项", exact: true }).click();
  let dialog = page.getByRole("dialog", { name: "添加人工事项" });
  await expect(dialog.getByLabel(/预计工时/)).toHaveCount(0);
  await dialog.getByLabel("任务标题").fill("对齐工时模板与预估工时");

  const editorChips = dialog.getByRole("group", { name: "工时模板 1" });
  for (const label of ["0.5h", "1h", "1.5h", "2h", "3h", "4h", "8h", "0h"]) {
    await expect(
      editorChips.getByRole("button", { name: label, exact: true }),
    ).toBeVisible();
  }
  await editorChips.getByRole("button", { name: "1.5h", exact: true }).click();
  await expect(dialog.getByLabel("完成工时小时 1")).toHaveValue("1.5");
  await expect(dialog).toContainText("= 90 分钟");
  await expect(dialog.getByLabel("固定此项工时，AI分配时保留")).toBeChecked();
  await dialog.getByRole("button", { name: "保存记录", exact: true }).click();
  await expect(dialog).not.toBeVisible();

  const row = page.locator("tbody tr", { hasText: "对齐工时模板与预估工时" });
  await expect(row).toBeVisible();
  await expect(row).toContainText("1.5 h");
  await expect(row).not.toContainText("预计");

  await row.getByRole("button", { name: "工时登记", exact: true }).click();
  dialog = page.getByRole("dialog", { name: "工时登记" });
  await expect(dialog.getByLabel(/预计工时/)).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: /同预计/ })).toHaveCount(0);

  const modalChips = dialog.getByRole("group", { name: "工时模板" });
  for (const label of ["0.5h", "1h", "1.5h", "2h", "3h", "4h", "8h", "0h"]) {
    await expect(
      modalChips.getByRole("button", { name: label, exact: true }),
    ).toBeVisible();
  }
  await modalChips.getByRole("button", { name: "0.5h", exact: true }).click();
  await expect(dialog.locator("#confirmed-hours-input")).toHaveValue("0.5");
  await expect(dialog).toContainText("= 30 分钟");
  await modalChips.getByRole("button", { name: "3h", exact: true }).click();
  await expect(dialog.locator("#confirmed-hours-input")).toHaveValue("3");
  await expect(dialog).toContainText("= 180 分钟");
  await expect(
    dialog.getByLabel("固定此项工时，后续 AI 分配与重新计算时保留不被覆盖"),
  ).toBeChecked();
  await dialog.getByRole("button", { name: "保存工时", exact: true }).click();
  await expect(dialog).not.toBeVisible();

  await expect(row).toContainText("3 h");
  const tasks = await (await page.request.get("/api/tasks?date=2026-10-14")).json();
  expect(tasks).toHaveLength(1);
  expect(tasks[0].confirmed_minutes).toBe(180);
  expect(tasks[0].suggested_minutes).toBe(180);
  expect(tasks[0].time_locked).toBe(true);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "添加人工事项", exact: true }).click();
  dialog = page.getByRole("dialog", { name: "添加人工事项" });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  const mobileChips = dialog.getByRole("group", { name: "工时模板 1" });
  await mobileChips.getByRole("button", { name: "1.5h", exact: true }).click();
  await expect(dialog.getByLabel("完成工时小时 1")).toHaveValue("1.5");
  await mobileChips.getByRole("button", { name: "0h", exact: true }).click();
  await expect(dialog.getByLabel("完成工时小时 1")).toHaveValue("0");
  await expect(dialog).toContainText("= 0 分钟");
  await expect(dialog.getByLabel("固定此项工时，AI分配时保留")).not.toBeChecked();
  await dialog.getByRole("button", { name: "取消", exact: true }).click();

  await row.getByRole("button", { name: "工时登记", exact: true }).click();
  dialog = page.getByRole("dialog", { name: "工时登记" });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  expect(errors).toEqual([]);
});

test("scheduled auto-generate settings persist, validate dirty state, show header pill and execute run-now cleanly", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const original = await (await page.request.get("/api/settings")).json();
  try {
    await page.goto("/?date=2026-10-13");
    await expect(page.locator(".auto-gen-header-pill")).toHaveCount(0);

    await page.getByRole("button", { name: "系统设置", exact: true }).click();
    await page.getByRole("tab", { name: "操作设置", exact: true }).click();

    const enableCheckbox = page.getByLabel("启用定时自动生成工作项", {
      exact: true,
    });
    await expect(enableCheckbox).not.toBeChecked();
    await expect(page.locator(".auto-gen-head-pill")).toHaveText("未启用");

    await enableCheckbox.check();
    await expect(page.locator(".auto-gen-unsaved-note")).toBeVisible();
    await expect(
      page.getByRole("button", { name: "立即执行一次", exact: true }),
    ).toBeDisabled();

    // Configure daily fixed times
    await page
      .locator(".auto-gen-presets")
      .getByRole("button", { name: "09:30", exact: true })
      .click();
    await page.getByLabel("新增每日执行时间", { exact: true }).fill("21:15");
    await page.getByRole("button", { name: "添加时间", exact: true }).click();

    const timesList = page.getByRole("list", { name: "已配置的每日执行时间" });
    await expect(timesList).toContainText("09:30");
    await expect(timesList).toContainText("18:00");
    await expect(timesList).toContainText("21:15");

    await page.getByRole("button", { name: "移除时间 09:30" }).click();
    await expect(timesList).not.toContainText("09:30");

    // Switch to fixed interval mode and enable weekdays-only guard
    await page.getByRole("radio", { name: "按固定间隔", exact: true }).click();
    await page
      .locator(".auto-gen-presets")
      .getByRole("button", { name: "2 小时", exact: true })
      .click();
    await expect(
      page.getByLabel("执行间隔（分钟）", { exact: true }),
    ).toHaveValue("120");
    await page
      .getByLabel("仅在工作日执行（周一至周五）", { exact: true })
      .check();

    // Verify mobile layout has zero horizontal overflow on settings pane
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBeTruthy();
    await page.setViewportSize({ width: 1440, height: 1000 });

    await page.getByRole("button", { name: "保存设置", exact: true }).click();
    await expect(
      page
        .getByRole("status", { name: "设置保存反馈" })
        .filter({ hasText: "设置已保存" }),
    ).toBeVisible();
    await expect(page.locator(".auto-gen-unsaved-note")).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "立即执行一次", exact: true }),
    ).toBeEnabled();

    // Reload and verify persisted header pill navigation back to operation settings
    await page.reload();
    const headerPill = page.locator(".auto-gen-header-pill");
    await expect(headerPill).toBeVisible();
    await expect(headerPill).toContainText("定时 每 120 分钟");
    await headerPill.click();

    await expect(
      page.getByRole("tab", { name: "操作设置", exact: true }),
    ).toHaveAttribute("aria-selected", "true");
    await expect(enableCheckbox).toBeChecked();
    await expect(
      page.getByRole("radio", { name: "按固定间隔", exact: true }),
    ).toHaveAttribute("aria-checked", "true");
    await expect(
      page.getByLabel("执行间隔（分钟）", { exact: true }),
    ).toHaveValue("120");
    await expect(
      page.getByLabel("仅在工作日执行（周一至周五）", { exact: true }),
    ).toBeChecked();

    // Trigger manual immediate execution and inspect live status card update
    await page.getByRole("button", { name: "立即执行一次", exact: true }).click();
    const statusCard = page.getByLabel("定时自动生成运行状态");
    await expect(statusCard).toContainText("最近已跳过");
    await expect(statusCard).toContainText("当天暂无可分析的工作对话记录，已跳过");

    // Disable again via UI and confirm pill disappears on daily view
    await enableCheckbox.uncheck();
    await page.getByRole("button", { name: "保存设置", exact: true }).click();
    await expect(
      page
        .getByRole("status", { name: "设置保存反馈" })
        .filter({ hasText: "设置已保存" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "每日日报", exact: true }).click();
    await expect(page.locator(".auto-gen-header-pill")).toHaveCount(0);
    expect(errors).toEqual([]);
  } finally {
    await page.request.put("/api/settings", { data: original });
  }
});
