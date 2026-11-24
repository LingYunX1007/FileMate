import {validateServiceUrl,type Role} from './security.ts';
export interface BackendConfig {url:string; toolToken:string; adminToken?:string}
export class BackendError extends Error {
  status:number;code:string;detail:Record<string,unknown>;
  constructor(status:number,code:string,message:string,detail:Record<string,unknown>={}){super(message);this.status=status;this.code=code;this.detail={...detail,code,message};}
}
export class BackendClient {
  private readonly base:string;
  private readonly config:BackendConfig;
  constructor(config:BackendConfig){
    this.base=validateServiceUrl(config.url);
    if(!config.toolToken) throw new Error('缺少 FILEMATE_TOOL_TOKEN');
    if(config.adminToken&&config.adminToken===config.toolToken) throw new Error('管理员和工具令牌必须不同');
    this.config=config;
  }
  async request(role:Role,method:string,path:string,body?:unknown,signal?:AbortSignal):Promise<Record<string,unknown>> {
    const token=role==='admin'?this.config.adminToken:this.config.toolToken;
    if(!token) throw new BackendError(503,'CONFIGURATION','审核服务未配置');
    if(!path.startsWith('/')||path.startsWith('//')||path.includes('..')||path.includes('\\')) throw new BackendError(400,'INVALID_PATH','非法业务路径');
    const response=await fetch(this.base+'/v1'+path,{
      method,headers:{authorization:`Bearer ${token}`,...(body===undefined?{}:{'content-type':'application/json'})},
      body:body===undefined?undefined:JSON.stringify(body),redirect:'error',
      signal:signal?AbortSignal.any([signal,AbortSignal.timeout(120000)]):AbortSignal.timeout(120000),
    });
    const text=await response.text();
    let data:Record<string,unknown>;
    try{data=JSON.parse(text);}catch{throw new BackendError(502,'BACKEND_RESPONSE','文件服务返回了无效响应');}
    if(!response.ok){
      const detail=data.detail;
      const err=detail&&typeof detail==='object'&&!Array.isArray(detail)?detail as Record<string,unknown>:{};
      let safe=JSON.stringify(err);
      for(const secret of [this.config.toolToken,this.config.adminToken]) if(secret) safe=safe.split(secret).join('[redacted]');
      const clean=JSON.parse(safe) as Record<string,unknown>;
      throw new BackendError(response.status,String(clean.code??'BACKEND_ERROR'),String(clean.message??'文件服务请求失败'),clean);
    }
    return data;
  }
}
