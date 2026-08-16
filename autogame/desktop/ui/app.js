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

const taskDisplayNames = {
  skyland_sign: "森空岛签到",
  maa: "MAA",
  maaend: "MaaEnd",
};

const taskIconSources = {
  skyland_sign: "assets/skland-task-icon.svg",
  maa: "assets/maa-logo.png",
  maaend: "assets/maaend-logo.png",
};

const previewState = {
  status: "idle",
  generated_at: new Date().toISOString(),
  config_revision: "preview",
  progress: { completed: 1, total: 3, percent: 33.3 },
  system: {
    log_level: "INFO",
    completion_action_delay_seconds: 60,
    automation_timeout_minutes: 40,
    completion_action: "hibernate",
    server_chan_enabled: false,
    server_chan_key_configured: true,
  },
  tasks: [
    {
      name: "skyland_sign",
      state: "completed",
      enabled: true,
      interval_hours: 20,
      script_path: "",
      requires_script: false,
      completion_description: "执行森空岛多账号签到并汇总结果。",
      waiting_for_completion: false,
      elapsed_seconds: 8.4,
      last_success_at: new Date(Date.now() - 3600000).toISOString(),
      last_error: null,
    },
    {
      name: "maa",
      state: "pending",
      enabled: true,
      interval_hours: 3,
      script_path: "D:\\game\\MAA\\MAA.exe",
      requires_script: true,
      completion_description: "启动 MAA，监听任务日志并等待脚本完成。",
      waiting_for_completion: false,
      elapsed_seconds: null,
      last_success_at: null,
      last_error: null,
    },
    {
      name: "maaend",
      state: "cooldown",
      enabled: true,
      interval_hours: 20,
      script_path: "D:\\game\\MaaEnd\\MaaEnd.exe",
      requires_script: true,
      completion_description: "启动 MaaEnd 并监听终末地任务日志。",
      waiting_for_completion: false,
      elapsed_seconds: null,
      last_success_at: new Date(Date.now() - 7200000).toISOString(),
      last_error: null,
    },
  ],
};

const previewLogs = [
  "2026-08-16 20:49:21.120 | INFO     | autogame | AutoGame 桌面控制台已启动",
  "2026-08-16 20:49:21.138 | INFO     | autogame | 正在等待任务操作……",
  "2026-08-16 20:49:21.142 | DEBUG    | autogame | 预览模式：这里会显示实时任务日志",
];

let currentState = null;
let currentTaskName = null;
let previewMode = false;
let refreshTimer = null;
let batchRunActive = false;
let logsCleared = false;

function hasDesktopApi() {
  return Boolean(window.pywebview?.api);
}

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
  box.style.borderColor = error ? "#b66c67" : "#8d7769";
  box.classList.add("visible");
  setTimeout(() => box.classList.remove("visible"), 2600);
}

async function callDesktop(method, ...args) {
  if (!hasDesktopApi()) throw new Error("当前为预览模式，桌面通信接口尚未连接");
  const result = await window.pywebview.api[method](...args);
  if (!result?.ok) throw new Error(result?.error || "桌面操作失败");
  return result;
}

function taskDisplayName(name) {
  return taskDisplayNames[name] || name;
}

function renderTaskIcon(name) {
  const source = taskIconSources[name];
  if (!source) return `<span>${escapeHtml(String(name).slice(0, 1).toUpperCase())}</span>`;
  return `<img class="task-icon-image" src="${source}" alt="">`;
}

function renderStatusBadge(task) {
  const state = task.state || "pending";
  return `<span class="status-badge ${stateClasses[state] || "status-gray"}">${stateLabels[state] || state}</span>`;
}

function renderSummary() {
  const tasks = currentState?.tasks || [];
  const running = tasks.filter((task) => ["starting", "running"].includes(task.state)).length;
  if (running === 0 && currentState?.status !== "running") batchRunActive = false;
  document.getElementById("taskCount").textContent = tasks.filter((task) => task.enabled).length;
  document.getElementById("runningCount").textContent = running;
  document.getElementById("overallProgress").textContent = `${currentState?.progress?.percent || 0}%`;
  document.getElementById("updatedAt").textContent = `更新于 ${formatTime(currentState?.generated_at)}`;
  renderBatchButton();
}

function renderBatchButton() {
  const button = document.getElementById("topRunButton");
  if (!button) return;
  const stopping = batchRunActive;
  button.textContent = stopping ? "■ 全部停止" : "▶ 全部开始";
  button.classList.toggle("primary-button", !stopping);
  button.classList.toggle("stop-button", stopping);
  button.title = stopping ? "停止全部运行中的任务" : "强制开始全部启用任务（忽略冷却）";
}

