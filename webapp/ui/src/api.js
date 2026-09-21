/** Thin fetch helpers for the FastAPI demo API. */

export async function getJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(await detail(res));
  return res.json();
}

export async function post(url, options = {}) {
  const res = await fetch(url, { method: 'POST', ...options });
  if (!res.ok) throw new Error(await detail(res));
  try {
    return await res.json();
  } catch {
    return {};
  }
}

async function detail(res) {
  try {
    const body = await res.json();
    return body.detail || JSON.stringify(body);
  } catch {
    return res.statusText;
  }
}
