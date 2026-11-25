import { localUrl } from './auth.mjs';

export const checkoutCookie = 'lcs-checkout';
export function commerceOrigin() {
  if (!process.env.DEMO_COMMERCE_ORIGIN) return null;
  const url = localUrl(process.env.DEMO_COMMERCE_ORIGIN);
  // The Django demo API is deliberately not a public payment endpoint.
  if (!['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)) throw new Error('Demo commerce API must be on loopback');
  return url.origin;
}
export async function commerceRequest(path, options = {}) {
  const base = commerceOrigin();
  if (!base) throw new Error('Demo commerce is disabled');
  const response = await fetch(new URL(path, base), { ...options, redirect: 'error', cache:'no-store', signal: AbortSignal.timeout(3500) });
  if (!response.ok) throw new Error('Demo commerce request did not complete');
  return response.json();
}
