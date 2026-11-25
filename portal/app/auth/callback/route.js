import * as oidc from 'openid-client';
import { NextResponse } from 'next/server';
import { cookieOptions, flowName, origin, provider, seal, sessionName, unseal } from '../../../lib/auth.mjs';

export async function GET(request) {
  let response;
  try {
    const flow = await unseal(request.cookies.get(flowName)?.value, 'flow');
    if (!flow) throw new Error('Missing or stale OIDC flow');
    const config = await provider();
    const address = new URL('/auth/callback', origin());
    address.search = new URL(request.url).search;
    const tokens = await oidc.authorizationCodeGrant(config, address, {
      pkceCodeVerifier: flow.verifier, expectedState: flow.state, expectedNonce: flow.nonce, idTokenExpected: true,
    });
    const claims = tokens.claims();
    if (!claims?.sub || !claims.iss || !claims.exp) throw new Error('Missing identity claims');
    const ttl = Math.min(3600, claims.exp - Math.floor(Date.now() / 1000));
    if (ttl <= 0) throw new Error('Expired identity');
    response = NextResponse.redirect(origin() + '/');
    response.cookies.set(sessionName, await seal({ iss: claims.iss, sub: claims.sub, name: String(claims.name || claims.preferred_username || 'Learner').slice(0, 120) }, 'session', ttl), cookieOptions(ttl));
  } catch (error) {
    console.warn('OIDC sign-in did not complete:', error.code || error.name);
    response = NextResponse.redirect(origin() + '/?signin=failed');
  }
  response.cookies.set(flowName, '', cookieOptions(0, '/auth'));
  response.headers.set('Cache-Control', 'no-store');
  return response;
}
