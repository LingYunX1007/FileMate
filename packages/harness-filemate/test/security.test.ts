import {test} from 'node:test';
import assert from 'node:assert/strict';
import {isAllowedTool, resolveUiRequest, requireSameOrigin, validateServiceUrl} from '../src/security.ts';

test('a model cannot approve, execute, or bypass file tools via shell',()=>{
  assert.equal(isAllowedTool('filemate_search_files'),true);
  for(const name of ['filemate_approve','filemate_execute','exec_command','run_code','filemate_unknown']) assert.equal(isAllowedTool(name),false);
});
test('UI proxy only forwards known routes and refuses encoded traversal',()=>{
  assert.equal(resolveUiRequest('POST','/plans/123/approve')?.role,'admin');
  assert.equal(resolveUiRequest('POST','/search')?.role,'tool');
  for(const path of ['/../settings','/plans/%2e%2e/approve','//evil.test','/roots/123/scan/extra']) assert.equal(resolveUiRequest('POST',path),undefined);
  assert.equal(resolveUiRequest('DELETE','/roots/123'),undefined);
});
test('mutations reject cross-origin and missing Origin; GET needs no Origin',()=>{
  assert.doesNotThrow(()=>requireSameOrigin('POST','http://127.0.0.1:3080','127.0.0.1:3080'));
  assert.throws(()=>requireSameOrigin('POST','https://evil.test','127.0.0.1:3080'));
  assert.throws(()=>requireSameOrigin('POST',undefined,'127.0.0.1:3080'));
  assert.doesNotThrow(()=>requireSameOrigin('GET',undefined,'127.0.0.1:3080'));
});
test('internal credentials cannot be routed to a remote service',()=>{
  assert.equal(validateServiceUrl('http://127.0.0.1:8765'),'http://127.0.0.1:8765');
  for(const url of ['https://example.com','http://127.0.0.1:8765/path','http://user:pass@localhost:8765','file:///tmp']) assert.throws(()=>validateServiceUrl(url));
});
