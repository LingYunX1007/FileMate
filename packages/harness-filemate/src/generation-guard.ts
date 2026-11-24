import type {Context} from '@deepseek-ai/cordis';
import type {Agent,AssistantStreamFrame} from '@deepseek-ai/dsh-agent';

export interface GenerationLimits {maxMs:number;maxChars:number}
export interface GenerationStopNotice {sessionId:string;attemptId:string;reason:string;stoppedAt:number}
function bounded(raw:string|undefined,fallback:number,min:number,max:number):number {
  const n=Number(raw);
  return raw?.trim()&&Number.isSafeInteger(n)&&n>0?Math.min(max,Math.max(min,n)):fallback;
}
/** Deliberately finite: environment settings cannot disable the safety budget. */
export function readGenerationLimits(env:NodeJS.ProcessEnv=process.env):GenerationLimits {
  return {maxMs:bounded(env.FILEMATE_GENERATION_TIMEOUT_MS,120000,1000,600000),
    maxChars:bounded(env.FILEMATE_GENERATION_MAX_CHARS,24000,1000,200000)};
}

/** Guard one model attempt, including time to first token, never tool execution.
 * rc.5 emits start before consuming the LLM stream and end before tool dispatch.
 * cancel aborts the request's turn signal and persists the hook reason in turn/end.
 * Text size is UTF-16 code units; reasoning and tool argument deltas count too.
 */
export function installGenerationGuard(ctx:Context,limits:GenerationLimits=readGenerationLimits(),onStop?:(notice:GenerationStopNotice)=>void):()=>void {
  if(!Number.isSafeInteger(limits.maxMs)||limits.maxMs<=0||limits.maxMs>600000||
    !Number.isSafeInteger(limits.maxChars)||limits.maxChars<=0||limits.maxChars>200000)throw new RangeError('Invalid generation limits');
  type Attempt={id:AssistantStreamFrame['attemptId'];timer:ReturnType<typeof setTimeout>;chars:number;blocks:Map<number,number>};
  const attempts=new Map<Agent,Attempt>();
  const signals=new Map<Agent,{signal:AbortSignal;remove:()=>void}>();
  const clear=(agent:Agent)=>{const state=attempts.get(agent);if(state)clearTimeout(state.timer);attempts.delete(agent);};
  const forget=(agent:Agent)=>{clear(agent);signals.get(agent)?.remove();signals.delete(agent);};
  const stop=(agent:Agent,state:Attempt,reason:string)=>{
    if(attempts.get(agent)!==state)return;
    clear(agent);
    // Default cancellation clears queued work; never automatically retry the looping turn.
    const notice:GenerationStopNotice={sessionId:String(agent.id),attemptId:String(state.id),reason:`FileMate：本次生成${reason}，已停止以避免持续空转。请缩小任务范围后重新发送。`,stoppedAt:Date.now()};
    try {agent.cancel({kind:'hook',reason:notice.reason});}
    catch {console.warn('FileMate generation cancellation encountered an inbox error; the dsh turn signal was still aborted.');}
    try {onStop?.(notice);}
    catch {console.warn('FileMate generation-stop notification failed.');}
  };
  const off=[
    ctx.on('agent/pre-step',async({agent,signal},next)=>{
      forget(agent);
      const abort=()=>clear(agent);
      signal.addEventListener('abort',abort,{once:true});
      signals.set(agent,{signal,remove:()=>signal.removeEventListener('abort',abort)});
      return next();
    }),
    ctx.on('agent/assistant-stream',({agent,frame})=>{
      if(frame.type==='start'){
        clear(agent);
        if(signals.get(agent)?.signal.aborted)return;
        const state:Attempt={id:frame.attemptId,chars:0,blocks:new Map(),timer:setTimeout(()=>stop(agent,state,`耗时超过 ${limits.maxMs/1000} 秒`),limits.maxMs)};
        state.timer.unref();attempts.set(agent,state);return;
      }
      const state=attempts.get(agent);
      if(!state||state.id!==frame.attemptId)return;
      if(frame.type==='end'){clear(agent);return;}
      const chunk=frame.chunk;
      let size=0;
      if(chunk.type==='text-delta'||chunk.type==='reasoning-delta')size=chunk.text.length;
      else if(chunk.type==='tool-call-delta')size=chunk.argumentsDelta.length;
      else if(chunk.type==='block-end'){
        const block=chunk.block;
        const total=block.type==='text'||block.type==='reasoning'?block.text.length:block.type==='tool-call'?block.arguments.length:0;
        size=Math.max(0,total-(state.blocks.get(chunk.index)??0));
      }
      if(size){
        if('index' in chunk)state.blocks.set(chunk.index,(state.blocks.get(chunk.index)??0)+size);
        state.chars+=size;
        if(state.chars>=limits.maxChars)stop(agent,state,`输出达到 ${limits.maxChars} 字符上限`);
      }
    }),
    ctx.on('agent/status',({agent,status})=>{if(status==='idle')forget(agent);}),
    ctx.on('agent/disposed',({agent})=>forget(agent)),
  ];
  const dispose=()=>{for(const remove of off)remove();for(const agent of attempts.keys())clear(agent);for(const agent of signals.keys())forget(agent);};
  ctx.effect(()=>dispose);
  return dispose;
}
