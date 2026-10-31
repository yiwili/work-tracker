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
    page.getByRole("button", { name: "修复订单查询接口超时", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "修复订单查询接口超时", exact: true })
    .click();
  let dialog = page.getByRole("dialog");
  await dialog.getByLabel("确认工时分钟 1").fill("360");
  await dialog.getByRole("button", { name: "保存记录" }).click();
  await expect(dialog).not.toBeVisible();
  await page.getByRole("button", { name: "添加人工事项", exact: true }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("任务标题").fill("与客户确认接口联调方案");
  await dialog.getByLabel("所属项目").fill("客户 A");
  await dialog
    .getByLabel("详细工作内容")
    .fill("沟通接口变更，确认联调时间和待办事项。");
  await dialog.getByLabel("工作分类").fill("客户沟通");
  await dialog.getByLabel("确认工时分钟 1").fill("119");
  await dialog.getByRole("button", { name: "保存记录" }).click();
  await expect(dialog).not.toBeVisible();
  await expect(
    page.getByRole("button", { name: "确认 8h 日报" }),
  ).toBeDisabled();
  await page
    .getByRole("button", { name: "与客户确认接口联调方案", exact: true })
    .click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("确认工时分钟 1").fill("121");
  await dialog.getByRole("button", { name: "保存记录" }).click();
  await expect(dialog).not.toBeVisible();
  await expect(
    page.getByRole("button", { name: "确认 8h 日报" }),
  ).toBeDisabled();
  await page
    .getByRole("button", { name: "与客户确认接口联调方案", exact: true })
    .click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("确认工时分钟 1").fill("120");
  await dialog.getByRole("button", { name: "保存记录" }).click();
  await expect(dialog).not.toBeVisible();
  await page.getByRole("button", { name: "确认 8h 日报" }).click();
  await expect(
    page.getByRole("button", { name: "已确认", exact: true }),
  ).toBeDisabled();
  await page.reload();
  await expect(page.getByLabel("工作日期")).toHaveValue("2026-10-24");
  await expect(
    page.getByRole("button", { name: "已确认", exact: true }),
  ).toBeVisible();
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
  await page.getByLabel("确认工时分钟 1").fill("120");
  await page.getByRole("button", { name: "保存记录" }).click();
  await page.getByLabel("拆分 评审需求并整理方案").click();
  let dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel("确认工时分钟 1")).toHaveValue("60");
  await expect(dialog.getByLabel("确认工时分钟 2")).toHaveValue("60");
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

test("AI time allocation preserves pinned communication and remains a draft", async ({
  page,
}) => {
  await page.goto("/?date=2026-10-22");
  await page.getByRole("button", { name: "添加人工事项", exact: true }).click();
  await page.getByLabel("任务标题").fill("客户需求沟通");
  await page.getByLabel("确认工时分钟 1").fill("60");
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
    .getByRole("button", { name: "生成工时分配草稿", exact: true })
    .click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "完成，返回审核", exact: true })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "保留 1h 固定工时" }),
  ).toBeVisible();
  const meeting = page.locator("tbody tr").filter({
    has: page.getByRole("button", { name: "客户需求沟通", exact: true }),
  });
  await expect(meeting.getByText("固定工时", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "客户需求沟通", exact: true }).click();
  await expect(page.getByLabel("确认工时分钟 1")).toHaveValue("60");
  await page.getByLabel("关闭编辑").click();
  await page
    .getByRole("button", { name: "优化档案查询功能", exact: true })
    .click();
  await expect(page.getByLabel("确认工时分钟 1")).toHaveValue("420");
  await expect(page.getByLabel("固定此项工时，AI分配时保留")).not.toBeChecked();
  await page.getByLabel("关闭编辑").click();
  await expect(
    page.getByRole("button", { name: "确认 8h 日报" }),
  ).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "已确认", exact: true }),
  ).toHaveCount(0);
  await page.reload();
  await expect(page.getByText("AI工时分配草稿", { exact: true })).toBeVisible();
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
  await expect(
    dialog.getByText("沟通附件挂接需求", { exact: false }),
  ).toBeVisible();
  await expect(dialog.getByText("1.5h", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "完成，返回审核" }).click();
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
  await page
    .getByLabel("Gitee Token（留空保留）")
    .fill("isolated-e2e-gitee-token");
  await page
    .getByRole("button", { name: "立即抓取工作项", exact: true })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "已抓取 1 条，新增 1 条" }),
  ).toBeVisible();
  await expect(page.getByLabel("Gitee Token（留空保留）")).toHaveValue("");
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
  await expect(dialog.getByRole("alert")).toContainText("工时估算格式无效");
  await expect(
    dialog.getByLabel("人工事项名称 3", { exact: true }),
  ).toHaveValue("服务维护3");
  expect(
    await page.request.get("/api/tasks?date=2026-10-19").then((r) => r.json()),
  ).toHaveLength(1);
  await page.reload();
  await page
    .getByRole("button", { name: "AI 估算并分配 8h", exact: true })
    .click();
  dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("alert")).toContainText("工时估算格式无效");
  await expect(
    dialog.getByLabel("人工事项名称 3", { exact: true }),
  ).toHaveValue("服务维护3");
  await page.unroute("**/api/reports/2026-10-19/estimate-time");
  const next = page.waitForRequest((r) =>
    r.url().endsWith("/api/reports/2026-10-19/estimate-time"),
  );
  await dialog.getByRole("button", { name: "生成条目并分配工时" }).click();
  expect((await next).postDataJSON()).toEqual(firstBody);
  await expect(
    dialog.getByRole("button", { name: "完成，返回审核" }),
  ).toBeVisible();
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

