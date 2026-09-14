import {
  ArrowRight,
  Check,
  CircleDot,
  History,
  ShieldCheck,
  Workflow,
} from "lucide-react";
import type {
  CalibrationReplay,
  LearningStatus,
  ReplayMetrics,
} from "./types";

const stateCopy: Record<string, { title: string; description: string }> = {
  collecting: {
    title: "正在积累真实样本",
    description: "反馈和实际结果会先冻结成可回放 Episode；样本不足时不会生成“学习成功”的结论。",
  },
  ready_for_replay: {
    title: "样本已达到回放门槛",
    description: "已经有足够标注样本，可以用历史 Episode 比较当前策略和候选策略。",
  },
  candidate_ready: {
    title: "候选改进通过回放",
    description: "候选在受保护指标上没有回归，可以进入人工审查，但仍不会自动应用。",
  },
  candidate_blocked: {
    title: "候选改进尚未通过推广门",
    description: "系统保留候选和回放结果，但不会让它替换当前策略。",
  },
  review: {
    title: "等待人工审查",
    description: "候选已经进入 staging；真正应用仍需要显式批准与 durable replay gate。",
  },
  applied: {
    title: "已有受控策略版本",
    description: "应用过的策略会留下 revision，可追溯来源并在安全条件满足时回滚。",
  },
};

const recommendationCopy: Record<string, string> = {
  insufficient_evidence: "证据不足",
  review_and_consider: "可以进入人工审查",
  reject_regression: "发现回归，拒绝推广",
  reject_no_gain: "没有测得改进",
};

const feedbackLabelCopy: Record<string, string> = {
  useful: "有用",
  not_useful: "不太有用",
  false_positive: "与项目无关",
  too_generic: "太泛",
};

function pct(value: number) {
  return `${Math.round(value * 100)}%`;
}

function valueText(value: unknown) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function Metrics({ title, metrics }: { title: string; metrics: ReplayMetrics }) {
  return (
    <div className="learning-metrics-column">
      <span>{title}</span>
      <strong>{pct(metrics.precision)} precision</strong>
      <small>{pct(metrics.recall)} recall</small>
      <small>
        {metrics.false_positive} 个误报 · {metrics.missed_positive} 个漏报
      </small>
    </div>
  );
}

