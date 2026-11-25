import { randomUUID } from 'node:crypto';
import { checkoutCookie, commerceOrigin, commerceRequest } from '../lib/commerce.mjs';
import { cookies } from 'next/headers';
import { courses, courseLink } from '../lib/catalog.mjs';
import { seal, sessionName, unseal } from '../lib/auth.mjs';

export default async function Home({ searchParams }) {
  const params = await searchParams;
  const jar = await cookies();
  const session = await unseal(jar.get(sessionName)?.value, 'session');
  const enabled = commerceOrigin();
  const receipt = session && await unseal(jar.get(checkoutCookie)?.value, 'receipt');
  const status = enabled && receipt && receipt.iss === session.iss && receipt.sub === session.sub && Number.isSafeInteger(receipt.id)
    ? await commerceRequest('/api/enrollments/' + receipt.id + '/').catch(() => ({ unavailable: true })) : null;
  const catalog = await Promise.all(courses.map(async course => ({ ...course,
    intent: enabled && session ? await seal({ iss:session.iss, sub:session.sub, course:course.code, id:'portal-' + randomUUID() }, 'intent', 900) : null,
  })));
  return <main><header><a href="/" aria-label="Learning Commerce Sync home">Learning Commerce Sync</a>
    {session ? <form action="/auth/logout" method="post"><button>Sign out of portal</button></form> :
      process.env.OIDC_ISSUER ? <a className="button" href="/auth/login">Sign in</a> : <span className="tag">Local demo</span>}</header>
    <section className="intro"><p className="tag">LEARNING PORTAL</p><h1>Keep learning.<br/>Leave the plumbing to us.</h1>
      <p>Explore the catalog, then continue in Open edX. The course player runs separately.</p>
      {session && <p>Signed in as <strong>{session.name}</strong>.</p>}
      {['failed', 'unavailable'].includes(params.signin) && <p role="status">Sign-in did not complete. Try again or check the local identity-provider configuration.</p>}
    </section><section aria-labelledby="catalog"><h2 id="catalog">Course catalog</h2>
    <div className="grid">{catalog.map(course => {
      const link = courseLink(course.code);
      return <article key={course.code}><p className="tag">{course.code}</p><h3>{course.title}</h3><p>{course.description}</p><p>Sample price: ${course.price}</p>
        {link ? <a className="button" href={link}>Continue to Open edX</a> : <p className="muted">Set LMS_ORIGIN and LMS_PY101_PATH to enable the course link.</p>}
        {course.intent && <form action="/demo/checkout" method="post"><input type="hidden" name="intent" value={course.intent}/><p><button>Simulate purchase</button></p></form>}</article>;
    })}</div></section>
    {status && <section aria-label="Demo receipt"><h2>Demo receipt</h2>
      <p>{status.unavailable ? 'Status is temporarily unavailable. Reload to check again.' :
        'Invoice: ' + (status.invoice_outcome || 'Pending') + ' · Learning activation: ' + (status.learning_status || 'Pending')}</p>
    </section>}
    <footer>Local reference application. No real charges are collected from this portal.</footer>
  </main>;
}
