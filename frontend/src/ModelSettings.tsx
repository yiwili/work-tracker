import { OutputTokenSettings } from "./OutputTokenSettings";
import type { Settings } from "./types";
import { ModelConnections, ModelToast } from "./ModelConnections";

export function ModelSettings({
  settings,
  onChange,
  busy,
  feedback,
  onFeedbackClose,
}: {
  settings: Settings;
  onChange: (s: Settings) => void;
  busy: boolean;
  feedback: {
    state: "running" | "success" | "error";
    message: string;
    test?: boolean;
  } | null;
  onFeedbackClose?: () => void;
}) {
  return (
    <>
      <ModelConnections settings={settings} onSettings={onChange} busy={busy} />
      <OutputTokenSettings
        settings={settings}
        onChange={onChange}
        busy={busy}
      />
      {feedback && (
        <ModelToast
          state={feedback.state}
          message={feedback.message}
          onClose={onFeedbackClose}
        />
      )}
    </>
  );
}
