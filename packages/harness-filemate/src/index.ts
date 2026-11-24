import type {Context} from '@deepseek-ai/cordis';
import type {} from '@deepseek-ai/dsh-host-webserver';
import type {} from '@deepseek-ai/dsh-tools';
import type {} from '@deepseek-ai/dsh-client-connection';
import type {} from '@deepseek-ai/dsh-agent';
import {readFile} from 'node:fs/promises';
import type {IncomingMessage,ServerResponse} from 'node:http';
import {BackendClient,BackendError} from './client.ts';
import {isAllowedTool,resolveUiRequest,requireSameOrigin} from './security.ts';
import {installGenerationGuard} from './generation-guard.ts';
import {GenerationNotices} from './generation-notices.ts';

export const name='filemate-host';
export const inject=['webServer','connection','tools','agents'];
function send(res:ServerResponse,status:number,data:unknown){
  res.writeHead(status,{'content-type':'application/json; charset=utf-8','cache-control':'no-store','x-content-type-options':'nosniff'});res.end(JSON.stringify(data));
}
async function body(req:IncomingMessage):Promise<unknown>{
  let size=0;const chunks:Buffer[]=[];
  for await(const chunk of req){size+=chunk.length;if(size>256*1024)throw new BackendError(413,'TOO_LARGE','计划过大，请分批处理');chunks.push(Buffer.from(chunk));}
  const raw=Buffer.concat(chunks).toString('utf8');
  if(!raw)return undefined;
  try{return JSON.parse(raw);}catch{throw new BackendError(400,'INVALID_JSON','请求不是有效 JSON');}
}
export function apply(ctx:Context):void {
  const notices=new GenerationNotices();
  installGenerationGuard(ctx,undefined,notice=>notices.add(notice));
  ctx.effect(()=>ctx.webServer.register({kind:'exact',path:'/filemate/generation-status',handler:async(req,res)=>{
    const admission=ctx.connection.admit(req);
    if('rejection' in admission){send(res,admission.rejection,{detail:{code:'UNAUTHORIZED',message:'请先登录 dsh'}});return;}
    if(admission.peer.principal.kind!=='owner'){send(res,403,{detail:{code:'OWNER_REQUIRED',message:'仅本机用户可查看'}});return;}
    if(req.method!=='GET'){send(res,405,{detail:{code:'METHOD_NOT_ALLOWED',message:'只读接口'}});return;}
    send(res,200,{items:notices.list()});
  }}));
  const client=new BackendClient({url:process.env.FILEMATE_SERVICE_URL??'http://127.0.0.1:8765',toolToken:process.env.FILEMATE_TOOL_TOKEN??'',adminToken:process.env.FILEMATE_ADMIN_TOKEN??''});
  // Global monotonic guard also covers an accidental switch to another preset.
  ctx.effect(()=>ctx.tools.guard(exec=>isAllowedTool(exec.name)?undefined:'FileMate 仅允许专用文件工具；通用代码、命令和写文件工具已禁用。'));
  const calls=new Map<string,{turn:number;count:number}>();
  ctx.effect(()=>ctx.tools.guard(exec=>{
    const key=String(exec.agent?.id??'unknown');
    const turn=exec.agent?ctx.agents.turnBoundaryOf(exec.agent.session).lastTurn:0;
    const old=calls.get(key);const n=(old?.turn===turn?old.count:0)+1;calls.set(key,{turn,count:n});
    return n>40?'本轮工具调用预算已用完，请汇报已知结果并等待用户继续。':undefined;
  }));
  ctx.effect(()=>ctx.webServer.register({kind:'prefix',path:'/api/filemate',handler:async(req,res)=>{
    const admission=ctx.connection.admit(req);
    if('rejection' in admission){send(res,admission.rejection,{detail:{code:'UNAUTHORIZED',message:'请先登录 dsh'}});return;}
    if(admission.peer.principal.kind!=='owner'){send(res,403,{detail:{code:'OWNER_REQUIRED',message:'只有当前本机用户可以访问文件管理'}});return;}
    try{
      const method=req.method??'GET';
      requireSameOrigin(method,req.headers.origin,req.headers.host);
      const path=(req.url??'').slice('/api/filemate'.length);
      const route=resolveUiRequest(method,path);
      if(!route){send(res,404,{detail:{code:'NOT_FOUND',message:'未知操作'}});return;}
      if(method==='POST'&&!String(req.headers['content-type']??'').startsWith('application/json'))throw new BackendError(415,'CONTENT_TYPE','请使用 JSON 请求');
      const result=await client.request(route.role,method,path,method==='POST'?await body(req):undefined);
      send(res,200,result);
    }catch(error){
      if(error instanceof BackendError)send(res,error.status,{detail:error.detail});
      else send(res,400,{detail:{code:'REQUEST_FAILED',message:error instanceof TypeError?'无法连接文件服务，请检查服务状态':'请求被拒绝，请刷新页面后重试'}});
    }
  }}));
  const assets:Record<string,[string,string]>={
    '/filemate':['index.html','text/html'], '/filemate/':['index.html','text/html'],
    '/filemate/app.js':['app.js','text/javascript'],'/filemate/style.css':['style.css','text/css'],
    '/filemate/launcher.js':['launcher.js','text/javascript'],
  };
  for(const [path,[file,type]] of Object.entries(assets))ctx.effect(()=>ctx.webServer.register({kind:'exact',path,handler:async(req,res)=>{
    const admission=ctx.connection.admit(req);
    if('rejection' in admission){res.writeHead(admission.rejection);res.end('Please sign in to dsh first.');return;}
    if(admission.peer.principal.kind!=='owner'){res.writeHead(403);res.end();return;}
    if(req.method!=='GET'&&req.method!=='HEAD'){res.writeHead(405);res.end();return;}
    try{
      const content=await readFile(new URL('../public/'+file,import.meta.url));
      res.writeHead(200,{'content-type':type+'; charset=utf-8','cache-control':'no-store','x-content-type-options':'nosniff','content-security-policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'self'; object-src 'none'; base-uri 'none'"});res.end(req.method==='HEAD'?undefined:content);
    }catch{res.writeHead(503);res.end('FileMate assets unavailable');}
  }}));
  ctx.on('webserver/index-inject',(table,peer)=>{
    if(peer.principal.kind==='owner')table.push({kind:'script-src',placement:'body',src:'/filemate/launcher.js'});
  });
}
