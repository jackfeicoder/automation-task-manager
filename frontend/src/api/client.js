export async function api(path, options = {}) {
  const response = await fetch('/api' + path, {
    ...options,
    headers: {'Content-Type': 'application/json', ...options.headers},
    credentials: 'same-origin',
  });
  const data = await response.json();
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map(x => x.msg).join('；') : data.detail;
    const error = new Error(detail || `请求错误 ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return data;
}
export const send = (path, body, method = 'POST') => api(path, {method, body: JSON.stringify(body)});