test("enterprise title mappings persist and confirmed publishing skips existing work items", async ({
  page,
}) => {
  await page.goto("/?date=2026-10-18");
  await page.getByRole("button", { name: "系统设置", exact: true }).click();
  await page.getByRole("tab", { name: "企业同步" }).click();
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
  await page.getByRole("tab", { name: "企业同步" }).click();
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
  for (const [title, minutes] of [
    ["【上海工程】修复附件提取问题", "90"],
    ["【2026】新增附件批量打印", "120"],
  ]) {
    await page
      .getByRole("button", { name: "添加人工事项", exact: true })
      .click();
    await page.getByLabel("任务标题").fill(title);
    await page.getByLabel("详细工作内容").fill("整理业务需求并验证功能。");
    await page.getByLabel("确认工时分钟 1").fill(minutes);
    await page.getByRole("button", { name: "保存记录", exact: true }).click();
  }
  await page.getByRole("button", { name: "提交到 Gitee", exact: true }).click();
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
  await page.getByRole("button", { name: "提交到 Gitee", exact: true }).click();
  dialog = page.getByRole("dialog");
  await expect(
    dialog.getByLabel("提交工作项 1", { exact: true }),
  ).toBeDisabled();
  await expect(
    dialog.getByRole("button", { name: "确认创建 0 条工作项", exact: true }),
  ).toBeDisabled();
  expect(
    (await page.request.get("/api/reports/2026-10-18").then((r) => r.json()))
      .total_minutes,
  ).toBe(210);
});

test("enterprise navigation remains visible without daily tasks and opens publishing directly", async ({
  page,
}) => {
  await page.goto("/?date=2026-10-17");
  await page.getByRole("button", { name: "企业工作项", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "推送到 Gitee 企业工作项", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "预览并推送工作项", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "读取企业列表", exact: true }),
  ).toBeVisible();
  await page.request.post("/api/tasks", {
    data: { date: "2026-10-16", title: "终端入口验证事项" },
  });
  await page.getByLabel("工作日期", { exact: true }).fill("2026-10-16");
  await page
    .getByRole("button", { name: "预览并推送工作项", exact: true })
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
        name: "Local User fixture work",
        exact: true,
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
      analysisToast.getByRole("button", { name: "前往查看" }),
    ).toBeVisible();
    await analysisToast.getByRole("button", { name: "关闭提示" }).click();
    await expect(page.locator("tbody tr")).toHaveCount(0);
    await page.getByRole("button", { name: "分析结果（1）" }).click();
    const dialog = page.getByRole("dialog", { name: "AI 分析结果" });
    await expect(dialog).toBeVisible();
    const rowCheckbox = dialog.getByLabel("导入 修复订单查询接口超时");
    await expect(rowCheckbox).toBeChecked();
    await dialog.getByLabel("工作项标题").fill("优化档案检索筛选条件");
    await dialog.getByRole("button", { name: "导入选中的 1 项" }).click();
    await expect(dialog.getByText("已导入 1 项工作")).toBeVisible();
    await dialog.getByRole("button", { name: "关闭分析结果" }).click();
    await expect(
      page.getByRole("button", {
        name: "优化档案检索筛选条件",
        exact: true,
      }),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "分析结果" })).toBeVisible();
  } finally {
    await page.request.put("/api/settings", { data: original });
  }
});
