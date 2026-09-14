import { useRef, useState } from "react";
import { FolderOpen, GitBranch, Plus } from "lucide-react";
import { api, request } from "./api";
import type { Profile, Project } from "./types";
const IMPORTANCE = [
  ["critical", "重点关注"],
  ["important", "比较重要"],
  ["normal", "正常关注"],
  ["low", "减少关注"],
  ["ignore", "不再关注"],
];
export function ProjectSettings({
  profile,
  onPreference,
  onNatural,
  onConnect,
  onRefreshArchitecture,
}: {
  profile: Profile | null;
  onPreference: (scope: string, key: string, value: string) => Promise<void>;
  onNatural: (text: string) => Promise<void>;
  onConnect: (id?: string) => Promise<void>;
  onRefreshArchitecture: () => Promise<void>;
}) {
  const [instruction, setInstruction] = useState("");
  const [url, setUrl] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const files = useRef<HTMLInputElement>(null);
  const groups: Array<[string, string, string[]]> = [
    ["provider", "外部服务与接口", profile?.effective_profile.providers || []],
    ["protocol", "协议", profile?.effective_profile.protocols || []],
    ["dependency", "项目依赖", profile?.effective_profile.dependencies || []],
  ];
  async function perform(action: () => Promise<void>, success: string) {
    setBusy(true);
    setStatus("正在保存…");
    try {
      await action();
      setStatus(success);
    } catch (e) {
      setStatus(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  async function connectLocal(selected: FileList | null) {
    if (!selected?.length) return;
    const safe = Array.from(selected).filter(
      (file) =>
        !file.webkitRelativePath
          .split("/")
          .some(
            (part) =>
              part.startsWith(".") ||
              [
                "Private-NoAI",
                "node_modules",
                "work",
                "outputs",
                "vendor",
              ].includes(part),
          ),
    );
    const names = new Set([
      "pyproject.toml",
      "package.json",
      "requirements.txt",
      "requirements-dev.txt",
      "requirements.in",
      "cargo.toml",
      "go.mod",
      "uv.lock",
      "package-lock.json",
    ]);
    const manifests = safe
      .filter(
        (file) => names.has(file.name.toLowerCase()) && file.size <= 200000,
      )
      .slice(0, 12);
    if (!manifests.length) throw new Error("没有找到可读取的项目依赖清单。");
    let total = 0;
    const payload = [];
    for (const file of manifests) {
      if (total + file.size > 500000) break;
      total += file.size;
      payload.push({
        path: file.webkitRelativePath || file.name,
        content: await file.text(),
      });
    }
    const result = await request<{ project: Project }>(
      "/projects/connect",
      "POST",
      {
        name_hint: safe[0]?.webkitRelativePath.split("/")[0],
        manifests: payload,
        paths: safe
          .map((file) => file.webkitRelativePath || file.name)
          .slice(0, 500),
      },
    );
    await onConnect(result.project.id);
  }
  return (
    <div className="settings-layout">
      <section className="paper-panel">
        <h2>项目理解与关注重点</h2>
        <p className="lead-copy">
          {profile?.effective_profile.purpose ||
            profile?.effective_profile.goal ||
            "连接项目后，将从依赖清单和配置建立项目画像。"}
        </p>
        <div className="stack-tags">
          {[
            ...new Set([
              ...(profile?.effective_profile.tech_stack || []),
              ...(profile?.effective_profile.runtimes || []),
            ]),
          ].map((item) => (
            <span key={item}>{item}</span>
          ))}
        </div>
        {profile?.effective_profile.discovery_profile && (
          <section className="discovery-profile-panel">
            <div className="discovery-profile-title">
              <span>系统理解的问题空间</span>
              <b>{profile.effective_profile.discovery_profile.project_domain || "软件项目"}</b>
            </div>
            {!!profile.effective_profile.discovery_profile.problem_spaces?.length && (
              <div>
                <small>正在解决的问题</small>
                <div className="discovery-tags">
                  {profile.effective_profile.discovery_profile.problem_spaces.map((item) => (
                    <span key={`problem-${item}`}>{item}</span>
                  ))}
                </div>
              </div>
            )}
            {!!profile.effective_profile.discovery_profile.solution_categories?.length && (
              <div>
                <small>外部雷达会寻找</small>
                <div className="discovery-tags secondary">
                  {profile.effective_profile.discovery_profile.solution_categories.map((item) => (
                    <span key={`solution-${item}`}>{item}</span>
                  ))}
                </div>
              </div>
            )}
            <p className="quiet-note">
              这决定“项目外部雷达”去哪里找此前未跟踪的新方案，不会给所有项目套同一组 AI 或热门技术主题。
            </p>
          </section>
        )}
        {(profile?.effective_profile.architecture_snapshot ||
          profile?.effective_profile.repository?.provider === "github") && (
          <section className="architecture-profile-panel">
            <div className="discovery-profile-title">
              <span>系统理解的项目结构</span>
              <div className="architecture-title-actions">
                <b>
                  {profile?.effective_profile.architecture_snapshot?.coverage === "source_sampled"
                    ? `已读取 ${profile.effective_profile.architecture_snapshot.source_files_sampled || 0} 个源码样本`
                    : "尚未读取源码结构"}
                </b>
                {profile?.effective_profile.repository?.provider === "github" && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      void perform(onRefreshArchitecture, "项目结构已重新分析并保存。")
                    }
                  >
                    {busy ? "分析中…" : "重新分析项目结构"}
                  </button>
                )}
              </div>
            </div>
            {!!profile?.effective_profile.architecture_snapshot?.subsystems?.length && (
              <div>
                <small>主要子系统</small>
                <div className="architecture-grid">
                  {profile.effective_profile.architecture_snapshot.subsystems.slice(0, 6).map((item) => (
                    <div className="architecture-group" key={item.name}>
                      <strong>{item.name}</strong>
                      {item.paths.slice(0, 3).map((path) => (
                        <code key={`${item.name}-${path}`}>{path}</code>
                      ))}
                    </div>
                  ))}
                </div>
              </div>
            )}
            {!!profile?.effective_profile.architecture_snapshot?.entrypoints?.length && (
              <div>
                <small>入口线索</small>
                <div className="architecture-paths">
                  {profile.effective_profile.architecture_snapshot.entrypoints.slice(0, 6).map((item) => (
                    <code key={item.path}>{item.path}</code>
                  ))}
                </div>
              </div>
            )}
            {!!profile?.effective_profile.architecture_snapshot?.dependency_usage?.length && (
              <div>
                <small>已定位的依赖使用</small>
                <div className="architecture-usage-list">
                  {profile.effective_profile.architecture_snapshot.dependency_usage.slice(0, 8).map((item) => (
                    <div key={item.dependency}>
                      <strong>{item.dependency}</strong>
                      <span>{item.files.slice(0, 3).join(" · ")}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {!!profile?.effective_profile.architecture_snapshot?.static_edges?.length && (
              <div>
                <small>静态 import 线索</small>
                <div className="architecture-edge-list">
                  {profile.effective_profile.architecture_snapshot.static_edges.slice(0, 8).map((item) => (
                    <div key={`${item.from}->${item.to}`}>
                      <code>{item.from}</code>
                      <span>→</span>
                      <code>{item.to}</code>
                    </div>
                  ))}
                </div>
              </div>
            )}
            <p className="quiet-note">
              这里只展示有路径或 import 证据的静态结构；它不是运行时调用图，也不会把未采样文件当成“没有使用”。
            </p>
          </section>
        )}
        <form
          className="preference-form"
          onSubmit={(e) => {
            e.preventDefault();
            void perform(async () => {
              await onNatural(instruction);
              setInstruction("");
            }, "关注重点已保存，将在下一次更新中生效。");
          }}
        >
          <label htmlFor="focus-instruction">直接告诉系统你更关心什么</label>
          <div className="input-action">
            <input
              id="focus-instruction"
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              placeholder="例如：MCP 很重要，不要关注 React"
              maxLength={1000}
            />
            <button type="submit" disabled={busy || !instruction.trim()}>
              保存重点
            </button>
          </div>
        </form>
        {groups.map(
          ([scope, label, items]) =>
            !!items.length && (
              <details
                className="preference-group"
                key={scope}
                open={scope !== "dependency"}
              >
                <summary>
                  {label} <span>{items.length} 项</span>
                </summary>
                {items.map((key) => {
                  const preference = profile?.preferences?.find(
                    (p) =>
                      p.active &&
                      p.scope_type === scope &&
                      p.scope_key.toLowerCase() === key.toLowerCase(),
                  );
                  return (
                    <label className="preference-row" key={key}>
                      <span>{key}</span>
                      <select
                        aria-label={`${key}关注程度`}
                        value={preference?.importance || "normal"}
                        disabled={busy}
                        onChange={(e) =>
                          void perform(
                            () => onPreference(scope, key, e.target.value),
                            "关注程度已保存，将在下一次更新中生效。",
                          )
                        }
                      >
                        {IMPORTANCE.map(([value, text]) => (
                          <option key={value} value={value}>
                            {text}
                          </option>
                        ))}
                      </select>
                    </label>
                  );
                })}
              </details>
            ),
        )}
        <p className="quiet-note">
          明确设置的关注重点优先于自动推断。已保存的历史报告保留当时的项目画像，不会被悄悄重写。
        </p>
      </section>
      <aside className="paper-panel connect-panel">
        <Plus size={22} />
        <h2>连接另一个项目</h2>
        <p>从 GitHub 仓库或本地依赖清单建立项目画像。</p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void perform(async () => {
              const result = await api.connect(url);
              await onConnect(result.project.id);
              setUrl("");
            }, "项目已连接。");
          }}
        >
          <label htmlFor="repo-url">GitHub 仓库地址</label>
          <input
            id="repo-url"
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://github.com/owner/repo"
            required
          />
          <button type="submit" disabled={busy || !url}>
            <GitBranch size={16} /> 连接仓库
          </button>
        </form>
        <button
          disabled={busy}
          onClick={() => {
            files.current?.setAttribute("webkitdirectory", "");
            files.current?.click();
          }}
        >
          <FolderOpen size={16} /> 选择本地项目
        </button>
        <input
          ref={files}
          type="file"
          multiple
          hidden
          onChange={(e) =>
            void perform(() => connectLocal(e.target.files), "项目画像已建立。")
          }
        />
        <p className="quiet-note">
          本地选择仅上传支持的依赖清单和文件路径，不执行项目代码。浏览器导入不等于授权后端访问整份源码。
        </p>
      </aside>
      <p className="settings-status" role="status">
        {status}
      </p>
    </div>
  );
}
