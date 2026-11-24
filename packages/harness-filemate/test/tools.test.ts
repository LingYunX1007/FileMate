import {test} from 'node:test';
import assert from 'node:assert/strict';
import {Context} from '@deepseek-ai/cordis';
import {SystemPrompt} from '@deepseek-ai/dsh-system-prompt';
import {ToolsService,assertSupportedJsonSchema,type ToolExecution} from '@deepseek-ai/dsh-tools';
import {BackendClient} from '../src/client.ts';
import {createTools} from '../src/tools.ts';
import {isAllowedTool} from '../src/security.ts';
import {createServer} from 'node:http';

test('actual dsh registry accepts the plugin catalog with no execute or approval capability',async()=>{
  const ctx=new Context();
  try{
    await ctx.plugin(SystemPrompt,{});
    await ctx.plugin(ToolsService);
    const tools=createTools(new BackendClient({url:'http://127.0.0.1:8765',toolToken:'test-tool-token'}));
    for(const tool of tools){assert.ok(isAllowedTool(tool.name));assertSupportedJsonSchema(tool.parameters);ctx.tools.register(tool);}
    assert.ok(ctx.tools.get('filemate_create_plan'));
    assert.equal(ctx.tools.get('filemate_approve'),undefined);
    assert.equal(ctx.tools.get('filemate_execute'),undefined);
    // Guard is part of the actual upstream registry, not a mocked permission check.
    ctx.tools.guard(exec=>isAllowedTool(exec.name)?undefined:'denied');
    assert.equal(ctx.tools.checkGuards({name:'exec_command'} as ToolExecution)?.reason,'denied');
    assert.equal(ctx.tools.checkGuards({name:'filemate_search_files'} as ToolExecution),undefined);
    assert.equal(ctx.tools.get('filemate_search_files')?.parameters.type,'object');
  }finally{await ctx.fiber.dispose();}
});

test('resume without a remembered task UUID queries the actual calling session',async(t)=>{
  const server=createServer((req,res)=>{res.setHeader('content-type','application/json');res.end(JSON.stringify({url:req.url}));});
  await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));t.after(()=>server.close());
  const client=new BackendClient({url:`http://127.0.0.1:${(server.address() as {port:number}).port}`,toolToken:'test'});
  const tool=createTools(client).find(t=>t.name==='filemate_get_task_context')!;
  const result=await tool.execute({}, {agent:{id:'session-actual'},signal:new AbortController().signal} as never);
  assert.deepEqual(result,{url:'/v1/tasks?session_id=session-actual'});
});

test('subdirectory enumeration forwards scope and paging to the service without mixing root files',async(t)=>{
  const received:unknown[]=[];
  const server=createServer(async(req,res)=>{
    let raw='';for await(const part of req)raw+=part;
    const query=JSON.parse(raw);received.push(query);
    res.setHeader('content-type','application/json');
    res.end(JSON.stringify({total:12,returned_count:query.offset===0?8:4,has_more:query.offset===0,next_offset:query.offset===0?8:null,items:[]}));
  });
  await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));t.after(()=>server.close());
  const client=new BackendClient({url:`http://127.0.0.1:${(server.address() as {port:number}).port}`,toolToken:'test'});
  const tool=createTools(client).find(t=>t.name==='filemate_search_files')!;
  const first=await tool.execute({root_id:'root-1',directory:'Test',limit:8,offset:0},{signal:new AbortController().signal} as never) as {next_offset:number};
  await tool.execute({root_id:'root-1',directory:'Test',limit:8,offset:first.next_offset},{signal:new AbortController().signal} as never);
  assert.deepEqual(received,[{root_id:'root-1',directory:'Test',limit:8,offset:0},{root_id:'root-1',directory:'Test',limit:8,offset:8}]);
});
