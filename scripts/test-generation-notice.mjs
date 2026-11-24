// Local browser fixture: no user sessions, model requests or file operations.
import {createServer} from 'node:http';
import {readFileSync} from 'node:fs';
import {once} from 'node:events';
import assert from 'node:assert/strict';
import {chromium} from 'playwright';

const notice={sessionId:'session-test',attemptId:'attempt-test',stoppedAt:1,
  reason:'FileMate：本次生成耗时超过 120 秒，已停止。<img src=x onerror=alert(1)>'};
const server=createServer((req,res)=>{
  if(req.url==='/filemate/generation-status'){
    res.setHeader('content-type','application/json');res.end(JSON.stringify({items:[notice]}));
  }else if(req.url==='/filemate/launcher.js'){
    res.setHeader('content-type','text/javascript; charset=utf-8');res.end(readFileSync('packages/harness-filemate/public/launcher.js'));
  }else{
    res.setHeader('content-type','text/html; charset=utf-8');res.end('<!doctype html><html><body><h1>Host fixture</h1><script src="/filemate/launcher.js"></script></body></html>');
  }
});
server.listen(0,'127.0.0.1');await once(server,'listening');
const browser=await chromium.launch({channel:'chrome',headless:true,args:['--disable-gpu','--no-sandbox']});
try{
  const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  const alert=page.getByRole('alert');await alert.waitFor({state:'visible'});
  assert.ok((await alert.textContent()).includes(notice.reason));
  assert.equal(await alert.locator('img').count(),0);
  await page.getByRole('button',{name:'知道了',exact:true}).click();
  await alert.waitFor({state:'hidden'});
  await page.reload();
  await page.waitForResponse(r=>r.url().endsWith('/filemate/generation-status'));
  assert.equal(await page.getByRole('alert').isVisible(),false);
  notice.stoppedAt=2;
  await page.getByRole('alert').waitFor({state:'visible',timeout:10000});
  assert.deepEqual(errors,[]);
  console.log('Generation notice browser test passed: visible reason, literal text, dismissal on reload, later attempt reuse still notifies.');
}finally{
  await browser.close();server.closeAllConnections();await new Promise(r=>server.close(r));
}
