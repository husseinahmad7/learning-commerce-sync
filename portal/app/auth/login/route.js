import * as oidc from 'openid-client';
import { NextResponse } from 'next/server';
import { cookieOptions, flowName, origin, provider, seal } from '../../../lib/auth.mjs';

export async function GET() {
  try {
    const config = await provider();
    const verifier = oidc.randomPKCECodeVerifier();
    const state = oidc.randomState();
    const nonce = oidc.randomNonce();
    const target = oidc.buildAuthorizationUrl(config, {
      redirect_uri: origin() + '/auth/callback', scope: 'openid profile',
      code_challenge: await oidc.calculatePKCECodeChallenge(verifier), code_challenge_method: 'S256', state, nonce,
    });
    const response = NextResponse.redirect(target);
    response.headers.set('Cache-Control', 'no-store');
    response.cookies.set(flowName, await seal({ verifier, state, nonce }, 'flow', 300), cookieOptions(300, '/auth'));
    return response;
  } catch {
    return NextResponse.redirect(origin() + '/?signin=unavailable');
  }
}
