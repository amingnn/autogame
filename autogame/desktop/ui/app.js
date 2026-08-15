const stateLabels = {
  disabled: "已禁用",
  cooldown: "冷却中",
  pending: "等待执行",
  starting: "启动中",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  timed_out: "已超时",
};

const stateClasses = {
  disabled: "status-gray",
  cooldown: "status-yellow",
  pending: "status-blue",
  starting: "status-yellow",
  running: "status-blue",
  completed: "status-green",
  failed: "status-red",
  timed_out: "status-red",
};

const sectionTitles = { tasks: "任务", logs: "日志", settings: "设置" };
let currentState = null;
let currentTaskName = null;

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>\"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[char]));
}

function formatTime(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function formatDuration(value) {
  if (value === null || value === undefined) return "—";
  if (value < 60) return `${value.toFixed(1)} 秒`;
  return `${Math.floor(value / 60)} 分 ${(value % 60).toFixed(0)} 秒`;
}

function showToast(message, error = false) {
  const box = document.getElementById("toast");
  box.textContent = message;
  box.style.borderColor = error ? "#a94d4d" : "#3d6d9e";
  box.classList.add("visible");
  setTimeout(() => box.classList.remove("visible"), 2600);
}

async function callDesktop(method, ...args) {
  if (!window.pywebview?.api?.[method]) throw new Error("桌面通信接口尚未就绪");
  const result = await window.pywebview.api[method](...args);
  if (!result?.ok) throw new Error(result?.error || "桌面操作失败");
  return result;
}

function renderStatusBadge(task) {
  const state = task.state || "pending";
  return `<span class="status-badge ${stateClasses[state] || "status-gray"}">${stateLabels[state] || state}</span>`;
}

function renderSummary() {
  const tasks = currentState?.tasks || [];
  const running = tasks.filter((task) => ["starting", "running"].includes(task.state)).length;
  document.getElementById("taskCount").textContent = tasks.filter((task) => task.enabled).length;
  document.getElementById("runningCount").textContent = running;
  document.getElementById("overallProgress").textContent = `${currentState?.progress?.percent || 0}%`;
  document.getElementById("updatedAt").textContent = `更新于 ${formatTime(currentState?.generated_at)}`;
}

function renderTaskTable() {
  const body = document.getElementById("taskTable");
  const tasks = currentState?.tasks || [];
  body.innerHTML = tasks.map((task) => {
    const selected = task.name === currentTaskName ? "selected" : "";
    const progress = ["completed", "cooldown"].includes(task.state) ? 100 : ["starting", "running"].includes(task.state) ? 50 : 0;
    return `<tr class="${selected}" data-task="${escapeHtml(task.name)}">
      <td><strong>${escapeHtml(task.name)}</strong></td>
      <td>${renderStatusBadge(task)}</td>
      <td><span class="progress-bar"><i style="width:${progress}%"></i></span> ${progress}%</td>
      <td>${formatTime(task.last_success_at)}</td>
      <td><div class="row-actions"><button title="运行" data-action="run" data-task="${escapeHtml(task.name)}">▶</button><button title="强制运行" data-action="force" data-task="${escapeHtml(task.name)}">⚡</button><button title="编辑" data-action="select" data-task="${escapeHtml(task.name)}">✎</button></div></td>
    </tr>`;
  }).join("") || `<tr><td colspan="5" class="empty-state">暂无任务</td></tr>`;
}

function renderScriptForm(task) {
  if (!task.requires_script) {
    return `<div class="info-card"><strong>内置任务</strong><p>${escapeHtml(task.completion_description)}</p></div>`;
  }
  return `<div class="detail-field"><label>脚本 exe 路径</label><input id="scriptPath" value="${escapeHtml(task.script_path)}" placeholder="选择脚本 exe 文件"></div>
    <div class="info-card"><p>${escapeHtml(task.completion_description)}</p><p>进程名、游戏启动路径和日志目录由任务适配器固定管理。</p></div>`;
}

function renderTaskDetails() {
  const task = (currentState?.tasks || []).find((item) => item.name === currentTaskName);
  const title = document.getElementById("detailTitle");
  const content = document.getElementById("detailContent");
  if (!task) {
    title.textContent = "选择任务";
    content.className = "detail-content empty-state";
    content.textContent = "请从左侧选择一个任务";
    return;
  }
  title.textContent = task.name;
  content.className = "detail-content";
  const completionDescription = task.waiting_for_completion ? "正在监听脚本进程和任务日志" : task.completion_description;
  content.innerHTML = `<div class="detail-status">${renderStatusBadge(task)}<p class="empty-state">${completionDescription}<br>耗时：${formatDuration(task.elapsed_seconds)}<br>最近完成：${formatTime(task.last_success_at)}${task.last_error ? `<br>错误：${escapeHtml(task.last_error)}` : ""}</p></div>
    <div class="detail-field"><label>是否启用</label><select id="taskEnabled"><option value="true" ${task.enabled ? "selected" : ""}>启用</option><option value="false" ${!task.enabled ? "selected" : ""}>禁用</option></select></div>
    <div class="detail-field"><label>间隔时间（小时）</label><input id="taskInterval" type="number" min="0" step="0.1" value="${task.interval_hours}"></div>
    ${renderScriptForm(task)}
    <div class="detail-actions"><button class="secondary-button" id="detailRun">▶ 运行</button><button class="primary-button" id="detailSave">保存配置</button></div>`;
  document.getElementById("detailRun").onclick = () => runTask(task.name, false);
  document.getElementById("detailSave").onclick = saveTask;
}

function renderSystemConfig() {
  const system = currentState?.system;
  if (!system) return;
  document.getElementById("systemLogLevel").value = system.log_level;
  document.getElementById("systemDelay").value = system.completion_action_delay_seconds;
  document.getElementById("systemTimeout").value = system.automation_timeout_minutes;
  document.getElementById("systemCompletionAction").value = system.completion_action;
  document.getElementById("systemServerChanEnabled").checked = system.server_chan_enabled;
  document.getElementById("systemSendKey").placeholder = system.server_chan_key_configured ? "已配置，留空保持原值" : "未配置";
}

function renderAll() { renderSummary(); renderTaskTable(); renderTaskDetails(); renderSystemConfig(); }

async function refreshStatus() {
  try {
    currentState = (await callDesktop("get_status")).data;
    document.getElementById("connectionText").textContent = "已连接";
    renderAll();
  } catch (error) {
    document.getElementById("connectionText").textContent = "连接失败";
  }
}

async function runTask(taskName, force) {
  try {
    const result = await callDesktop("run_task", taskName, force);
    showToast(result.message || "操作已提交");
    await refreshStatus();
  } catch (error) { showToast(error.message, true); }
}

async function saveTask() {
  const task = (currentState?.tasks || []).find((item) => item.name === currentTaskName);
  if (!task) return;
  const patch = { enabled: document.getElementById("taskEnabled").value === "true", interval_hours: Number(document.getElementById("taskInterval").value), config_revision: currentState.config_revision };
  if (task.requires_script) patch.script_path = document.getElementById("scriptPath").value.trim();
  try {
    await callDesktop("update_task_config", task.name, patch);
    showToast("任务配置已保存并重新加载");
    await refreshStatus();
  } catch (error) { showToast(error.message, true); }
}

async function saveSystemConfig() {
  const sendKey = document.getElementById("systemSendKey").value.trim();
  const patch = {
    log_level: document.getElementById("systemLogLevel").value,
    completion_action_delay_seconds: Number(document.getElementById("systemDelay").value),
    automation_timeout_minutes: Number(document.getElementById("systemTimeout").value),
    completion_action: document.getElementById("systemCompletionAction").value,
    server_chan_enabled: document.getElementById("systemServerChanEnabled").checked,
    clear_server_chan_key: document.getElementById("clearSendKey").checked,
    config_revision: currentState.config_revision,
  };
  if (sendKey) patch.server_chan_key = sendKey;
  try {
    const result = await callDesktop("update_system_config", patch);
    document.getElementById("systemSendKey").value = "";
    document.getElementById("clearSendKey").checked = false;
    showToast(result.message || "全局配置已保存");
    await refreshStatus();
  } catch (error) { showToast(error.message, true); }
}

async function reloadConfig() {
  try { await callDesktop("reload_config"); showToast("配置已重新加载"); await refreshStatus(); }
  catch (error) { showToast(error.message, true); }
}

async function refreshLogs() {
  try { const result = await callDesktop("get_recent_logs", 100); document.getElementById("logsContent").textContent = result.data.join("\n") || "暂无日志"; }
  catch (error) { showToast(error.message, true); }
}

document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-action]");
  if (target) {
    const name = target.dataset.task;
    if (target.dataset.action === "select") { currentTaskName = name; renderAll(); }
    if (target.dataset.action === "run") runTask(name, false);
    if (target.dataset.action === "force") runTask(name, true);
  }
  const nav = event.target.closest(".nav-item");
  if (nav) {
    document.querySelectorAll(".nav-item").forEach((item) => item.classList.remove("active"));
    nav.classList.add("active");
    const section = nav.dataset.section;
    document.getElementById("tasksSection").classList.toggle("hidden", section !== "tasks");
    document.getElementById("logsSection").classList.toggle("hidden", section !== "logs");
    document.getElementById("settingsSection").classList.toggle("hidden", section !== "settings");
    document.getElementById("pageTitle").textContent = sectionTitles[section] || section;
    if (section === "logs") refreshLogs();
  }
});

document.getElementById("reloadButton").onclick = reloadConfig;
document.getElementById("refreshLogsButton").onclick = refreshLogs;
document.getElementById("saveSystemConfigButton").onclick = saveSystemConfig;
document.getElementById("topRunButton").onclick = () => {
  const first = (currentState?.tasks || []).find((task) => task.enabled && ["pending", "failed"].includes(task.state));
  if (first) runTask(first.name, false); else showToast("当前没有可直接运行的任务");
};
document.getElementById("closeDetails").onclick = () => { currentTaskName = null; renderTaskDetails(); };

document.addEventListener("pywebviewready", () => {
  refreshStatus();
  setInterval(refreshStatus, 2500);
});
