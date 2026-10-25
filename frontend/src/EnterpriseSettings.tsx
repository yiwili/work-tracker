import { useEffect, useState } from "react";
import { Building2, Plus, Trash2 } from "lucide-react";
import type { Settings, GiteeProgram, GiteeProjectRule } from "./types";
import "./workflows.css";

export async function enterpriseApi(
  path: string,
  body?: unknown,
  method = "POST",
) {
  const r = await fetch("/api/gitee/enterprise/" + path, {
    method,
    headers: { "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const data = await r.json();
  if (!r.ok)
    throw Error(
      typeof data.detail === "string"
        ? data.detail
        : "请求格式无效，请检查企业、项目和映射规则",
    );
  return data;
}

export function EnterpriseSettings({
  settings,
  onChange,
}: {
  settings: Settings;
  onChange: (s: Settings) => void;
}) {
  const config = settings.gitee_enterprise;
  const [configured, setConfigured] = useState(false),
    [token, setToken] = useState(""),
    [companies, setCompanies] = useState<{ id: number; name: string }[]>([]),
    [programs, setPrograms] = useState<GiteeProgram[]>([]),
    [prefixes, setPrefixes] = useState<string[]>([]);
  const [busy, setBusy] = useState(false),
    [message, setMessage] = useState("");
  useEffect(() => {
    enterpriseApi("status", undefined, "GET")
      .then((s) => {
        setConfigured(s.configured);
        setPrograms(s.programs);
        setPrefixes(s.common_prefixes);
      })
      .catch((e) => setMessage(e.message));
  }, []);
  const change = (patch: Partial<typeof config>) =>
    onChange({ ...settings, gitee_enterprise: { ...config, ...patch } });
  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setMessage("");
    try {
      await fn();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const saveKey = async () => {
    if (token) {
      await enterpriseApi("token", { api_key: token });
      setToken("");
      setConfigured(true);
    }
  };
  const invalid = config.rules.some((r) => !r.pattern.trim() || !r.program_id);
  const updateRule = (index: number, patch: Partial<GiteeProjectRule>) =>
    change({
      rules: config.rules.map((r, i) => (i === index ? { ...r, ...patch } : r)),
    });
  return (
    <section className="settings-section enterprise-settings">
      <h2>
        <Building2 size={19} />
        Gitee 企业版项目与工作项
      </h2>
      <p>
        用于把本地工作项创建到公司企业项目。预先设置标题与项目映射，日报中即可一键预览并提交。普通仓库历史抓取使用上方独立配置。
      </p>
      <label>
        企业版 Token（留空保留）
        <input
          type="password"
          autoComplete="new-password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder={
            configured
              ? "企业版凭据已配置"
              : "填写具有企业项目读取和工作项创建权限的Token"
          }
        />
      </label>
      <div className="actions">
        <button
          type="button"
          className="outline"
          disabled={busy || (!configured && !token)}
          onClick={() =>
            run(async () => {
              await saveKey();
              setCompanies(
                await enterpriseApi("enterprises", undefined, "GET"),
              );
              setMessage("已读取你有权限访问的企业，请选择目标企业。");
            })
          }
        >
          读取企业列表
        </button>
        {configured && (
          <button
            type="button"
            className="outline"
            disabled={busy}
            onClick={() =>
              run(async () => {
                const r = await enterpriseApi("token", undefined, "DELETE");
                setConfigured(r.configured);
                setMessage(
                  "已移除本机保存的企业版Token，已有映射和创建记录保留。",
                );
              })
            }
          >
            移除企业版 Token
          </button>
        )}
      </div>
      <label>
        目标企业
        <select
          aria-label="目标企业"
          value={config.enterprise_id || ""}
          onChange={(e) => {
            setPrograms([]);
            change({ enterprise_id: Number(e.target.value), rules: [] });
          }}
        >
          <option value="">请选择企业</option>
          {!!config.enterprise_id &&
            !companies.some((c) => c.id === config.enterprise_id) && (
              <option value={config.enterprise_id}>
                已保存企业 #{config.enterprise_id}
              </option>
            )}
          {companies.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}（{c.id}）
            </option>
          ))}
        </select>
      </label>
      <button
        type="button"
        className="outline"
        disabled={busy || !config.enterprise_id || (!configured && !token)}
        onClick={() =>
          run(async () => {
            await saveKey();
            setPrograms(
              await enterpriseApi(
                `programs?enterprise_id=${config.enterprise_id}`,
                undefined,
                "GET",
              ),
            );
            setMessage(
              "项目列表已更新。可以按项目前缀、标题关键词或完整标题设置映射。",
            );
          })
        }
      >
        读取企业项目
      </button>
      <h3>标题与项目映射</h3>
      <p>
        例如前缀“上海工程” → “上海工程技术大学档案系统”；标题包含“消息中间件” →
        对应学习或集成项目。完整标题优先，其次标题关键词，最后项目前缀；相同优先级按更长的规则匹配。
      </p>
      <div className="actions">
        <button
          type="button"
          className="outline"
          disabled={busy || config.rules.length >= 100}
          onClick={() =>
            change({
              rules: [
                ...config.rules,
                { pattern: "", mode: "prefix", program_id: 0 },
              ],
            })
          }
        >
          <Plus size={15} />
          添加映射规则
        </button>
        <button
          type="button"
          className="outline"
          disabled={busy || !prefixes.length}
          onClick={() => {
            const known = new Set(
              config.rules
                .filter((r) => r.mode === "prefix")
                .map((r) => r.pattern),
            );
            const added = prefixes
              .filter((p) => !known.has(p))
              .slice(0, 100 - config.rules.length)
              .map((p) => {
                const matches = programs.filter(
                  (v) => v.name.includes(p) || v.ident === p,
                );
                return {
                  pattern: p,
                  mode: "prefix" as const,
                  program_id: matches.length === 1 ? matches[0].id : 0,
                };
              });
            change({ rules: [...config.rules, ...added] });
          }}
        >
          从历史标题添加常用前缀
        </button>
      </div>
      {config.rules.map((rule, i) => (
        <div className="enterprise-rule" key={i}>
          <label>
            匹配方式
            <select
              aria-label={`映射方式 ${i + 1}`}
              value={rule.mode}
              onChange={(e) =>
                updateRule(i, {
                  mode: e.target.value as GiteeProjectRule["mode"],
                })
              }
            >
              <option value="prefix">项目前缀</option>
              <option value="contains">标题包含</option>
              <option value="exact">完整标题</option>
            </select>
          </label>
          <label>
            前缀或标题文字
            <input
              aria-label={`映射文字 ${i + 1}`}
              maxLength={500}
              value={rule.pattern}
              placeholder="上海工程、2026、消息中间件…"
              onChange={(e) => updateRule(i, { pattern: e.target.value })}
            />
          </label>
          <label>
            Gitee 企业项目
            <select
              aria-label={`映射项目 ${i + 1}`}
              value={rule.program_id || ""}
              onChange={(e) =>
                updateRule(i, { program_id: Number(e.target.value) })
              }
            >
              <option value="">请选择目标项目</option>
              {rule.program_id &&
                !programs.some((p) => p.id === rule.program_id) && (
                  <option value={rule.program_id}>
                    已保存项目 #{rule.program_id}（请读取列表核对）
                  </option>
                )}
              {programs.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                  {p.ident ? " · " + p.ident : ""}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="icon-button"
            aria-label={`删除映射 ${i + 1}`}
            disabled={busy}
            onClick={() =>
              change({ rules: config.rules.filter((_, n) => n !== i) })
            }
          >
            <Trash2 size={16} />
          </button>
        </div>
      ))}
      <label className="check-label">
        <input
          type="checkbox"
          checked={config.include_details}
          onChange={(e) => change({ include_details: e.target.checked })}
        />
        提交时包含工作内容详情（预览中可核对）
      </label>
      <p>
        只发送核对过的标题、详情、日期和预估工时，不上传原始对话或证据日志。创建工作项不代表登记实际工时或自动关闭任务。
      </p>
      <button
        type="button"
        className="outline"
        disabled={busy || !config.enterprise_id || invalid}
        onClick={() =>
          run(async () => {
            await saveKey();
            await enterpriseApi("config", config, "PUT");
            setMessage(
              "企业项目和映射已保存，日报中的“提交到 Gitee”将优先使用这些规则。",
            );
          })
        }
      >
        保存企业与项目映射
      </button>
      {invalid && <p>请为每条映射填写文字并选择目标项目，或删除未用规则。</p>}
      {busy && <p role="status">正在连接Gitee企业版…</p>}
      {message && <p role="status">{message}</p>}
    </section>
  );
}
