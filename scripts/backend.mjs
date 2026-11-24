import {existsSync} from 'node:fs';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
process.chdir(resolve(dirname(fileURLToPath(import.meta.url)),'..'));
if(existsSync('.env'))process.loadEnvFile('.env');
const python=process.env.FILEMATE_PYTHON??(process.platform==='win32'?'.venv/Scripts/python.exe':'.venv/bin/python');
const commands={migrate:['-m','alembic','upgrade','head'],api:['-m','uvicorn','services.filemate.api:create_app','--factory','--host','127.0.0.1','--port','8765'],worker:['-m','services.filemate.worker'],test:['-m','pytest','-q']};
const args=commands[process.argv[2]];
if(!args){console.error('用法：node scripts/backend.mjs migrate|api|worker|test');process.exit(1);}
const result=spawnSync(python,[...args,...process.argv.slice(3)],{stdio:'inherit',env:process.env,windowsHide:true});
if(result.error)console.error('Python 启动失败，请先创建 .venv 并安装项目依赖：'+result.error.message);
process.exit(result.status??1);
