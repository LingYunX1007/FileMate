import {randomBytes} from 'node:crypto';
import {writeFileSync,existsSync,mkdirSync} from 'node:fs';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
process.chdir(resolve(dirname(fileURLToPath(import.meta.url)),'..'));
if(existsSync('.env')){console.log('.env 已存在，保留现有配置。');process.exit(0);}
mkdirSync('.runtime',{recursive:true});
const secret=()=>randomBytes(32).toString('hex');
const password=secret();
writeFileSync('.env',`# FileMate 本机配置。不要提交或分享此文件。\nPOSTGRES_PASSWORD=${password}\nFILEMATE_DATABASE_URL=postgresql+psycopg://filemate:${password}@127.0.0.1:54329/filemate\nFILEMATE_SERVICE_URL=http://127.0.0.1:8765\nFILEMATE_TOOL_TOKEN=${secret()}\nFILEMATE_ADMIN_TOKEN=${secret()}\n# 可在 dsh Web 的模型设置中填写 API Key\n# DEEPSEEK_API_KEY=\n`,{flag:'wx',mode:0o600});
console.log('已生成 .env（随机独立令牌）。请配置数据库和模型。');
