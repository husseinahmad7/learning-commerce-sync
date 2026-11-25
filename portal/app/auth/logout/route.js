import { checkoutCookie } from '../../../lib/commerce.mjs';
import { NextResponse } from 'next/server';
import { cookieOptions, origin, sessionName } from '../../../lib/auth.mjs';

export function POST(request) {
  if (request.headers.get('origin') !== origin()) return new Response('Invalid origin', { status: 403 });
  const response = NextResponse.redirect(origin() + '/', 303);
  response.cookies.set(sessionName, '', cookieOptions(0));
  response.cookies.set(checkoutCookie, '', cookieOptions(0));
  response.headers.set('Cache-Control', 'no-store');
  return response;
}