function renderTaskTable() {
  const body = document.getElementById("taskTable");
  const tasks = currentState?.tasks || [];
  body.innerHTML = tasks.map((task) => {
    const selected = task.name === currentTaskName ? "selected" : "";
    return `<div class="task-item ${selected}" data-action="select" data-task="${escapeHtml(task.name)}" role="button" tabindex="0">
      <div class="task-item-main">
        <span class="task-icon">${renderTaskIcon(task.name)}</span>
        <div class="task-item-copy"><strong>${escapeHtml(taskDisplayName(task.name))}</strong></div>
      </div>
      <div class="task-item-side">
        ${renderStatusBadge(task)}
      </div>
    </div>`;
  }).join("") || `<div class="detail-empty">暂无任务</div>`;
}

function renderScriptForm(task) {
  if (!task.requires_script) {
    return "";
  }
  return `<div class="detail-field"><label>脚本 exe 路径</label><input id="scriptPath" value="${escapeHtml(task.script_path)}" placeholder="选择脚本 exe 文件"></div>`;
}

function renderTaskDetails() {
  const task = (currentState?.tasks || []).find((item) => item.name === currentTaskName);
  const title = document.getElementById("detailTitle");
  const content = document.getElementById("detailContent");
  if (!task) {
    title.textContent = "选择任务";
    content.className = "detail-content detail-empty";
    content.textContent = "请从左侧选择一个任务";
    return;
  }

  title.textContent = taskDisplayName(task.name);
  content.className = "detail-content";
  const completionDescription = task.waiting_for_completion ? "正在监听脚本进程和任务日志" : task.completion_description;
  const isRunning = ["starting", "running"].includes(task.state);
  content.innerHTML = `<div class="detail-overview">
      <div><span class="eyebrow">运行状态</span><p>${escapeHtml(completionDescription)}<br>耗时：${formatDuration(task.elapsed_seconds)}<br>最近完成：${formatTime(task.last_success_at)}${task.last_error ? `<br>错误：${escapeHtml(task.last_error)}` : ""}</p></div>
      ${renderStatusBadge(task)}
    </div>
    <div class="detail-field"><label>是否启用</label><select id="taskEnabled"><option value="true" ${task.enabled ? "selected" : ""}>启用</option><option value="false" ${!task.enabled ? "selected" : ""}>禁用</option></select></div>
    <div class="detail-field"><label>间隔时间（小时）</label><input id="taskInterval" type="number" min="0" step="0.1" value="${task.interval_hours}"></div>
    ${renderScriptForm(task)}
    <div class="detail-save-row"><button class="primary-button" id="detailSave">保存配置</button></div>
    <div class="detail-actions"><div class="detail-run-actions"><button class="secondary-button" id="detailForceRun" ${isRunning ? "disabled" : ""}>⚡ 强制运行</button><button class="secondary-button" id="detailRun">${isRunning ? "⏸ 暂停" : "▶ 运行"}</button></div></div>`;
  document.getElementById("detailRun").onclick = () => (isRunning ? stopTask(task.name) : runTask(task.name, false));
  document.getElementById("detailForceRun").onclick = () => runTask(task.name, true);
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

function renderAll() {
  renderSummary();
  renderTaskTable();
  renderTaskDetails();
  renderSystemConfig();
}

function applyPreviewState() {
  previewMode = true;
  currentState = previewState;
  currentTaskName = currentTaskName || "maa";
  document.getElementById("connectionText").textContent = "预览模式";
  if (!logsCleared) renderLogs([]);
  renderAll();
}

async function refreshStatus() {
  if (!hasDesktopApi()) {
    applyPreviewState();
    return;
  }
  try {
    currentState = (await callDesktop("get_status")).data;
    previewMode = false;
    document.getElementById("connectionText").textContent = "已连接";
    if (!currentTaskName || !currentState.tasks.some((task) => task.name === currentTaskName)) {
      currentTaskName = currentState.tasks[0]?.name || null;
    }
    renderAll();
  } catch (error) {
    document.getElementById("connectionText").textContent = "连接失败";
  }
}

async function runTask(taskName, force) {
  if (previewMode) {
    showToast("预览模式不会真正运行任务");
    logsCleared = false;
    renderLogs(previewLogs);
    return;
  }
  try {
    const result = await callDesktop("run_task", taskName, force);
    showToast(result.message || "操作已提交");
    logsCleared = false;
    await loadLogs();
    await refreshStatus();
  } catch (error) { showToast(error.message, true); }
}

async function stopTask(taskName) {
  if (previewMode) {
    showToast("预览模式不会真正停止任务");
    logsCleared = false;
    renderLogs(previewLogs);
    return;
  }
  try {
    const result = await callDesktop("stop_task", taskName);
    showToast(result.message || "任务已暂停");
    logsCleared = false;
    await loadLogs();
    await refreshStatus();
  } catch (error) { showToast(error.message, true); }
}

async function runAllTasks() {
  batchRunActive = true;
  renderBatchButton();
  if (previewMode) {
    showToast("预览模式不会真正运行任务");
    logsCleared = false;
    renderLogs(previewLogs);
    return;
  }
  const tasks = (currentState?.tasks || []).filter((task) => task.enabled);
  if (!tasks.length) {
    batchRunActive = false;
    renderBatchButton();
    showToast("当前没有启用的任务");
    return;
  }
  let accepted = 0;
  for (const task of tasks) {
    if (!batchRunActive) break;
    try {
      const result = await callDesktop("run_task", task.name, true);
      if (result.accepted) accepted += 1;
    } catch (error) {
      showToast(`${task.name}：${error.message}`, true);
    }
  }
  showToast(`已强制提交 ${accepted}/${tasks.length} 个任务`);
  logsCleared = false;
  await loadLogs();
  await refreshStatus();
}

async function stopAllTasks() {
  batchRunActive = false;
  renderBatchButton();
  if (previewMode) {
    showToast("预览模式不会真正停止任务");
    logsCleared = false;
    renderLogs(previewLogs);
    return;
  }
  try {
    const result = await callDesktop("stop_all_tasks");
    showToast(result.message || "已停止全部运行中的任务");
    logsCleared = false;
    await loadLogs();
    await refreshStatus();
  } catch (error) {
    batchRunActive = true;
    renderBatchButton();
    showToast(error.message, true);
  }
}

async function saveTask() {
  const task = (currentState?.tasks || []).find((item) => item.name === currentTaskName);
  if (!task || previewMode) {
    if (previewMode) showToast("预览模式不会保存配置");
    return;
  }
  const patch = {
    enabled: document.getElementById("taskEnabled").value === "true",
    interval_hours: Number(document.getElementById("taskInterval").value),
    config_revision: currentState.config_revision,
  };
  if (task.requires_script) patch.script_path = document.getElementById("scriptPath").value.trim();
  try {
    await callDesktop("update_task_config", task.name, patch);
    showToast("任务配置已保存并重新加载");
    await refreshStatus();
  } catch (error) { showToast(error.message, true); }
}

async function saveSystemConfig() {
  if (previewMode) {
    showToast("预览模式不会保存配置");
    return;
  }
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
    setSettingsOpen(false);
    await refreshStatus();
  } catch (error) { showToast(error.message, true); }
}

