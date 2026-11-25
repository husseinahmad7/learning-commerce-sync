import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { once } from 'node:events';
import { exportJWK, generateKeyPair, SignJWT } from 'jose';
import { calculatePKCECodeChallenge } from 'openid-client';

test('built portal completes a signed OIDC round trip, rejects bad state and bad signature, and signs out', { timeout: 30000 }, async t => {
  const { privateKey, publicKey } = await generateKeyPair('RS256');
  const wrong = await generateKeyPair('RS256');
  const jwk = { ...await exportJWK(publicKey), kid: 'integration-test', alg: 'RS256', use: 'sig' };
  const orders = [];
  let issuer, pending, signatureValid = true, exchanges = 0;
  const checks = [];
  const fake = createServer(async (req,res) => {
    res.setHeader('Content-Type','application/json');
    if (req.url === '/.well-known/openid-configuration') {
      return res.end(JSON.stringify({issuer,authorization_endpoint:issuer+'/authorize',token_endpoint:issuer+'/token',jwks_uri:issuer+'/keys',response_types_supported:['code'],subject_types_supported:['public'],id_token_signing_alg_values_supported:['RS256'],code_challenge_methods_supported:['S256']}));
    }
    if (req.url === '/api/checkout/' && req.method === 'POST') {
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      orders.push(JSON.parse(Buffer.concat(chunks).toString('utf8')));
      return res.end(JSON.stringify({enrollment_id:42}));
    }
    if (req.url === '/api/enrollments/42/') return res.end(JSON.stringify({invoice_outcome:'Unknown', learning_status:'Active'}));
    if (req.url === '/keys') return res.end(JSON.stringify({keys:[jwk]}));
    if (req.url === '/token' && req.method === 'POST' && pending) {
      exchanges++;
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      const form = new URLSearchParams(Buffer.concat(chunks).toString('utf8'));

      const matching = [
        form.get('client_id') === 'portal-test' && form.get('client_secret') === 'secret-test',
        form.get('code') === 'mock-code',
        form.get('redirect_uri') === pending.get('redirect_uri'),
        await calculatePKCECodeChallenge(form.get('code_verifier') || 'missing') === pending.get('code_challenge'),
      ];
      checks.push(matching);
      if (matching.some(value => !value)) {
        res.statusCode = 400;
        return res.end(JSON.stringify({error:'invalid_grant'}));
      }
      const nonce = pending.get('nonce');
      pending = null;
      const token = await new SignJWT({nonce,name:'Demo Learner'})
        .setProtectedHeader({alg:'RS256',kid:jwk.kid}).setIssuer(issuer).setSubject('demo-learner')
        .setAudience('portal-test').setIssuedAt().setExpirationTime('5m')
        .sign(signatureValid ? privateKey : wrong.privateKey);
      return res.end(JSON.stringify({access_token:'never-display-this',token_type:'Bearer',id_token:token}));
    }
    res.statusCode = 404;
    res.end('{}');
  });
  fake.listen(0,'127.0.0.1');
  t.after(() => fake.closeAllConnections());
  t.after(() => fake.close());
  await once(fake,'listening');
  issuer = 'http://127.0.0.1:' + fake.address().port;
  const reserve = createServer();
  reserve.listen(0,'127.0.0.1');
  await once(reserve,'listening');
  const port = reserve.address().port;
  await new Promise(resolve => reserve.close(resolve));
  const origin = 'http://127.0.0.1:' + port;
  const next = spawn(process.execPath,['node_modules/next/dist/bin/next','start','--hostname','127.0.0.1','--port',String(port)], {
    env:{ ...process.env, NEXT_TELEMETRY_DISABLED:'1', OIDC_ISSUER:issuer, OIDC_CLIENT_ID:'portal-test', OIDC_CLIENT_SECRET:'secret-test',
      PORTAL_ORIGIN:origin, DEMO_COMMERCE_ORIGIN:issuer, PORTAL_SESSION_SECRET:randomBytes(32).toString('hex'),
      LMS_ORIGIN:'https://learn.example.test', LMS_PY101_PATH:'/courseware/py101/' },
    stdio:['ignore','pipe','pipe'],
  });
  t.after(() => next.kill());
  let output = '';
  next.stdout.on('data',chunk => { output += chunk; });
  next.stderr.on('data',chunk => { output += chunk; });
  const get = (path, options = {}) => fetch(origin + path,{redirect:'manual',...options,signal:AbortSignal.timeout(4000)});
  let home;
  for(let i=0;i<60;i++){
    home = await get('/').catch(() => null);
    if (home) break;
    await new Promise(resolve => setTimeout(resolve,100));
  }
  assert.equal(home?.status,200,output);
  assert.match(await home.text(), /https:\/\/learn\.example\.test\/courseware\/py101\//);
  function callback(state){ return '/auth/callback?code=mock-code&state='+encodeURIComponent(state); }
  async function begin(){
    const result = await get('/auth/login');
    assert.equal(result.status,307);
    const redirect = new URL(result.headers.get('location'));
    assert.equal(redirect.origin,issuer);
    pending = redirect.searchParams;
    assert.equal(pending.get('code_challenge_method'),'S256');
    assert.equal(pending.get('redirect_uri'),origin + '/auth/callback');
    const raw = result.headers.get('set-cookie');
    assert.match(raw,/HttpOnly/i);
    assert.match(raw,/SameSite=lax/i);
    return {cookie:raw.split(';')[0],state:pending.get('state')};
  }
  const bad = await begin();
  assert.match((await get(callback('wrong-state'),{headers:{cookie:bad.cookie}})).headers.get('location'),/signin=failed/);
  assert.equal(exchanges,0);
  signatureValid = false;
  const forged = await begin();
  assert.match((await get(callback(forged.state),{headers:{cookie:forged.cookie}})).headers.get('location'),/signin=failed/);
  signatureValid = true;
  const good = await begin();
  const completed = await get(callback(good.state),{headers:{cookie:good.cookie}});
  assert.equal(completed.headers.get('location'),origin+'/',output+JSON.stringify(checks));
  const session = completed.headers.getSetCookie().find(cookie => cookie.startsWith('lcs-session=')).split(';')[0];
  const signedIn = await (await get('/',{headers:{cookie:session}})).text();
  assert.match(signedIn,/Demo Learner/);
  assert.doesNotMatch(signedIn,/never-display-this/);
  assert.equal((await get('/demo/checkout',{method:'POST',headers:{origin}})).status,401);
  const intent = signedIn.match(/name="intent" value="([^"]+)"/)?.[1];
  assert.ok(intent);
  const submit = () => get('/demo/checkout',{method:'POST',headers:{origin,cookie:session},body:new URLSearchParams({intent})});
  const purchase = await submit();
  assert.equal(purchase.status,303);
  assert.equal((await submit()).status,303);
  assert.equal(orders.length,2);
  assert.equal(orders[0].transaction_id,orders[1].transaction_id);
  assert.equal(orders[0].amount_paid,'99.00');
  assert.match(orders[0].student_email,/@example\.test$/);
  const receipt = purchase.headers.getSetCookie().find(cookie => cookie.startsWith('lcs-checkout=')).split(';')[0];
  assert.match(await (await get('/',{headers:{cookie:session+'; '+receipt}})).text(),/Invoice: Unknown · Learning activation: Active/);
  const rejectedLogout = await get('/auth/logout',{method:'POST',headers:{cookie:session,origin:'https://unrelated.example'}});
  assert.equal(rejectedLogout.status,403);
  const logout = await get('/auth/logout',{method:'POST',headers:{cookie:session,origin}});
  assert.equal(logout.status,303);
  assert.match(logout.headers.get('set-cookie'),/Max-Age=0/i);
});
