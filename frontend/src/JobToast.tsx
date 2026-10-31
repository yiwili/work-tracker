import { ArrowUpRight, CheckCircle2, X } from "lucide-react";

export type JobToastState = {
  title: string;
  message: string;
  actionLabel?: string;
  onAction?: () => void;
};

export function JobToast({
  toast,
  onClose,
}: {
  toast: JobToastState;
  onClose: () => void;
}) {
  return (
    <div className="job-toast" role="status">
      <CheckCircle2 className="job-toast-icon" size={20} />
      <div className="job-toast-copy">
        <strong>{toast.title}</strong>
        <p>{toast.message}</p>
        {toast.actionLabel && toast.onAction && (
          <button className="job-toast-action" onClick={toast.onAction}>
            {toast.actionLabel}
            <ArrowUpRight size={13} />
          </button>
        )}
      </div>
      <button
        className="job-toast-close"
        aria-label="关闭提示"
        onClick={onClose}
      >
        <X size={15} />
      </button>
    </div>
  );
}
