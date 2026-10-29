import {t} from './i18n.js';

/** Only error text belongs here; never pass request bodies, headers or snapshots. */
export function safeErrorText(value) {
  if (typeof value !== 'string') return '';
  if (/\b(?:traceback|current_inputs|current_outputs|input_config|received_value)\b/i.test(value))
    return t('Unsafe diagnostic detail was omitted');
  return value
    .replace(/\bBearer\s+[^\s,;"']+/gi, 'Bearer [redacted]')
    .replace(/(["']?(?:api[_-]?key|access[_-]?token|client[_-]?token|auth[_-]?token|token|password|secret|authorization|cookie)["']?\s*[=:]\s*)(?:"[^"]*"|'[^']*'|[^\s,;&}]+)/gi, '$1[redacted]')
    .replace(/\b[A-Za-z]:[\\/][^\s"'<>]+/g, '[path redacted]')
    .replace(/\bhttps?:\/\/[^\s"'<>]+/gi, '[URL redacted]')
    .replace(/data:[^\s]+/gi, '[media redacted]')
    .replace(/[A-Za-z0-9+/=_-]{128,}/g, '[long value redacted]')
    .replace(/[\u0000-\u001f\u007f]/g, ' ').slice(0, 2048).trim();
}

/** @param {Response} response @param {string} path @param {any} data */
export async function responseError(response, path, data = undefined) {
  if (data === undefined) {
    try { data = await response.json(); } catch { data = null; }
  }
  const source = data?.error;
  const detail = source && typeof source === 'object' && !Array.isArray(source) ? source : undefined;
  const code = typeof detail?.code === 'string' && /^[A-Z][A-Z0-9_]+$/.test(detail.code) ? detail.code : '';
  const reason = safeErrorText(detail?.message || (typeof source === 'string' ? source : data?.message))
    || t('The response provided no displayable error description');
  /** @type {{message: string, code?: string, field?: string, nodeId?: string,
   * inputName?: string, paramId?: string, retryable?: boolean,
   * currentRevision?: number, snapshot?: object} | undefined} */
  const safeDetail = detail ? {message: reason, ...(code ? {code} : {})} : undefined;
  if (safeDetail) {
    for (const key of ['field', 'nodeId', 'inputName', 'paramId'])
      if (typeof detail[key] === 'string') safeDetail[key] = safeErrorText(detail[key]);
    if (typeof detail.retryable === 'boolean') safeDetail.retryable = detail.retryable;
    if (Number.isSafeInteger(detail.currentRevision) && detail.currentRevision >= 0) safeDetail.currentRevision = detail.currentRevision;
    // Revision-conflict recovery needs the authoritative snapshot. It is state,
    // not display text: never format or log it as an exception description.
    if (code === 'REVISION_CONFLICT' && detail.snapshot && typeof detail.snapshot === 'object') safeDetail.snapshot = detail.snapshot;
  }
  return Object.assign(new Error(`${path} · HTTP ${response.status}${code ? ` · ${code}` : ''}: ${reason}`),
    {path, status: response.status, detail: safeDetail});
}

export function requestError(path, error, timedOut = false) {
  const reason = timedOut ? t('The request did not complete within 16 seconds')
    : t('The browser reported a request error; no HTTP response was available');
  return Object.assign(new Error(`${path}: ${reason} (${safeErrorText(error?.name) || 'Error'})`), {path});
}

export function socketClosed(event) {
  const reason = safeErrorText(event?.reason) || t('No close reason was provided');
  return new Error(t('Bridge WebSocket closed (code {code}): {reason}', {code: event?.code ?? t('unavailable'), reason}));
}

export function waitForSocket(socket, timeout = 16000) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = error => {
      if (settled) return;
      settled = true; clearTimeout(timer);
      socket.onopen = socket.onerror = socket.onclose = null;
      if (error) { reject(error); socket.close(); } else resolve(undefined);
    };
    const timer = setTimeout(() => finish(new Error(t('Bridge WebSocket did not connect within 16 seconds'))), timeout);
    socket.onopen = () => finish(null);
    socket.onerror = () => finish(new Error(t('The browser reported a WebSocket error; no specific cause was provided')));
    socket.onclose = event => finish(socketClosed(event));
  });
}
