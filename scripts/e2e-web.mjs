// Opt-in integration test: uses only fresh fixtures under work/e2e-*.
// Requires the test API/worker + dsh Web running with .env service tokens.
const {chromium}=await import(process.env.FILEMATE_PLAYWRIGHT_MODULE??'playwright');
import {readFileSync,mkdirSync,writeFileSync,existsSync} from 'node:fs';
import {resolve} from 'node:path';
import assert from 'node:assert/strict';
import {BackendClient} from '../packages/harness-filemate/dist/client.js';
process.loadEnvFile('.env');
const client=new BackendClient({url:'http://127.0.0.1:8765',toolToken:process.env.FILEMATE_TOOL_TOKEN,adminToken:process.env.FILEMATE_ADMIN_TOKEN});
const directory=resolve('work/e2e-'+Date.now());mkdirSync(directory,{recursive:true});
writeFileSync(directory+'/合同草稿.txt','客户：测试公司\n这是一份待归档的合同。\n');
const root=await client.request('admin','POST','/roots',{path:directory,label:'浏览器端到端测试'});
await client.request('tool','POST',`/roots/${root.id}/scan`);
const results=await client.request('tool','POST','/search',{root_id:root.id,query:'合同'});
assert.equal(results.items.length,1);
const file=results.items[0];
const evidence=await client.request('tool','GET',`/files/${file.id}/excerpt`);
const plan=await client.request('tool','POST','/plans',{title:'浏览器审核测试',items:[{file_id:file.id,target_root_id:root.id,target_path:'测试公司/合同.txt',reason:'正文客户名称',evidence:{file_id:file.id,version:evidence.version,location:evidence.location}}]});
const launch=readFileSync('work/dsh-server.log','utf8').match(/dsh web: (http:\/\/127\.0\.0\.1:\d+\/\?token=\S+)/)?.[1];assert.ok(launch);
const base=new URL(launch).origin;
const browser=await chromium.launch({channel:'chrome',headless:true,args:['--disable-gpu','--no-sandbox']});
try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(launch);await page.waitForURL(url=>!url.searchParams.has('token'));
 await page.getByRole('button',{name:'打开 FileMate 文件工作台'}).click();
 const frame=page.frameLocator('iframe[title="FileMate 文件工作台"]');
 await frame.getByRole('heading',{name:'授权目录'}).waitFor({timeout:15000});
 await page.screenshot({path:'work/dsh-embedded.png',fullPage:true});
 // Open the same authenticated business page for full-width plan interaction.
 await page.goto(base+'/filemate?plan='+plan.id);
 await page.getByRole('button',{name:'校验计划',exact:true}).click();
 await page.locator('#confirm-plan').check();
 await page.getByRole('button',{name:'批准此版本',exact:true}).click();
 await page.getByRole('button',{name:'执行已批准版本',exact:true}).click();
 await page.locator('#job-detail .detail-heading').getByText('已完成',{exact:true}).waitFor({timeout:20000});
 assert.ok(existsSync(directory+'/测试公司/合同.txt'));
 assert.ok(!existsSync(directory+'/合同草稿.txt'));
 await page.screenshot({path:'work/filemate-report.png',fullPage:true});
 assert.deepEqual(errors,[]);
 console.log('Browser E2E passed: dsh login → embedded panel → validate → approve → execute → real file moved; no page errors');
}finally{await browser.close();}
