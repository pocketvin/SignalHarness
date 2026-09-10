const DIMENSIONS = {
  factual_grounding: ["事实依据", "是否忠于事件与证据，不夸大、不编造"],
  technical_explanation: ["技术解释", "有没有讲清楚实际发生的技术变化"],
  project_specificity: ["项目针对性", "是否真正结合当前项目而不是泛泛而谈"],
  uncertainty_calibration: ["不确定性", "证据不足、proposal、冲突来源是否表达得恰当"],
  actionability: ["行动建议", "建议是否具体、可执行、不过度反应"],
  natural_language: ["自然语言", "是否像正常工程师在解释，而不是字段拼接"],
  no_internal_leakage: ["无内部泄漏", "是否避免 score/category/fallback 等内部调试话术"],
};

const OVERALL = [
  ["A", "A 更好"], ["tie", "差不多"], ["B", "B 更好"], ["invalid", "样本无效"],
];
const DIM_CHOICES = [
  ["A", "A"], ["tie", "持平"], ["B", "B"], ["not_applicable", "不适用"],
];

const state = { data: null, index: 0 };
const $ = (id) => document.getElementById(id);

function setText(id, text) { $(id).textContent = text ?? ""; }
function renderList(id, items) {
  const root = $(id); root.replaceChildren();
  for (const item of items || []) {
    const li = document.createElement("li"); li.textContent = item; root.appendChild(li);
  }
}
function choiceButton(value, label, selected, onClick) {
  const button = document.createElement("button");
  button.type = "button"; button.className = "choice-button" + (selected ? " selected" : "");
  button.textContent = label; button.addEventListener("click", onClick); return button;
}
function currentPair() { return state.data?.pairs?.[state.index] ?? null; }

function renderStatus() {
  const status = state.data.status;
  const total = status.pair_count || 0; const labeled = status.labeled_pairs || 0;
  setText("progressText", `${labeled} / ${total} 已完成`);
  $("progressBar").style.width = total ? `${(labeled / total) * 100}%` : "0%";
  setText("eligibilityText", state.data.calibration_eligible
    ? `真实 ${state.data.source_provider} · ${state.data.source_model}；至少 ${state.data.minimum_labeled_pairs} 条后可进入 Judge 校准实验。`
    : "当前数据不具备生产 Judge 校准资格。"
  );
}

function renderOverall(pair) {
  const root = $("overallChoices"); root.replaceChildren();
  for (const [value, label] of OVERALL) {
    root.appendChild(choiceButton(value, label, pair.human_preference === value, () => {
      pair.human_preference = value; renderOverall(pair); setText("saveMessage", "尚未保存");
    }));
  }
}

function renderDimensions(pair) {
  const root = $("dimensionRows"); root.replaceChildren();
  for (const dimension of state.data.rubric_dimensions) {
    const row = document.createElement("div"); row.className = "dimension-row";
    const name = document.createElement("div"); name.className = "dimension-name";
    const strong = document.createElement("strong");
    const hint = document.createElement("span");
    const [title, description] = DIMENSIONS[dimension] || [dimension, ""];
    strong.textContent = title; hint.textContent = description; name.append(strong, hint);
    const choices = document.createElement("div"); choices.className = "dimension-choices";
    for (const [value, label] of DIM_CHOICES) {
      choices.appendChild(choiceButton(value, label, pair.dimension_preferences[dimension] === value, () => {
        pair.dimension_preferences[dimension] = value; renderDimensions(pair); setText("saveMessage", "尚未保存");
      }));
    }
    row.append(name, choices); root.appendChild(row);
  }
}

function renderPair() {
  const pair = currentPair(); if (!pair) return;
  setText("caseId", pair.case_id); setText("caseTitle", pair.case_title);
  setText("truthStatus", pair.truth_status);
  setText("sourceMeta", `${pair.context.source_name} · ${pair.context.source_type}`);
  setText("evidenceSummary", pair.context.evidence_summary);
  setText("eventContent", `${pair.context.event_title}\n\n${pair.context.event_content}`);
  const uncertainty = [pair.context.evidence_uncertainty, ...(pair.context.unsupported_claims || [])].filter(Boolean);
  $("uncertaintyBox").hidden = uncertainty.length === 0;
  setText("uncertaintyBox", uncertainty.length ? `证据边界：${uncertainty.join("；")}` : "");
  setText("aWhat", pair.A.what_changed_zh); setText("aWhy", pair.A.why_relevant_zh); renderList("aActions", pair.A.recommended_actions_zh);
  setText("bWhat", pair.B.what_changed_zh); setText("bWhy", pair.B.why_relevant_zh); renderList("bActions", pair.B.recommended_actions_zh);
  $("humanNote").value = pair.human_note || "";
  renderOverall(pair); renderDimensions(pair);
  $("prevButton").disabled = state.index === 0;
  $("nextButton").disabled = state.index === state.data.pairs.length - 1;
  $("pairSelect").value = String(state.index);
  setText("saveMessage", pair.human_preference ? "这条已有保存记录，可修改后再次保存。" : "请选择总体偏好和全部维度。" );
}

function populateSelect() {
  const select = $("pairSelect"); select.replaceChildren();
  state.data.pairs.forEach((pair, index) => {
    const option = document.createElement("option"); option.value = String(index);
    option.textContent = `${index + 1} / ${state.data.pairs.length}${pair.human_preference ? " · 已标" : ""}`;
    select.appendChild(option);
  });
}

function go(delta) {
  state.index = Math.max(0, Math.min(state.data.pairs.length - 1, state.index + delta)); renderPair();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function saveCurrent() {
  const pair = currentPair();
  pair.human_note = $("humanNote").value.trim();
  const missingDimensions = state.data.rubric_dimensions.filter((d) => !pair.dimension_preferences[d]);
  if (!pair.human_preference || missingDimensions.length) {
    setText("saveMessage", `还没完成：${!pair.human_preference ? "总体偏好 " : ""}${missingDimensions.length ? `${missingDimensions.length} 个维度` : ""}`);
    return;
  }
  $("saveButton").disabled = true; setText("saveMessage", "保存中…");
  try {
    const response = await fetch(`/eval/narrative/pairs/${encodeURIComponent(pair.pair_id)}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        human_preference: pair.human_preference,
        dimension_preferences: pair.dimension_preferences,
        human_note: pair.human_note,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "保存失败");
    state.data.status = payload.status; renderStatus(); populateSelect();
    setText("saveMessage", "已保存。");
    const nextUnlabeled = state.data.pairs.findIndex((item, idx) => idx > state.index && !item.human_preference);
    if (nextUnlabeled >= 0) { state.index = nextUnlabeled; renderPair(); }
  } catch (error) {
    setText("saveMessage", error.message || String(error));
  } finally { $("saveButton").disabled = false; }
}

async function init() {
  try {
    const response = await fetch("/eval/narrative/data");
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "无法加载评测数据");
    state.data = payload;
    $("reviewPanel").hidden = false; renderStatus(); populateSelect();
    const firstUnlabeled = payload.pairs.findIndex((pair) => !pair.human_preference);
    state.index = firstUnlabeled >= 0 ? firstUnlabeled : 0; renderPair();
  } catch (error) {
    $("emptyState").hidden = false; setText("emptyMessage", error.message || String(error));
  }
}

$("prevButton").addEventListener("click", () => go(-1));
$("nextButton").addEventListener("click", () => go(1));
$("pairSelect").addEventListener("change", (event) => { state.index = Number(event.target.value); renderPair(); });
$("saveButton").addEventListener("click", saveCurrent);
init();
