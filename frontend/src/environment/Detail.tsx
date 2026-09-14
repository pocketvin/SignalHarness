import { useEffect, useRef, useState } from "react";
import { BookOpen, Check, ExternalLink, LoaderCircle, X } from "lucide-react";
import { api, dateText, safeUrl } from "./api";
import type { Change, DeepDive } from "./types";

export function ChangeDetail({
  change,
  deep,
  error,
  projectId,
  scanId,
  onClose,
  onRetry,
  onLearningStateChanged,
  onOpenLearning,
}: {
  change: Change | null;
  deep: DeepDive | null;
  error: string;
  projectId: string;
  scanId: string;
  onClose: () => void;
  onRetry: () => void;
  onLearningStateChanged: () => void;
  onOpenLearning: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [outcomeStatus, setOutcomeStatus] = useState("");
  const [feedbackStatus, setFeedbackStatus] = useState("");
  const [feedbackSaved, setFeedbackSaved] = useState(false);
  const [outcomeSaved, setOutcomeSaved] = useState(false);
  useEffect(() => {
    const node = ref.current;
    if (change && node && !node.open) node.showModal();
    if (!change && node?.open) node.close();
    setOutcomeStatus("");
    setFeedbackStatus("");
    setFeedbackSaved(false);
    setOutcomeSaved(false);
  }, [change?.change_id]);
  const projectActivity = change?.corpus_role === "project_activity";
  const busy = !projectActivity && (!deep || ["queued", "running"].includes(deep.status));
  async function feedback(
    label: "useful" | "not_useful" | "false_positive" | "too_generic",
  ) {
    if (!change) return;
    try {
      await api.changeFeedback(projectId, scanId, change.change_id, label);
      setFeedbackSaved(true);
      setFeedbackStatus("已加入校准样本。不会立即改变当前报告或策略。");
      onLearningStateChanged();
    } catch (e) {
      setFeedbackSaved(false);
      setFeedbackStatus(e instanceof Error ? e.message : String(e));
    }
  }
  async function outcome(value: boolean) {
    if (!change) return;
    try {
      await api.changeOutcome(projectId, scanId, change.change_id, {
        impact_observed: value,
        note: "",
      });
      setOutcomeSaved(true);
      setOutcomeStatus("实际结果已记录。不会自动改写本期报告。");
      onLearningStateChanged();
    } catch (e) {
      setOutcomeSaved(false);
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
              <span className="context-label">{projectActivity ? "项目活动" : "变化核实"}</span>
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
          {projectActivity ? (
            <section className="project-activity-detail-note">
              <h3>项目活动</h3>
              <p>
                这是当前项目自身已经发生的提交、合并或发布记录。这里直接展示已保存的一手证据，
                不会再创建模型 Deep Dive。
              </p>
            </section>
          ) : (
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
          )}
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
          {!projectActivity && (
            <section className="change-feedback">
              <h3>这条判断对你有用吗</h3>
              <p>反馈进入可回放的校准数据，不会让当前扫描自己改规则。</p>
              <div className="button-row">
                <button onClick={() => void feedback("useful")}>有用</button>
                <button onClick={() => void feedback("not_useful")}>不太有用</button>
                <button onClick={() => void feedback("false_positive")}>与项目无关</button>
                <button onClick={() => void feedback("too_generic")}>太泛</button>
              </div>
              <p role="status">{feedbackStatus}</p>
              {feedbackSaved && (
                <button className="text-button" onClick={onOpenLearning}>
                  查看这条反馈进入哪里
                </button>
              )}
            </section>
          )}
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
              {outcomeSaved && (
                <button className="text-button" onClick={onOpenLearning}>
                  查看校准状态
                </button>
              )}
            </section>
          )}
        </div>
      )}
    </dialog>
  );
}