async function reloadConfig() {
  if (previewMode) {
    showToast("预览模式没有可重新加载的配置");
    return;
  }
  try { await callDesktop("reload_config"); showToast("配置已重新加载"); await refreshStatus(); }
  catch (error) { showToast(error.message, true); }
}

function parseLogLine(line) {
  const match = String(line).match(/^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)\s*\|\s*([A-Za-z]+)\s*\|\s*([^|]+?)\s*\|\s*(.*)$/);
  if (!match) return { time: "", level: "INFO", component: "", message: String(line) };
  return { time: match[1], level: match[2].toUpperCase(), component: match[3].trim(), message: match[4] };
}

function renderLogs(lines) {
  const content = document.getElementById("logsContent");
  const entries = (lines || []).map(parseLogLine);
  content.innerHTML = entries.map((entry) => {
    const levelClass = `log-${entry.level.toLowerCase().replace(/[^a-z]/g, "")}`;
    return `<div class="log-line"><time class="log-time">${escapeHtml(entry.time)}</time><span class="log-level ${levelClass}">${escapeHtml(entry.level)}</span><span class="log-message"><span class="log-component">${escapeHtml(entry.component)}</span>${escapeHtml(entry.message)}</span></div>`;
  }).join("");
}

async function loadLogs() {
  if (logsCleared) return;
  if (previewMode || !hasDesktopApi()) {
    renderLogs(previewLogs);
    return;
  }
  try {
    const result = await callDesktop("get_recent_logs", 100);
    renderLogs(result.data || []);
  } catch (error) { showToast(error.message, true); }
}

function clearLogs() {
  logsCleared = true;
  document.getElementById("logsContent").replaceChildren();
}

async function openLogsFolder() {
  if (previewMode) {
    showToast("预览模式无法打开日志文件夹");
    return;
  }
  try {
    const result = await callDesktop("open_logs_folder");
    showToast(result.message || "日志文件夹已打开");
  } catch (error) { showToast(error.message, true); }
}

function setSettingsOpen(open) {
  const section = document.getElementById("settingsSection");
  section.classList.toggle("hidden", !open);
  section.setAttribute("aria-hidden", String(!open));
}

async function runWindowAction(method) {
  if (previewMode) {
    showToast("预览模式无法控制窗口");
    return;
  }
  try {
    return await callDesktop(method);
  } catch (error) {
    showToast(error.message, true);
    return null;
  }
}

