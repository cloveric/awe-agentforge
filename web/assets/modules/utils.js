export function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function normalizeProjectPath(path) {
  return String(path || '.').trim() || '.';
}

export function escapeHtml(value) {
  const text = String(value ?? '');
  const table = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '\"': '&quot;',
    "'": '&#39;',
  };
  return text.replace(/[&<>\"']/g, (ch) => table[ch] || ch);
}

export function projectName(path) {
  const clean = normalizeProjectPath(path).replace(/\\/g, '/');
  const parts = clean.split('/').filter(Boolean);
  return parts.length ? parts[parts.length - 1] : clean;
}

export function hashText(text) {
  let hash = 2166136261;
  const src = String(text || '');
  for (let i = 0; i < src.length; i += 1) {
    hash ^= src.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

export function seededRandom(seed) {
  let value = seed >>> 0;
  return () => {
    value += 0x6d2b79f5;
    let t = value;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

