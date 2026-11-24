// Requires a running isolated dsh started with scripts/start-dsh.mjs.
// Reads its launch token locally; never prints credentials or cookies.
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
const log=readFileSync(process.env.FILEMATE_DSH_SMOKE_LOG??'work/dsh-server.log','utf8');
const launch=log.match(/dsh web: (http:\/\/127\.0\.0\.1:\d+\/\?token=\S+)/)?.[1];
assert.ok(launch,'dsh has not printed a launch URL');
const base=new URL(launch).origin;
assert.equal((await fetch(base+'/api/filemate/roots')).status,401,'anonymous API must be denied');
assert.equal((await fetch(base+'/filemate/generation-status')).status,401,'anonymous generation notices must be denied');
const login=await fetch(launch,{redirect:'manual'});
const cookie=login.headers.getSetCookie().map(s=>s.split(';')[0]).join('; ');
assert.ok(cookie,'dsh login did not issue a cookie');
const headers={cookie};
const notices=await fetch(base+'/filemate/generation-status',{headers});assert.equal(notices.status,200);
assert.ok(Array.isArray((await notices.json()).items));
assert.equal((await fetch(base+'/filemate/generation-status',{method:'POST',headers})).status,405);
const page=await fetch(base+'/',{headers});assert.equal(page.status,200);
assert.ok((await page.text()).includes('/filemate/launcher.js'),'original Web must load FileMate extension');
for(const path of ['/filemate','/filemate/app.js','/filemate/style.css','/filemate/launcher.js'])assert.equal((await fetch(base+path,{headers})).status,200,path);
const cross=await fetch(base+'/api/filemate/roots',{method:'POST',headers:{...headers,origin:'https://evil.example','content-type':'application/json'},body:'{}'});
assert.ok([400,403].includes(cross.status),'cross-origin mutation must fail');
const unknown=await fetch(base+'/api/filemate/shell',{method:'POST',headers:{...headers,origin:base,'content-type':'application/json'},body:'{}'});
assert.equal(unknown.status,404,'arbitrary proxy paths must fail');
console.log('dsh Web smoke: login, embedded assets, anonymous rejection, CSRF rejection, route allowlist passed');
