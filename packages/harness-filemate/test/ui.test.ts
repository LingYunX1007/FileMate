import {test} from 'node:test';
import assert from 'node:assert/strict';
import {getExcerpt,bindingMatches} from '../public/app.js';

test('evidence on a later PDF page opens its global character offset',async()=>{
  const original=globalThis.fetch;
  const requested:string[]=[];
  try{
    globalThis.fetch=async(input)=>{requested.push(String(input));return new Response(JSON.stringify({text:'cited passage'}),{status:200});};
    await getExcerpt('file-id',{evidence:{version:'v1',location:{kind:'page',page:2,start:5100,end:5600,local_start:100,local_end:600}}});
    assert.equal(requested[0],'/api/filemate/files/file-id/excerpt?start=5100&limit=4000');
  }finally{globalThis.fetch=original;}
});
test('approval binding rejects a changed version, content hash, or plan identity',()=>{
  const approved={id:'p1',version:2,hash:'hash-a'};
  assert.equal(bindingMatches(approved,{...approved}),true);
  assert.equal(bindingMatches(approved,{...approved,version:3}),false);
  assert.equal(bindingMatches(approved,{...approved,hash:'hash-b'}),false);
  assert.equal(bindingMatches(approved,{...approved,id:'p2'}),false);
});
