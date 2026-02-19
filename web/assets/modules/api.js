export function createApiClient({ setApiHealth, fetchImpl = fetch, sleepFn }) {
  const wait = typeof sleepFn === 'function'
    ? sleepFn
    : ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));

  return async function api(path, options = {}) {
    const {
      retryable: retryableOption,
      retryAttempts: retryAttemptsOption,
      healthImpact = true,
      ...fetchOptions
    } = options;
    const method = String(fetchOptions.method || 'GET').toUpperCase();
    const retryable = retryableOption !== undefined ? !!retryableOption : true;
    const retryAttempts = Number(retryAttemptsOption || (method === 'GET' ? 3 : 2));
    let lastError = null;

    for (let attempt = 1; attempt <= retryAttempts; attempt += 1) {
      try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 10000);
        const resp = await fetchImpl(path, {
          headers: { 'Content-Type': 'application/json' },
          ...fetchOptions,
          signal: controller.signal,
        });
        clearTimeout(timeout);

        const text = await resp.text();
        let data = {};
        try {
          data = text ? JSON.parse(text) : {};
        } catch {
          data = { raw: text };
        }
        if (!resp.ok) {
          throw new Error(`HTTP ${resp.status}: ${JSON.stringify(data)}`);
        }
        if (healthImpact) {
          setApiHealth(true);
        }
        return data;
      } catch (err) {
        lastError = err;
        const canRetry = retryable && attempt < retryAttempts;
        if (healthImpact) {
          setApiHealth(false, String(err), { increment: !canRetry });
        }
        if (!canRetry) {
          break;
        }
        await wait(Math.min(2000, 250 * (2 ** (attempt - 1))));
      }
    }

    throw lastError || new Error('API request failed');
  };
}

