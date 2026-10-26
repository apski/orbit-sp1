import fs from 'node:fs'; import os from 'node:os';
const dir = os.homedir()+'/.argent'; const f = fs.readdirSync(dir).find(n=>n.startsWith('tool-server-')&&n.endsWith('.json'));
const {port, token} = JSON.parse(fs.readFileSync(`${dir}/${f}`));
const udid = process.argv[2];
export async function call(name, args={}) {
  const t = performance.now();
  const r = await fetch(`http://127.0.0.1:${port}/tools/${name}`, {method:'POST', headers:{authorization:`Bearer ${token}`,'content-type':'application/json'}, body: JSON.stringify({udid, ...args})});
  const j = await r.json(); return {ms: performance.now()-t, j};
}
async function bench(label, fn, n=10){ const xs=[]; for(let i=0;i<n;i++){ xs.push(await fn()); } xs.sort((a,b)=>a-b); console.log(label.padEnd(28), 'p50', xs[n>>1].toFixed(0).padStart(5), 'min', xs[0].toFixed(0).padStart(5), 'max', xs[n-1].toFixed(0).padStart(5)); }
await call('describe'); // warm
await bench('describe', async()=> (await call('describe')).ms);
await bench('screenshot(0.25)', async()=> (await call('screenshot')).ms);
await bench('tap (blank area)', async()=> (await call('gesture-tap',{x:0.5,y:0.05})).ms);
await bench('await-screen-idle', async()=> (await call('await-screen-idle',{timeoutMs:1500})).ms);
await bench('MCP-style tap (tap+idle+shot+desc)', async()=>{ const t=performance.now(); await call('gesture-tap',{x:0.5,y:0.05}); await call('await-screen-idle',{timeoutMs:1500}); await Promise.all([call('screenshot'),call('describe')]); return performance.now()-t; }, 5);
const d = (await call('describe')).j; console.log(JSON.stringify(d).slice(0,1500));
