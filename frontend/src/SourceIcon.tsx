const iconFiles: Record<string, string> = {
  claude_code: "claude-code.svg",
  codex: "codex.svg",
  workbuddy: "workbuddy.svg",
  workbuddy_cn: "workbuddy.svg",
  workbuddy_intl: "workbuddy.svg",
  deepseek_harness: "deepseek.svg",
  qoder: "qoder.svg",
  qoder_cn: "qoder.svg",
  qoder_intl: "qoder.svg",
  trae: "trae.svg",
  trae_cn: "trae.svg",
  trae_intl: "trae.svg",
  git: "git.svg",
};

export function SourceIcon({
  source,
  size = 18,
}: {
  source: string;
  size?: number;
}) {
  const file = iconFiles[source];
  if (!file) return null;
  return (
    <img
      className="source-icon"
      src={`/icons/${file}`}
      width={size}
      height={size}
      alt=""
      aria-hidden="true"
    />
  );
}
