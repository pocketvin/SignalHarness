import { useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Check,
  ChevronRight,
  Compass,
  Layers3,
  LoaderCircle,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  X,
} from "lucide-react";
import { api, dateText } from "./api";
import type { Change, Direction, Progress, WindowMode } from "./types";
import { useEnvironment } from "./useEnvironment";
import { ChangeDetail } from "./Detail";
import { ProjectSettings } from "./Settings";
import { EnvironmentMonitoring } from "./Monitoring";
import "./environment.css";

const states: Record<string, string> = {
  new: "首次观察",
  continuing: "持续关注",
  strengthening: "迹象增强",
  weakening: "迹象减弱",
  uncertain: "有待核实",
};
const windows: Array<[WindowMode, string]> = [
  ["since_last", "上次查看后"],
  ["24h", "最近一天"],
  ["7d", "最近一周"],
  ["30d", "最近一月"],
  ["custom", "自定义范围"],
];

function LiveProgress({
  progress,
  reconnecting,
}: {
  progress: Progress;
  reconnecting: boolean;
}) {
  const steps = [
    ["collecting_sources", "检查来源"],
    ["organizing_changes", "去重整理"],
    ["interpreting_changes", "理解变化"],
    ["forming_directions", "综合方向"],
    ["assembling_report", "保存报告"],
  ];
  const current =
    progress.stage === "complete"
      ? steps.length
      : Math.max(
          0,
          steps.findIndex(([id]) => id === progress.stage),
        );
  return (
    <section className="live-progress" role="status" aria-live="polite">
      <div className="progress-message">
        {current === steps.length ? (
          <Check size={19} />
        ) : (
          <LoaderCircle size={19} className="spinning" />
        )}
        <strong>{progress.message}</strong>
        {progress.stage === "interpreting_changes" &&
          progress.total !== null && (
            <span>
              {progress.completed ?? 0} / {progress.total}
            </span>
          )}
        {reconnecting && (
          <span className="quiet-note">连接恢复中，任务仍在继续</span>
        )}
      </div>
      <div className="progress-steps">
        {steps.map(([key, label], i) => (
          <span
            key={key}
            className={i < current ? "done" : i === current ? "current" : ""}
          >
            <i>{i < current ? <Check size={12} /> : ""}</i>
            {label}
          </span>
        ))}
      </div>
    </section>
  );
}
function ChangeRow({
  item,
  onOpen,
}: {
  item: Change;
  onOpen: (item: Change) => void;
}) {
  return (
    <button
      className="change-row"
      onClick={() => onOpen(item)}
      aria-label={`深入核实：${item.summary}`}
    >
      <div className="change-meta">
        <span>{item.entity}</span>
        <time>{dateText(item.published_at)}</time>
      </div>
      <div className="change-copy">
        <h3>{item.summary}</h3>
        <p>{item.relation_reason || item.what_changed}</p>
      </div>
      <span
        className={`attention ${item.interpretation_status === "unavailable" ? "unknown" : item.attention}`}
      >
        {item.corpus_role === "project_activity"
          ? "项目自身"
          : item.interpretation_status === "unavailable"
            ? "解释待完成"
            : item.attention === "watch"
              ? "值得关注"
              : item.project_relation === "none"
                ? "环境背景"
                : "了解变化"}
      </span>
      <ChevronRight size={17} />
    </button>
  );
}
function DirectionCard({
  item,
  onEvidence,
}: {
  item: Direction;
  onEvidence: (item: Direction) => void;
}) {
  return (
    <article className="direction-card">
      <div className="direction-top">
        <div className="direction-tags">
          <span className={`state-tag ${item.state}`}>
            {states[item.state] || "持续观察"}
          </span>
          <span className={`posture-tag ${item.evidence_posture}`}>
            {item.evidence_posture === "reported_issue"
              ? "问题 / 讨论信号"
              : item.evidence_posture === "mixed"
                ? "混合证据"
                : "已观察变化"}
          </span>
        </div>
        <span>
          {item.supporting_change_ids.length} 个变化 ·{" "}
          {item.independent_source_count} 个来源
        </span>
      </div>
      <h3>{item.title}</h3>
      <p className="direction-explanation">{item.explanation}</p>
      {item.project_connection && (
        <div className="project-connection">
          <span>与你的项目</span>
          <p>{item.project_connection}</p>
        </div>
      )}
      {!!item.watch_next.length && (
        <div className="watch-next">
          <span>接下来注意</span>
          <p>{item.watch_next.join("；")}</p>
        </div>
      )}
      <div className="direction-bottom">
        <button className="text-button" onClick={() => onEvidence(item)}>
          查看支持与反向证据 <ArrowRight size={15} />
        </button>
        <details>
          <summary aria-label="判断边界">判断边界</summary>
          <p>{item.state_reason}</p>
          {item.uncertainty && <p>{item.uncertainty}</p>}
        </details>
      </div>
    </article>
  );
}
export default function Workspace() {
  const sh = useEnvironment();
  const [tab, setTab] = useState<"overview" | "changes" | "settings">(
    "overview",
  );
  const [windowMode, setWindowMode] = useState<WindowMode>("since_last");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [search, setSearch] = useState("");
  const [audit, setAudit] = useState<Record<string, unknown> | null>(null);
  const active = sh.scan?.status === "running" || sh.scan?.status === "queued";
  const project = sh.meta?.projects.find((item) => item.id === sh.projectId);
  const report = sh.report;
  const selectedDirection = report?.directions.find(
    (d) => d.direction_id === sh.filters.directionId,
  );
  function viewEvidence(direction: Direction) {
    setTab("changes");
    setSearch("");
    sh.setFilters({
      view: "all",
      query: "",
      directionId: direction.direction_id,
      offset: 0,
    });
    window.scrollTo({ top: 0, behavior: "instant" });
  }
  function navigate(value: "overview" | "changes" | "settings") {
    setTab(value);
    if (value === "changes")
      sh.setFilters({
        view: "relevant",
        query: "",
        directionId: "",
        offset: 0,
      });
  }
  async function start() {
    if (
      windowMode === "custom" &&
      (!from || !to || new Date(from) >= new Date(to))
    ) {
      sh.setError("请选择有效的开始和结束时间。");
      return;
    }
    await sh.startScan({
      window: windowMode,
      ...(windowMode === "custom"
        ? {
            window_from: new Date(from).toISOString(),
            window_to: new Date(to).toISOString(),
          }
        : {}),
    });
  }
  if (!sh.meta)
    return (
      <div className="env-bootstrap">
        <Compass size={32} />
        <h1>SignalHarness</h1>
        <p role="status">{sh.error || "正在读取项目环境…"}</p>
        {sh.error && (
          <button onClick={() => void sh.refreshMeta()}>重新连接</button>
        )}
      </div>
    );
  return (
    <div className="env-app">
      <a className="env-skip" href="#environment-main">
        跳到主要内容
      </a>
      <aside className="env-sidebar">
        <a
          className="env-brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            navigate("overview");
          }}
        >
          <span>
            <Compass size={25} />
          </span>
          <b>
            SignalHarness<small>项目环境情报</small>
          </b>
        </a>
        <div className="project-switch">
          <label htmlFor="environment-project">当前项目</label>
          <select
            id="environment-project"
            value={sh.projectId}
            onChange={(e) => {
              sh.setProjectId(e.target.value);
              setAudit(null);
              setSearch("");
            }}
          >
            {sh.meta.projects.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </div>
        <nav aria-label="主要导航">
          <button
            aria-current={tab === "overview" ? "page" : undefined}
            onClick={() => navigate("overview")}
          >
            <Compass size={18} />
            环境总览
          </button>
          <button
            aria-current={tab === "changes" ? "page" : undefined}
            onClick={() => navigate("changes")}
          >
            <Layers3 size={18} />
            全部变化{report && <span>{report.counts.relevant}</span>}
          </button>
          <button
            aria-current={tab === "settings" ? "page" : undefined}
            onClick={() => navigate("settings")}
          >
            <Settings2 size={18} />
            项目与关注
          </button>
        </nav>
        <div className="sidebar-context">
          <span>当前观察范围</span>
          <p>{project?.watchlist?.source_count ?? 0} 个已连接来源</p>
          <small>围绕项目依赖、协议与技术生态持续理解变化。</small>
        </div>
        <button
          className="sidebar-connect"
          onClick={() => navigate("settings")}
        >
          <Plus size={16} />
          连接项目
        </button>
      </aside>
      <main id="environment-main" className="env-main" tabIndex={-1}>
        <header className="workspace-header">
          <div>
            <p className="workspace-project">{project?.name}</p>
            <h1>
              {tab === "settings"
                ? "项目与关注重点"
                : tab === "changes"
                  ? "查看每一个变化"
                  : "最近形成了什么方向"}
            </h1>
          </div>
          {tab !== "settings" && (
            <div className="scan-controls">
              <label className="sr-only" htmlFor="environment-window">
                观察时间范围
              </label>
              <select
                id="environment-window"
                value={windowMode}
                onChange={(e) => setWindowMode(e.target.value as WindowMode)}
              >
                {windows.map(([id, label]) => (
                  <option key={id} value={id}>
                    {label}
                  </option>
                ))}
              </select>
              <button
                className="primary-button"
                onClick={() => void start()}
                disabled={active || !sh.meta.intelligence_ready}
              >
                {active ? (
                  <LoaderCircle className="spinning" size={16} />
                ) : (
                  <RefreshCw size={16} />
                )}
                {active ? "正在检查" : "检查最新变化"}
              </button>
            </div>
          )}
        </header>
        {windowMode === "custom" && tab !== "settings" && (
          <div className="custom-window">
            <label>
              从
              <input
                type="datetime-local"
                value={from}
                onChange={(e) => setFrom(e.target.value)}
              />
            </label>
            <label>
              到
              <input
                type="datetime-local"
                value={to}
                onChange={(e) => setTo(e.target.value)}
              />
            </label>
          </div>
        )}
        {!sh.meta.intelligence_ready && (
          <div className="notice">
            分析服务尚未配置可用接口。已有报告仍可阅读；接口配置由服务端管理。
          </div>
        )}
        {sh.error && (
          <div className="notice error" role="alert">
            <span>{sh.error}</span>
            <button
              className="icon-button"
              onClick={() => sh.setError("")}
              aria-label="关闭提示"
            >
              <X size={16} />
            </button>
          </div>
        )}
        {sh.progress && tab !== "settings" && (
          <LiveProgress progress={sh.progress} reconnecting={sh.reconnecting} />
        )}
        {sh.loading ? (
          <div className="loading-area" role="status">
            <LoaderCircle className="spinning" />
            正在读取已保存的环境报告
          </div>
        ) : tab === "settings" ? (
          <>
            <ProjectSettings
              profile={sh.profile}
              onPreference={sh.savePreference}
              onNatural={sh.naturalPreference}
              onConnect={sh.refreshMeta}
            />
            <EnvironmentMonitoring
              key={sh.projectId}
              projectId={sh.projectId}
            />
          </>
        ) : !report ? (
          <section className="empty-environment">
            <span className="empty-symbol">
              <Compass size={38} />
            </span>
            <h2>先看清项目周围，正在发生什么。</h2>
            <p>
              检查已连接的来源，整理每一个独立变化，再把它们合成有证据的环境方向。不会只根据几条重点信息替你判断整体。
            </p>
            <div className="empty-flow">
              <span>整理所有变化</span>
              <ChevronRight size={16} />
              <span>形成环境方向</span>
              <ChevronRight size={16} />
              <span>按需深入核实</span>
            </div>
            <button
              className="primary-button"
              onClick={() => void start()}
              disabled={active || !sh.meta.intelligence_ready}
            >
              开始第一次环境检查
            </button>
            <button
              className="text-button"
              onClick={() => navigate("settings")}
            >
              先查看项目与关注重点
            </button>
            <small>
              首次“上次查看后”默认检查最近 7
              天。旧版单条分析结果不会冒充全量方向报告。
            </small>
          </section>
        ) : (
          <>
            <div className="report-scope">
              <div>
                <span>
                  {dateText(report.window.from)} — {dateText(report.window.to)}
                </span>
                <p>
                  {report.counts.observations} 条观察，整理为{" "}
                  <strong>
                    {report.counts.external_changes ?? report.counts.changes}
                  </strong>{" "}
                  个外部环境变化，其中 <strong>{report.counts.relevant}</strong>{" "}
                  个与项目有关。
                  {(report.counts.project_activity ?? 0) > 0 && (
                    <>
                      {" "}
                      另参考 <strong>
                        {report.counts.project_activity}
                      </strong>{" "}
                      个项目自身变化。
                    </>
                  )}
                </p>
              </div>
              <label>
                历史报告
                <select
                  aria-label="历史报告"
                  value={report.scan_id}
                  onChange={(e) => {
                    setAudit(null);
                    void sh.chooseReport(e.target.value);
                  }}
                >
                  {sh.history.map((item) => (
                    <option key={item.scan_id} value={item.scan_id}>
                      {dateText(item.created_at, true)}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {!/^environment-v1\.[3-9]|^environment-v[2-9]/.test(
              report.contract_version || "",
            ) && (
              <div className="notice">
                这是旧分析规则生成的历史报告；方向可能尚未区分项目自身活动，也未应用当前的方向重叠与时间事实校验。
              </div>
            )}
            {report.notices.map((notice, i) => (
              <div className="notice" key={i}>
                {notice}
              </div>
            ))}
            {tab === "overview" ? (
              <>
                <section className="directions-section">
                  <div className="section-heading">
                    <h2>本期值得关注的方向</h2>
                    <span>只由外部环境变化形成；项目自身活动仅作关联背景</span>
                  </div>
                  {report.directions.length ? (
                    <div className="direction-grid">
                      {report.directions.map((item) => (
                        <DirectionCard
                          item={item}
                          onEvidence={viewEvidence}
                          key={item.direction_id}
                        />
                      ))}
                    </div>
                  ) : (
                    <div className="paper-panel no-directions">
                      <Compass size={24} />
                      <h3>
                        {report.status === "degraded"
                          ? "本期方向仍待形成"
                          : (report.counts.external_changes ??
                                report.counts.changes) === 0
                            ? "本期没有新的外部环境变化"
                            : "暂时没有足够证据支持新的方向"}
                      </h3>
                      <p>
                        {report.status === "degraded"
                          ? "已取得的变化与证据仍保留在列表中，分析失败不代表没有重要变化。"
                          : (report.counts.external_changes ??
                                report.counts.changes) === 0
                            ? "项目自身活动仍保留在单独列表，但不会被拿来制造外部趋势。"
                            : "这不等于没有变化。系统不会为了凑数，把零散消息写成趋势。"}
                      </p>
                      <button
                        className="text-button"
                        onClick={() => navigate("changes")}
                      >
                        查看已经整理的变化
                      </button>
                    </div>
                  )}
                </section>
                <div className="brief-layout">
                  <section className="paper-panel environment-brief">
                    <div className="section-heading">
                      <h2>最近整体发生了什么</h2>
                      <BookOpen size={19} />
                    </div>
                    {report.brief.length ? (
                      report.brief.map((item, i) => (
                        <div key={i}>
                          <p className="brief-paragraph">{item.text}</p>
                          <details className="claim-evidence">
                            <summary>
                              这段判断的依据 ·{" "}
                              {item.supporting_change_ids.length} 个变化
                            </summary>
                            <div>
                              {item.supporting_change_ids.map(
                                (id, evidenceIndex) => (
                                  <button
                                    key={id}
                                    className="text-button"
                                    onClick={() =>
                                      void api
                                        .change(
                                          sh.projectId,
                                          report.scan_id,
                                          id,
                                        )
                                        .then((item) => sh.openChange(item))
                                        .catch((e) => sh.setError(e.message))
                                    }
                                  >
                                    查看证据变化 {evidenceIndex + 1}
                                  </button>
                                ),
                              )}
                            </div>
                          </details>
                        </div>
                      ))
                    ) : (
                      <p className="quiet-note">
                        {(report.counts.external_changes ??
                        report.counts.changes)
                          ? "全局综合未完成，暂不展示推测性总结。"
                          : "本期没有取得新的外部环境变化；项目自身活动不会替代环境总结。"}
                      </p>
                    )}
                    {!!report.opportunities.length && (
                      <div className="brief-callout opportunity">
                        <h3>值得观察的机会</h3>
                        {report.opportunities.map((item, i) => (
                          <p key={i}>{item.text}</p>
                        ))}
                      </div>
                    )}
                  </section>
                  <aside className="source-panel">
                    <h2>观察范围</h2>
                    <p>
                      {report.coverage_status === "complete"
                        ? "已连接来源覆盖完整"
                        : "部分来源有覆盖缺口"}
                    </p>
                    <div className="source-list">
                      {report.sources.map((source, i) => (
                        <div key={`${source.source_name}-${i}`}>
                          <span
                            className={
                              source.coverage_status === "complete" &&
                              source.status === "success"
                                ? "source-dot ready"
                                : "source-dot"
                            }
                          />
                          <b title={source.source_name}>{source.source_name}</b>
                          <span>{source.output_count}</span>
                        </div>
                      ))}
                    </div>
                    <small>
                      这里统计取得的观察条目，不把同一发布的重复报道当作多次变化。
                    </small>
                  </aside>
                </div>
                <section className="featured-section">
                  <div className="section-heading">
                    <div>
                      <h2>建议先看这几条</h2>
                      <p>
                        仅改变阅读顺序。点击一条变化，才深入核对证据与项目影响。
                      </p>
                    </div>
                    <button
                      className="text-button"
                      onClick={() => navigate("changes")}
                    >
                      全部变化 <ArrowRight size={16} />
                    </button>
                  </div>
                  <div className="change-list">
                    {report.featured.length ? (
                      report.featured.map((item) => (
                        <ChangeRow
                          item={item}
                          key={item.change_id}
                          onOpen={(item) => void sh.openChange(item)}
                        />
                      ))
                    ) : (
                      <p className="empty-list">
                        暂无可优先推荐的条目，仍可查看完整变化集。
                      </p>
                    )}
                  </div>
                </section>
              </>
            ) : (
              <section className="all-changes-section">
                {selectedDirection && (
                  <div className="direction-filter">
                    <button
                      className="icon-button"
                      onClick={() => {
                        setTab("overview");
                        sh.setFilters({ ...sh.filters, directionId: "" });
                      }}
                      aria-label="返回方向"
                    >
                      <ArrowLeft size={18} />
                    </button>
                    <div>
                      <span>这个方向的支持与反向证据</span>
                      <h2>{selectedDirection.title}</h2>
                    </div>
                  </div>
                )}
                <div className="changes-toolbar">
                  <div className="view-tabs" role="group" aria-label="变化范围">
                    {[
                      ["relevant", "与项目有关"],
                      ["all", "全部环境变化"],
                      ...((report.counts.project_activity ?? 0) > 0
                        ? [["activity", "项目自身"]]
                        : []),
                      ["unavailable", "解释待完成"],
                    ].map(([value, label]) => (
                      <button
                        key={value}
                        aria-pressed={
                          sh.filters.view === value && !sh.filters.directionId
                        }
                        onClick={() =>
                          sh.setFilters({
                            ...sh.filters,
                            view: value,
                            directionId: "",
                            offset: 0,
                          })
                        }
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                  <form
                    className="change-search"
                    onSubmit={(e) => {
                      e.preventDefault();
                      sh.setFilters({
                        ...sh.filters,
                        query: search,
                        offset: 0,
                      });
                    }}
                  >
                    <Search size={17} />
                    <input
                      aria-label="搜索变化"
                      placeholder="搜索变化、主体或主题"
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                    />
                    <button type="submit">搜索</button>
                  </form>
                </div>
                <p className="list-intro">
                  每条变化先提供轻量解释。点击条目，才开始深入核实。
                  {sh.pageLoading && <span> 正在加载…</span>}
                </p>
                <div className="change-list" aria-busy={sh.pageLoading}>
                  {sh.page?.items.map((item) => (
                    <ChangeRow
                      item={item}
                      key={item.change_id}
                      onOpen={(item) => void sh.openChange(item)}
                    />
                  ))}
                  {sh.page && !sh.page.items.length && (
                    <p className="empty-list">
                      当前范围内没有匹配的变化。可以切换范围或修改搜索词。
                    </p>
                  )}
                </div>
                {sh.page && (
                  <div className="pagination">
                    <button
                      disabled={sh.page.offset === 0 || sh.pageLoading}
                      onClick={() =>
                        sh.setFilters({
                          ...sh.filters,
                          offset: Math.max(0, sh.page!.offset - sh.page!.limit),
                        })
                      }
                    >
                      <ArrowLeft size={15} />
                      上一页
                    </button>
                    <span>
                      {sh.page.count ? sh.page.offset + 1 : 0}–
                      {sh.page.offset + sh.page.items.length} / {sh.page.count}
                    </span>
                    <button
                      disabled={!sh.page.has_more || sh.pageLoading}
                      onClick={() =>
                        sh.setFilters({
                          ...sh.filters,
                          offset: sh.page!.offset + sh.page!.limit,
                        })
                      }
                    >
                      下一页
                      <ArrowRight size={15} />
                    </button>
                  </div>
                )}
              </section>
            )}
            <footer className="report-footer">
              <span>
                <ShieldCheck size={15} />
                AI 综合基于已保存的来源。方向是有证据的判断，不是确定的预测。
              </span>
              <details
                onToggle={(event) => {
                  if (event.currentTarget.open && !audit)
                    void api
                      .audit(sh.projectId, report.scan_id)
                      .then(setAudit)
                      .catch((e) => sh.setError(e.message));
                }}
              >
                <summary>技术审计</summary>
                <pre>
                  {audit ? JSON.stringify(audit, null, 2) : "正在读取审计记录…"}
                </pre>
              </details>
            </footer>
          </>
        )}
      </main>
      <ChangeDetail
        change={sh.detail}
        deep={sh.deep}
        error={sh.detailError}
        projectId={sh.projectId}
        scanId={report?.scan_id || ""}
        onClose={sh.closeDetail}
        onRetry={() => {
          if (sh.detail) void sh.openChange(sh.detail, true);
        }}
      />
    </div>
  );
}
