import {test} from 'node:test';
import assert from 'node:assert/strict';
import {apply} from '../src/index.ts';

test('generation notices enforce host admission and owner-only read access',async()=>{
  const routes=new Map<string,any>();const disposers:Function[]=[];
  let admission:any={rejection:401};
  const previous=process.env.FILEMATE_TOOL_TOKEN;
  process.env.FILEMATE_TOOL_TOKEN='route-test-tool';
  try{
    const ctx={
      effect(fn:Function){const dispose=fn();if(typeof dispose==='function')disposers.push(dispose);},
      on(){return ()=>{};},
      webServer:{register(route:any){routes.set(route.path,route);return ()=>{};}},
      connection:{admit(){return admission;}},
      tools:{guard(){return ()=>{};}},
    };
    apply(ctx as never);
    const handler=routes.get('/filemate/generation-status').handler;
    async function request(method='GET'){
      let status=0,headers:any,body:any;
      await handler({method},{writeHead(s:number,h:any){status=s;headers=h;},end(raw:string){body=JSON.parse(raw);}});
      return {status,headers,body};
    }
    assert.equal((await request()).status,401);
    admission={peer:{principal:{kind:'guest'}}};assert.equal((await request()).status,403);
    admission={peer:{principal:{kind:'owner'}}};
    const ok=await request();assert.equal(ok.status,200);assert.deepEqual(ok.body,{items:[]});
    assert.equal(ok.headers['cache-control'],'no-store');
    assert.equal((await request('POST')).status,405);
  }finally{
    for(const dispose of disposers.reverse())dispose();
    if(previous===undefined)delete process.env.FILEMATE_TOOL_TOKEN;else process.env.FILEMATE_TOOL_TOKEN=previous;
  }
});
