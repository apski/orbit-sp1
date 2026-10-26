import { connect } from '../driver.mjs';
const d = await connect(process.argv[2]);
// time wire round-trip vs JS work on the same payload
let raw; const net=[]; for(let i=0;i<20;i++){const t=performance.now(); raw=await d.describe(); net.push(performance.now()-t);}
const s = JSON.stringify(raw); const js=[]; for(let i=0;i<200;i++){const t=performance.now(); const o=JSON.parse(s); o.elements.find(e=>e.identifier==='nope'); js.push(performance.now()-t);}
net.sort((a,b)=>a-b); js.sort((a,b)=>a-b);
console.log('describe round-trip p50 ms', net[10].toFixed(2), '| payload bytes', s.length, '| JS parse+find p50 ms', js[100].toFixed(3));
const ws=[]; for(let i=0;i<20;i++){const t=performance.now(); await d.cmd({cmd:'touch',type:'Move',x:0.5,y:0.02,second_x:null,second_y:null}); ws.push(performance.now()-t);} ws.sort((a,b)=>a-b);
console.log('HID cmd ack round-trip p50 ms', ws[10].toFixed(2));
d.close(); process.exit(0);
