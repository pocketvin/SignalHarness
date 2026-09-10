import { useEffect, useRef, useState } from "react";
import { BookOpen, Check, ExternalLink, LoaderCircle, X } from "lucide-react";
import { dateText, request, safeUrl } from "./api";
import type { Change, DeepDive } from "./types";

export function ChangeDetail({
  change,
  deep,
  error,
  projectId,
  scanId,
  onClose,
  onRetry,
}: {
  change: Change | null;
  deep: DeepDive | null;
  error: string;
  projectId: string;
  scanId: string;
  onClose: () => void;
  onRetry: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [outcomeStatus, setOutcomeStatus] = useState("");
  useEffect(() => {
    const node = ref.current;
    if (change && node && !node.open) node.showModal();
    if (!change && node?.open) node.close();
    setOutcomeStatus("");
  }, [change?.change_id]);
  const busy = !deep || ["queued", "running"].includes(deep.status);
  async function outcome(value: boolean) {
    if (!change) return;
    try {
      await request(
        `/projects/${encodeURIComponent(projectId)}/outcomes`,
        "POST",
        {
          scan_id: scanId,
          change_id: change.change_id,
          impact_observed: value,
          note: "",
        },
      );
      setOutcomeStatus("实际结果已记录。不会自动改写本期报告。");
    } catch (e) {
      setOutcomeStatus(e instanceof Error ? e.message : String(e));
    }
  }
  return (
    <dialog
      ref={ref}
      className="env-detail"
      onCancel={onClose}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      {change && (
        <div className="detail-content">
          <header className="detail-header">
            <div>
              <span className="context-label">变化核实</span>
              <p>
                {change.entity} <span>{dateText(change.published_at)}</span>
              </p>
            </div>
            <button
              className="icon-button"
              onClick={onClose}
              aria-label="关闭详情"
            >
              <X size={20} />
            </button>
          </header>
          <h2>{change.summary}</h2>
          <section>
            <h3>发生了什么</h3>
            <p>{change.what_changed}</p>
          </section>
          <section>
            <h3>与项目的初步关系</h3>
            <p>{change.relation_reason}</p>
            {change.uncertainty && (
              <p className="quiet-note">{change.uncertainty}</p>
            )}
          </section>
          <section className="deep-section">
            <div className="section-heading">
              <h3>深入核实</h3>
              {deep?.status === "complete" && (
                <span className="state-tag continuing">
                  <Check size={13} /> 已保存
                </span>
              )}
            </div>
            {busy && !error && (
              <div className="deep-progress" role="status">
                <LoaderCircle className="spinning" size={18} />
                <div>
                  <b>{deep?.progress.message || "正在准备证据与项目上下文"}</b>
                  <p>只读取资料，不修改项目代码。关闭面板不会删除任务结果。</p>
                </div>
              </div>
            )}
            {(error || deep?.error) && (
              <div className="notice">
                <p>{error || deep?.error}</p>
                <button className="text-button" onClick={onRetry}>
                  重新核实
                </button>
              </div>
            )}
            {deep?.result && (
              <>
                <p className="deep-summary">{deep.result.summary}</p>
                <h4>可能的项目影响</h4>
                <p>{deep.result.impact}</p>
                {!!deep.result.verification_steps.length && (
                  <>
                    <h4>建议如何验证</h4>
                    <ol className="verification-list">
                      {deep.result.verification_steps.map((step, i) => (
                        <li key={i}>{step}</li>
                      ))}
                    </ol>
                  </>
                )}
                {deep.result.uncertainty && (
                  <p className="quiet-note">{deep.result.uncertainty}</p>
                )}
              </>
            )}
            {deep?.usage && (
              <details className="evidence-details">
                <summary>
                  项目中的实际引用{" "}
                  <span>{deep.usage.references.length} 处</span>
                </summary>
                <p className="quiet-note">
                  {deep.usage.notice}
                  {deep.usage.limited ? " 本次检查达到范围上限。" : ""}
                </p>
                {deep.usage.references.map((item) => (
                  <div className="code-reference" key={item.reference_id}>
                    <b>
                      {item.path}:{item.line}
                    </b>
                    <pre>{item.excerpt}</pre>
                  </div>
                ))}
              </details>
            )}
          </section>
          <section>
            <h3>
              <BookOpen size={17} /> 来源与证据
            </h3>
            <div className="evidence-list">
              {change.evidence.map((evidence) => (
                <details key={evidence.evidence_id}>
                  <summary>
                    <span>{evidence.source_name}</span>
                    <small>
                      {evidence.authority === "official"
                        ? "官方来源"
                        : evidence.authority === "maintainer"
                          ? "维护者"
                          : "待核对来源"}
                    </small>
                  </summary>
                  <p>{evidence.excerpt}</p>
                  {evidence.excerpt_truncated && (
                    <p className="quiet-note">这里是截取的原始资料片段。</p>
                  )}
                  {safeUrl(evidence.url) && (
                    <a
                      href={safeUrl(evidence.url)}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      查看原始页面 <ExternalLink size={13} />
                    </a>
                  )}
                </details>
              ))}
            </div>
            {!!deep?.sources.length && (
              <p className="quiet-note">
                本次额外核对{" "}
                {
                  deep.sources.filter(
                    (s) => s.fetch_status === "fetched_current",
                  ).length
                }{" "}
                个原始页面。无法重新取得的来源仍以已保存快照呈现。
              </p>
            )}
          </section>
          {deep?.result && (
            <section className="outcome">
              <h3>后来验证的实际情况</h3>
              <p>只记录已经观察到的结果，不把预测当作事实。</p>
              <div className="button-row">
                <button onClick={() => void outcome(true)}>确实影响项目</button>
                <button onClick={() => void outcome(false)}>
                  已验证没有影响
                </button>
              </div>
              <p role="status">{outcomeStatus}</p>
            </section>
          )}
        </div>
      )}
    </dialog>
  );
}
