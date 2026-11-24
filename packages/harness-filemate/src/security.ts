export const TOOL_NAMES = [
  'filemate_list_roots','filemate_scan_root','filemate_inspect_directory','filemate_search_files',
  'filemate_get_file_metadata','filemate_read_document_excerpt','filemate_create_task',
  'filemate_get_task_context','filemate_create_plan','filemate_get_plan','filemate_validate_plan',
  'filemate_revise_plan','filemate_get_job_report','filemate_create_undo_plan',
] as const;
export const isAllowedTool = (name:string):boolean => (TOOL_NAMES as readonly string[]).includes(name);
export type Role = 'tool'|'admin';
const ID='[a-zA-Z0-9-]+';
const routes:[string,RegExp,Role][] = [
  ['GET',/^\/roots$/,'tool'], ['POST',/^\/roots$/,'admin'],
  ['POST',new RegExp(`^/roots/${ID}/scan$`),'tool'],
  ['GET',new RegExp(`^/roots/${ID}/inspect$`),'tool'],
  ['POST',/^\/tasks$/,'tool'],['GET',/^\/tasks$/,'tool'],['GET',new RegExp(`^/tasks/${ID}$`),'tool'],
  ['POST',/^\/search$/,'tool'],['GET',new RegExp(`^/files/${ID}(/excerpt)?$`),'tool'],
  ['GET',/^\/plans$/,'tool'],['POST',/^\/plans$/,'tool'],
  ['GET',new RegExp(`^/plans/${ID}$`),'tool'],
  ['POST',new RegExp(`^/plans/${ID}/(validate|revise|undo)$`),'tool'],
  ['POST',new RegExp(`^/plans/${ID}/(approve|execute)$`),'admin'],
  ['GET',new RegExp(`^/jobs(/${ID})?$`),'tool'],
  ['POST',new RegExp(`^/jobs/${ID}/(recover|cancel)$`),'admin'],
];
export function resolveUiRequest(method:string,path:string):{role:Role}|undefined {
  const pathname=path.split('?')[0];
  if(pathname.includes('%')||pathname.includes('..')||path.includes('#')||path.includes('\\')) return;
  const route=routes.find(([m,re])=>m===method&&re.test(pathname));
  return route ? {role:route[2]} : undefined;
}
export function requireSameOrigin(method:string,origin:string|undefined,host:string|undefined):void {
  if(method==='GET'||method==='HEAD') return;
  if(!origin||!host) throw new Error('请求缺少来源，请刷新页面后重试');
  const parsed=new URL(origin);
  if(!['http:','https:'].includes(parsed.protocol)||parsed.host!==host||parsed.origin!==origin) throw new Error('拒绝跨站修改请求');
}
export function validateServiceUrl(input:string):string {
  const url=new URL(input);
  if(url.protocol!=='http:'||!['localhost','127.0.0.1','[::1]'].includes(url.hostname)||url.username||url.password||url.pathname!=='/'||url.search||url.hash) throw new Error('FILEMATE_SERVICE_URL 必须是无路径、无凭据的本机 HTTP 地址');
  return url.origin;
}
