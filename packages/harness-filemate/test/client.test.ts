import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {BackendClient} from '../src/client.ts';

test('actual HTTP transport uses separate credentials and does not follow credential redirects',async(t)=>{
  const seen:string[]=[];
  const server=createServer((req,res)=>{
    seen.push(req.headers.authorization??'');
    if(req.url==='/v1/redirect'){res.writeHead(302,{location:'/v1/leak'});res.end();return;}
    res.setHeader('content-type','application/json');res.end(JSON.stringify({path:req.url}));
  });
  await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));t.after(()=>server.close());
  const address=server.address() as {port:number};
  const client=new BackendClient({url:`http://127.0.0.1:${address.port}`,toolToken:'tool-secret',adminToken:'admin-secret'});
  assert.deepEqual(await client.request('tool','GET','/roots'),{path:'/v1/roots'});
  await client.request('admin','POST','/roots',{path:'D:/fixture'});
  await assert.rejects(()=>client.request('tool','GET','/redirect'));
  assert.deepEqual(seen,['Bearer tool-secret','Bearer admin-secret','Bearer tool-secret']);
});
test('service error messages never echo backend credentials',async(t)=>{
  const server=createServer((_req,res)=>{res.writeHead(500);res.end('tool-secret admin-secret');});
  await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));t.after(()=>server.close());
  const client=new BackendClient({url:`http://127.0.0.1:${(server.address() as {port:number}).port}`,toolToken:'tool-secret',adminToken:'admin-secret'});
  await assert.rejects(()=>client.request('tool','GET','/roots'),e=> !String(e).includes('secret'));
});
