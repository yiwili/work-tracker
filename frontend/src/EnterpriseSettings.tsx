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
        Gitee 企业版配置
      </h2>
      <p className="muted" style={{ fontSize: "13px", margin: "4px 0 12px 0" }}>
        统一配置 Gitee 企业令牌、目标企业、项目映射与去重规则。工作项获取、提交以及历史同步均统一由此接管。
      </p>
      <label>
        MCP 企业令牌（留空保留）
        <input
          type="password"
          autoComplete="new-password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder={
            configured
              ? "企业版凭据已配置"
              : "填写个人设置中生成的 MCP 企业令牌"
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
        {configured && !!config.enterprise_id && (
          <button
            type="button"
            className="outline"
            disabled={busy}
            onClick={() =>
              run(async () => {
                await saveKey();
                const r = await fetch("/api/gitee/sync", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: "{}",
                }).then((res) => res.json());
                setMessage(
                  `已成功同步 ${r.records || 0} 条企业工作项到历史参考库。`,
                );
              })
            }
          >
            同步企业工作项到历史库
          </button>
        )}
      </div>
      <label>
        目标企业
        <select
          aria-label="目标企业"
          value={config.enterprise_id || ""}
          onChange={(e) => {
            const entId = Number(e.target.value);
            setPrograms([]);
            change({ enterprise_id: entId, rules: [] });
            if (entId > 0 && (configured || token)) {
              run(async () => {
                await saveKey();
                const list = await enterpriseApi(
                  `programs?enterprise_id=${entId}`,
                  undefined,
                  "GET",
                );
                setPrograms(list);
                setMessage("已读取企业项目列表。");
              });
            }
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

      {/* Gitee 云端去重与工时匹配设置 */}
      <div style={{ marginTop: "24px", paddingTop: "16px", borderTop: "1px solid var(--border)" }}>
        <h3>云端未完成工作项去重与工时匹配</h3>
        <p style={{ fontSize: "13px", color: "var(--muted)", margin: "4px 0 12px 0" }}>
          对云端已有未完成工作项进行智能去重：高置信度自动通过并继承本地工时，模糊区间由用户确认，低置信度自动丢弃。
        </p>

        {(() => {
          const dedup = settings.gitee_dedup || {
            enabled: true,
            dedup_on_pull: true,
            dedup_on_publish: false,
            threshold_high: 0.8,
            threshold_low: 0.4,
            date_weight: 0.2,
          };
          const updateDedup = (patch: Partial<typeof dedup>) => {
            onChange({
              ...settings,
              gitee_dedup: { ...dedup, ...patch },
            });
          };

          return (
            <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
              <label className="check-label">
                <input
                  type="checkbox"
                  checked={dedup.enabled}
                  onChange={(e) => updateDedup({ enabled: e.target.checked })}
                />
                启用云端去重与工时匹配机制
              </label>

              {dedup.enabled && (
                <div style={{ paddingLeft: "20px", display: "flex", flexDirection: "column", gap: "10px" }}>
                  <label className="check-label">
                    <input
                      type="checkbox"
                      checked={dedup.dedup_on_pull}
                      onChange={(e) => updateDedup({ dedup_on_pull: e.target.checked })}
                    />
                    从 Gitee 同步未完成项到今日清单时进行去重（优先使用云端工作项并保留本地工时）
                  </label>
                  <label className="check-label">
                    <input
                      type="checkbox"
                      checked={dedup.dedup_on_publish}
                      onChange={(e) => updateDedup({ dedup_on_publish: e.target.checked })}
                    />
                    “提交到 Gitee”前检查云端未完成项（若存在则直接关联并补充工时，避免重复创建）
                  </label>

                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "16px", marginTop: "8px" }}>
                    <label style={{ display: "flex", flexDirection: "column", gap: "4px", fontSize: "12px" }}>
                      <span>自动通过阈值：{Math.round(dedup.threshold_high * 100)}%</span>
                      <input
                        type="range"
                        min="50"
                        max="95"
                        step="5"
                        value={Math.round(dedup.threshold_high * 100)}
                        onChange={(e) => updateDedup({ threshold_high: Number(e.target.value) / 100 })}
                      />
                    </label>
                    <label style={{ display: "flex", flexDirection: "column", gap: "4px", fontSize: "12px" }}>
                      <span>自动丢弃阈值：{Math.round(dedup.threshold_low * 100)}%</span>
                      <input
                        type="range"
                        min="10"
                        max="50"
                        step="5"
                        value={Math.round(dedup.threshold_low * 100)}
                        onChange={(e) => updateDedup({ threshold_low: Number(e.target.value) / 100 })}
                      />
                    </label>
                    <label style={{ display: "flex", flexDirection: "column", gap: "4px", fontSize: "12px" }}>
                      <span>日期相关权重：{Math.round(dedup.date_weight * 100)}%</span>
                      <input
                        type="range"
                        min="0"
                        max="50"
                        step="5"
                        value={Math.round(dedup.date_weight * 100)}
                        onChange={(e) => updateDedup({ date_weight: Number(e.target.value) / 100 })}
                      />
                    </label>
                  </div>
                </div>
              )}
            </div>
          );
        })()}
      </div>
      <button
        type="button"
        className="outline"
        disabled={busy || !config.enterprise_id || invalid}
        onClick={() =>
          run(async () => {
            await saveKey();
            await enterpriseApi("config", config, "PUT");
            if (settings.gitee_dedup) {
              await fetch("/api/gitee/dedup/config", {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(settings.gitee_dedup),
              });
            }
            setMessage(
              "企业项目和映射已保存；云端去重设置也已生效，日报与提交将优先使用这些规则。",
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
