const 状态文字 = {
  disabled: "已禁用",
  cooldown: "冷却中",
  pending: "等待执行",
  starting: "启动中",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  timed_out: "已超时",
};

const 状态样式 = {
  disabled: "灰色",
  cooldown: "黄色",
  pending: "蓝色",
  starting: "黄色",
  running: "蓝色",
  completed: "绿色",
  failed: "红色",
  timed_out: "红色",
};

let 当前状态 = null;
let 当前任务名 = null;

function 转义(value) {
  return String(value ?? "").replace(/[&<>\"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[char]));
}

function 格式化时间(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function 格式化耗时(value) {
  if (value === null || value === undefined) return "—";
  if (value < 60) return `${value.toFixed(1)} 秒`;
  return `${Math.floor(value / 60)} 分 ${(value % 60).toFixed(0)} 秒`;
}

function 显示提示(message, error = false) {
  const box = document.getElementById("提示框");
  box.textContent = message;
  box.style.borderColor = error ? "#a94d4d" : "#3d6d9e";
  box.classList.add("显示");
  setTimeout(() => box.classList.remove("显示"), 2600);
}

async function 请求(url, options = {}) {
  const response = await fetch(url, { headers: { "Content-Type": "application/json" }, ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || data.message || `请求失败（${response.status}）`);
  return data;
}

function 状态标签(task) {
  const state = task.state || "pending";
  return `<span class="状态标签 ${状态样式[state] || "灰色"}">${状态文字[state] || state}</span>`;
}

function 渲染统计() {
  const tasks = 当前状态?.tasks || [];
  const running = tasks.filter((task) => ["starting", "running"].includes(task.state)).length;
  document.getElementById("任务总数").textContent = tasks.filter((task) => task.enabled).length;
  document.getElementById("运行数量").textContent = running;
  document.getElementById("总体进度").textContent = `${当前状态?.progress?.percent || 0}%`;
  document.getElementById("更新时间").textContent = `更新于 ${格式化时间(当前状态?.generated_at)}`;
}

function 渲染任务表() {
  const body = document.getElementById("任务表格");
  const tasks = 当前状态?.tasks || [];
  body.innerHTML = tasks.map((task) => {
    const selected = task.name === 当前任务名 ? "选中" : "";
    const progress = ["completed", "cooldown"].includes(task.state) ? 100 : ["starting", "running"].includes(task.state) ? 50 : 0;
    return `<tr class="${selected}" data-task="${转义(task.name)}">
      <td><strong>${转义(task.name)}</strong></td>
      <td>${状态标签(task)}</td>
      <td><span class="进度条"><i style="width:${progress}%"></i></span> ${progress}%</td>
      <td>${格式化时间(task.last_success_at)}</td>
      <td><div class="行操作"><button title="运行" data-action="run" data-task="${转义(task.name)}">▶</button><button title="强制运行" data-action="force" data-task="${转义(task.name)}">⚡</button><button title="编辑" data-action="select" data-task="${转义(task.name)}">✎</button></div></td>
    </tr>`;
  }).join("") || `<tr><td colspan="5" class="空状态">暂无任务</td></tr>`;
}

function 启动器表单(task) {
  const launcher = task.launcher || { type: "none", path: "", process_name: "", startup_timeout_seconds: 15 };
  const isApplication = launcher.type === "application";
  return `<div class="详情字段"><label>启动方式</label><select id="编辑启动类型"><option value="none" ${!isApplication ? "selected" : ""}>无外部应用（内置任务）</option><option value="application" ${isApplication ? "selected" : ""}>启动应用并验证进程</option></select></div>
    <div class="详情字段"><label>应用路径</label><input id="编辑应用路径" value="${转义(launcher.path)}" placeholder=".exe 或 .lnk 路径"></div>
    <div class="详情字段"><label>进程名</label><input id="编辑进程名" value="${转义(launcher.process_name)}" placeholder="例如 MAA.exe"></div>
    <div class="详情字段"><label>启动验证超时（秒）</label><input id="编辑启动超时" type="number" min="1" max="300" step="1" value="${launcher.startup_timeout_seconds || 15}"></div>`;
}

function 渲染详情() {
  const task = (当前状态?.tasks || []).find((item) => item.name === 当前任务名);
  const title = document.getElementById("详情标题");
  const content = document.getElementById("详情内容");
  if (!task) {
    title.textContent = "选择任务";
    content.className = "详情内容 空状态";
    content.textContent = "请从左侧选择一个任务";
    return;
  }
  title.textContent = task.name;
  content.className = "详情内容";
  const 回调说明 = task.waiting_for_callback ? "等待外部完成回调" : task.completion_description;
  content.innerHTML = `<div class="详情状态">${状态标签(task)}<p class="空状态">${回调说明}<br>耗时：${格式化耗时(task.elapsed_seconds)}<br>最近完成：${格式化时间(task.last_success_at)}${task.last_error ? `<br>错误：${转义(task.last_error)}` : ""}</p></div>
    <div class="详情字段"><label>是否启用</label><select id="编辑启用"><option value="true" ${task.enabled ? "selected" : ""}>启用</option><option value="false" ${!task.enabled ? "selected" : ""}>禁用</option></select></div>
    <div class="详情字段"><label>间隔时间（小时）</label><input id="编辑间隔" type="number" min="0" step="0.1" value="${task.interval_hours}"></div>
    ${启动器表单(task)}
    <div class="详情按钮组"><button class="次要按钮" id="详情运行">▶ 运行</button><button class="主要按钮" id="详情保存">保存配置</button></div>`;
  document.getElementById("详情运行").onclick = () => 运行任务(task.name, false);
  document.getElementById("详情保存").onclick = 保存任务;
}

function 渲染全局配置() {
  const system = 当前状态?.system;
  if (!system) return;
  document.getElementById("全局日志级别").value = system.log_level;
  document.getElementById("全局端口").value = system.webhook_port;
  document.getElementById("全局完成开关").value = String(system.shutdown_on_complete);
  document.getElementById("全局延迟").value = system.shutdown_delay_seconds;
  document.getElementById("全局超时").value = system.shutdown_timeout_hours;
  document.getElementById("全局完成动作").value = system.completion_action;
  document.getElementById("全局SendKey").placeholder = system.server_chan_key_configured ? "已配置，留空保持原值" : "未配置";
}

function 渲染全部() { 渲染统计(); 渲染任务表(); 渲染详情(); 渲染全局配置(); }

async function 刷新状态() {
  try {
    当前状态 = await 请求("/api/status");
    document.getElementById("连接文字").textContent = "已连接";
    渲染全部();
  } catch (error) {
    document.getElementById("连接文字").textContent = "连接失败";
  }
}

async function 运行任务(taskName, force) {
  try {
    const result = await 请求(`/api/tasks/${encodeURIComponent(taskName)}/run`, { method: "POST", body: JSON.stringify({ force }) });
    显示提示(result.message || "操作已提交");
    await 刷新状态();
  } catch (error) { 显示提示(error.message, true); }
}

async function 保存任务() {
  const task = (当前状态?.tasks || []).find((item) => item.name === 当前任务名);
  if (!task) return;
  const type = document.getElementById("编辑启动类型").value;
  const launcher = {
    type,
    path: document.getElementById("编辑应用路径").value.trim(),
    process_name: document.getElementById("编辑进程名").value.trim(),
    startup_timeout_seconds: Number(document.getElementById("编辑启动超时").value),
  };
  if (type === "none") { launcher.path = ""; launcher.process_name = ""; }
  const patch = { enabled: document.getElementById("编辑启用").value === "true", interval_hours: Number(document.getElementById("编辑间隔").value), launcher, config_revision: 当前状态.config_revision };
  try {
    await 请求(`/api/tasks/${encodeURIComponent(task.name)}`, { method: "PATCH", body: JSON.stringify(patch) });
    显示提示("任务配置已保存并重新加载");
    await 刷新状态();
  } catch (error) { 显示提示(error.message, true); }
}

async function 保存全局配置() {
  const sendKey = document.getElementById("全局SendKey").value.trim();
  const patch = {
    log_level: document.getElementById("全局日志级别").value,
    webhook_port: Number(document.getElementById("全局端口").value),
    shutdown_on_complete: document.getElementById("全局完成开关").value === "true",
    shutdown_delay_seconds: Number(document.getElementById("全局延迟").value),
    shutdown_timeout_hours: Number(document.getElementById("全局超时").value),
    completion_action: document.getElementById("全局完成动作").value,
    clear_server_chan_key: document.getElementById("清除SendKey").checked,
    config_revision: 当前状态.config_revision,
  };
  if (sendKey) patch.server_chan_key = sendKey;
  try {
    const result = await 请求("/api/config/system", { method: "PATCH", body: JSON.stringify(patch) });
    document.getElementById("全局SendKey").value = "";
    document.getElementById("清除SendKey").checked = false;
    显示提示(result.message || "全局配置已保存");
    await 刷新状态();
  } catch (error) { 显示提示(error.message, true); }
}

async function 重新加载配置() {
  try { await 请求("/api/config/reload", { method: "POST" }); 显示提示("配置已重新加载"); await 刷新状态(); }
  catch (error) { 显示提示(error.message, true); }
}

async function 刷新日志() {
  try { const data = await 请求("/api/logs/recent"); document.getElementById("日志内容").textContent = data.lines.join("\n") || "暂无日志"; }
  catch (error) { 显示提示(error.message, true); }
}

document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-action]");
  if (target) {
    const name = target.dataset.task;
    if (target.dataset.action === "select") { 当前任务名 = name; 渲染全部(); }
    if (target.dataset.action === "run") 运行任务(name, false);
    if (target.dataset.action === "force") 运行任务(name, true);
  }
  const nav = event.target.closest(".导航项");
  if (nav) {
    document.querySelectorAll(".导航项").forEach((item) => item.classList.remove("active"));
    nav.classList.add("active");
    const section = nav.dataset.section;
    document.getElementById("任务区域").classList.toggle("隐藏", section !== "任务");
    document.getElementById("日志区域").classList.toggle("隐藏", section !== "日志");
    document.getElementById("设置区域").classList.toggle("隐藏", section !== "设置");
    document.getElementById("页面标题").textContent = section;
    if (section === "日志") 刷新日志();
  }
});

document.getElementById("重载按钮").onclick = 重新加载配置;
document.getElementById("刷新日志").onclick = 刷新日志;
document.getElementById("保存全局配置").onclick = 保存全局配置;
document.getElementById("顶部运行按钮").onclick = () => {
  const first = (当前状态?.tasks || []).find((task) => task.enabled && ["pending", "failed"].includes(task.state));
  if (first) 运行任务(first.name, false); else 显示提示("当前没有可直接运行的任务");
};
document.getElementById("关闭详情").onclick = () => { 当前任务名 = null; 渲染详情(); };

刷新状态();
setInterval(刷新状态, 2500);
