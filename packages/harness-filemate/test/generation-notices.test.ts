import {test} from 'node:test';
import assert from 'node:assert/strict';
import {GenerationNotices} from '../src/generation-notices.ts';

test('stop notices retain distinct sessions, deduplicate attempts and bound memory',()=>{
  const notices=new GenerationNotices(2);
  notices.add({sessionId:'a',attemptId:'1',reason:'超时',stoppedAt:1});
  notices.add({sessionId:'a',attemptId:'1',reason:'重复回调',stoppedAt:1});
  notices.add({sessionId:'b',attemptId:'1',reason:'超限',stoppedAt:3});
  assert.equal(notices.list().length,2);
  assert.equal(notices.list()[0].reason,'超时');
  notices.add({sessionId:'a',attemptId:'2',reason:'超时',stoppedAt:4});
  assert.deepEqual(notices.list().map(n=>n.sessionId),['b','a']);
  const copied=notices.list();copied[0].reason='changed';
  assert.equal(notices.list()[0].reason,'超限');
});

test('a later agent lifecycle can reuse an attempt id and must still notify',()=>{
  const notices=new GenerationNotices();
  notices.add({sessionId:'a',attemptId:'1',reason:'first',stoppedAt:1});
  notices.add({sessionId:'a',attemptId:'1',reason:'after restart',stoppedAt:2});
  assert.equal(notices.list().length,2);
});
