type Reply = { ok: boolean; result?: Record<string, unknown>; error?: string };

const statusNode = document.querySelector<HTMLParagraphElement>("#status")!;
const statusPanel = document.querySelector<HTMLDivElement>(".status-panel")!;
const primary = document.querySelector<HTMLButtonElement>("#primary")!;
const disconnect = document.querySelector<HTMLButtonElement>("#disconnect")!;
const libraryButton = document.querySelector<HTMLButtonElement>("#library")!;
const libraryPanel = document.querySelector<HTMLElement>("#library-panel")!;
const workspace = document.querySelector<HTMLButtonElement>("#workspace")!;
const loginForm = document.querySelector<HTMLFormElement>("#login-form")!;
const emailInput = document.querySelector<HTMLInputElement>("#email")!;
const codeInput = document.querySelector<HTMLInputElement>("#code")!;
const codeLabel = document.querySelector<HTMLLabelElement>("#code-label")!;
const loginButton = document.querySelector<HTMLButtonElement>("#login")!;
let action: "pair" | "finish" | "capture" = "pair";

const messages: Record<string, string> = {
  unsupported_page: "请打开 YouTube 或 NTULearn/Kaltura 视频页面后再试。",
  pairing_pending: "插件连接尚未完成，请在工作区完成登录后重试。",
  pairing_expired: "配对已过期，请重新连接。",
  pairing_used: "这次配对已经失效，请重新连接。",
  pairing_missing: "插件里没有待完成的配对，请重新连接。",
  extension_pairing_pending: "插件连接尚未完成，请在工作区完成登录后重试。",
  extension_pairing_expired: "配对已过期，请重新连接。",
  extension_pairing_used: "这次配对已经失效，请重新连接。",
  extension_pairing_invalid: "配对凭据不匹配，请重新连接。",
  extension_origin_invalid: "服务器拒绝了当前插件来源，请重新连接；本机开发环境请检查扩展来源配置。",
  network_unavailable: "无法连接搜藏助手，请确认服务正在运行。",
  request_timeout: "连接搜藏助手超时，请确认服务地址和网络后重试。",
  extension_api_origin_invalid: "插件的服务地址配置无效，请重新构建并加载正确版本。",
  request_failed: "搜藏助手没有完成授权，请稍后重试。",
  caption_fetch_failed: "页面字幕暂时读取失败，请刷新视频页面后重试。",
  caption_parse_failed: "页面字幕格式暂时无法解析，请刷新视频页面后重试。",
  stale_player_response: "视频页面刚刚切换，插件没有使用旧视频字幕；请稍后重试。",
  kaltura_entry_missing: "没有识别到 Kaltura 视频，请确认视频已加载后刷新页面重试。",
  capture_unavailable: "页面结构可能已经变化，请刷新视频页面或升级插件后重试。",
  extension_grant_revoked: "这个连接已经被撤销，请重新连接。",
};

const resetPairingErrors = new Set([
  "pairing_expired",
  "pairing_used",
  "pairing_missing",
  "extension_pairing_expired",
  "extension_pairing_used",
  "extension_pairing_invalid",
]);

async function send(type: string): Promise<Reply> {
  return chrome.runtime.sendMessage({ type }) as Promise<Reply>;
}

function showStatus(
  message: string,
  tone: "loading" | "ready" | "action" | "success" | "error" = "action",
) {
  statusNode.textContent = message;
  statusPanel.dataset.tone = tone;
}

function render(reply: Reply) {
  const paired = reply.ok && reply.result?.paired === true;
  const pairing = reply.ok && reply.result?.pairing === true;
  action = paired ? "capture" : pairing ? "finish" : "pair";
  showStatus(
    paired ? "已连接。打开一个视频，然后保存当前页面字幕。" : pairing ? "请先在搜藏助手工作区完成登录。" : "登录后，可从当前页面保存字幕。",
    paired ? "ready" : "action",
  );
  primary.textContent = paired ? "保存当前视频" : pairing ? "完成连接" : "连接搜藏助手";
  primary.hidden = !paired;
  loginForm.hidden = paired;
  primary.disabled = false;
  disconnect.hidden = !paired && !pairing;
}

primary.addEventListener("click", async () => {
  primary.disabled = true;
  showStatus(action === "capture" ? "正在读取并提交字幕…" : "正在连接…", "loading");
  const reply = action === "capture" ? await send("CAPTURE") : ({ ok: false, error: "login_required" } as Reply);
  if (!reply.ok) {
    const error = reply.error ?? "request_failed";
    showStatus(messages[error] ?? `操作没有完成（${error}），请稍后重试。`, "error");
    if (resetPairingErrors.has(error)) {
      await send("DISCONNECT");
      action = "pair";
      primary.textContent = "重新连接";
      disconnect.hidden = true;
    }
    primary.disabled = false;
    return;
  }
  if (action === "capture") {
    const lifecycle = String(reply.result?.lifecycle ?? "queued");
    showStatus(
      lifecycle === "needs_asr" ? "视频没有可用字幕，已标记为需要语音转写。" : "已提交到收藏内容，搜藏助手正在准备对话。",
      lifecycle === "needs_asr" ? "action" : "success",
    );
    primary.textContent = "再次保存";
    primary.disabled = false;
  } else if (action === "pair") {
    action = "finish";
    showStatus("请在搜藏助手工作区完成登录，然后回到这里继续。", "action");
    primary.textContent = "完成连接";
    primary.disabled = false;
    disconnect.hidden = false;
  } else {
    render({ ok: true, result: { paired: true } });
  }
});

workspace.addEventListener("click", () => { void chrome.runtime.sendMessage({ type: "OPEN_WORKSPACE" }); });
loginForm.hidden = false;
loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  loginButton.disabled = true;
  const command = codeInput.hidden
    ? { type: "START_LOGIN", email: emailInput.value }
    : { type: "FINISH_LOGIN", email: emailInput.value, code: codeInput.value };
  const reply = await chrome.runtime.sendMessage(command) as Reply;
  loginButton.disabled = false;
  if (!reply.ok) { showStatus(messages[reply.error ?? "request_failed"] ?? "登录失败，请重试。", "error"); return; }
  if (codeInput.hidden) { codeInput.hidden = false; codeLabel.hidden = false; loginButton.textContent = "完成登录"; showStatus("验证码已发送，请输入邮件中的验证码。", "action"); }
  else { loginForm.hidden = true; render({ ok: true, result: { paired: true } }); }
});

disconnect.addEventListener("click", async () => { await send("DISCONNECT"); render({ ok: true, result: { paired: false } }); });
libraryButton.addEventListener("click", async () => {
  libraryButton.disabled = true;
  libraryPanel.hidden = false;
  libraryPanel.textContent = "正在读取收藏…";
  const reply = await send("LIBRARY");
  libraryButton.disabled = false;
  if (!reply.ok) {
    libraryPanel.textContent = messages[reply.error ?? "request_failed"] ?? "暂时无法读取收藏。";
    return;
  }
  const items = Array.isArray(reply.result?.items) ? reply.result.items as Array<Record<string, unknown>> : [];
  libraryPanel.replaceChildren(...items.slice(0, 8).map((item) => {
    const row = document.createElement("p");
    row.textContent = `${String(item.title ?? "未命名视频")} · ${String(item.lifecycle ?? "")}`;
    return row;
  }));
  if (!items.length) libraryPanel.textContent = "还没有收藏内容。";
});
void send("STATUS").then(render);
