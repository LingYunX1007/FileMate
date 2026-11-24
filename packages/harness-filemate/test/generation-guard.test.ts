import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {Context} from '@deepseek-ai/cordis';
import {installGenerationGuard,readGenerationLimits} from '../src/generation-guard.ts';

function harness(limits={maxMs:30,maxChars:20}) {
  const ctx=new Context();
  const abort=new AbortController();
  const causes:unknown[]=[];
  const notices:unknown[]=[];
  const agent={id:'session-1',status:'running',cancel(cause:unknown){causes.push(cause);abort.abort(cause);}};
  const dispose=installGenerationGuard(ctx,limits,notice=>notices.push(notice));
  let revision=0;
  const frame=(value:object)=>ctx.emit('agent/assistant-stream',{agent,frame:{attemptId:'attempt-1',revision:++revision,...value}} as never);
  const start=()=>frame({type:'start',turn:1,step:1});
  const delta=(text:string,type='reasoning-delta')=>frame({type:'chunk',index:0,time:Date.now(),chunk:{type,index:0,text}});
  return {ctx,agent,abort,causes,notices,dispose,frame,start,delta};
}
const delay=(ms:number)=>new Promise(resolve=>setTimeout(resolve,ms));

test('reasoning alone cancels the actual request signal at the character budget',()=>{
  const h=harness();h.start();h.delta('x'.repeat(19));assert.equal(h.abort.signal.aborted,false);
  h.delta('x');assert.equal(h.abort.signal.aborted,true);
  assert.match((h.abort.signal.reason as {reason:string}).reason,/已停止/);
  h.delta('more');assert.equal(h.causes.length,1);assert.equal(h.notices.length,1);
  assert.equal((h.notices[0] as {sessionId:string}).sessionId,'session-1');h.dispose();
});
test('timeout abort reaches a pending local HTTP request with no tokens',{timeout:5000},async(t)=>{
  const server=createServer((_req,res)=>{res.writeHead(200);res.flushHeaders();res.write('pending');});
  server.listen(0,'127.0.0.1');await once(server,'listening');
  const h=harness({maxMs:80,maxChars:100});
  try {
    const address=server.address() as {port:number};
    const response=await fetch(`http://127.0.0.1:${address.port}`,{signal:AbortSignal.any([h.abort.signal,t.signal])});
    const reading=response.text();h.start();
    await assert.rejects(reading);assert.equal(h.abort.signal.aborted,true);assert.equal(h.causes.length,1);
  } finally {h.dispose();server.closeAllConnections();await new Promise<void>(resolve=>server.close(()=>resolve()));}
});
test('completed generation disarms timer during tool waits; next generation resets count',async()=>{
  const h=harness();h.start();h.delta('x'.repeat(19));h.frame({type:'end'});
  await delay(50);assert.equal(h.causes.length,0);
  h.start();h.delta('xx');assert.equal(h.causes.length,0);h.dispose();
  await delay(50);assert.equal(h.causes.length,0);
});
test('idle and agent disposal clear pending timers',async()=>{
  for(const event of ['agent/status','agent/disposed'] as const){
    const h=harness();h.start();h.ctx.emit(event,{agent:h.agent,status:'idle'} as never);
    await delay(50);assert.equal(h.causes.length,0);h.dispose();
  }
});
test('assembled block does not count its deltas twice; block-only output is capped',()=>{
  const h=harness();h.start();h.delta('1234567890');
  h.frame({type:'chunk',chunk:{type:'block-end',index:0,block:{type:'reasoning',text:'1234567890'}}});
  assert.equal(h.causes.length,0);
  h.frame({type:'chunk',chunk:{type:'block-end',index:1,block:{type:'text',text:'1234567890'}}});
  assert.equal(h.causes.length,1);h.dispose();
});
test('environment overrides are bounded and malformed values use defaults',()=>{
  assert.deepEqual(readGenerationLimits({}),{maxMs:120000,maxChars:24000});
  assert.deepEqual(readGenerationLimits({FILEMATE_GENERATION_TIMEOUT_MS:'1',FILEMATE_GENERATION_MAX_CHARS:'999999999'}),{maxMs:1000,maxChars:200000});
  assert.deepEqual(readGenerationLimits({FILEMATE_GENERATION_TIMEOUT_MS:'NaN',FILEMATE_GENERATION_MAX_CHARS:'-1'}),{maxMs:120000,maxChars:24000});
});
test('external turn cancellation disarms immediately and cannot arm that aborted turn again',async()=>{
  const h=harness();
  await h.ctx.waterfall('agent/pre-step',{agent:h.agent,signal:h.abort.signal} as never,async()=>({kind:'reject'}));
  h.start();h.abort.abort({kind:'user'});await delay(50);
  h.start();await delay(50);assert.equal(h.causes.length,0);h.dispose();
});
test('tool arguments count and a stale attempt end cannot clear a newer generation',()=>{
  const h=harness();h.start();
  h.frame({type:'start',attemptId:'attempt-2',turn:1,step:2});
  h.frame({type:'end',attemptId:'attempt-1'});
  h.frame({type:'chunk',attemptId:'attempt-2',chunk:{type:'tool-call-delta',index:1,id:'call-1',argumentsDelta:'x'.repeat(20)}});
  assert.equal(h.causes.length,1);h.dispose();
});
test('identical attempt ids in different agents do not share output budgets',()=>{
  const ctx=new Context();const canceled:string[]=[];
  const dispose=installGenerationGuard(ctx,{maxMs:100,maxChars:20});
  const a={id:'a',cancel(){canceled.push('a');}},b={id:'b',cancel(){canceled.push('b');}};
  for(const agent of [a,b])ctx.emit('agent/assistant-stream',{agent,frame:{type:'start',attemptId:'same',turn:1,step:1}} as never);
  for(const [agent,text] of [[a,'x'.repeat(20)],[b,'x']] as const)ctx.emit('agent/assistant-stream',{agent,frame:{type:'chunk',attemptId:'same',chunk:{type:'text-delta',index:0,text}}} as never);
  assert.deepEqual(canceled,['a']);dispose();
});
test('real dsh AgentLoop cancellation aborts HTTP and persists hook cause without retry',{timeout:5000},async(t)=>{
  const [{default:SessionStore},{default:Projections},{default:Agents},{default:SystemPrompt},{default:Tools},{default:AgentLoop},{createUserMessage},Executor]=await Promise.all([
    import('@deepseek-ai/dsh-session'),import('@deepseek-ai/dsh-session-projection'),import('@deepseek-ai/dsh-agent'),
    import('@deepseek-ai/dsh-system-prompt'),import('@deepseek-ai/dsh-tools'),import('@deepseek-ai/dsh-agent-loop'),import('@deepseek-ai/dsh-llm'),import('@deepseek-ai/dsh-tool-executor-native'),
  ]);
  const server=createServer((_req,res)=>{res.writeHead(200);res.flushHeaders();res.write('pending');});
  server.listen(0,'127.0.0.1');await once(server,'listening');
  const ctx=new Context();let requestSignal:AbortSignal|undefined;let requests=0;let requestAborted=false;
  const records:unknown[]=[];
  const errors:unknown[]=[];ctx.on('agent/error',({error})=>errors.push(error));
  const address=server.address() as {port:number};
  ctx.provide('llm',{
    async prepareCall(config:object){return {config,stream:async function*(request:{signal:AbortSignal}){
      requestSignal=request.signal;requests++;
      try {const response=await fetch(`http://127.0.0.1:${address.port}`,{signal:AbortSignal.any([request.signal,t.signal])});await response.text();}
      catch(error){requestAborted=request.signal.aborted;throw error;}
    }};},
  } as never);
  try {
    for(const plugin of [SessionStore,Projections,Agents,SystemPrompt,Tools,Executor,AgentLoop])await ctx.plugin(plugin as never,{}).await();
    ctx.on('session/event',(_session,event)=>{records.push(event);});
    installGenerationGuard(ctx,{maxMs:100,maxChars:100});
    const agent=await ctx.agentLoop.create('guard-integration' as never,{provider:'local-test',model:'fake'});
    agent.followup(createUserMessage({content:[{type:'text',text:'test'}],source:{kind:'user'}}));
    await agent.whenIdle();
    assert.deepEqual(errors,[]);assert.equal(requests,1);assert.equal(requestSignal?.aborted,true);assert.equal(requestAborted,true);
    const end=records.find((value:any)=>value.type==='turn/end') as any;
    assert.equal(end?.data.reason.kind,'aborted');assert.equal(end.data.reason.reason.kind,'hook');assert.match(end.data.reason.reason.reason,/已停止/);
  } finally {await ctx.fiber.dispose();server.closeAllConnections();await new Promise<void>(resolve=>server.close(()=>resolve()));}
});