function ReplayComparison({ replay }: { replay: CalibrationReplay }) {
  return (
    <section className="learning-replay-card">
      <div className="learning-card-heading">
        <div>
          <span>历史回放</span>
          <h3>{recommendationCopy[replay.recommendation] || replay.recommendation}</h3>
        </div>
        <b className={replay.promotion_allowed ? "gate-pass" : "gate-hold"}>
          {replay.promotion_allowed ? "推广门通过" : "暂不推广"}
        </b>
      </div>
      <div className="learning-metrics-grid">
        <Metrics title="当前策略" metrics={replay.old_metrics} />
        <ArrowRight size={18} />
        <Metrics title="候选策略" metrics={replay.proposed_metrics} />
      </div>
      <div className="learning-replay-facts">
        <span>已标注 {replay.labeled_count}</span>
        <span>误报减少 {replay.false_positive_reduction}</span>
        <span>漏报减少 {replay.missed_positive_reduction}</span>
        <span>排序变化 {replay.ranking_change_count}</span>
      </div>
      {!!replay.reasons.length && (
        <details>
          <summary>为什么得到这个结论</summary>
          <ul>
            {replay.reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

export function LearningSummaryCard({
  status,
  onOpen,
}: {
  status: LearningStatus | null;
  onOpen: () => void;
}) {
  if (!status) return null;
  const copy = stateCopy[status.learning_state] || stateCopy.collecting;
  const progress = Math.min(
    100,
    Math.round((status.labeled_count / Math.max(1, status.minimum_labeled_required)) * 100),
  );
  return (
    <section className="learning-summary-card">
      <div className="learning-summary-icon">
        <Workflow size={20} />
      </div>
      <div className="learning-summary-copy">
        <span>学习与校准</span>
        <h2>系统正在变得更懂这个项目</h2>
        <p>{copy.title}。{copy.description}</p>
        <div className="learning-summary-progress" aria-label="有效学习样本进度">
          <i style={{ width: `${progress}%` }} />
        </div>
        <small>
          {status.feedback_count} 条反馈 · {status.outcome_count} 条实际结果 · {status.labeled_count}/
          {status.minimum_labeled_required} 个可回放标注
        </small>
      </div>
      <button className="text-button" onClick={onOpen}>
        查看学习过程 <ArrowRight size={15} />
      </button>
    </section>
  );
}

export function LearningDashboard({
  status,
  loading,
  error,
  onRefresh,
}: {
  status: LearningStatus | null;
  loading: boolean;
  error: string;
  onRefresh: () => void;
}) {
  if (loading && !status) {
    return <div className="learning-loading">正在读取学习与校准状态…</div>;
  }
  if (!status) {
    return (
      <section className="paper-panel learning-empty">
        <h2>学习状态暂时不可读取</h2>
        <p>{error || "当前项目没有可用的校准状态。"}</p>
        <button onClick={onRefresh}>重新读取</button>
      </section>
    );
  }
  const copy = stateCopy[status.learning_state] || stateCopy.collecting;
  const replay = status.candidate_replay || status.durable_replay;
  const flow = [
    {
      title: "反馈与实际结果",
      done: status.feedback_count + status.outcome_count > 0,
      text: `${status.feedback_count} 条反馈 · ${status.outcome_count} 条 outcome`,
    },
    {
      title: "冻结 Episode",
      done: status.episode_count > 0,
      text: `${status.episode_count} 个可追溯历史样本`,
    },
    {
      title: "历史回放",
      done: Boolean(replay),
      text: replay
        ? recommendationCopy[replay.recommendation] || replay.recommendation
        : `还差 ${status.labels_needed} 个有效标注`,
    },
    {
      title: "候选改进",
      done: Boolean(status.candidate),
      text: status.candidate?.proposal_id || "尚未生成候选",
    },
    {
      title: "人工推广门",
      done: status.staged_proposals.length > 0 || Boolean(replay?.promotion_allowed),
      text: replay?.promotion_allowed ? "回放通过，仍需人工批准" : "未允许替换当前策略",
    },
    {
      title: "Revision / Rollback",
      done: status.revision_count > 0,
      text: status.revision_count
        ? `${status.revision_count} 个策略 revision`
        : "尚无已应用 revision",
    },
  ];
  return (
    <div className="learning-dashboard">
      <section className="learning-hero">
        <div>
          <span className="learning-eyebrow">Review-first self-improvement</span>
          <h2>{copy.title}</h2>
          <p>{copy.description}</p>
        </div>
        <div className="learning-guard">
          <ShieldCheck size={19} />
          <div>
            <b>不会 Scan-time 自改</b>
            <small>只有回放证明不回归并经过显式批准，策略 revision 才能替换当前版本。</small>
          </div>
        </div>
      </section>

      <section className="learning-stat-grid">
        <article><span>用户反馈</span><strong>{status.feedback_count}</strong><small>真实 Change feedback</small></article>
        <article><span>实际结果</span><strong>{status.outcome_count}</strong><small>已观察 outcome</small></article>
        <article><span>有效标注</span><strong>{status.labeled_count}</strong><small>推广门至少需要 {status.minimum_labeled_required}</small></article>
        <article><span>策略版本</span><strong>{status.revision_count}</strong><small>每次应用都可追溯</small></article>
      </section>

      <section className="paper-panel learning-flow-panel">
        <div className="section-heading">
          <div>
            <h2>一条反馈是怎么变成候选改进的</h2>
            <p>每一步都来自持久化事实；没有样本时不会把流程动画冒充学习结果。</p>
          </div>
          <button className="text-button" onClick={onRefresh}>刷新状态</button>
        </div>
        <div className="learning-flow">
          {flow.map((step, index) => (
            <div className={step.done ? "done" : "waiting"} key={step.title}>
              <i>{step.done ? <Check size={13} /> : <CircleDot size={12} />}</i>
              <span>{index + 1}</span>
              <div>
                <b>{step.title}</b>
                <small>{step.text}</small>
              </div>
            </div>
          ))}
        </div>
      </section>

      <div className="learning-main-grid">
        <section className="paper-panel learning-evidence-panel">
          <div className="learning-card-heading">
            <div>
              <span>真实学习数据</span>
              <h2>当前积累了什么</h2>
            </div>
            <b>{status.episode_count} Episodes</b>
          </div>
          <div className="learning-label-grid">
            <div><strong>{status.positive_count}</strong><span>正向</span></div>
            <div><strong>{status.negative_count}</strong><span>负向</span></div>
            <div><strong>{status.ambiguous_count}</strong><span>冲突 / 模糊</span></div>
            <div><strong>{status.unlabeled_count}</strong><span>未形成标签</span></div>
          </div>
          <div className="learning-feedback-tags">
            {Object.entries(status.feedback_labels).length ? (
              Object.entries(status.feedback_labels).map(([label, count]) => (
                <span key={label}>
                  {feedbackLabelCopy[label] || label} · {count}
                </span>
              ))
            ) : (
              <p>当前项目还没有真实反馈。可以在 Change Detail 中标记“有用 / 不相关 / 太泛”。</p>
            )}
          </div>
          {status.orphan_feedback_count > 0 && (
            <p className="learning-warning">
              {status.orphan_feedback_count} 条旧反馈没有绑定 frozen Change，不会被当作强校准证据。
            </p>
          )}
        </section>

        {replay ? (
          <ReplayComparison replay={replay} />
        ) : (
          <section className="paper-panel learning-waiting-card">
            <Workflow size={24} />
            <h2>还没有可以展示的历史回放</h2>
            <p>
              {status.labels_needed > 0
                ? `还需要 ${status.labels_needed} 个有效标注，才达到默认 replay evidence floor。`
                : "样本已经达到门槛；下一步是生成候选并在冻结 Episode 上做 shadow replay。"}
            </p>
          </section>
        )}
      </div>

      <section className="paper-panel learning-candidate-panel">
        <div className="learning-card-heading">
          <div>
            <span>Candidate Policy</span>
            <h2>候选改进与推广边界</h2>
          </div>
          <b>{status.candidate?.proposal_id || "暂无候选"}</b>
        </div>
        {status.candidate ? (
          <>
            {(status.candidate.reason || status.candidate.expected_effect) && (
              <div className="learning-candidate-copy">
                {status.candidate.reason && <p><b>为什么提出：</b>{status.candidate.reason}</p>}
                {status.candidate.expected_effect && <p><b>预期效果：</b>{status.candidate.expected_effect}</p>}
              </div>
            )}
            {(status.candidate.changed_keywords.length > 0 || status.candidate.changed_sources.length > 0) && (
              <div className="learning-change-tags">
                {status.candidate.changed_keywords.map((item) => <span key={`k-${item}`}>关键词 · {item}</span>)}
                {status.candidate.changed_sources.map((item) => <span key={`s-${item}`}>来源 · {item}</span>)}
              </div>
            )}
            {status.candidate.policy_changes.length > 0 && (
              <div className="learning-policy-diff">
                <div className="learning-policy-row header"><span>策略项</span><span>当前</span><span>候选</span></div>
                {status.candidate.policy_changes.map((item) => (
                  <div className="learning-policy-row" key={item.path}>
                    <code>{item.path}</code>
                    <span>{valueText(item.old)}</span>
                    <span>{valueText(item.new)}</span>
                  </div>
                ))}
              </div>
            )}
            <div className="learning-approval-note">
              <ShieldCheck size={18} />
              <p>
                即使候选回放通过，也不会自动应用。真实 policy apply 仍要求 durable calibration gate + 显式人工批准；高风险变更会继续被额外阻断。
              </p>
            </div>
          </>
        ) : (
          <p className="learning-muted">
            当前没有候选策略。SignalHarness 不会为了展示“自进化”而凭空生成一次改动。
          </p>
        )}
      </section>

      <div className="learning-history-grid">
        <section className="paper-panel">
          <div className="learning-card-heading">
            <div><span>Review queue</span><h2>待审候选</h2></div>
            <b>{status.staged_proposals.length}</b>
          </div>
          {status.staged_proposals.length ? (
            <div className="learning-history-list">
              {status.staged_proposals.slice().reverse().map((item) => (
                <article key={item.proposal_id}>
                  <div><strong>{item.proposal_id}</strong><span>{item.status}</span></div>
                  <p>{item.risk.risk_level} risk · replay {item.risk.replay_gate_passed ? "passed" : "not passed"}</p>
                  {item.risk.reasons[0] && <small>{item.risk.reasons[0]}</small>}
                </article>
              ))}
            </div>
          ) : <p className="learning-muted">暂无进入 staging 的候选。</p>}
        </section>
        <section className="paper-panel">
          <div className="learning-card-heading">
            <div><span>Policy history</span><h2>版本与回滚记录</h2></div>
            <History size={18} />
          </div>
          {status.policy_revisions.length ? (
            <div className="learning-history-list">
              {status.policy_revisions.map((item) => (
                <article key={item.revision_id}>
                  <div><strong>{item.revision_id}</strong><span>{item.rolled_back_at ? "已回滚" : "已应用"}</span></div>
                  <p>proposal · {item.proposal_id || "unknown"}</p>
                  <small>{item.created_at || "未记录时间"}</small>
                </article>
              ))}
            </div>
          ) : <p className="learning-muted">当前项目还没有真正应用过策略 revision。</p>}
        </section>
      </div>
      {error && <p className="learning-warning">{error}</p>}
    </div>
  );
}