let resizeState = null;

function resizeDimensions(state, event) {
  const deltaX = event.screenX - state.startX;
  const deltaY = event.screenY - state.startY;
  let width = state.startWidth;
  let height = state.startHeight;
  if (state.edge.includes("e")) width += deltaX;
  if (state.edge.includes("w")) width -= deltaX;
  if (state.edge.includes("s")) height += deltaY;
  if (state.edge.includes("n")) height -= deltaY;
  return { width: Math.round(width), height: Math.round(height), edge: state.edge };
}

async function flushWindowResize() {
  const state = resizeState;
  if (!state) return;
  if (state.inFlight) {
    await state.inFlight;
    return;
  }
  state.inFlight = (async () => {
    try {
      while (resizeState === state && state.pending) {
        const dimensions = state.pending;
        state.pending = null;
        await callDesktop("resize_window", dimensions.width, dimensions.height, dimensions.edge);
      }
    } catch (error) {
      showToast(error.message, true);
    }
  })();
  await state.inFlight;
  state.inFlight = null;
}

async function beginWindowResize(event) {
  if (event.button !== 0 || previewMode || !hasDesktopApi()) return;
  event.preventDefault();
  event.stopPropagation();
  const edge = event.currentTarget.dataset.resizeEdge;
  try {
    const result = await callDesktop("get_window_size");
    resizeState = {
      edge,
      startX: event.screenX,
      startY: event.screenY,
      startWidth: result.width,
      startHeight: result.height,
      pending: null,
      inFlight: null,
    };
    document.body.classList.add("is-resizing");
    window.addEventListener("pointermove", updateWindowResize);
    window.addEventListener("pointerup", endWindowResize, { once: true });
    window.addEventListener("pointercancel", endWindowResize, { once: true });
    event.currentTarget.setPointerCapture?.(event.pointerId);
  } catch (error) {
    showToast(error.message, true);
  }
}

function updateWindowResize(event) {
  if (!resizeState) return;
  event.preventDefault();
  resizeState.pending = resizeDimensions(resizeState, event);
  void flushWindowResize();
}

async function endWindowResize(event) {
  const state = resizeState;
  if (!state) return;
  event.preventDefault();
  state.pending = resizeDimensions(state, event);
  window.removeEventListener("pointermove", updateWindowResize);
  await flushWindowResize();
  if (resizeState === state) resizeState = null;
  document.body.classList.remove("is-resizing");
}

document.querySelectorAll(".resize-handle").forEach((handle) => {
  handle.addEventListener("pointerdown", beginWindowResize);
});

document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-action]");
  if (target) {
    const name = target.dataset.task;
    if (target.dataset.action === "select") { currentTaskName = name; renderAll(); }
  }
  if (event.target === document.querySelector(".settings-backdrop")) setSettingsOpen(false);
});

document.getElementById("reloadButton").onclick = reloadConfig;
document.getElementById("clearLogsButton").onclick = clearLogs;
document.getElementById("openLogsFolderButton").onclick = openLogsFolder;
document.getElementById("settingsButton").onclick = () => setSettingsOpen(true);
document.getElementById("closeSettingsButton").onclick = () => setSettingsOpen(false);
document.getElementById("cancelSettingsButton").onclick = () => setSettingsOpen(false);
document.getElementById("saveSystemConfigButton").onclick = saveSystemConfig;
document.getElementById("topRunButton").onclick = () => (batchRunActive ? stopAllTasks() : runAllTasks());
document.getElementById("minimizeButton").onclick = () => runWindowAction("minimize_window");
document.getElementById("closeButton").onclick = () => runWindowAction("close_window");
document.getElementById("maximizeButton").onclick = async () => {
  const result = await runWindowAction("toggle_maximize_window");
  if (result) {
    const button = document.getElementById("maximizeButton");
    button.title = result.maximized ? "还原" : "最大化";
    button.setAttribute("aria-label", button.title);
  }
};

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") setSettingsOpen(false);
});

let desktopSyncStarted = false;
let previewFallbackTimer = null;

async function initializeDesktopSync() {
  if (desktopSyncStarted || !hasDesktopApi()) return;
  desktopSyncStarted = true;
  if (previewFallbackTimer) clearTimeout(previewFallbackTimer);
  previewMode = false;
  await refreshStatus();
  refreshTimer = refreshTimer || setInterval(refreshStatus, 2500);
}

document.addEventListener("pywebviewready", initializeDesktopSync);

const desktopReadyPoll = setInterval(() => {
  if (hasDesktopApi()) {
    clearInterval(desktopReadyPoll);
    initializeDesktopSync();
  }
}, 100);

previewFallbackTimer = setTimeout(() => {
  if (!desktopSyncStarted && !hasDesktopApi()) applyPreviewState();
}, 1000);
