import {existsSync,mkdirSync} from 'node:fs';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';

const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
process.chdir(root);
if(existsSync('.env'))process.loadEnvFile('.env');
if(!process.env.FILEMATE_TOOL_TOKEN||!process.env.FILEMATE_ADMIN_TOKEN){console.error('请先运行 node scripts/init-local.mjs 并配置 .env。');process.exit(1);}
if(!existsSync('packages/harness-filemate/dist/index.js')){console.error('请先执行 npm run build。');process.exit(1);}
const home=resolve('.runtime/dsh');mkdirSync(home,{recursive:true});
const args=['node_modules/@deepseek-ai/dsh/lib/bin.js','--profile','filemate'];
if(!existsSync(resolve(home,'profiles/filemate/package.json')))args.push('--from-default-profile','web');
args.push('--patch',resolve('profiles/filemate.patch.yml'));
const passed=process.argv.slice(2);
args.push(...(passed.length?passed:['--no-open','--host','127.0.0.1','--port','3080']));
const result=spawnSync(process.execPath,args,{stdio:'inherit',env:{...process.env,DSH_HOME:home},windowsHide:true});
if(result.error)console.error(result.error.message);
process.exit(result.status??1);
