import { createHash } from 'node:crypto';
import { NextResponse } from 'next/server';
import { courses } from '../../../lib/catalog.mjs';
import { cookieOptions, origin, seal, sessionName, unseal } from '../../../lib/auth.mjs';
import { checkoutCookie, commerceRequest } from '../../../lib/commerce.mjs';

export async function POST(request) {
  if (request.headers.get('origin') !== origin()) return new Response('Invalid origin', {status:403});
  const session = await unseal(request.cookies.get(sessionName)?.value, 'session');
  if (!session) return new Response('Sign in before starting the demo checkout', {status:401});
  try {
    const form = await request.formData();
    const intent = await unseal(form.get('intent'), 'intent');
    if (!intent || intent.iss !== session.iss || intent.sub !== session.sub) return new Response('Invalid checkout intent',{status:400});
    const course = courses.find(entry => entry.code === intent.course);
    if (!course) return new Response('Unknown course',{status:400});
    const email = createHash('sha256').update(JSON.stringify([session.iss,session.sub])).digest('hex').slice(0,40) + '@example.test';
    const receipt = await commerceRequest('/api/checkout/', { method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({student_email:email,student_name:'Demo Learner',course_code:course.code,amount_paid:course.price,
        transaction_id:intent.id}) });
    if (!Number.isSafeInteger(receipt.enrollment_id) || receipt.enrollment_id < 1) throw new Error('Missing receipt');
    const response = NextResponse.redirect(origin() + '/',303);
    response.cookies.set(checkoutCookie,await seal({iss:session.iss,sub:session.sub,id:receipt.enrollment_id},'receipt',3600),cookieOptions(3600));
    response.headers.set('Cache-Control','no-store');
    return response;
  } catch {
    // 503 leaves the browser on the same submitted URL, so the same intent can be retried.
    return new Response('Demo checkout could not be confirmed. Retry this same submission to avoid a duplicate.', {status:503, headers:{'Cache-Control':'no-store','Retry-After':'5'}});
  }
}
