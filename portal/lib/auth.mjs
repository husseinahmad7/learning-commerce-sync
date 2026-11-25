import * as oidc from 'openid-client';
import { EncryptJWT, jwtDecrypt } from 'jose';
import { createHash } from 'node:crypto';

export const flowName = 'lcs-oidc';
export const sessionName = 'lcs-session';
const audience = 'learning-commerce-portal';
const loopback = url => ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
export function localUrl(value) {
  const url = new URL(value);
  if (url.username || url.password || url.search || url.hash ||
      (url.protocol !== 'https:' && !(url.protocol === 'http:' && loopback(url)))) throw new Error('Use HTTPS or loopback HTTP');
  return url;
}
export function origin() { return localUrl(process.env.PORTAL_ORIGIN || 'http://127.0.0.1:3000').origin; }
export function cookieOptions(maxAge, path = '/') {
  return { httpOnly: true, sameSite: 'lax', secure: origin().startsWith('https:'), path, maxAge };
}
function sessionKey() {
  const secret = process.env.PORTAL_SESSION_SECRET;
  if (!secret || !/^[a-fA-F0-9]{64}$/.test(secret)) throw new Error('Set a 32-byte hex PORTAL_SESSION_SECRET');
  return createHash('sha256').update(Buffer.from(secret, 'hex')).digest();
}
export async function seal(data, kind, seconds) {
  return new EncryptJWT({ ...data, kind }).setProtectedHeader({ alg: 'dir', enc: 'A256GCM' })
    .setAudience(audience).setIssuedAt().setExpirationTime(Math.floor(Date.now() / 1000) + seconds).encrypt(sessionKey());
}
export async function unseal(value, kind) {
  if (!value) return null;
  try {
    const { payload } = await jwtDecrypt(value, sessionKey(), { audience, keyManagementAlgorithms: ['dir'], contentEncryptionAlgorithms: ['A256GCM'] });
    return payload.kind === kind ? payload : null;
  } catch { return null; }
}
let discovery;
export function provider() {
  if (!discovery) {
    const server = localUrl(process.env.OIDC_ISSUER);
    const id = process.env.OIDC_CLIENT_ID;
    const secret = process.env.OIDC_CLIENT_SECRET;
    if (!id || !secret) throw new Error('Configure the portal OIDC client');
    discovery = oidc.discovery(server, id, secret, oidc.ClientSecretPost(secret), server.protocol === 'http:' ? { execute: [oidc.allowInsecureRequests] } : undefined)
      .then(config => { config.timeout = 8; oidc.enableNonRepudiationChecks(config); return config; })
      .catch(error => { discovery = undefined; throw error; });
  }
  return discovery;
}
