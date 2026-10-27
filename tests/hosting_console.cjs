'use strict';
const {createHash}=require('node:crypto');
const assert=require('node:assert/strict');

// Observability classification only. The application's CSP is never modified.
async function collectHostingHashes(page,selectedOrigin,hashes){
 const url=new URL(page.url());
 if(url.origin!==selectedOrigin||!url.hostname.endsWith('.chatgpt.site'))return;
 const inline=await page.locator('script:not([src])').allTextContents();
 for(const text of inline){
  if(text.startsWith('(function(){function c(){var b=a.contentDocument||')&&
     text.includes('window.__CF$cv$params=')&&
     text.includes('/cdn-cgi/challenge-platform/scripts/jsd/main.js')){
   hashes.add(createHash('sha256').update(text).digest('base64'));
  }
 }
}
function classifyConsole(messages,hashes){
 const applicationErrors=[],hostingWarnings=[];
 for(const message of messages){
  const match=message.match(/'sha256-([^']+)'/);
  if(message.startsWith('Executing inline script violates the following Content Security Policy directive')&&match&&hashes.has(match[1])){
   hostingWarnings.push({kind:'hosting_edge_inline_script_blocked',sha256:match[1],message});
  }else applicationErrors.push(message);
 }
 return {applicationErrors,hostingWarnings};
}
function selfTest(){
 const known="Executing inline script violates the following Content Security Policy directive 'script-src self'. A hash ('sha256-known') is required.";
 const unknown=known.replace('sha256-known','sha256-unknown');
 const resource='Failed to load resource: 404';
 const runtime='TypeError: application failed';
 const result=classifyConsole([known,unknown,resource,runtime],new Set(['known']));
 assert.equal(result.hostingWarnings.length,1);
 assert.deepEqual(result.applicationErrors,[unknown,resource,runtime]);
 assert.equal(classifyConsole([known],new Set()).applicationErrors.length,1);
}
selfTest();
module.exports={collectHostingHashes,classifyConsole};
