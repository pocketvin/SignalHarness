import { useCallback, useEffect, useState } from "react";
import { CalendarClock, Plus, Trash2 } from "lucide-react";
import { dateText, projectBase, request } from "./api";
interface Schedule {
  schedule_id: string;
  cadence: string;
  local_time: string | null;
  timezone: string;
  enabled: boolean;
  next_run_at: string;
  last_status: string;
}
export function EnvironmentMonitoring({ projectId }: { projectId: string }) {
  const [items, setItems] = useState<Schedule[]>([]);
  const [cadence, setCadence] = useState("24h");
  const [time, setTime] = useState("09:00");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const timezone =
    Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai";
  const load = useCallback(async () => {
    const response = await request<{ items: Schedule[] }>(
      `${projectBase(projectId)}/schedules`,
    );
    setItems(response.items);
  }, [projectId]);
  useEffect(() => {
    let stopped = false;
    void request<{ items: Schedule[] }>(`${projectBase(projectId)}/schedules`)
      .then((data) => {
        if (!stopped) setItems(data.items);
      })
      .catch((e) => {
        if (!stopped) setStatus(e.message);
      });
    return () => {
      stopped = true;
    };
  }, [projectId]);
  async function create() {
    setBusy(true);
    try {
      await request(`${projectBase(projectId)}/schedules`, "POST", {
        cadence,
        timezone,
        ...(cadence === "daily" ? { local_time: time } : {}),
      });
      await load();
      setStatus(
        "定期检查已开启。按全量理解流程生成报告，不会自动深入核实单条变化。",
      );
    } catch (e) {
      setStatus(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  async function disable(id: string) {
    try {
      await request(
        `${projectBase(projectId)}/schedules/${encodeURIComponent(id)}`,
        "DELETE",
      );
      await load();
      setStatus("该计划已停用。");
    } catch (e) {
      setStatus(e instanceof Error ? e.message : String(e));
    }
  }
  return (
    <section className="paper-panel environment-monitoring">
      <h2>
        <CalendarClock size={20} /> 持续检查环境
      </h2>
      <p className="quiet-note">
        服务运行时按计划更新报告。使用你的本地时区：{timezone}。
      </p>
      <div className="monitoring-controls">
        <label>
          检查频率
          <select value={cadence} onChange={(e) => setCadence(e.target.value)}>
            <option value="12h">每 12 小时</option>
            <option value="24h">每 24 小时</option>
            <option value="daily">每天固定时间</option>
          </select>
        </label>
        {cadence === "daily" && (
          <label>
            本地时间
            <input
              type="time"
              value={time}
              onChange={(e) => setTime(e.target.value)}
            />
          </label>
        )}
        <button
          className="primary-button"
          disabled={busy}
          onClick={() => void create()}
        >
          <Plus size={16} />
          开启定期检查
        </button>
      </div>
      <div>
        {items
          .filter((item) => item.enabled)
          .map((item) => (
            <div className="schedule-row" key={item.schedule_id}>
              <div>
                <b>
                  {item.cadence === "daily"
                    ? `每天 ${item.local_time}`
                    : item.cadence === "12h"
                      ? "每 12 小时"
                      : "每 24 小时"}
                </b>
                <p>下一次：{dateText(item.next_run_at, true)}</p>
              </div>
              <button
                className="icon-button"
                aria-label="停用计划"
                onClick={() => void disable(item.schedule_id)}
              >
                <Trash2 size={16} />
              </button>
            </div>
          ))}
      </div>
      <p className="quiet-note" role="status">
        {status}
      </p>
    </section>
  );
}
