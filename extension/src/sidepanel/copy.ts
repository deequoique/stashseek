import { ApiError, ConversationStreamError } from "../api/client";
import { assistantTextWithoutSourceList } from "../chat/answerText";
import { WorkerError } from "./workerApi";

const WORKER_COPY: Record<string, string> = {
  unsupported_page: "请打开 YouTube 或哔哩哔哩视频页面后再试。",
  active_tab_missing: "没有找到当前标签页，请切回视频页面后重试。",
  pairing_pending: "插件连接尚未完成，请重新获取验证码。",
  pairing_required: "保存字幕前需要先连接这台设备。",
  pairing_expired: "配对已过期，请重新连接。",
  pairing_used: "这次配对已经失效，请重新连接。",
  pairing_missing: "插件里没有待完成的配对，请重新获取验证码。",
  extension_pairing_pending: "插件连接尚未完成，请重新获取验证码。",
  extension_pairing_expired: "配对已过期，请重新连接。",
  extension_pairing_used: "这次配对已经失效，请重新连接。",
  extension_pairing_invalid: "配对凭据不匹配，请重新连接。",
  extension_origin_invalid: "服务器拒绝了当前插件来源，请重新连接；本机开发环境请检查扩展来源配置。",
  extension_grant_revoked: "这个连接已经被撤销，请重新连接。",
  verification_failed: "验证码无效或已过期，请重试。",
  network_unavailable: "无法连接搜藏助手，请确认服务正在运行。",
  request_timeout: "连接搜藏助手超时，请确认服务地址和网络后重试。",
  extension_api_origin_invalid: "插件的服务地址配置无效，请重新构建并加载正确版本。",
  worker_unavailable: "插件后台没有响应，请重新加载插件后重试。",
  caption_fetch_failed: "页面字幕暂时读取失败，请刷新视频页面后重试。",
  caption_parse_failed: "页面字幕格式暂时无法解析，请刷新视频页面后重试。",
  stale_player_response: "视频页面刚刚切换，插件没有使用旧视频字幕；请稍后重试。",
  caption_player_not_ready: "播放器还没准备好：请先播放视频几秒，再点「重新读取」。",
  kaltura_entry_missing: "没有识别到 Kaltura 视频，请确认视频已加载后刷新页面重试。",
  capture_unavailable: "页面结构可能已经变化，请刷新视频页面或升级插件后重试。",
  capture_conflict: "这个视频正在保存或处理中，请稍后再试。",
  capture_upload_failed: "字幕上传失败，请稍后重试。",
  queue_unavailable: "保存任务暂时无法排队，请稍后重试。",
  capture_too_large: "这个视频的字幕太长，无法保存。",
  request_failed: "搜藏助手没有完成请求，请稍后重试。",
};

/** Pairing problems that make the stored pairing unusable; the user starts again from the email step. */
export const PAIRING_RESET_CODES: ReadonlySet<string> = new Set([
  "pairing_expired",
  "pairing_used",
  "pairing_missing",
  "extension_pairing_expired",
  "extension_pairing_used",
  "extension_pairing_invalid",
]);

/** Shown on a Bilibili page whose read returned no caption track. */
export const BILIBILI_NO_CAPTIONS_HINT = "没有读取到字幕：登录哔哩哔哩后可能会有 AI 字幕。";

/** The capture device grant itself was rejected, so saving needs a new connection. */
export function isGrantRejected(code: string): boolean {
  return code === "pairing_required" || code.startsWith("extension_grant_");
}

export function errorCode(error: unknown): string {
  if (error instanceof WorkerError) return error.code;
  if (error instanceof Error && error.message) return error.message;
  return "request_failed";
}

export function workerErrorCopy(code: string): string {
  return WORKER_COPY[code] ?? `操作没有完成（${code}），请稍后重试。`;
}

export interface AskFailure {
  copy: string;
  /** The extension session is no longer valid and the user must log in again. */
  sessionInvalid: boolean;
  stopped: boolean;
}

/** Short Chinese copy for a failed page question. */
export function askFailure(error: unknown): AskFailure {
  const failure = (copy: string, extra: Partial<AskFailure> = {}): AskFailure => ({
    copy,
    sessionInvalid: false,
    stopped: false,
    ...extra,
  });
  if (error instanceof ApiError) {
    if (error.status === 401) return failure("登录已失效，请重新登录。", { sessionInvalid: true });
    if (error.status === 429 || error.code === "rate_limited") return failure("提问太频繁了，请稍后再试。");
    if (error.status === 413) return failure("这个视频的字幕太长，暂时无法就它提问。");
    if (error.status === 422) return failure("这个页面暂时无法提问（字幕不可用或内容无效），请刷新视频页面后重试。");
    if (error.status === 404 || error.status === 405) return failure("当前服务未开放视频问答。");
    return failure("服务暂时不可用，请稍后重试。");
  }
  if (error instanceof ConversationStreamError) {
    switch (error.code) {
      case "cancelled": return failure("已停止。", { stopped: true });
      case "answer_unavailable": return failure("暂时无法生成可靠回答，请换个问法或稍后重试。");
      case "timeout": return failure("回答超时，请稍后重试。");
      case "rate_limited": return failure("提问太频繁了，请稍后再试。");
      case "stream_protocol_error":
      case "stream_unavailable": return failure("回答连接中断，请重试。");
      default: return failure("请求未能完成，请稍后重试。");
    }
  }
  if (error instanceof TypeError) return failure("无法连接搜藏助手，请确认服务正在运行。");
  return failure("请求未能完成，请稍后重试。");
}

/** `m:ss`, or `h:mm:ss` from one hour. */
export function formatTimestamp(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = String(total % 60).padStart(2, "0");
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}` : `${minutes}:${rest}`;
}

/** Answer text for the panel: no appended source block and no raw `[S<n>]` markers (the chips carry the sources). */
export function panelAnswerText(text: string, hasCitations: boolean): string {
  const withoutSources = assistantTextWithoutSourceList(text, hasCitations);
  return withoutSources.replace(/[ \t]*\[S\d+\]/g, "").replace(/[ \t]*\[S\d*$/, "");
}
